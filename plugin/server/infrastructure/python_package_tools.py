"""Prepare dependency installers without changing the host Python environment.

Source servers use their interpreter and existing uv/pip. Packaged servers
prepare an app-owned uv and matching CPython when a sync is requested; merely
opening the workbench never downloads tools. Both paths install into vendor/.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import CancelledError
from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from typing import Protocol
from urllib.parse import urlsplit
import zipfile

from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename
import portalocker

from plugin.core.packaged_metadata import build_environment
from plugin.utils.http_imports import load_httpx

_UV_VERSION = "0.13.6"
_MAX_WHEEL_BYTES = 128 * 1024 * 1024
_MAX_BINARY_BYTES = 256 * 1024 * 1024
_SUPPORTED_PLATFORMS = frozenset({"win32", "darwin", "linux"})


class CommandRunner(Protocol):
    def __call__(
        self, cmd: list[str], *, label: str, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]: ...


@dataclass(frozen=True)
class PackageTools:
    python: str
    uv_executable: str | None
    allow_pip_fallback: bool = True
    env: dict[str, str] = field(default_factory=dict)


def _is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def _tools_root() -> Path:
    from plugin.settings import get_plugin_state_root

    return get_plugin_state_root().parent / "python-package-tools"


def _find_uv() -> str | None:
    from plugin.neko_plugin_cli.core.vendor_sync import find_uv

    return find_uv()


def _cached_uv() -> Path:
    environment = build_environment()
    name = "uv.exe" if sys.platform == "win32" else "uv"
    return _tools_root() / "uv" / _UV_VERSION / f"{environment['os']}-{environment['arch']}" / name


def _available_uv() -> str | None:
    installed = _find_uv()
    if installed:
        return installed
    cached = _cached_uv()
    return str(cached) if cached.is_file() else None


def _pip_available() -> bool:
    return not _is_packaged() and importlib.util.find_spec("pip") is not None


def _prepared_python() -> str | None:
    try:
        record = json.loads((_tools_root() / "python.json").read_text(encoding="utf-8"))
        python = record["executable"]
        if (
            record["environment"] == build_environment()
            and record.get("python_version") == list(sys.version_info[:3])
            and isinstance(python, str)
            and Path(python).is_file()
        ):
            return python
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def read_package_tools() -> dict[str, object]:
    """Local capability facts; no child process, network or directory creation."""
    packaged = _is_packaged()
    uv = _available_uv()
    pip = _pip_available()
    prepared_python = _prepared_python() if packaged else sys.executable
    supported = sys.platform in _SUPPORTED_PLATFORMS
    return {
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": sys.platform,
        "architecture": build_environment()["arch"],
        "python_executable": prepared_python,
        "installer": "uv" if uv else "pip" if pip else None,
        "installer_path": uv or (sys.executable if pip else None),
        "packaged": packaged,
        "can_sync": supported or (not packaged and (uv is not None or pip)),
        "reason": None if supported or (not packaged and (uv or pip)) else "unsupported_platform",
        "setup_required": (packaged and (prepared_python is None or uv is None)) or (uv is None and not pip),
    }


def _check_cancel(
    cancel_event: threading.Event, check_cancelled: Callable[[], None] | None = None,
) -> None:
    if cancel_event.is_set():
        raise CancelledError("Python package task canceled")
    if check_cancelled is not None:
        check_cancelled()


def _select_uv_wheel(metadata: dict, compatible_tags: list | None = None) -> dict:
    tags = list(sys_tags()) if compatible_tags is None else compatible_tags
    ranks = {tag: rank for rank, tag in enumerate(tags)}
    matches: list[tuple[int, dict]] = []
    for item in metadata.get("urls", []):
        if item.get("packagetype") != "bdist_wheel" or item.get("yanked"):
            continue
        try:
            name, version, _, wheel_tags = parse_wheel_filename(item["filename"])
        except (ValueError, KeyError):
            continue
        matching = [ranks[tag] for tag in wheel_tags if tag in ranks]
        if name == "uv" and str(version) == _UV_VERSION and matching:
            matches.append((min(matching), item))
    if not matches:
        raise RuntimeError("No dependency installer is available for this platform")
    return min(matches, key=lambda match: match[0])[1]


def _download_uv(
    destination: Path, log: Callable[[str], None], cancel_event: threading.Event,
    *, check_cancelled: Callable[[], None] | None = None,
) -> None:
    """Extract only uv and its licenses from a pinned, SHA-256 checked wheel."""
    _check_cancel(cancel_event, check_cancelled)
    httpx = load_httpx()
    log(f"Preparing uv {_UV_VERSION}")
    with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False) as client:
        with client.stream("GET", f"https://pypi.org/pypi/uv/{_UV_VERSION}/json") as response:
            response.raise_for_status()
            metadata_bytes = bytearray()
            for chunk in response.iter_bytes():
                _check_cancel(cancel_event, check_cancelled)
                metadata_bytes.extend(chunk)
                if len(metadata_bytes) > 2 * 1024 * 1024:
                    raise RuntimeError("Dependency installer metadata exceeds its size limit")
        _check_cancel(cancel_event, check_cancelled)
        wheel = _select_uv_wheel(json.loads(metadata_bytes))
        address = urlsplit(wheel["url"])
        if address.scheme != "https" or address.hostname != "files.pythonhosted.org":
            raise RuntimeError("Unexpected dependency installer download origin")
        expected = wheel.get("digests", {}).get("sha256", "")
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            raise RuntimeError("Dependency installer checksum is unavailable")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".uv-", dir=destination.parent) as temporary:
            archive_path = Path(temporary) / "uv.whl"
            digest = hashlib.sha256()
            received = 0
            with client.stream("GET", wheel["url"]) as response, archive_path.open("wb") as output:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    _check_cancel(cancel_event, check_cancelled)
                    received += len(chunk)
                    if received > _MAX_WHEEL_BYTES:
                        raise RuntimeError("Dependency installer download exceeds its size limit")
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != expected:
                raise RuntimeError("Dependency installer checksum verification failed")
            binary = Path(temporary) / destination.name
            with zipfile.ZipFile(archive_path) as archive:
                member = archive.getinfo(f"uv-{_UV_VERSION}.data/scripts/{destination.name}")
                if member.file_size > _MAX_BINARY_BYTES:
                    raise RuntimeError("Dependency installer executable exceeds its size limit")
                with archive.open(member) as source, binary.open("wb") as output:
                    while chunk := source.read(65536):
                        _check_cancel(cancel_event, check_cancelled)
                        output.write(chunk)
                for name in archive.namelist():
                    if ".dist-info/licenses/" in name and not name.endswith("/"):
                        license_info = archive.getinfo(name)
                        if license_info.file_size <= 1024 * 1024:
                            license_path = destination.parent / f"{Path(name).name}.txt"
                            license_path.write_bytes(archive.read(name))
            _check_cancel(cancel_event, check_cancelled)
            binary.chmod(0o755)
            os.replace(binary, destination)


def _prepare_packaged_python(
    uv: str, run_command: CommandRunner, cancel_event: threading.Event,
    *, check_cancelled: Callable[[], None] | None = None,
) -> PackageTools:
    python_root = _tools_root() / "python"
    env = {"UV_PYTHON_INSTALL_DIR": str(python_root)}
    # python_full_version markers must resolve as they do in the host, not
    # against a newer patch release with the same extension-module ABI.
    request = f"cpython@{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    find = [uv, "python", "find", "--system", "--no-project", "--managed-python", "--no-python-downloads", request]
    found = run_command(find, label="Locate compatible Python", env=env)
    if found.returncode:
        _check_cancel(cancel_event, check_cancelled)
        installed = run_command(
            [uv, "python", "install", request, "--install-dir", str(python_root), "--no-bin", "--no-registry"],
            label="Prepare compatible Python", env=env,
        )
        if installed.returncode:
            raise RuntimeError("Could not prepare the Python interpreter for dependency installation")
        found = run_command(find, label="Locate compatible Python", env=env)
    if found.returncode or not found.stdout.strip():
        raise RuntimeError("The dependency installer could not locate compatible Python")
    python = found.stdout.strip().splitlines()[-1]
    probe = run_command(
        [python, "-c", "import json,sys,struct; print(json.dumps([list(sys.version_info[:3]),sys.platform,struct.calcsize('P')]))"],
        label="Check compatible Python", env=env,
    )
    import struct

    expected = [list(sys.version_info[:3]), sys.platform, struct.calcsize("P")]
    if probe.returncode or json.loads(probe.stdout) != expected:
        raise RuntimeError("The dependency interpreter does not match the running host")
    from utils.file_utils import atomic_write_json

    _check_cancel(cancel_event, check_cancelled)
    atomic_write_json(_tools_root() / "python.json", {
        "executable": python, "environment": build_environment(), "python_version": list(sys.version_info[:3]),
    })
    return PackageTools(python, uv, allow_pip_fallback=False, env=env)


def prepare_package_tools(
    run_command: CommandRunner, log: Callable[[str], None], cancel_event: threading.Event,
    *, check_cancelled: Callable[[], None] | None = None,
) -> PackageTools:
    """Called in the dependency task's worker thread, after explicit sync."""
    _check_cancel(cancel_event, check_cancelled)
    uv = _available_uv()
    if not _is_packaged() and (uv or _pip_available()):
        return PackageTools(sys.executable, uv)
    if sys.platform not in _SUPPORTED_PLATFORMS:
        raise RuntimeError("Dependency installation is unavailable on this platform")
    root = _tools_root()
    root.mkdir(parents=True, exist_ok=True)
    lock = portalocker.Lock(root / ".prepare.lock", timeout=0)
    while True:
        _check_cancel(cancel_event, check_cancelled)
        try:
            lock.acquire()
            break
        except portalocker.exceptions.LockException:
            cancel_event.wait(0.1)
    try:
        _check_cancel(cancel_event, check_cancelled)
        uv = _available_uv()
        if uv is None:
            target = _cached_uv()
            _download_uv(target, log, cancel_event, check_cancelled=check_cancelled)
            uv = str(target)
        if _is_packaged():
            return _prepare_packaged_python(uv, run_command, cancel_event, check_cancelled=check_cancelled)
        return PackageTools(sys.executable, uv)
    finally:
        lock.release()
