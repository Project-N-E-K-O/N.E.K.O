"""Shared uv / Python preparation for packaged plugin tools.

No test here reaches the network: release assets are served by a fake in
place of the market transfer (test_package_fetch covers that bridge), and uv
itself is replaced by a fake command runner.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlparse
from urllib.request import url2pathname
import zipfile

from packaging.tags import Tag
import pytest

from plugin.server.infrastructure import package_download as module_download
from plugin.server.infrastructure import plugin_tool_runtime as module
from plugin.server.infrastructure.package_fetch import FetchedAsset, NoRoutesLeft, RouteFailed


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tools = tmp_path / "app-docs" / "plugin-runtime" / "tools"
    monkeypatch.setattr(module, "tools_root", lambda: tools)
    monkeypatch.setattr(module, "_known_tools_root", lambda: tools)
    monkeypatch.setattr(module, "_is_packaged", lambda: True)
    monkeypatch.setattr(module, "_system_uv_candidates", lambda: [])
    monkeypatch.setattr(module, "_POLL_SECONDS", 0.01)
    monkeypatch.setattr(module, "_last_error", None)
    monkeypatch.setattr(module, "_progress", None)
    monkeypatch.setattr(module, "_background", None)
    monkeypatch.setattr(
        module, "_fetch_release_asset",
        lambda *_args, **_kwargs: pytest.fail("this test must not download anything"),
    )
    monkeypatch.setattr(module, "_record_route_failure", lambda *_args: pytest.fail("nothing to penalize"))
    return tools


_TEST_TARGET = "x86_64-pc-windows-msvc" if sys.platform == "win32" else "x86_64-unknown-linux-gnu"
PYTHON_TAG = "20251007"
PYTHON_FILE = "cpython-test+20251007-install_only_stripped.tar.gz"
PYTHON_BODY = b"python-build-standalone archive"


def _uv_archive(target: str, binary_name: str, data: bytes = b"uv-executable") -> bytes:
    buffer = io.BytesIO()
    if target.endswith("-windows-msvc"):
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(binary_name, data)
            archive.writestr("uvx.exe", b"uvx")
            # Extraction must never deploy any other member.
            archive.writestr("../../outside", b"unsafe")
    else:
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for name, payload in ((f"uv-{target}/{binary_name}", data), (f"uv-{target}/uvx", b"uvx"),
                                  ("../../outside", b"unsafe")):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


class FakeRelease:
    """Serves GitHub release assets in place of the market transfer; counts downloads.

    ``bodies`` maps an asset URL to the bytes each successive attempt delivers
    (the last repeats); the uv archive is served by default. Routes are
    ``github-direct``, then the mirrors ``route-1``, ``route-2``..., in the
    order they are used.
    """

    def __init__(
        self, *, digest: str | None = None, fail: bool = False, gate: threading.Event | None = None,
        routes: int = 5,
    ) -> None:
        self.binary_name = "uv.exe" if sys.platform == "win32" else "uv"
        self.target = _TEST_TARGET
        self.body = _uv_archive(self.target, self.binary_name)
        self.uv_url = f"{module._UV_RELEASE_URL}/{module._uv_archive_name(self.target)}"
        self.pinned = {self.target: digest or hashlib.sha256(self.body).hexdigest()}
        self.bodies: dict[str, list[bytes]] = {
            self.uv_url: [self.body],
            module._PYTHON_GITHUB_PREFIX + f"{PYTHON_TAG}/{PYTHON_FILE.replace('+', '%2B')}": [PYTHON_BODY],
        }
        self.fail = fail
        self.gate = gate
        self.routes = routes
        self.wheel_downloads = 0
        self.requests: list[tuple[str, tuple[str, ...]]] = []
        self.penalized: list[str] = []
        self.single: list[bool] = []
        # Routes that fail when used one at a time.
        self.broken_routes: set[str] = set()
        # Defaults to github-direct, then mirrors route-1, route-2...
        self.route_names: list[str] | None = None
        # The race delivers files spliced from the first two routes.
        self.spliced = False
        self.on_chunk = None

    def pin_served_archive(self) -> None:
        """Make the pinned digest match the uv archive served."""
        self.pinned[self.target] = hashlib.sha256(self.body).hexdigest()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeRelease:
        release = self
        monkeypatch.setattr(module, "_uv_target", lambda: release.target)
        monkeypatch.setattr(module, "_UV_ARCHIVE_SHA256", self.pinned)

        def fetch(url, directory, *, maximum_bytes, deadline, check_stopped, progress, exclude=(),
                  single_route=False):
            assert deadline > 0
            release.requests.append((url, tuple(exclude)))
            release.single.append(single_route)
            check_stopped()
            names = release.route_names or ["github-direct", *(f"route-{index}" for index in range(1, release.routes))]
            available = [name for name in names[:release.routes] if name not in exclude]
            if not available:
                raise NoRoutesLeft(url)
            if single_route and available[0] in release.broken_routes:
                raise RouteFailed(available[0], OSError("connection reset"))
            if release.fail:
                raise OSError("connection refused")
            if url == release.uv_url:
                release.wheel_downloads += 1
            if release.gate is not None:
                release.gate.wait(5)
            bodies = release.bodies[url]
            attempt = sum(1 for requested, _exclude in release.requests[:-1] if requested == url)
            data = bodies[min(attempt, len(bodies) - 1)]
            if len(data) > maximum_bytes:
                raise module_download.PackageSizeExceeded(len(data), maximum_bytes)
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"download-{len(release.requests)}"
            received = 0
            midpoint = len(data) // 2
            try:
                with path.open("wb") as output:
                    for chunk in (data[:midpoint], data[midpoint:]):
                        if release.on_chunk is not None:
                            release.on_chunk(url, received)
                        check_stopped()
                        output.write(chunk)
                        received += len(chunk)
                        progress(received, len(data))
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            spliced = release.spliced and not single_route
            return FetchedAsset(path, tuple(available[:2]) if spliced else (available[0],), 7)

        def penalize(source_id, generation, cancel, deadline):
            assert generation == 7 and isinstance(cancel, threading.Event) and deadline > 0
            release.penalized.append(source_id)

        monkeypatch.setattr(module, "_fetch_release_asset", fetch)
        monkeypatch.setattr(module, "_record_route_failure", penalize)
        return self


def _identity(**overrides) -> str:
    values = [
        list(sys.version_info[:3]), sys.platform, struct.calcsize("P"),
        module.sysconfig.get_platform(), module.platform.machine() if sys.platform == "darwin" else None,
    ]
    for index, value in overrides.items():
        values[int(index[1:])] = value
    return json.dumps(values)


_original_fetch = module._fetch_release_asset


class FakeUv:
    """Stands in for uv and the managed interpreter."""

    def __init__(self, root: Path, *, identity: str | None = None, install_fails: bool = False,
                 versions: dict[str, str] | None = None, listing: str | None = None) -> None:
        self.python = str(root / "python" / "cpython" / "bin" / "python3")
        self.identity = _identity() if identity is None else identity
        self.install_fails = install_fails
        self.versions = versions or {}
        # `python list` output; None makes the listing fail, so uv downloads by itself.
        self.listing = listing
        self.mirrored: list[bytes] = []
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, cmd, *, label, env, cancel, deadline):
        assert isinstance(label, str) and isinstance(cancel, threading.Event) and deadline > 0
        self.calls.append((cmd, env))
        if cmd[1:] == ["--version"]:
            # A real file is the uv this module downloaded (the fake wheel's).
            version = self.versions.get(cmd[0]) or (module.PINNED_UV_VERSION if Path(cmd[0]).is_file() else None)
            return subprocess.CompletedProcess(cmd, 0 if version else 1, f"uv {version}\n" if version else "", "")
        # Modelled on uv 0.13.6: `find` queries the interpreter, so an empty or
        # non-executable one fails it; `install` sees the installation directory
        # and answers "already installed" (exit 0) without repairing anything.
        if cmd[1:3] == ["python", "find"]:
            if module._usable_executable(self.python):
                return subprocess.CompletedProcess(cmd, 0, self.python + "\n", "")
            if Path(self.python).exists():
                return subprocess.CompletedProcess(cmd, 2, "", "error: Failed to query Python interpreter")
            return subprocess.CompletedProcess(cmd, 2, "", "not found")
        if cmd[1:3] == ["python", "list"]:
            assert "--offline" in cmd
            if self.listing is None:
                return subprocess.CompletedProcess(cmd, 2, "", "error: unexpected argument")
            return subprocess.CompletedProcess(cmd, 0, self.listing, "")
        if cmd[1:3] == ["python", "install"] and "--mirror" in cmd:
            # uv reads the build from the mirror and checks the SHA-256 it carries.
            uri = urlparse(cmd[cmd.index("--mirror") + 1])
            # A UNC temporary directory keeps its host in the URI's netloc.
            mirror = Path(url2pathname(f"//{uri.netloc}{uri.path}" if uri.netloc else uri.path))
            data = (mirror / PYTHON_TAG / PYTHON_FILE).read_bytes()
            self.mirrored.append(data)
            if data == b"<html>":
                return subprocess.CompletedProcess(
                    cmd, 1, "", "error: Failed to install\n  cause: Failed to extract archive: x.tar.gz\n"
                    "  cause: Invalid gzip header",
                )
            if data != PYTHON_BODY:
                return subprocess.CompletedProcess(cmd, 1, "", "error: Failed to install\n  cause: Hash mismatch for `x`")
        if cmd[1:3] == ["python", "install"]:
            if self.install_fails:
                return subprocess.CompletedProcess(cmd, 1, "", "error: no download for this platform")
            if Path(self.python).exists():
                return subprocess.CompletedProcess(cmd, 0, "", "already installed")
            _write_executable(Path(self.python), b"#!python")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == self.python:
            if Path(self.python).read_bytes() != b"#!python":
                # Listed by uv but no longer starts (a quarantined DLL, say).
                return subprocess.CompletedProcess(cmd, 1, "", "error while loading shared libraries")
            return subprocess.CompletedProcess(cmd, 0, self.identity, "")
        raise AssertionError(f"unexpected command {cmd}")


def _write_executable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o755)


def _prepare(runner, **kwargs) -> module.ToolRuntime:
    kwargs.setdefault("cancel", threading.Event())
    kwargs.setdefault("deadline", time.monotonic() + 60)
    return module.prepare_tool_runtime(run_command=runner, **kwargs)


def _no_runner(*_args, **_kwargs):
    pytest.fail("no child process may start here")


# ---------------------------------------------------------------------------
# Source checkout


def test_source_returns_current_interpreter_without_preparing(root, monkeypatch):
    monkeypatch.setattr(module, "_is_packaged", lambda: False)
    result = _prepare(_no_runner)
    assert result.python == sys.executable
    assert result.packaged is False
    assert result.root is None
    assert module.read_tool_runtime_status().state == "source"
    assert asyncio.run(module.ensure_tool_runtime(timeout=1)).python == sys.executable
    assert not root.exists()


def test_source_environment_keeps_the_developers_uv_settings(root, monkeypatch):
    monkeypatch.setattr(module, "_is_packaged", lambda: False)
    monkeypatch.setenv("UV_INDEX_URL", "https://mirror.invalid/simple")
    assert _prepare(_no_runner).env["UV_INDEX_URL"] == "https://mirror.invalid/simple"


# ---------------------------------------------------------------------------
# Packaged first preparation and read-only status


def test_packaged_first_preparation_downloads_verifies_installs_and_records(root, monkeypatch):
    pypi = FakeRelease().install(monkeypatch)
    uv = FakeUv(root)
    events: list[module.ProgressEvent] = []

    result = _prepare(uv, progress=events.append)

    cached = module.cached_uv_path()
    assert cached.read_bytes() == b"uv-executable"
    # Only the executable is taken from the archive.
    assert sorted(path.name for path in cached.parent.iterdir()) == [pypi.binary_name]
    assert pypi.requests == [(f"https://github.com/astral-sh/uv/releases/download/{module.PINNED_UV_VERSION}/"
                              f"{module._uv_archive_name(pypi.target)}", ())]
    assert cached.parent.parent.name == module.PINNED_UV_VERSION
    assert result == module.ToolRuntime(uv.python, str(cached), result.env, root, True)
    assert result.env["UV_PYTHON_INSTALL_DIR"] == str(root / "python")
    install = next(cmd for cmd, _env in uv.calls if cmd[1:3] == ["python", "install"])
    assert module.packaged_python_request() in install
    assert {"--no-bin", "--no-registry", "--no-config"} <= set(install)
    assert install[install.index("--install-dir") + 1] == str(root / "python")
    assert all(env == result.env for _cmd, env in uv.calls)
    record = json.loads((root / "python.json").read_text(encoding="utf-8"))
    assert record["executable"] == uv.python
    assert record["uv"] == str(cached)
    assert record["uv_version"] == module.PINNED_UV_VERSION
    assert record["python_version"] == list(sys.version_info[:3])
    assert [event.phase for event in events][0] == "downloading_uv"
    assert {"downloading_uv", "installing_python", "checking_python"} <= {event.phase for event in events}
    assert events[-1].phase == "checking_python"
    downloaded = [event for event in events if event.phase == "downloading_uv" and event.downloaded_bytes]
    assert downloaded[-1].downloaded_bytes == downloaded[-1].total_bytes == len(pypi.body)
    assert not (root / "envs").exists()


def test_ready_status_and_prepare_start_no_child_process(root, monkeypatch):
    FakeRelease().install(monkeypatch)
    _prepare(FakeUv(root))
    monkeypatch.setattr(module, "load_httpx", lambda: pytest.fail("no network"))
    monkeypatch.setattr(subprocess, "Popen", _no_runner)

    status = module.read_tool_runtime_status()
    assert status.state == "ready"
    assert status.uv == str(module.cached_uv_path())
    assert _prepare(_no_runner).uv == status.uv


def test_status_read_creates_nothing_when_missing(root, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _no_runner)
    status = module.read_tool_runtime_status()
    assert status.state == "missing"
    assert status.as_dict()["uv_version"] == module.PINNED_UV_VERSION
    assert not root.exists()


def test_stale_record_is_not_ready(root, monkeypatch):
    FakeRelease().install(monkeypatch)
    uv = FakeUv(root)
    _prepare(uv)
    Path(uv.python).unlink()
    assert module.read_tool_runtime_status().state == "missing"
    record = json.loads((root / "python.json").read_text(encoding="utf-8"))
    _write_executable(Path(uv.python), b"#!python")
    record["python_version"] = [3, 0, 0]
    (root / "python.json").write_text(json.dumps(record), encoding="utf-8")
    assert module.read_tool_runtime_status().state == "missing"


def test_unsupported_platform(root, monkeypatch):
    monkeypatch.setattr(module, "_SUPPORTED_PLATFORMS", frozenset())
    assert module.read_tool_runtime_status().state == "unsupported"
    with pytest.raises(module.ToolRuntimeError) as raised:
        _prepare(_no_runner)
    assert raised.value.code == "unsupported_platform"
    assert not root.exists()


@pytest.mark.parametrize(("platform_name", "architecture", "libc", "target"), [
    ("win32", "x86_64", None, "x86_64-pc-windows-msvc"),
    ("win32", "aarch64", None, "aarch64-pc-windows-msvc"),
    ("win32", "x86", None, "i686-pc-windows-msvc"),
    ("darwin", "aarch64", None, "aarch64-apple-darwin"),
    ("darwin", "x86_64", None, "x86_64-apple-darwin"),
    ("linux", "x86_64", "gnu", "x86_64-unknown-linux-gnu"),
    ("linux", "aarch64", "musl", "aarch64-unknown-linux-musl"),
    ("linux", "armv7", "gnu", "armv7-unknown-linux-gnueabihf"),
    ("linux", "x86", "musl", "i686-unknown-linux-musl"),
    ("darwin", "x86", None, None),
    ("linux", "s390x", "musl", None),
    ("freebsd", "x86_64", None, None),
])
def test_uv_archive_for_each_host(platform_name, architecture, libc, target):
    assert module._uv_target(platform_name, architecture, libc) == target


def test_every_pinned_uv_archive_has_a_sha256():
    assert len(module._UV_ARCHIVE_SHA256) == 17
    for target, digest in module._UV_ARCHIVE_SHA256.items():
        assert len(digest) == 64 and set(digest) <= set("0123456789abcdef"), target
    assert module._uv_archive_name("x86_64-pc-windows-msvc") == "uv-x86_64-pc-windows-msvc.zip"
    assert module._uv_archive_name("aarch64-apple-darwin") == "uv-aarch64-apple-darwin.tar.gz"


def test_linux_libc_follows_the_interpreter_tags():
    assert module._linux_libc([Tag("cp311", "cp311", "manylinux_2_17_x86_64")]) == "gnu"
    assert module._linux_libc([Tag("cp311", "cp311", "musllinux_1_2_x86_64")]) == "musl"
    assert module._linux_libc([Tag("cp311", "cp311", "linux_x86_64")]) is None


# ---------------------------------------------------------------------------
# Download failures


def _expect_failure(runner, code: str, **kwargs) -> module.ToolRuntimeError:
    with pytest.raises(module.ToolRuntimeError) as raised:
        _prepare(runner, **kwargs)
    assert raised.value.code == code
    return raised.value


def test_checksum_mismatch_leaves_no_executable_and_is_reported(root, monkeypatch):
    FakeRelease(digest="0" * 64).install(monkeypatch)
    _expect_failure(FakeUv(root), "checksum_mismatch")
    assert not module.cached_uv_path().exists()
    assert list(module.cached_uv_path().parent.glob(".uv-*")) == []
    status = module.read_tool_runtime_status()
    assert (status.state, status.error_code) == ("failed", "checksum_mismatch")
    assert status.error_detail


def test_download_over_the_size_limit_is_rejected(root, monkeypatch):
    FakeRelease().install(monkeypatch)
    monkeypatch.setattr(module, "_MAX_ARCHIVE_BYTES", 100)
    _expect_failure(FakeUv(root), "download_failed")
    assert not module.cached_uv_path().exists()
    assert list(module.cached_uv_path().parent.glob(".uv-*")) == []


def test_download_gets_the_preparation_deadline(root, monkeypatch):
    pypi = FakeRelease().install(monkeypatch)
    seen: list[float] = []
    fetch = module._fetch_release_asset

    def spy(url, directory, **kwargs):
        seen.append(kwargs["deadline"])
        return fetch(url, directory, **kwargs)

    monkeypatch.setattr(module, "_fetch_release_asset", spy)
    deadline = time.monotonic() + 1234.0
    module._download_uv(module.cached_uv_path(), lambda _event: None, threading.Event(), deadline)
    assert seen == [deadline]
    assert pypi.wheel_downloads == 1


# ---------------------------------------------------------------------------
# Python installation


def test_python_identity_mismatch_records_nothing(root, monkeypatch):
    FakeRelease().install(monkeypatch)
    _expect_failure(FakeUv(root, identity=_identity(i0=[3, 0, 0])), "python_mismatch")
    assert not (root / "python.json").exists()


def test_python_of_another_architecture_is_rejected(root, monkeypatch):
    FakeRelease().install(monkeypatch)
    monkeypatch.setattr(module, "_host_architecture", lambda: "x86_64")
    _expect_failure(FakeUv(root, identity=_identity(i3="linux-aarch64", i4=None)), "python_mismatch")
    assert not (root / "python.json").exists()


@pytest.mark.parametrize("stdout", ["not json", "{}", "[1, 2]"])
def test_freshly_installed_python_with_unreadable_probe_is_an_install_failure(root, monkeypatch, stdout):
    FakeRelease().install(monkeypatch)
    error = _expect_failure(FakeUv(root, identity=stdout), "python_install_failed")
    assert "does not run" in error.detail and "unreadable output" in error.detail


def test_python_install_failure(root, monkeypatch):
    FakeRelease().install(monkeypatch)
    error = _expect_failure(FakeUv(root, install_fails=True), "python_install_failed")
    assert "no download for this platform" in error.detail


@pytest.mark.parametrize(("tag", "architecture"), [
    ("win-amd64", "x86_64"), ("win-arm64", "aarch64"), ("win32", "x86"),
    ("macosx-15.0-x86_64", "x86_64"), ("macosx-15.0-arm64", "aarch64"),
    ("linux-x86_64", "x86_64"), ("linux-aarch64", "aarch64"),
])
def test_architecture_uses_interpreter_platform(tag, architecture):
    assert module._python_architecture(tag) == architecture


@pytest.mark.parametrize(("machine", "architecture"), [("arm64", "aarch64"), ("x86_64", "x86_64")])
def test_macos_universal_interpreter_uses_running_architecture(machine, architecture):
    assert module._python_architecture("macosx-11.0-universal2", machine) == architecture


# ---------------------------------------------------------------------------
# Cancel and deadline


def test_cancel_before_start_creates_nothing_and_is_not_a_failure(root):
    canceled = threading.Event()
    canceled.set()
    _expect_failure(_no_runner, "canceled", cancel=canceled)
    assert not root.exists()
    assert module.read_tool_runtime_status().state == "missing"


def test_cancel_during_download_is_not_recorded_as_failure(root, monkeypatch):
    pypi = FakeRelease().install(monkeypatch)
    canceled = threading.Event()
    pypi.on_chunk = lambda _url, _received: canceled.set()
    _expect_failure(FakeUv(root), "canceled", cancel=canceled)
    assert not module.cached_uv_path().exists()
    assert module.read_tool_runtime_status().state == "missing"


def test_deadline_passed(root):
    _expect_failure(_no_runner, "timeout", deadline=time.monotonic() - 1)


@pytest.mark.parametrize("phase", [0, 1])
def test_download_checks_the_deadline_between_chunks(root, monkeypatch, phase):
    from tests.fake_clock import patch_module_clock

    clock = [100.0]
    patch_module_clock(monkeypatch, module, monotonic=lambda: clock[0])
    pypi = FakeRelease().install(monkeypatch)

    def on_chunk(_url, received):
        if (received > 0) == bool(phase):
            clock[0] = 200.0

    pypi.on_chunk = on_chunk
    _expect_failure(FakeUv(root), "timeout", deadline=150.0)
    assert not module.cached_uv_path().exists()
    assert module.read_tool_runtime_status().error_code == "timeout"


def test_default_runner_kills_the_child_on_cancel():
    canceled = threading.Event()
    threading.Timer(0.2, canceled.set).start()
    started = time.monotonic()
    with pytest.raises(module.ToolRuntimeError) as raised:
        module._run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"], label="sleep",
            env=dict(os.environ), cancel=canceled, deadline=time.monotonic() + 60,
        )
    assert raised.value.code == "canceled"
    assert time.monotonic() - started < 10


def test_default_runner_returns_output():
    result = module._run_command(
        [sys.executable, "-c", "print('ok')"], label="echo",
        env=dict(os.environ), cancel=threading.Event(), deadline=time.monotonic() + 60,
    )
    assert (result.returncode, result.stdout.strip()) == (0, "ok")


# ---------------------------------------------------------------------------
# Single flight and the cross-process lock


def test_two_threads_preparing_together_download_once(root, monkeypatch):
    gate = threading.Event()
    pypi = FakeRelease(gate=gate).install(monkeypatch)
    uv = FakeUv(root)
    results: list[module.ToolRuntime] = []
    errors: list[Exception] = []

    def worker():
        try:
            results.append(_prepare(uv))
        except Exception as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 5
    while pypi.wheel_downloads == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert module.read_tool_runtime_status().state == "preparing"
    gate.set()
    for thread in threads:
        thread.join(10)
    assert errors == []
    assert pypi.wheel_downloads == 1
    assert len(results) == 2 and results[0] == results[1]
    assert sum(1 for cmd, _env in uv.calls if cmd[1:3] == ["python", "install"]) == 1


def test_cancel_clears_an_earlier_failure(root, monkeypatch):
    pypi = FakeRelease(digest="0" * 64).install(monkeypatch)
    _expect_failure(FakeUv(root), "checksum_mismatch")
    assert module.read_tool_runtime_status().state == "failed"
    pypi.pin_served_archive()
    canceled = threading.Event()
    pypi.on_chunk = lambda _url, _received: canceled.set()
    _expect_failure(FakeUv(root), "canceled", cancel=canceled)
    assert module.read_tool_runtime_status().state == "missing"


def test_damaged_cached_uv_is_downloaded_again(root, monkeypatch):
    pypi = FakeRelease().install(monkeypatch)
    cached = module.cached_uv_path()
    cached.parent.mkdir(parents=True)
    _write_executable(cached, b"damaged")
    uv = FakeUv(root)

    def runner(cmd, **kwargs):
        if cmd == [str(cached), "--version"] and cached.read_bytes() == b"damaged":
            return subprocess.CompletedProcess(cmd, 1, "", "exec format error")
        return uv(cmd, **kwargs)

    assert _prepare(runner).uv == str(cached)
    assert pypi.wheel_downloads == 1
    assert cached.read_bytes() == b"uv-executable"


def test_intact_cached_uv_is_reused(root, monkeypatch):
    pypi = FakeRelease().install(monkeypatch)
    cached = module.cached_uv_path()
    cached.parent.mkdir(parents=True)
    _write_executable(cached, b"cached")
    assert _prepare(FakeUv(root)).uv == str(cached)
    assert pypi.wheel_downloads == 0


def test_tools_root_does_not_run_config_migrations(tmp_path, monkeypatch):
    import utils.config_manager as config_manager

    calls = []

    def get_config_manager(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(app_docs_dir=str(tmp_path))

    monkeypatch.setattr(config_manager, "get_config_manager", get_config_manager)
    assert module.tools_root() == tmp_path / "plugin-runtime" / "tools"
    assert calls == [{"migrate": False}]


# ---------------------------------------------------------------------------
# uv on PATH as fallback


def test_failed_download_falls_back_to_a_new_enough_system_uv(root, monkeypatch):
    FakeRelease(fail=True).install(monkeypatch)
    monkeypatch.setattr(module, "_system_uv_candidates", lambda: ["/old/uv", "/new/uv"])
    uv = FakeUv(root, versions={"/old/uv": "0.3.0", "/new/uv": "99.0.0"})
    result = _prepare(uv)
    assert result.uv == "/new/uv"
    assert json.loads((root / "python.json").read_text(encoding="utf-8"))["uv_version"] == "99.0.0"


def test_failed_download_without_a_new_enough_system_uv_fails(root, monkeypatch):
    FakeRelease(fail=True).install(monkeypatch)
    monkeypatch.setattr(module, "_system_uv_candidates", lambda: ["/old/uv", "/broken/uv"])
    uv = FakeUv(root, versions={"/old/uv": "0.3.0", "/broken/uv": "not-a-version"})
    _expect_failure(uv, "download_failed")
    assert not any(cmd[1:3] == ["python", "install"] for cmd, _env in uv.calls)


def test_checksum_mismatch_does_not_fall_back(root, monkeypatch):
    FakeRelease(digest="0" * 64).install(monkeypatch)
    monkeypatch.setattr(module, "_system_uv_candidates", lambda: ["/new/uv"])
    _expect_failure(FakeUv(root, versions={"/new/uv": "99.0.0"}), "checksum_mismatch")


def test_prefer_system_uv_skips_the_download(root, monkeypatch):
    monkeypatch.setattr(module, "_system_uv_candidates", lambda: ["/new/uv"])
    uv = FakeUv(root, versions={"/new/uv": module.PINNED_UV_VERSION})
    assert _prepare(uv, prefer_system_uv=True).uv == "/new/uv"
    assert not module.cached_uv_path().exists()


def test_system_uv_candidates_include_user_install_dirs(tmp_path, monkeypatch):
    if sys.platform not in {"linux", "darwin"}:
        pytest.skip("~/.local/bin is a POSIX location")
    home = tmp_path / "home"
    local_uv = home / ".local" / "bin" / "uv"
    local_uv.parent.mkdir(parents=True)
    local_uv.write_text("#!/bin/sh\n", encoding="utf-8")
    local_uv.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("UV", raising=False)
    assert module._system_uv_candidates() == [str(local_uv)]


# ---------------------------------------------------------------------------
# Environment, storage and background preparation


def test_tool_env_is_git_env_without_foreign_uv_settings(root, monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
    monkeypatch.setenv("UV_CACHE_DIR", "/elsewhere/cache")
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", "/elsewhere/python")
    monkeypatch.setenv("NEKO_TOOL_RUNTIME_MARKER", "kept")

    env = module.tool_runtime_env()

    assert "GIT_DIR" not in env
    assert "UV_CACHE_DIR" not in env
    assert env["NEKO_TOOL_RUNTIME_MARKER"] == "kept"
    assert env["UV_PYTHON_INSTALL_DIR"] == str(root / "python")
    assert (env["PYTHONUTF8"], env["PYTHONIOENCODING"]) == ("1", "utf-8")
    assert sorted(name for name in env if name.startswith("UV_")) == ["UV_PYTHON_INSTALL_DIR"]
    explicit = module.tool_runtime_env({"UV_INDEX_URL": "x", "PATH": "/bin"})
    assert explicit["PATH"] == "/bin" and "UV_INDEX_URL" not in explicit


def test_tool_env_is_built_on_git_env(root, monkeypatch):
    monkeypatch.setattr(module, "git_env", lambda: {"FROM_GIT_ENV": "1", "UV_OFFLINE": "1"})
    assert module.tool_runtime_env()["FROM_GIT_ENV"] == "1"
    assert "UV_OFFLINE" not in module.tool_runtime_env()


def test_tools_live_under_the_regenerable_plugin_runtime_entry(tmp_path, monkeypatch):
    # The managed CPython holds symlinks a storage migration cannot copy: the
    # tools root must stay inside an entry migrations skip and recreate.
    import utils.config_manager as config_manager
    from utils.storage.migration import MIGRATED_RUNTIME_ENTRY_NAMES, REGENERABLE_RUNTIME_ENTRY_NAMES

    monkeypatch.setattr(config_manager, "get_config_manager", lambda **_kwargs: SimpleNamespace(app_docs_dir=tmp_path))
    relative = module.tools_root().relative_to(tmp_path)
    assert relative.parts[0] in REGENERABLE_RUNTIME_ENTRY_NAMES
    assert relative.parts[0] not in MIGRATED_RUNTIME_ENTRY_NAMES
    for path in (module.python_install_dir(), module.tool_envs_dir(), module.cached_uv_path()):
        assert path.is_relative_to(module.tools_root())
    assert not (tmp_path / "plugin-runtime").exists()


def test_background_preparation_starts_once_and_finishes(root, monkeypatch):
    gate = threading.Event()
    pypi = FakeRelease(gate=gate).install(monkeypatch)
    uv = FakeUv(root)
    monkeypatch.setattr(module, "_run_command", uv)

    first = module.start_tool_runtime_preparation()
    second = module.start_tool_runtime_preparation()
    assert first.state == second.state == "preparing"
    background = module._background
    gate.set()
    background[0].join(10)
    assert module.start_tool_runtime_preparation().state == "ready"
    assert module._background is background
    assert pypi.wheel_downloads == 1


def test_background_preparation_can_be_canceled(root, monkeypatch):
    gate = threading.Event()
    FakeRelease(gate=gate).install(monkeypatch)
    monkeypatch.setattr(module, "_run_command", FakeUv(root))
    assert module.cancel_tool_runtime_preparation() is False
    module.start_tool_runtime_preparation()
    assert module.cancel_tool_runtime_preparation() is True
    gate.set()
    module._background[0].join(10)
    assert module.read_tool_runtime_status().state == "missing"


def test_background_failure_is_reported_then_retried(root, monkeypatch):
    pypi = FakeRelease(digest="0" * 64).install(monkeypatch)
    monkeypatch.setattr(module, "_run_command", FakeUv(root))
    module.start_tool_runtime_preparation()
    module._background[0].join(10)
    assert module.read_tool_runtime_status().error_code == "checksum_mismatch"
    pypi.pin_served_archive()
    module.start_tool_runtime_preparation()
    module._background[0].join(10)
    status = module.read_tool_runtime_status()
    assert (status.state, status.error_code) == ("ready", None)


def test_unexpected_error_becomes_internal_error(root, monkeypatch):
    def broken(*_args, **_kwargs):
        raise LookupError("boom")

    monkeypatch.setattr(module, "_prepare_in_flight", broken)
    _expect_failure(_no_runner, "internal_error")
    assert module.read_tool_runtime_status().error_code == "internal_error"
    assert not module._flight.locked()


async def test_async_wrapper_cancels_the_worker(root, monkeypatch):
    gate = threading.Event()
    pypi = FakeRelease(gate=gate).install(monkeypatch)
    monkeypatch.setattr(module, "_run_command", FakeUv(root))
    task = asyncio.ensure_future(module.ensure_tool_runtime(timeout=60))
    try:
        limit = time.monotonic() + 10
        while pypi.wheel_downloads == 0:
            if task.done():
                task.result()  # Surfaces why it ended before downloading.
                pytest.fail("preparation finished without downloading")
            assert time.monotonic() < limit, "the wheel download never started"
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        gate.set()
    limit = time.monotonic() + 10
    while module._flight.locked():
        assert time.monotonic() < limit, "the worker thread never finished"
        await asyncio.sleep(0.01)
    assert module.read_tool_runtime_status().state == "missing"
    assert not module.cached_uv_path().exists()


async def test_async_wrapper_returns_the_prepared_runtime(root, monkeypatch):
    FakeRelease().install(monkeypatch)
    uv = FakeUv(root)
    monkeypatch.setattr(module, "_run_command", uv)
    result = await module.ensure_tool_runtime(timeout=60)
    assert result.python == uv.python


async def test_concurrent_async_calls_do_not_starve_the_loops_default_executor(root, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    release = FakeRelease().install(monkeypatch)
    monkeypatch.setattr(module, "_run_command", FakeUv(root))
    loop = asyncio.get_running_loop()
    fake = module._fetch_release_asset

    def fetch(url, directory, **kwargs):
        # Like the real transfer: its file operations use the loop's default executor.
        asyncio.run_coroutine_threadsafe(asyncio.to_thread(lambda: None), loop).result(timeout=5)
        return fake(url, directory, **kwargs)

    monkeypatch.setattr(module, "_fetch_release_asset", fetch)
    executor = ThreadPoolExecutor(max_workers=2)
    loop.set_default_executor(executor)
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(module.ensure_tool_runtime(timeout=20) for _ in range(3))), timeout=15,
        )
    finally:
        executor.shutdown(wait=False)
    assert len({result.uv for result in results}) == 1
    assert [url for url, _exclude in release.requests].count(release.uv_url) == 1


async def test_async_call_keeps_the_callers_context(root, monkeypatch):
    import contextvars

    FakeRelease().install(monkeypatch)
    marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker", default="unset")
    seen: list[str] = []
    uv = FakeUv(root)

    def runner(cmd, **kwargs):
        seen.append(marker.get())
        return uv(cmd, **kwargs)

    monkeypatch.setattr(module, "_run_command", runner)
    marker.set("caller")
    await module.ensure_tool_runtime(timeout=60)
    assert seen and set(seen) == {"caller"}


@pytest.mark.parametrize("asset", ["uv", "python"])
def test_server_shutting_down_mid_download_is_a_stop_without_fallback(root, monkeypatch, asset):
    from plugin.server.infrastructure import package_fetch

    release = FakeRelease().install(monkeypatch)
    fake = module._fetch_release_asset
    target = release.uv_url if asset == "uv" else _python_url()

    def real_wrapper(url, directory, **kwargs):
        if url == target:
            def shutting_down(*_args, **_kwargs):
                raise package_fetch.PackageLoopUnavailable("the server loop stopped during a package download")

            monkeypatch.setattr(package_fetch, "fetch_release_asset", shutting_down)
            return _original_fetch(url, directory, **kwargs)
        return fake(url, directory, **kwargs)

    monkeypatch.setattr(module, "_fetch_release_asset", real_wrapper)
    monkeypatch.setattr(module, "_system_uv_candidates", lambda: ["/new/uv"])
    uv = FakeUv(root, listing=_listing(), versions={"/new/uv": "99.0.0"})
    _expect_failure(uv, "canceled")
    assert _plain_installs(uv) == []
    # A stop records no failure for the status.
    assert module.read_tool_runtime_status().error_code is None


# ---------------------------------------------------------------------------
# Review follow-ups


def test_status_read_never_creates_the_config_manager(tmp_path, monkeypatch):
    # The real resolution path: constructing ConfigManager probes and creates
    # app-data directories, so a status read must not be the one to do it.
    import utils.config_manager as config_manager

    monkeypatch.setattr(config_manager, "_config_manager", None)
    monkeypatch.delenv("NEKO_STORAGE_SELECTED_ROOT", raising=False)
    monkeypatch.setattr(module, "_is_packaged", lambda: True)
    monkeypatch.setattr(module, "_last_error", None)
    monkeypatch.setattr(module, "_progress", None)
    monkeypatch.setattr(module, "_background", None)
    assert module.read_tool_runtime_status().state == "missing"
    assert config_manager._config_manager is None
    monkeypatch.setattr(
        config_manager, "_config_manager", SimpleNamespace(app_docs_dir=str(tmp_path / "docs")),
    )
    assert module._known_tools_root() == tmp_path / "docs" / "plugin-runtime" / "tools"
    assert module.read_tool_runtime_status().state == "missing"
    assert not (tmp_path / "docs").exists()


@pytest.mark.parametrize("damage", ["empty", "not_executable"])
@pytest.mark.parametrize("tool", ["uv", "python"])
def test_damaged_tool_after_preparation_is_not_ready_and_is_repaired(root, monkeypatch, damage, tool):
    if damage == "not_executable" and sys.platform == "win32":
        pytest.skip("Windows has no executable bit")
    pypi = FakeRelease().install(monkeypatch)
    uv = FakeUv(root)
    first = _prepare(uv)
    target = Path(first.uv if tool == "uv" else first.python)
    if damage == "empty":
        target.write_bytes(b"")
    else:
        target.chmod(0o644)
    assert module.read_tool_runtime_status().state == "missing"
    # The damaged file stays: preparation itself has to get rid of it.
    repaired = _prepare(uv)
    assert module.read_tool_runtime_status().state == "ready"
    assert pypi.wheel_downloads == (2 if tool == "uv" else 1)
    assert module._usable_executable(repaired.uv) and module._usable_executable(repaired.python)


def test_cancel_before_starting_a_retry_clears_the_earlier_failure(root, monkeypatch):
    FakeRelease(digest="0" * 64).install(monkeypatch)
    _expect_failure(FakeUv(root), "checksum_mismatch")
    canceled = threading.Event()
    canceled.set()
    _expect_failure(_no_runner, "canceled", cancel=canceled)
    assert module.read_tool_runtime_status().state == "missing"


def test_unstarted_attempt_leaves_an_active_preparation_alone(root, monkeypatch):
    earlier = module.ToolRuntimeError("checksum_mismatch", "earlier")
    progress = module.ProgressEvent("downloading_uv", 5, 10)
    monkeypatch.setattr(module, "_last_error", earlier)
    monkeypatch.setattr(module, "_progress", progress)
    canceled = threading.Event()
    canceled.set()
    with module._flight:
        _expect_failure(_no_runner, "canceled", cancel=canceled)
        _expect_failure(_no_runner, "timeout", deadline=time.monotonic() - 1)
        status = module.read_tool_runtime_status()
        assert (status.state, status.progress) == ("preparing", progress)
    assert module._last_error is earlier
    assert not module._flight.locked()


def test_background_start_after_the_deadline_reports_timeout(root, monkeypatch):
    module.start_tool_runtime_preparation(timeout=0)
    module._background[0].join(10)
    status = module.read_tool_runtime_status()
    assert (status.state, status.error_code) == ("failed", "timeout")


def test_network_error_at_the_deadline_is_a_timeout_without_fallback(root, monkeypatch):
    from tests.fake_clock import patch_module_clock

    clock = [100.0]
    patch_module_clock(monkeypatch, module, monotonic=lambda: clock[0])
    pypi = FakeRelease().install(monkeypatch)
    monkeypatch.setattr(module, "_system_uv_candidates", lambda: ["/new/uv"])

    def stalled(_url, _received):
        clock[0] = 200.0
        raise module.load_httpx().ReadTimeout("read timed out")

    pypi.on_chunk = stalled
    uv = FakeUv(root, versions={"/new/uv": "99.0.0"})
    _expect_failure(uv, "timeout", deadline=150.0)
    assert uv.calls == []


def test_network_error_within_the_deadline_still_falls_back(root, monkeypatch):
    pypi = FakeRelease().install(monkeypatch)
    monkeypatch.setattr(module, "_system_uv_candidates", lambda: ["/new/uv"])

    def broken(_url, _received):
        raise module.load_httpx().ReadError("connection reset")

    pypi.on_chunk = broken
    assert _prepare(FakeUv(root, versions={"/new/uv": "99.0.0"})).uv == "/new/uv"


def _tree(path: Path) -> list[str]:
    return sorted(str(item.relative_to(path)) for item in path.rglob("*"))


def test_tools_from_an_earlier_run_read_ready_before_the_config_manager_exists(tmp_path, monkeypatch):
    # A restarted packaged server: the launcher exported the storage root, the
    # tools were prepared by the previous run, and nothing in this process has
    # created the config manager yet.
    import utils.config_manager as config_manager

    docs = tmp_path / "docs"
    tools = docs / "plugin-runtime" / "tools"
    python = tools / "python" / "cpython" / "bin" / "python3"
    uv = tools / "uv" / module.PINNED_UV_VERSION / "uv"
    _write_executable(python, b"#!python")
    _write_executable(uv, b"uv")
    (tools / "python.json").write_text(json.dumps({
        "executable": str(python), "uv": str(uv), "uv_version": module.PINNED_UV_VERSION,
        "environment": module.build_environment(), "python_version": list(sys.version_info[:3]),
        "python_architecture": module._host_architecture(),
    }), encoding="utf-8")
    before = _tree(tmp_path)
    monkeypatch.setattr(config_manager, "_config_manager", None)
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(docs))
    monkeypatch.setattr(module, "_is_packaged", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", _no_runner)

    status = module.read_tool_runtime_status()

    assert (status.state, status.python, status.uv) == ("ready", str(python), str(uv))
    assert config_manager._config_manager is None
    assert _tree(tmp_path) == before


def test_python_that_no_longer_starts_is_reinstalled_once(root, monkeypatch):
    FakeRelease().install(monkeypatch)
    uv = FakeUv(root)
    first = _prepare(uv)
    (root / "python.json").unlink()
    Path(first.python).write_bytes(b"quarantined")  # still listed by `uv python find`
    uv.calls.clear()
    repaired = _prepare(uv)
    assert Path(repaired.python).read_bytes() == b"#!python"
    assert sum(1 for cmd, _env in uv.calls if cmd[1:3] == ["python", "install"]) == 1
    assert module.read_tool_runtime_status().state == "ready"


def test_python_that_never_starts_fails_the_install_after_one_reinstall(root, monkeypatch):
    FakeRelease().install(monkeypatch)
    uv = FakeUv(root)
    _prepare(uv)
    (root / "python.json").unlink()
    uv.identity = "garbage"
    uv.calls.clear()
    _expect_failure(uv, "python_install_failed")
    assert sum(1 for cmd, _env in uv.calls if cmd[1:3] == ["python", "install"]) == 1


def test_unexpected_error_before_the_flight_is_internal_error(root, monkeypatch):
    def broken():
        raise PermissionError("app data unreadable")

    monkeypatch.setattr(module, "_prepared_runtime", broken)
    _expect_failure(_no_runner, "internal_error")
    assert module.read_tool_runtime_status().error_code == "internal_error"
    monkeypatch.setattr(module, "_last_error", None)
    module.start_tool_runtime_preparation()
    module._background[0].join(10)
    assert module.read_tool_runtime_status().error_code == "internal_error"


def test_foreground_call_past_its_deadline_keeps_the_previous_state(root, monkeypatch):
    FakeRelease(digest="0" * 64).install(monkeypatch)
    _expect_failure(FakeUv(root), "checksum_mismatch")
    _expect_failure(_no_runner, "timeout", deadline=time.monotonic() - 1)
    assert module.read_tool_runtime_status().error_code == "checksum_mismatch"
    with pytest.raises(module.ToolRuntimeError):
        asyncio.run(module.ensure_tool_runtime(timeout=0))
    assert module.read_tool_runtime_status().error_code == "checksum_mismatch"


def test_cancel_does_not_wait_for_a_grandchild_holding_the_output(tmp_path):
    # The grandchild inherits the child's output handles and outlives it; the
    # cancel must not wait for it (it did on Windows while output used pipes).
    pid_file = tmp_path / "grandchild.pid"
    script = (
        "import pathlib, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid)); "
        "print('started', flush=True); time.sleep(30)"
    )
    canceled = threading.Event()
    set_at: list[float] = []

    def cancel_once_started():
        limit = time.monotonic() + 20
        while not (pid_file.exists() and pid_file.read_text()) and time.monotonic() < limit:
            time.sleep(0.05)
        set_at.append(time.monotonic())
        canceled.set()

    threading.Thread(target=cancel_once_started, daemon=True).start()
    try:
        with pytest.raises(module.ToolRuntimeError) as raised:
            module._run_command(
                [sys.executable, "-c", script], label="spawn",
                env=dict(os.environ), cancel=canceled, deadline=time.monotonic() + 60,
            )
        returned_at = time.monotonic()
        assert raised.value.code == "canceled"
        assert pid_file.read_text()  # the grandchild was running when the cancel came
    finally:
        if pid_file.exists() and pid_file.read_text():
            _kill_pid(int(pid_file.read_text()))
    # Only the poll interval and the kill of the child itself remain.
    assert returned_at - set_at[0] < 3


def _kill_pid(pid: int) -> None:
    import signal

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass  # Already gone.


def test_default_runner_keeps_only_the_tail_of_huge_output(monkeypatch):
    monkeypatch.setattr(module, "_MAX_OUTPUT_BYTES", 10)
    result = module._run_command(
        [sys.executable, "-c", "print('x' * 100 + 'END')"], label="echo",
        env=dict(os.environ), cancel=threading.Event(), deadline=time.monotonic() + 60,
    )
    assert result.stdout.strip().endswith("END") and len(result.stdout) <= 10


def test_python_that_cannot_be_started_is_reinstalled_once(root, monkeypatch):
    FakeRelease().install(monkeypatch)
    uv = FakeUv(root)
    first = _prepare(uv)
    (root / "python.json").unlink()
    Path(first.python).write_bytes(b"blocked")

    def runner(cmd, **kwargs):
        if cmd[0] == first.python and Path(first.python).read_bytes() == b"blocked":
            raise PermissionError("[WinError 5] Access is denied")
        return uv(cmd, **kwargs)

    assert Path(_prepare(runner).python).read_bytes() == b"#!python"


def test_tool_env_drops_variables_that_redirect_python_or_its_environment(root):
    base = {
        "PATH": "/bin", "PYTHONHOME": "/elsewhere", "PYTHONPATH": "/elsewhere/lib",
        "PYTHONSTARTUP": "/elsewhere/startup.py", "PYTHONUSERBASE": "/elsewhere/user",
        "PYTHONEXECUTABLE": "/elsewhere/python", "VIRTUAL_ENV": "/elsewhere/venv",
        "CONDA_PREFIX": "/elsewhere/conda", "__PYVENV_LAUNCHER__": "/elsewhere/python",
        "PYTHONINSPECT": "1", "PYTHONPLATLIBDIR": "lib64",
        # Windows spells names any way it likes; they are the same variable.
        "PythonPath": "/elsewhere/mixed", "uv_index_url": "https://mirror.invalid",
        # Settings that only make Python stricter are kept.
        "PYTHONNOUSERSITE": "1", "PYTHONSAFEPATH": "1",
        # Forced values replace any spelling of the same name.
        "PythonUtf8": "0", "pythonioencoding": "latin-1",
    }
    env = module.tool_runtime_env(base)
    assert sorted(name for name in env if name in base) == ["PATH", "PYTHONNOUSERSITE", "PYTHONSAFEPATH"]
    assert env["PYTHONNOUSERSITE"] == "1"
    assert [name for name in env if name.upper() == "PYTHONUTF8"] == ["PYTHONUTF8"]
    assert [name for name in env if name.upper() == "PYTHONIOENCODING"] == ["PYTHONIOENCODING"]
    assert module.tool_runtime_env({"PythonNoUserSite": "0"}) == {
        "UV_PYTHON_INSTALL_DIR": str(root / "python"), "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
    }
    assert env["UV_PYTHON_INSTALL_DIR"] == str(root / "python")


def test_source_environment_keeps_python_settings(root, monkeypatch):
    monkeypatch.setattr(module, "_is_packaged", lambda: False)
    monkeypatch.setenv("VIRTUAL_ENV", "/developer/venv")
    assert module.prepare_tool_runtime(cancel=threading.Event(), deadline=time.monotonic() + 60).env["VIRTUAL_ENV"] == (
        "/developer/venv"
    )


def test_no_pinned_wheel_for_this_platform_falls_back_to_a_system_uv(root, monkeypatch):
    FakeRelease().install(monkeypatch)
    monkeypatch.setattr(module, "_uv_target", lambda: None)
    monkeypatch.setattr(module, "_system_uv_candidates", lambda: ["/new/uv"])
    assert _prepare(FakeUv(root, versions={"/new/uv": "99.0.0"})).uv == "/new/uv"
    monkeypatch.setattr(module, "_system_uv_candidates", lambda: [])
    (root / "python.json").unlink()
    _expect_failure(FakeUv(root), "unsupported_platform")
    # As final as an unsupported OS: not restarted in the background.
    status = module.read_tool_runtime_status()
    assert (status.state, status.error_code) == ("unsupported", "unsupported_platform")
    assert module.start_tool_runtime_preparation().state == "unsupported"
    assert module._background is None


def test_falling_back_to_a_system_uv_reports_its_own_phase(root, monkeypatch):
    FakeRelease(fail=True).install(monkeypatch)
    monkeypatch.setattr(module, "_system_uv_candidates", lambda: ["/new/uv"])
    events: list = []
    _prepare(FakeUv(root, versions={"/new/uv": "99.0.0"}), progress=events.append)
    phases = [event.phase for event in events]
    assert phases.index("downloading_uv") < phases.index("checking_uv") < phases.index("installing_python")


def test_waiting_behind_another_thread_honours_the_deadline_and_cancel(root):
    with module._flight:
        _expect_failure(_no_runner, "timeout", deadline=time.monotonic() + 0.2)
        canceled = threading.Event()
        threading.Timer(0.1, canceled.set).start()
        started = time.monotonic()
        _expect_failure(_no_runner, "canceled", cancel=canceled)
        assert time.monotonic() - started < 5


def test_timeout_behind_another_thread_keeps_the_previous_state(root, monkeypatch):
    FakeRelease(digest="0" * 64).install(monkeypatch)
    _expect_failure(FakeUv(root), "checksum_mismatch")
    with module._flight:
        _expect_failure(_no_runner, "timeout", deadline=time.monotonic() + 0.2)
    status = module.read_tool_runtime_status()
    assert (status.state, status.error_code) == ("failed", "checksum_mismatch")


def test_waiter_uses_what_the_other_thread_prepared(root, monkeypatch):
    FakeRelease().install(monkeypatch)
    uv = FakeUv(root)
    _prepare(uv)
    record = (root / "python.json").read_text(encoding="utf-8")
    (root / "python.json").unlink()
    module._flight.acquire()

    def finish():
        (root / "python.json").write_text(record, encoding="utf-8")
        module._flight.release()

    threading.Timer(0.1, finish).start()
    result = _prepare(_no_runner)
    assert result.python == uv.python


def test_no_cross_process_lock_and_a_leftover_one_is_ignored(root, monkeypatch):
    # An earlier build left .prepare.lock behind (or another one even holds it).
    root.mkdir(parents=True)
    (root / ".prepare.lock").write_bytes(b"")
    FakeRelease().install(monkeypatch)
    events: list[module.ProgressEvent] = []
    _prepare(FakeUv(root), progress=events.append)
    assert module.read_tool_runtime_status().state == "ready"
    assert "waiting" not in {event.phase for event in events}
    assert "busy" not in module.ErrorCode.__args__ and "waiting" not in module.ProgressPhase.__args__


# ---------------------------------------------------------------------------
# Downloads over the market's routes


def _listing(url: str | None = None, **overrides) -> str:
    entry = {
        "key": "cpython-test", "implementation": "cpython", "variant": "default",
        "version": ".".join(str(part) for part in sys.version_info[:3]),
        "url": url or f"https://releases.astral.sh/github/python-build-standalone/releases/download/"
                      f"{PYTHON_TAG}/{PYTHON_FILE.replace('+', '%2B')}",
    }
    entry.update(overrides)
    return json.dumps([entry])


def _python_url() -> str:
    return (f"https://github.com/astral-sh/python-build-standalone/releases/download/"
            f"{PYTHON_TAG}/{PYTHON_FILE.replace('+', '%2B')}")


def test_python_build_is_fetched_over_github_routes_and_installed_from_the_file(root, monkeypatch):
    release = FakeRelease().install(monkeypatch)
    uv = FakeUv(root, listing=_listing())
    events: list[module.ProgressEvent] = []
    _prepare(uv, progress=events.append)
    assert [url for url, _exclude in release.requests] == [release.uv_url, _python_url()]
    install = next(cmd for cmd, _env in uv.calls if cmd[1:3] == ["python", "install"])
    mirror = install[install.index("--mirror") + 1]
    assert mirror == (root / ".python-download" / "mirror").as_uri()
    assert {"--no-config", "--no-bin", "--no-registry"} <= set(install)
    assert uv.mirrored == [PYTHON_BODY]
    # Nothing of the download is left once uv has installed it.
    assert not (root / ".python-download").exists()
    python_bytes = [event for event in events if event.phase == "installing_python" and event.downloaded_bytes]
    assert python_bytes[-1].downloaded_bytes == python_bytes[-1].total_bytes == len(PYTHON_BODY)
    assert release.penalized == []


@pytest.mark.parametrize("listing", [
    None,
    "not json",
    _listing("https://example.com/python-build-standalone/releases/download/x/y.tar.gz"),
    _listing(url=f"{module._PYTHON_RELEASE_PREFIXES[0]}{PYTHON_TAG}/sub/{PYTHON_FILE}"),
    _listing(url=f"{module._PYTHON_RELEASE_PREFIXES[0]}{PYTHON_TAG}/..%2Fescape.tar.gz"),
    _listing(url=f"{module._PYTHON_RELEASE_PREFIXES[0]}{PYTHON_TAG}/..%5Cescape.tar.gz"),
    _listing(url=f"{module._PYTHON_RELEASE_PREFIXES[0]}..\\x/{PYTHON_FILE}"),
    _listing(url=f"{module._PYTHON_RELEASE_PREFIXES[0]}../{PYTHON_FILE}"),
    _listing(version="3.0.0"),
])
def test_unreadable_python_listing_lets_uv_download_by_itself(root, monkeypatch, listing):
    release = FakeRelease().install(monkeypatch)
    uv = FakeUv(root, listing=listing)
    _prepare(uv)
    install = next(cmd for cmd, _env in uv.calls if cmd[1:3] == ["python", "install"])
    assert "--mirror" not in install
    assert [url for url, _exclude in release.requests] == [release.uv_url]


def test_python_build_failing_uvs_checksum_penalizes_the_route_and_tries_the_next(root, monkeypatch):
    release = FakeRelease().install(monkeypatch)
    release.bodies[_python_url()] = [b"tampered", PYTHON_BODY]
    uv = FakeUv(root, listing=_listing())
    _prepare(uv)
    assert uv.mirrored == [b"tampered", PYTHON_BODY]
    assert release.penalized == ["github-direct"]
    assert release.requests[-2:] == [(_python_url(), ()), (_python_url(), ("github-direct",))]


def _plain_installs(uv: FakeUv) -> list[list[str]]:
    return [cmd for cmd, _env in uv.calls if cmd[1:3] == ["python", "install"] and "--mirror" not in cmd]


def test_python_build_rejected_on_every_route_falls_back_to_uvs_own_download(root, monkeypatch):
    release = FakeRelease(routes=2).install(monkeypatch)
    release.bodies[_python_url()] = [b"tampered"]
    uv = FakeUv(root, listing=_listing())
    _prepare(uv)
    assert release.penalized == ["github-direct", "route-1"]
    # As on main: uv downloads from its own host and checks its own digest.
    assert len(_plain_installs(uv)) == 1
    assert (root / "python.json").exists()
    assert not (root / ".python-download").exists()


def test_failed_fallback_reports_both_downloads(root, monkeypatch):
    release = FakeRelease(routes=2).install(monkeypatch)
    release.bodies[_python_url()] = [b"tampered"]
    uv = FakeUv(root, listing=_listing())

    def runner(cmd, **kwargs):
        if cmd[1:3] == ["python", "install"] and "--mirror" not in cmd:
            uv.calls.append((cmd, kwargs["env"]))
            return subprocess.CompletedProcess(cmd, 1, "", "error: releases.astral.sh unreachable")
        return FakeUv.__call__(uv, cmd, **kwargs)

    error = _expect_failure(runner, "python_install_failed")
    assert "download routes" in error.detail and "releases.astral.sh unreachable" in error.detail
    assert not (root / "python.json").exists()


@pytest.mark.parametrize("error", ["error: disk full", "error: No such file or directory (os error 2)"])
def test_other_uv_mirror_install_errors_fall_back_without_trying_another_route(root, monkeypatch, error):
    release = FakeRelease(routes=2).install(monkeypatch)
    uv = FakeUv(root, listing=_listing())

    def mirror_fails(cmd, **kwargs):
        if cmd[1:3] == ["python", "install"] and "--mirror" in cmd:
            uv.calls.append((cmd, kwargs["env"]))
            return subprocess.CompletedProcess(cmd, 1, "", error)
        return FakeUv.__call__(uv, cmd, **kwargs)

    _prepare(mirror_fails)
    # The file is not at fault: no route is penalized or retried, and uv's
    # own download installs the build, as on main.
    assert [url for url, _exclude in release.requests].count(_python_url()) == 1
    assert release.penalized == []
    assert len(_plain_installs(uv)) == 1
    assert (root / "python.json").exists()
    assert not (root / ".python-download").exists()


def test_uv_failing_with_and_without_the_mirror_reports_both(root, monkeypatch):
    FakeRelease().install(monkeypatch)
    uv = FakeUv(root, listing=_listing())

    def install_fails(cmd, **kwargs):
        if cmd[1:3] == ["python", "install"]:
            uv.calls.append((cmd, kwargs["env"]))
            reason = "mirror unusable" if "--mirror" in cmd else "disk full"
            return subprocess.CompletedProcess(cmd, 1, "", f"error: {reason}")
        return FakeUv.__call__(uv, cmd, **kwargs)

    error = _expect_failure(install_fails, "python_install_failed")
    assert "mirror unusable" in error.detail and "disk full" in error.detail
    assert len(_plain_installs(uv)) == 1


def test_leftover_python_download_directory_does_not_fail_the_install(root, monkeypatch):
    FakeRelease().install(monkeypatch)
    leftover = root / ".python-download" / "mirror" / PYTHON_TAG
    leftover.mkdir(parents=True)
    # A file another program holds open survives the cleanup on Windows.
    rmtree = shutil.rmtree

    def held_open(path, *args, **kwargs):
        if Path(path).name != ".python-download":
            rmtree(path, *args, **kwargs)

    monkeypatch.setattr(module.shutil, "rmtree", held_open)
    uv = FakeUv(root, listing=_listing())
    _prepare(uv)
    assert uv.mirrored == [PYTHON_BODY]
    assert _plain_installs(uv) == []


def test_python_routes_unreachable_fall_back_to_uvs_own_download(root, monkeypatch):
    release = FakeRelease().install(monkeypatch)

    def broken(url, _received):
        if url == _python_url():
            raise module.load_httpx().ConnectError("connection refused")

    release.on_chunk = broken
    uv = FakeUv(root, listing=_listing())
    _prepare(uv)
    assert uv.mirrored == []
    assert len(_plain_installs(uv)) == 1
    assert not (root / ".python-download").exists()


def test_local_write_failure_of_the_python_download_does_not_fall_back(root, monkeypatch):
    from plugin.server.infrastructure.package_download import PackageDownloadWriteError

    FakeRelease().install(monkeypatch)
    fake = module._fetch_release_asset

    def fetch(url, directory, **kwargs):
        if url == _python_url():
            raise PackageDownloadWriteError(OSError(28, "No space left on device"))
        return fake(url, directory, **kwargs)

    monkeypatch.setattr(module, "_fetch_release_asset", fetch)
    uv = FakeUv(root, listing=_listing())
    _expect_failure(uv, "python_install_failed")
    assert _plain_installs(uv) == []


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_stopping_during_the_python_download(root, monkeypatch, stop):
    from tests.fake_clock import patch_module_clock

    clock = [100.0]
    patch_module_clock(monkeypatch, module, monotonic=lambda: clock[0])
    release = FakeRelease().install(monkeypatch)
    canceled = threading.Event()

    def on_chunk(url, received):
        if url == _python_url() and received:
            if stop == "cancel":
                canceled.set()
            else:
                clock[0] = 200.0

    release.on_chunk = on_chunk
    uv = FakeUv(root, listing=_listing())
    _expect_failure(uv, "canceled" if stop == "cancel" else "timeout", cancel=canceled, deadline=150.0)
    assert uv.mirrored == []
    # Stopping is not a failed download: uv's own download is not tried.
    assert _plain_installs(uv) == []
    assert not (root / ".python-download").exists()


def test_uv_archive_failing_the_pinned_checksum_penalizes_the_route_and_tries_the_next(root, monkeypatch):
    release = FakeRelease().install(monkeypatch)
    good = release.body
    release.bodies[release.uv_url] = [_uv_archive(release.target, release.binary_name, b"evil"), good]
    _prepare(FakeUv(root))
    assert module.cached_uv_path().read_bytes() == b"uv-executable"
    assert release.penalized == ["github-direct"]
    assert release.requests[:2] == [(release.uv_url, ()), (release.uv_url, ("github-direct",))]


def test_uv_archive_failing_on_the_last_route_does_not_fall_back_to_a_system_uv(root, monkeypatch):
    release = FakeRelease(digest="0" * 64, routes=2).install(monkeypatch)
    monkeypatch.setattr(module, "_system_uv_candidates", lambda: ["/new/uv"])
    error = _expect_failure(FakeUv(root, versions={"/new/uv": "99.0.0"}), "checksum_mismatch")
    assert "github-direct, route-1" in error.detail
    assert release.penalized == ["github-direct", "route-1"]
    assert not module.cached_uv_path().exists()


def test_network_failure_after_a_mismatch_stays_a_checksum_mismatch(root, monkeypatch):
    release = FakeRelease(digest="0" * 64).install(monkeypatch)
    calls = []

    def broken(url, _received):
        calls.append(url)
        if len(calls) > 2:
            raise OSError("connection reset")

    release.on_chunk = broken
    monkeypatch.setattr(module, "_system_uv_candidates", lambda: ["/new/uv"])
    _expect_failure(FakeUv(root, versions={"/new/uv": "99.0.0"}), "checksum_mismatch")
    assert release.penalized == ["github-direct"]


def test_spliced_file_failing_its_checksum_blames_no_route_and_retries_one_route_at_a_time(root, monkeypatch):
    release = FakeRelease().install(monkeypatch)
    release.spliced = True
    release.bodies[release.uv_url] = [b"spliced from two routes", release.body]
    _prepare(FakeUv(root))
    assert module.cached_uv_path().read_bytes() == b"uv-executable"
    assert release.penalized == []
    # The whole file is fetched again, from the best route alone; none is ruled out.
    assert release.requests[:2] == [(release.uv_url, ()), (release.uv_url, ())]
    assert release.single[:2] == [False, True]


def test_spliced_then_single_route_mismatches_rule_out_each_route_in_turn(root, monkeypatch):
    release = FakeRelease(digest="0" * 64, routes=2).install(monkeypatch)
    release.spliced = True
    _expect_failure(FakeUv(root), "checksum_mismatch")
    assert release.penalized == ["github-direct", "route-1"]
    assert [exclude for _url, exclude in release.requests] == [(), (), ("github-direct",), ("github-direct", "route-1")]


def test_spliced_file_with_github_bytes_rejected_only_with_mirrors_is_a_download_failure(root, monkeypatch):
    release = FakeRelease(digest="0" * 64, routes=2).install(monkeypatch)
    release.spliced = True
    release.broken_routes = {"github-direct"}
    # GitHub delivered part of the spliced file and nothing whole: the bad
    # bytes may be a mirror's error page, so this is no proof of tampering.
    _expect_failure(FakeUv(root), "download_failed")
    assert release.penalized == ["route-1"]


def test_route_failing_while_tried_alone_is_skipped(root, monkeypatch):
    release = FakeRelease().install(monkeypatch)
    good = release.body
    release.bodies[release.uv_url] = [_uv_archive(release.target, release.binary_name, b"evil"), good]
    release.broken_routes = {"route-1"}
    _prepare(FakeUv(root))
    assert release.penalized == ["github-direct"]
    assert [exclude for _url, exclude in release.requests[:3]] == [(), ("github-direct",), ("github-direct", "route-1")]


def test_python_file_uv_cannot_extract_is_blamed_on_its_route(root, monkeypatch):
    release = FakeRelease().install(monkeypatch)
    release.bodies[_python_url()] = [b"<html>", PYTHON_BODY]
    uv = FakeUv(root, listing=_listing())
    _prepare(uv)
    assert uv.mirrored == [b"<html>", PYTHON_BODY]
    assert release.penalized == ["github-direct"]


@pytest.mark.parametrize(("stderr", "rejected"), [
    ("error: Failed to install\n  cause: Hash mismatch for `cpython`", True),
    ("error: Failed to install\n  cause: Failed to extract archive: x\n  cause: Invalid gzip header", True),
    ("error: Failed to extract archive: x\n  cause: No space left on device (os error 28)", False),
    ("error: Permission denied (os error 13)", False),
])
def test_uv_rejecting_the_file_is_told_apart_from_a_local_failure(stderr, rejected):
    result = subprocess.CompletedProcess([], 1, "", stderr)
    assert module._rejected_python_archive(result) is rejected


def test_local_write_failure_after_a_rejection_is_reported_as_such(root, monkeypatch):
    from plugin.server.infrastructure.package_download import PackageDownloadWriteError

    release = FakeRelease(digest="0" * 64).install(monkeypatch)
    fake = module._fetch_release_asset

    def fetch(url, directory, **kwargs):
        if kwargs["exclude"]:
            raise PackageDownloadWriteError(OSError(28, "No space left on device"))
        return fake(url, directory, **kwargs)

    monkeypatch.setattr(module, "_fetch_release_asset", fetch)
    error = _expect_failure(FakeUv(root), "download_failed")
    assert "No space left" in error.detail
    assert release.penalized == ["github-direct"]


def test_uv_archive_rejected_only_from_mirrors_is_a_download_failure_and_falls_back(root, monkeypatch):
    release = FakeRelease(digest="0" * 64, routes=2).install(monkeypatch)
    release.route_names = ["gh-proxy-com", "gh-proxy-org"]
    monkeypatch.setattr(module, "_system_uv_candidates", lambda: ["/new/uv"])
    # Mirrors often answer with an error page: that is not tampering.
    assert _prepare(FakeUv(root, versions={"/new/uv": "99.0.0"})).uv == "/new/uv"
    assert release.penalized == ["gh-proxy-com", "gh-proxy-org"]


def test_pinned_digests_belong_to_the_pinned_uv():
    assert module._UV_ARCHIVE_SHA256_VERSION == module.PINNED_UV_VERSION


def test_staging_the_python_download_failing_locally_does_not_fall_back(root, monkeypatch):
    FakeRelease().install(monkeypatch)
    replace = os.replace

    def failing(source, destination):
        if Path(destination).name == PYTHON_FILE:
            raise PermissionError(13, "Permission denied")
        return replace(source, destination)

    monkeypatch.setattr(module.os, "replace", failing)
    uv = FakeUv(root, listing=_listing())
    error = _expect_failure(uv, "python_install_failed")
    assert "stage" in error.detail
    assert _plain_installs(uv) == []


def test_staged_file_that_cannot_be_removed_neither_fails_nor_falls_back(root, monkeypatch):
    release = FakeRelease().install(monkeypatch)
    release.bodies[_python_url()] = [b"tampered", PYTHON_BODY]
    unlink = Path.unlink

    def held_open(self, missing_ok=False):
        if self.name == PYTHON_FILE:
            raise PermissionError(13, "The file is in use")
        return unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", held_open)
    uv = FakeUv(root, listing=_listing())
    _prepare(uv)
    assert uv.mirrored == [b"tampered", PYTHON_BODY]
    assert _plain_installs(uv) == []


def test_digests_of_another_uv_release_download_nothing(root, monkeypatch):
    release = FakeRelease().install(monkeypatch)
    monkeypatch.setattr(module, "_UV_ARCHIVE_SHA256_VERSION", "0.0.1")
    monkeypatch.setattr(module, "_system_uv_candidates", lambda: ["/new/uv"])
    error = _expect_failure(FakeUv(root, versions={"/new/uv": "99.0.0"}), "internal_error")
    assert "0.0.1" in error.detail
    assert release.requests == []
