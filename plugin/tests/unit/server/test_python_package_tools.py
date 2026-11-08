"""Tool preparation stays local until a dependency sync is requested."""

from __future__ import annotations

from concurrent.futures import CancelledError
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import threading
from types import SimpleNamespace
import zipfile

from packaging.tags import Tag
import pytest

from plugin.server.infrastructure import python_package_tools as module


@pytest.fixture
def tools(tmp_path: Path, monkeypatch):
    root = tmp_path / "managed-tools"
    monkeypatch.setattr(module, "_tools_root", lambda: root)
    monkeypatch.setattr(module, "_is_packaged", lambda: False)
    monkeypatch.setattr(module, "_find_uv", lambda: None)
    monkeypatch.setattr(module, "_pip_available", lambda: False)
    return root


def test_capability_read_does_not_create_or_download_tools(tools):
    result = module.read_package_tools()
    assert not tools.exists()
    assert result["installer"] is None
    assert result["setup_required"] is True
    assert result["can_sync"] is True


def test_source_uses_current_interpreter_and_existing_uv_without_setup(tools, monkeypatch):
    monkeypatch.setattr(module, "_find_uv", lambda: "/existing/uv")
    result = module.prepare_package_tools(
        lambda *_args, **_kwargs: pytest.fail("source tools need no child process"),
        lambda _line: None,
        threading.Event(),
    )
    assert result == module.PackageTools(sys.executable, "/existing/uv")
    assert not tools.exists()


def test_source_keeps_existing_pip_fallback(tools, monkeypatch):
    monkeypatch.setattr(module, "_pip_available", lambda: True)
    result = module.prepare_package_tools(lambda *_args, **_kwargs: None, lambda _line: None, threading.Event())
    assert result.python == sys.executable
    assert result.uv_executable is None
    assert result.allow_pip_fallback is True
    assert not tools.exists()


def test_canceled_setup_never_downloads_or_creates_tools(tools):
    canceled = threading.Event()
    canceled.set()
    with pytest.raises(CancelledError):
        module.prepare_package_tools(lambda *_args, **_kwargs: None, lambda _line: None, canceled)
    assert not tools.exists()


def test_wheel_selection_uses_runtime_tags_and_pinned_version():
    def wheel(filename):
        return {"filename": filename, "packagetype": "bdist_wheel", "yanked": False}

    target = wheel(f"uv-{module._UV_VERSION}-py3-none-win_amd64.whl")
    metadata = {"urls": [
        wheel(f"uv-{module._UV_VERSION}-py3-none-manylinux_2_17_x86_64.whl"),
        wheel("uv-0.1.0-py3-none-win_amd64.whl"),
        target,
    ]}
    assert module._select_uv_wheel(metadata, [Tag("py3", "none", "win_amd64")]) is target
    with pytest.raises(RuntimeError, match="platform"):
        module._select_uv_wheel(metadata, [Tag("py3", "none", "macosx_11_0_arm64")])


def _wheel_download(
    monkeypatch, *, invalid_digest=False, origin="https://files.pythonhosted.org/uv.whl", on_chunk=None, timeouts=None, split_chunks=False,
):
    binary_name = "uv.exe" if sys.platform == "win32" else "uv"
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr(f"uv-{module._UV_VERSION}.data/scripts/{binary_name}", b"uv-executable")
        archive.writestr(f"uv-{module._UV_VERSION}.dist-info/licenses/LICENSE-MIT", b"license")
        # Extraction must never deploy arbitrary wheel members.
        archive.writestr("../../outside", b"unsafe")
    body = archive_bytes.getvalue()
    metadata = {"urls": [{
        "filename": f"uv-{module._UV_VERSION}-py3-none-any.whl",
        "packagetype": "bdist_wheel",
        "yanked": False,
        "url": origin,
        "digests": {"sha256": "0" * 64 if invalid_digest else hashlib.sha256(body).hexdigest()},
    }]}

    class Response:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            pass

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            midpoint = len(self.data) // 2
            chunks = [self.data[:midpoint], self.data[midpoint:]] if split_chunks else [self.data]
            for chunk in chunks:
                if on_chunk is not None:
                    on_chunk(chunk)
                yield chunk

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            pass

        def stream(self, method, url, **kwargs):
            assert method == "GET"
            return Response(json.dumps(metadata).encode() if url.endswith("/json") else body)

    def timeout(*args, **kwargs):
        if timeouts is not None:
            timeouts.append((args, kwargs))
        return None

    monkeypatch.setattr(module, "load_httpx", lambda: SimpleNamespace(Client=Client, Timeout=timeout))
    return binary_name


def test_download_verifies_wheel_and_extracts_only_uv_and_licenses(tools, monkeypatch):
    binary_name = _wheel_download(monkeypatch)
    destination = tools / binary_name
    module._download_uv(destination, lambda _line: None, threading.Event())
    assert destination.read_bytes() == b"uv-executable"
    assert (tools / "LICENSE-MIT.txt").read_bytes() == b"license"
    assert sorted(path.name for path in tools.iterdir()) == ["LICENSE-MIT.txt", binary_name]


def test_checksum_failure_leaves_no_executable_or_partial_archive(tools, monkeypatch):
    binary_name = _wheel_download(monkeypatch, invalid_digest=True)
    destination = tools / binary_name
    with pytest.raises(RuntimeError, match="checksum"):
        module._download_uv(destination, lambda _line: None, threading.Event())
    assert not destination.exists()
    assert list(tools.glob(".uv-*")) == []


def test_download_rejects_an_unexpected_origin(tools, monkeypatch):
    binary_name = _wheel_download(monkeypatch, origin="https://other.example/uv.whl")
    with pytest.raises(RuntimeError, match="origin"):
        module._download_uv(tools / binary_name, lambda _line: None, threading.Event())
    assert not tools.exists()


def test_packaged_setup_uses_managed_interpreter_and_checks_host_match(tools, monkeypatch):
    monkeypatch.setattr(module, "_is_packaged", lambda: True)
    monkeypatch.setattr(module, "_find_uv", lambda: "/existing/uv")
    calls = []
    python_path = str(tools / "python" / "python")
    find_count = 0

    def runner(cmd, *, label, env=None):
        nonlocal find_count
        calls.append((cmd, label, env))
        if cmd[1:3] == ["python", "find"]:
            find_count += 1
            return subprocess.CompletedProcess(cmd, 1 if find_count == 1 else 0, "" if find_count == 1 else python_path)
        if cmd[1:3] == ["python", "install"]:
            return subprocess.CompletedProcess(cmd, 0, "")
        return subprocess.CompletedProcess(cmd, 0, json.dumps([list(sys.version_info[:3]), sys.platform, struct.calcsize("P"), module.sysconfig.get_platform(), module.platform.machine() if sys.platform == "darwin" else None]))

    result = module.prepare_package_tools(runner, lambda _line: None, threading.Event())
    assert result.python == python_path
    assert result.uv_executable == "/existing/uv"
    assert result.allow_pip_fallback is False
    assert result.env == {"UV_PYTHON_INSTALL_DIR": str(tools / "python")}
    assert len(calls) == 4
    assert "--system" in calls[0][0]
    assert "--no-bin" in calls[1][0]
    assert "--no-registry" in calls[1][0]
    assert any(arg.startswith(f"cpython-{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}-")
               and arg.endswith(module._host_architecture()) for arg in calls[1][0])
    assert all(call[2] == result.env for call in calls)


def test_packaged_setup_rejects_an_interpreter_mismatch(tools):
    def runner(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 0, "/python" if "find" in cmd else "[[0, 0], \"other\", 0]")

    with pytest.raises(RuntimeError, match="does not match"):
        module._prepare_packaged_python("/uv", runner, threading.Event())


def test_packaged_capabilities_do_not_report_host_binary_as_python(tools, monkeypatch):
    monkeypatch.setattr(module, "_is_packaged", lambda: True)
    result = module.read_package_tools()
    assert result["packaged"] is True
    assert result["python_executable"] is None
    assert result["can_sync"] is True
    assert result["setup_required"] is True


def test_packaged_capabilities_recognize_prepared_tools_without_running_them(tools, monkeypatch):
    monkeypatch.setattr(module, "_is_packaged", lambda: True)
    monkeypatch.setattr(module, "_find_uv", lambda: "/existing/uv")
    tools.mkdir()
    python = tools / "python.exe"
    python.touch()
    (tools / "python.json").write_text(json.dumps({
        "executable": str(python), "environment": module.build_environment(),
        "python_version": list(sys.version_info[:3]), "python_architecture": module._host_architecture(),
    }), encoding="utf-8")
    result = module.read_package_tools()
    assert result["python_executable"] == str(python)
    assert result["setup_required"] is False
    python.unlink()
    assert module.read_package_tools()["setup_required"] is True


@pytest.mark.parametrize("phase", ["metadata", "wheel"])
def test_download_checks_task_budget_between_chunks(tools, monkeypatch, phase):
    expired = False
    received = []

    def on_chunk(data):
        nonlocal expired
        received.append(data)
        if (data.startswith(b"{") and phase == "metadata") or (data.startswith(b"PK") and phase == "wheel"):
            expired = True

    def checkpoint():
        if expired:
            raise TimeoutError("The dependency task exceeded its time budget")

    binary_name = _wheel_download(monkeypatch, on_chunk=on_chunk, split_chunks=True)
    with pytest.raises(TimeoutError, match="time budget"):
        module._download_uv(
            tools / binary_name, lambda _line: None, threading.Event(), check_cancelled=checkpoint,
        )
    assert len(received) == (1 if phase == "metadata" else 3)
    assert not (tools / binary_name).exists()
    assert list(tools.glob(".uv-*")) == []


def test_packaged_tools_prefer_cached_uv_over_system_install(tools, monkeypatch):
    monkeypatch.setattr(module, "_is_packaged", lambda: True)
    monkeypatch.setattr(module, "_find_uv", lambda: "/old/system/uv")
    cached = module._cached_uv()
    cached.parent.mkdir(parents=True)
    cached.touch()
    assert module._available_uv() == str(cached)


@pytest.mark.parametrize(("tag", "architecture"), [
    ("win-amd64", "x86_64"), ("win-arm64", "aarch64"), ("win32", "x86"),
    ("macosx-15.0-x86_64", "x86_64"), ("macosx-15.0-arm64", "aarch64"),
    ("linux-x86_64", "x86_64"), ("linux-aarch64", "aarch64"),
])
def test_architecture_uses_interpreter_platform(tag, architecture):
    assert module._python_architecture(tag) == architecture


def test_packaged_setup_rejects_other_architecture_with_same_pointer_size(tools, monkeypatch):
    monkeypatch.setattr(module, "_host_architecture", lambda: "x86_64")

    def runner(cmd, **_kwargs):
        data = "/python" if "find" in cmd else json.dumps([
            list(sys.version_info[:3]), sys.platform, struct.calcsize("P"), "linux-aarch64", None,
        ])
        return subprocess.CompletedProcess(cmd, 0, data)

    with pytest.raises(RuntimeError, match="does not match"):
        module._prepare_packaged_python("/uv", runner, threading.Event())
    assert not (tools / "python.json").exists()


def test_download_requests_cap_io_timeout_by_remaining_budget(tools, monkeypatch):
    clock = [100.0]
    from tests.fake_clock import patch_module_clock

    patch_module_clock(monkeypatch, module, monotonic=lambda: clock[0])
    timeouts = []

    def advance(_chunk):
        clock[0] += 1

    binary_name = _wheel_download(monkeypatch, on_chunk=advance, timeouts=timeouts)
    module._download_uv(tools / binary_name, lambda _line: None, threading.Event(), deadline=102.0)
    assert timeouts[1:] == [((2.0,), {"connect": 2.0}), ((1.0,), {"connect": 1.0})]


@pytest.mark.parametrize(("machine", "architecture"), [("arm64", "aarch64"), ("x86_64", "x86_64")])
def test_macos_universal_interpreter_uses_running_architecture(machine, architecture):
    assert module._python_architecture("macosx-11.0-universal2", machine) == architecture
