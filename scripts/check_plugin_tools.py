"""Exercise staged plugin workbench tools outside the source checkout."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import tarfile
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest.mock import patch
from zipfile import ZipFile


def _check_desktop_runtime(backend: Path, root: Path) -> None:
    """Launch the packaged backend and drive its plugin workbench over HTTP."""
    import httpx

    from scripts.runtime_memory_baseline import (
        _capture_process_tree,
        _port_released,
        _terminate_process_trees,
    )
    from utils.port_utils import set_port_probe_reuse

    port_keys = ("MAIN_SERVER_PORT", "MEMORY_SERVER_PORT", "TOOL_SERVER_PORT")
    with ExitStack() as sockets:
        ports = {}
        for key in port_keys:
            bound = sockets.enter_context(socket.socket())
            set_port_probe_reuse(bound)
            bound.bind(("127.0.0.1", 0))
            ports[key] = bound.getsockname()[1]
    runtime = root / "runtime"
    storage = root / "storage"
    profile = root / "profile"
    for directory in (runtime, storage, profile):
        directory.mkdir(parents=True)
    token = secrets.token_urlsafe(32)
    child_env = {
        **os.environ, **{f"NEKO_{key}": str(port) for key, port in ports.items()},
        "LOCALAPPDATA": str(profile), "APPDATA": str(profile), "USERPROFILE": str(profile),
        "HOME": str(profile), "XDG_CONFIG_HOME": str(profile / ".config"),
        "XDG_DATA_HOME": str(profile / ".local/share"), "XDG_CACHE_HOME": str(profile / ".cache"),
        "NEKO_USER_DATA_DIR": str(profile), "NEKO_RUNTIME_STATE_DIR": str(runtime),
        "NEKO_STORAGE_SELECTED_ROOT": str(storage), "NEKO_STORAGE_ANCHOR_ROOT": str(storage),
        "NEKO_RUNTIME_SHUTDOWN_TOKEN": token, "NEKO_DISABLE_AGENT_AUTO_RESTORE": "1",
    }
    log_path = root / "desktop.log"
    main_url = f"http://127.0.0.1:{ports['MAIN_SERVER_PORT']}"
    launched_ports = set(ports.values())
    print(f"Starting {backend} at {main_url}", flush=True)
    with log_path.open("wb") as output, httpx.Client(trust_env=False, timeout=30) as client:
        process = subprocess.Popen(
            [str(backend)], cwd=backend.parent, env=child_env, stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            record_path = runtime / "launcher.json"
            deadline = monotonic() + 180
            while True:
                with suppress(FileNotFoundError, PermissionError, KeyError, json.JSONDecodeError):
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                    if record["pid"] != process.pid:
                        raise RuntimeError("The isolated launcher record belongs to another process")
                    launched_ports.update(record.get("ports", {}).values())
                    launched_ports.update(record.get("internal_ports", {}).values())
                    if record["state"] == "ready":
                        plugin_port = record["internal_ports"]["USER_PLUGIN_SERVER_PORT"]
                        break
                if process.poll() is not None or monotonic() >= deadline:
                    raise RuntimeError(f"The packaged desktop backend did not become ready (exit {process.poll()})")
                sleep(0.25)

            plugin_url = f"http://127.0.0.1:{plugin_port}"
            csrf = client.get(f"{plugin_url}/security/csrf-token", headers={"Origin": plugin_url})
            csrf.raise_for_status()
            headers = {"Origin": plugin_url, "X-CSRF-Token": csrf.json()["csrf_token"]}

            prepared = client.post(f"{plugin_url}/plugin-projects/tool-environment", headers=headers)
            prepared.raise_for_status()
            scaffold = client.post(f"{plugin_url}/plugin-projects/create", headers=headers, json={
                "parent_dir": str(root), "plugin_id": "packaged_probe", "name": "Packaged Probe",
            })
            scaffold.raise_for_status()
            project_id = scaffold.json()["project_id"]
            toolchain_url = f"{plugin_url}/plugin-projects/{project_id}/toolchain"

            deadline = monotonic() + 1800
            while True:
                tools = client.get(toolchain_url)
                tools.raise_for_status()
                state = tools.json()
                environment = state.get("tool_environment")
                if environment in {"ready", "failed", "unavailable"}:
                    break
                if environment != "preparing" or monotonic() >= deadline:
                    raise RuntimeError(f"Unexpected tool environment state: {state}")
                sleep(5)
            if environment != "ready":
                raise RuntimeError(f"Packaged tool environment did not become ready: {state}")

            report = client.post(
                f"{plugin_url}/plugin-projects/{project_id}/precheck",
                headers=headers, timeout=1200,
            )
            report.raise_for_status()
            result = report.json()
            # A fresh scaffold must clear every gate. A failed stage skips the
            # later ones, so require each of them to have run and passed.
            from plugin.neko_plugin_cli.commands.precheck_cmd import (
                STAGE_LINT,
                STAGE_RELEASE_CHECK,
                STAGE_SYNC,
            )

            passed_stages = {
                stage.get("name") for stage in result.get("stages") or [] if stage.get("passed") is True
            }
            required = {STAGE_LINT, STAGE_SYNC, STAGE_RELEASE_CHECK}
            if result.get("passed") is not True or not required <= passed_stages:
                raise RuntimeError(f"Packaged runtime precheck failed: {json.dumps(result, ensure_ascii=False)}")
            print(f"Packaged desktop prepared plugin tools and ran precheck: {json.dumps(result)[:400]}", flush=True)
        except Exception:
            print("\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]), file=sys.stderr)
            raise
        finally:
            tree = _capture_process_tree(process.pid)
            if process.poll() is None:
                with suppress(httpx.HTTPError, subprocess.TimeoutExpired):
                    response = client.post(f"{main_url}/api/runtime/shutdown", headers={
                        "X-Neko-Runtime-Shutdown-Token": token,
                    })
                    response.raise_for_status()
                    process.wait(timeout=30)
            _, remaining = _terminate_process_trees([process], tree)
            process.wait(timeout=5)
            if remaining:
                raise RuntimeError(f"Packaged desktop left test processes running: {remaining}")
            busy_ports = sorted(port for port in launched_ports if not _port_released(port))
            if busy_ports:
                raise RuntimeError(f"The packaged backend did not release ports: {busy_ports}")


def check(backend: Path, *, desktop_runtime: bool = False) -> None:
    with TemporaryDirectory(prefix="neko plugin tools ") as directory:
        root = Path(directory)
        state = root / "state"
        state.mkdir()
        with patch.dict(os.environ, {
            "NEKO_STORAGE_SELECTED_ROOT": str(state),
            "NEKO_STORAGE_ANCHOR_ROOT": str(state),
        }):
            from plugin.server.application import plugin_tool_environment

            resources = plugin_tool_environment.tool_resources(backend)
            plugin_tool_environment.verify_tool_resources(resources)
            print(f"Packaged plugin tools verified (manifest {resources.key[:12]})")
            if desktop_runtime:
                _check_desktop_runtime(backend, root / "desktop")


def _extract_windows_backend(archive: Path, destination: Path) -> Path:
    prefix = "resources/bin/"
    executable = f"{prefix}projectneko_server.exe"
    with ZipFile(archive) as bundle:
        names = bundle.namelist()
        missing = {"N.E.K.O.exe", executable}.difference(names)
        if missing:
            raise RuntimeError(f"Desktop ZIP is missing: {', '.join(sorted(missing))}")
        # Keep the backend layout and test independently of the build tree.
        bundle.extractall(destination, members=(name for name in names if name.startswith(prefix)))
    return destination / executable


@contextmanager
def _packaged_backend(artifact: Path):
    if not artifact.is_file():
        raise RuntimeError(f"Packaged artifact not found: {artifact}")
    suffix = artifact.suffix.casefold()
    is_tar = artifact.name.endswith(".tar.gz")
    if suffix not in {".zip", ".appimage"} and not is_tar:
        yield artifact
        return

    with TemporaryDirectory(prefix="neko extracted desktop ") as directory:
        destination = Path(directory)
        if suffix == ".zip":
            backend = _extract_windows_backend(artifact, destination)
        elif is_tar:
            if sys.platform == "darwin":
                destination /= "N.E.K.O.app"
                destination.mkdir()
            with tarfile.open(artifact, "r:gz") as bundle:
                bundle.extractall(destination, filter="data")
            if sys.platform == "darwin":
                desktop = destination / "Contents/MacOS/N.E.K.O"
                backend_app = destination / "Contents/Resources/bin/projectneko_server.app"
                backend = backend_app / "Contents/MacOS/projectneko_server"
                # The outer app may be unsigned in nightly builds. The inner
                # Nuitka app is signed even when Electron signing is disabled.
                signed_apps = [backend_app]
                if (destination / "Contents/_CodeSignature/CodeResources").is_file():
                    signed_apps.append(destination)
                for app in signed_apps:
                    subprocess.run(
                        ["codesign", "--verify", "--deep", "--strict", str(app)],
                        check=True, timeout=120,
                    )
            else:
                desktop = destination / "n.e.k.o"
                backend = destination / "resources/bin/projectneko_server"
            if not desktop.is_file():
                raise RuntimeError(f"Portable archive is missing the desktop executable: {desktop}")
        else:
            subprocess.run(
                [str(artifact), "--appimage-extract"], cwd=destination,
                check=True, timeout=300, stdout=subprocess.DEVNULL,
            )
            backend = destination / "squashfs-root/resources/bin/projectneko_server"
        if not backend.is_file():
            raise RuntimeError(f"Desktop artifact is missing the backend: {backend}")
        yield backend


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="Frozen backend executable, Portable ZIP/tar.gz, or Linux AppImage")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-desktop-runtime", action="store_true", help="Start the complete desktop backend")
    mode.add_argument("--resources-only", action="store_true", help="Verify the bundled plugin tool resources")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    with _packaged_backend(args.artifact.resolve()) as backend:
        check(backend, desktop_runtime=args.check_desktop_runtime)
