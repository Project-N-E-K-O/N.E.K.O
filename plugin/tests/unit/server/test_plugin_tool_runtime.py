"""Shared uv / Python preparation for packaged plugin tools.

No test here reaches the network: PyPI metadata and the uv wheel are served by
a fake httpx client, and uv itself is replaced by a fake command runner.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import zipfile

from packaging.tags import Tag
import portalocker
import pytest

from plugin.server.infrastructure import plugin_tool_runtime as module


class FakeHTTPError(Exception):
    pass


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tools = tmp_path / "app-docs" / "plugin-runtime" / "tools"
    monkeypatch.setattr(module, "tools_root", lambda: tools)
    monkeypatch.setattr(module, "_is_packaged", lambda: True)
    monkeypatch.setattr(module, "_system_uv_candidates", lambda: [])
    monkeypatch.setattr(module, "_POLL_SECONDS", 0.01)
    monkeypatch.setattr(module, "_last_error", None)
    monkeypatch.setattr(module, "_progress", None)
    monkeypatch.setattr(module, "_background", None)
    monkeypatch.setattr(
        module, "load_httpx",
        lambda: pytest.fail("this test must not download anything"),
    )
    return tools


class FakePyPI:
    """Serves the pinned release metadata and one wheel; counts wheel downloads."""

    def __init__(
        self, *, digest: str | None = None, origin: str = "https://files.pythonhosted.org/uv.whl",
        declared_size: int | None = None, fail: bool = False, gate: threading.Event | None = None,
    ) -> None:
        self.binary_name = "uv.exe" if sys.platform == "win32" else "uv"
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as wheel:
            wheel.writestr(f"uv-{module.PINNED_UV_VERSION}.data/scripts/{self.binary_name}", b"uv-executable")
            wheel.writestr(f"uv-{module.PINNED_UV_VERSION}.dist-info/licenses/LICENSE-MIT", b"license")
            # Extraction must never deploy arbitrary wheel members.
            wheel.writestr("../../outside", b"unsafe")
        self.body = archive.getvalue()
        self.metadata = {"urls": [{
            "filename": f"uv-{module.PINNED_UV_VERSION}-py3-none-any.whl",
            "packagetype": "bdist_wheel",
            "yanked": False,
            "url": origin,
            "size": len(self.body) if declared_size is None else declared_size,
            "digests": {"sha256": digest or hashlib.sha256(self.body).hexdigest()},
        }]}
        self.fail = fail
        self.gate = gate
        self.wheel_downloads = 0
        self.timeouts: list[tuple] = []
        self.on_chunk = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakePyPI:
        pypi = self

        class Response:
            def __init__(self, data: bytes) -> None:
                self.data = data

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                pass

            def raise_for_status(self):
                pass

            def iter_bytes(self):
                midpoint = len(self.data) // 2
                for chunk in (self.data[:midpoint], self.data[midpoint:]):
                    if pypi.on_chunk is not None:
                        pypi.on_chunk(chunk)
                    yield chunk

        class Client:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                pass

            def stream(self, method, url, **_kwargs):
                assert method == "GET"
                if pypi.fail:
                    raise FakeHTTPError("connection refused")
                if url.endswith("/json"):
                    return Response(json.dumps(pypi.metadata).encode())
                pypi.wheel_downloads += 1
                if pypi.gate is not None:
                    pypi.gate.wait(5)
                return Response(pypi.body)

        def timeout(*args, **kwargs):
            pypi.timeouts.append((args, kwargs))

        monkeypatch.setattr(
            module, "load_httpx",
            lambda: SimpleNamespace(Client=Client, Timeout=timeout, HTTPError=FakeHTTPError),
        )
        return self


def _identity(**overrides) -> str:
    values = [
        list(sys.version_info[:3]), sys.platform, struct.calcsize("P"),
        module.sysconfig.get_platform(), module.platform.machine() if sys.platform == "darwin" else None,
    ]
    for index, value in overrides.items():
        values[int(index[1:])] = value
    return json.dumps(values)


class FakeUv:
    """Stands in for uv and the managed interpreter."""

    def __init__(self, root: Path, *, identity: str | None = None, install_fails: bool = False,
                 versions: dict[str, str] | None = None) -> None:
        self.python = str(root / "python" / "cpython" / "bin" / "python3")
        self.identity = _identity() if identity is None else identity
        self.install_fails = install_fails
        self.versions = versions or {}
        self.installed = False
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, cmd, *, label, env, cancel, deadline):
        assert isinstance(label, str) and isinstance(cancel, threading.Event) and deadline > 0
        self.calls.append((cmd, env))
        if cmd[1:] == ["--version"]:
            version = self.versions.get(cmd[0])
            return subprocess.CompletedProcess(cmd, 0 if version else 1, f"uv {version}\n" if version else "", "")
        if cmd[1:3] == ["python", "find"]:
            if self.installed:
                return subprocess.CompletedProcess(cmd, 0, self.python + "\n", "")
            return subprocess.CompletedProcess(cmd, 2, "", "not found")
        if cmd[1:3] == ["python", "install"]:
            if self.install_fails:
                return subprocess.CompletedProcess(cmd, 1, "", "error: no download for this platform")
            self.installed = True
            Path(self.python).parent.mkdir(parents=True, exist_ok=True)
            Path(self.python).write_bytes(b"")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == self.python:
            return subprocess.CompletedProcess(cmd, 0, self.identity, "")
        raise AssertionError(f"unexpected command {cmd}")


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
    pypi = FakePyPI().install(monkeypatch)
    uv = FakeUv(root)
    events: list[module.ProgressEvent] = []

    result = _prepare(uv, progress=events.append)

    cached = module.cached_uv_path()
    assert cached.read_bytes() == b"uv-executable"
    assert (cached.parent / "LICENSE-MIT.txt").read_bytes() == b"license"
    assert sorted(path.name for path in cached.parent.iterdir()) == ["LICENSE-MIT.txt", pypi.binary_name]
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
    assert [event.phase for event in events][0] == "waiting"
    assert {"downloading_uv", "installing_python", "checking_python"} <= {event.phase for event in events}
    assert events[-1].phase == "checking_python"
    downloaded = [event for event in events if event.phase == "downloading_uv" and event.downloaded_bytes]
    assert downloaded[-1].downloaded_bytes == downloaded[-1].total_bytes == len(pypi.body)
    assert not (root / "envs").exists()


def test_ready_status_and_prepare_start_no_child_process(root, monkeypatch):
    FakePyPI().install(monkeypatch)
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
    FakePyPI().install(monkeypatch)
    uv = FakeUv(root)
    _prepare(uv)
    Path(uv.python).unlink()
    assert module.read_tool_runtime_status().state == "missing"
    record = json.loads((root / "python.json").read_text(encoding="utf-8"))
    Path(uv.python).write_bytes(b"")
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


def test_no_wheel_for_this_platform_is_unsupported():
    def wheel(filename):
        return {"filename": filename, "packagetype": "bdist_wheel", "yanked": False}

    target = wheel(f"uv-{module.PINNED_UV_VERSION}-py3-none-win_amd64.whl")
    metadata = {"urls": [
        wheel(f"uv-{module.PINNED_UV_VERSION}-py3-none-manylinux_2_17_x86_64.whl"),
        wheel("uv-0.1.0-py3-none-win_amd64.whl"),
        target,
    ]}
    assert module._select_uv_wheel(metadata, [Tag("py3", "none", "win_amd64")]) is target
    with pytest.raises(module.ToolRuntimeError) as raised:
        module._select_uv_wheel(metadata, [Tag("py3", "none", "macosx_11_0_arm64")])
    assert raised.value.code == "unsupported_platform"


# ---------------------------------------------------------------------------
# Download failures


def _expect_failure(runner, code: str, **kwargs) -> module.ToolRuntimeError:
    with pytest.raises(module.ToolRuntimeError) as raised:
        _prepare(runner, **kwargs)
    assert raised.value.code == code
    return raised.value


def test_checksum_mismatch_leaves_no_executable_and_is_reported(root, monkeypatch):
    FakePyPI(digest="0" * 64).install(monkeypatch)
    _expect_failure(FakeUv(root), "checksum_mismatch")
    assert not module.cached_uv_path().exists()
    assert list(module.cached_uv_path().parent.glob(".uv-*")) == []
    status = module.read_tool_runtime_status()
    assert (status.state, status.error_code) == ("failed", "checksum_mismatch")
    assert status.error_detail


def test_download_over_the_size_limit_is_rejected(root, monkeypatch):
    FakePyPI().install(monkeypatch)
    monkeypatch.setattr(module, "_MAX_WHEEL_BYTES", 100)
    _expect_failure(FakeUv(root), "download_failed")
    assert not module.cached_uv_path().exists()
    assert list(module.cached_uv_path().parent.glob(".uv-*")) == []


def test_declared_size_over_the_limit_is_rejected_before_downloading(root, monkeypatch):
    pypi = FakePyPI(declared_size=module._MAX_WHEEL_BYTES + 1).install(monkeypatch)
    _expect_failure(FakeUv(root), "download_failed")
    assert pypi.wheel_downloads == 0


def test_unexpected_origin_is_rejected(root, monkeypatch):
    FakePyPI(origin="https://other.example/uv.whl").install(monkeypatch)
    _expect_failure(FakeUv(root), "download_failed")
    assert not module.cached_uv_path().parent.exists()


def test_download_io_timeout_is_capped_by_the_remaining_budget(root, monkeypatch):
    from tests.fake_clock import patch_module_clock

    clock = [100.0]
    patch_module_clock(monkeypatch, module, monotonic=lambda: clock[0])
    pypi = FakePyPI().install(monkeypatch)

    def advance(_chunk):
        clock[0] += 1

    pypi.on_chunk = advance
    module._download_uv(module.cached_uv_path(), lambda _event: None, threading.Event(), 105.0)
    assert pypi.timeouts[1:] == [((5.0,), {"connect": 5.0}), ((3.0,), {"connect": 3.0})]


# ---------------------------------------------------------------------------
# Python installation


def test_python_identity_mismatch_records_nothing(root, monkeypatch):
    FakePyPI().install(monkeypatch)
    _expect_failure(FakeUv(root, identity=_identity(i0=[3, 0, 0])), "python_mismatch")
    assert not (root / "python.json").exists()


def test_python_of_another_architecture_is_rejected(root, monkeypatch):
    FakePyPI().install(monkeypatch)
    monkeypatch.setattr(module, "_host_architecture", lambda: "x86_64")
    _expect_failure(FakeUv(root, identity=_identity(i3="linux-aarch64", i4=None)), "python_mismatch")
    assert not (root / "python.json").exists()


@pytest.mark.parametrize("stdout", ["not json", "{}", "[1, 2]"])
def test_unreadable_python_probe_is_a_mismatch(root, monkeypatch, stdout):
    FakePyPI().install(monkeypatch)
    _expect_failure(FakeUv(root, identity=stdout), "python_mismatch")


def test_python_install_failure(root, monkeypatch):
    FakePyPI().install(monkeypatch)
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
    pypi = FakePyPI().install(monkeypatch)
    canceled = threading.Event()
    pypi.on_chunk = lambda chunk: canceled.set() if chunk.startswith(b"PK") else None
    _expect_failure(FakeUv(root), "canceled", cancel=canceled)
    assert not module.cached_uv_path().exists()
    assert module.read_tool_runtime_status().state == "missing"


def test_deadline_passed(root):
    _expect_failure(_no_runner, "timeout", deadline=time.monotonic() - 1)


@pytest.mark.parametrize("phase", ["metadata", "wheel"])
def test_download_checks_the_deadline_between_chunks(root, monkeypatch, phase):
    from tests.fake_clock import patch_module_clock

    clock = [100.0]
    patch_module_clock(monkeypatch, module, monotonic=lambda: clock[0])
    pypi = FakePyPI().install(monkeypatch)

    def on_chunk(data):
        if (data.startswith(b"{") and phase == "metadata") or (data.startswith(b"PK") and phase == "wheel"):
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
    pypi = FakePyPI(gate=gate).install(monkeypatch)
    uv = FakeUv(root)
    results: list[module.ToolRuntime] = []
    errors: list[BaseException] = []

    def worker():
        try:
            results.append(_prepare(uv))
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
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


def test_lock_held_elsewhere_reports_busy_without_waiting(root):
    root.mkdir(parents=True)
    with portalocker.Lock(root / ".prepare.lock", timeout=0, fail_when_locked=True):
        _expect_failure(_no_runner, "busy", wait_for_lock=False)


def test_in_process_preparation_reports_busy_without_waiting(root):
    with module._flight:
        _expect_failure(_no_runner, "busy", wait_for_lock=False)


def test_waiting_on_a_held_lock_honours_the_deadline_and_cancel(root):
    root.mkdir(parents=True)
    with portalocker.Lock(root / ".prepare.lock", timeout=0, fail_when_locked=True):
        _expect_failure(_no_runner, "timeout", deadline=time.monotonic() + 0.2)
        canceled = threading.Event()
        threading.Timer(0.1, canceled.set).start()
        started = time.monotonic()
        _expect_failure(_no_runner, "canceled", cancel=canceled)
        assert time.monotonic() - started < 5


def test_waiter_uses_what_the_other_process_prepared(root, monkeypatch):
    FakePyPI().install(monkeypatch)
    uv = FakeUv(root)
    _prepare(uv)
    record = (root / "python.json").read_text(encoding="utf-8")
    (root / "python.json").unlink()
    lock = portalocker.Lock(root / ".prepare.lock", timeout=0, fail_when_locked=True)
    lock.acquire()

    def finish():
        (root / "python.json").write_text(record, encoding="utf-8")
        lock.release()

    threading.Timer(0.1, finish).start()
    result = _prepare(_no_runner)
    assert result.python == uv.python


# ---------------------------------------------------------------------------
# uv on PATH as fallback


def test_failed_download_falls_back_to_a_new_enough_system_uv(root, monkeypatch):
    FakePyPI(fail=True).install(monkeypatch)
    monkeypatch.setattr(module, "_system_uv_candidates", lambda: ["/old/uv", "/new/uv"])
    uv = FakeUv(root, versions={"/old/uv": "0.3.0", "/new/uv": "99.0.0"})
    result = _prepare(uv)
    assert result.uv == "/new/uv"
    assert json.loads((root / "python.json").read_text(encoding="utf-8"))["uv_version"] == "99.0.0"


def test_failed_download_without_a_new_enough_system_uv_fails(root, monkeypatch):
    FakePyPI(fail=True).install(monkeypatch)
    monkeypatch.setattr(module, "_system_uv_candidates", lambda: ["/old/uv", "/broken/uv"])
    uv = FakeUv(root, versions={"/old/uv": "0.3.0", "/broken/uv": "not-a-version"})
    _expect_failure(uv, "download_failed")
    assert not any(cmd[1:3] == ["python", "install"] for cmd, _env in uv.calls)


def test_checksum_mismatch_does_not_fall_back(root, monkeypatch):
    FakePyPI(digest="0" * 64).install(monkeypatch)
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

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: SimpleNamespace(app_docs_dir=tmp_path))
    relative = module.tools_root().relative_to(tmp_path)
    assert relative.parts[0] in REGENERABLE_RUNTIME_ENTRY_NAMES
    assert relative.parts[0] not in MIGRATED_RUNTIME_ENTRY_NAMES
    for path in (module.python_install_dir(), module.tool_envs_dir(), module.cached_uv_path()):
        assert path.is_relative_to(module.tools_root())
    assert not (tmp_path / "plugin-runtime").exists()


def test_background_preparation_starts_once_and_finishes(root, monkeypatch):
    gate = threading.Event()
    pypi = FakePyPI(gate=gate).install(monkeypatch)
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
    FakePyPI(gate=gate).install(monkeypatch)
    monkeypatch.setattr(module, "_run_command", FakeUv(root))
    assert module.cancel_tool_runtime_preparation() is False
    module.start_tool_runtime_preparation()
    assert module.cancel_tool_runtime_preparation() is True
    gate.set()
    module._background[0].join(10)
    assert module.read_tool_runtime_status().state == "missing"


def test_background_failure_is_reported_then_retried(root, monkeypatch):
    pypi = FakePyPI(digest="0" * 64).install(monkeypatch)
    monkeypatch.setattr(module, "_run_command", FakeUv(root))
    module.start_tool_runtime_preparation()
    module._background[0].join(10)
    assert module.read_tool_runtime_status().error_code == "checksum_mismatch"
    pypi.metadata["urls"][0]["digests"]["sha256"] = hashlib.sha256(pypi.body).hexdigest()
    module.start_tool_runtime_preparation()
    module._background[0].join(10)
    status = module.read_tool_runtime_status()
    assert (status.state, status.error_code) == ("ready", None)


def test_unexpected_error_becomes_internal_error(root, monkeypatch):
    def broken(*_args, **_kwargs):
        raise LookupError("boom")

    monkeypatch.setattr(module, "_prepare_locked", broken)
    _expect_failure(_no_runner, "internal_error")
    assert module.read_tool_runtime_status().error_code == "internal_error"
    assert not module._flight.locked()


async def test_async_wrapper_cancels_the_worker(root, monkeypatch):
    gate = threading.Event()
    pypi = FakePyPI(gate=gate).install(monkeypatch)
    monkeypatch.setattr(module, "_run_command", FakeUv(root))
    task = asyncio.ensure_future(module.ensure_tool_runtime(timeout=60))
    while pypi.wheel_downloads == 0:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    gate.set()
    while module._flight.locked():
        await asyncio.sleep(0.01)
    assert module.read_tool_runtime_status().state == "missing"
    assert not module.cached_uv_path().exists()


async def test_async_wrapper_returns_the_prepared_runtime(root, monkeypatch):
    FakePyPI().install(monkeypatch)
    uv = FakeUv(root)
    monkeypatch.setattr(module, "_run_command", uv)
    result = await module.ensure_tool_runtime(timeout=60)
    assert result.python == uv.python
