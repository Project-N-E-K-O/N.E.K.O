"""Animation lists must survive file names that are not valid UTF-8.

On Linux a file extracted from a GBK / Shift-JIS archive keeps its raw bytes,
and Python hands its name back with lone surrogates. One such ``.vmd`` /
``.vrma`` used to break JSON rendering and fail the whole list with a 500.
The lists now skip those entries and log only how many were skipped, never a
file system path. All names here are synthetic and live under ``tmp_path``.
"""

import os
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from main_routers import mmd_router, vrm_router


pytestmark = pytest.mark.unit

# "你好世界" in GBK: valid bytes on disk, invalid as UTF-8.
BAD_NAME_BYTES = "你好世界".encode("gbk")
BAD_NAME = BAD_NAME_BYTES.decode("utf-8", "surrogateescape")


def _make_bad_file(directory, suffix: bytes, *, is_dir: bool = False):
    """Create ``directory/<GBK bytes><suffix>``; skip where the filesystem refuses raw bytes."""
    if sys.platform == "win32":
        pytest.skip("Windows file names are UTF-16; non-UTF-8 byte names cannot occur")
    target = os.path.join(os.fsencode(directory), BAD_NAME_BYTES + suffix)
    try:
        if is_dir:
            os.mkdir(target)
        else:
            with open(target, "wb") as file_obj:
                file_obj.write(b"x")
    except OSError as exc:  # e.g. APFS rejects invalid UTF-8 names
        pytest.skip(f"filesystem rejects non-UTF-8 names: {exc}")


class _RecordingLogger:
    """Stands in for the module logger and keeps every formatted message."""

    def __init__(self):
        self.messages = []

    def _record(self, msg, *args, exc_info=None, **_kwargs):
        self.messages.append(str(msg) % args if args else str(msg))
        if exc_info:
            self.messages.append(traceback.format_exc())

    def exception(self, msg, *args, **kwargs):
        self._record(msg, *args, exc_info=True, **kwargs)

    debug = info = warning = error = critical = _record


def _assert_logs_have_no_paths(log: _RecordingLogger, tmp_path):
    for message in log.messages:
        assert str(tmp_path) not in message
        assert BAD_NAME not in message


def _client(router_module):
    app = FastAPI()
    app.include_router(router_module.router)
    return TestClient(app)


@pytest.fixture
def mmd_env(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    static_anim = project_root / "static" / "mmd" / "animation"
    static_anim.mkdir(parents=True)
    mmd_dir = tmp_path / "mmd"
    user_anim = mmd_dir / "animation"
    user_anim.mkdir(parents=True)
    config = SimpleNamespace(project_root=project_root, mmd_dir=mmd_dir, mmd_animation_dir=user_anim)
    monkeypatch.setattr(mmd_router, "get_config_manager", lambda: config)
    log = _RecordingLogger()
    monkeypatch.setattr(mmd_router, "logger", log)
    return static_anim, user_anim, log


@pytest.fixture
def vrm_env(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    static_anim = project_root / "static" / "vrm" / "animation"
    static_anim.mkdir(parents=True)
    user_anim = tmp_path / "vrm" / "animation"
    user_anim.mkdir(parents=True)
    config = SimpleNamespace(
        project_root=project_root,
        vrm_animation_dir=user_anim,
        ensure_vrm_directory=lambda: True,
    )
    monkeypatch.setattr(vrm_router, "get_config_manager", lambda: config)
    log = _RecordingLogger()
    monkeypatch.setattr(vrm_router, "logger", log)
    return static_anim, user_anim, log


def test_mmd_animations_skip_non_utf8_entries(mmd_env, tmp_path):
    static_anim, user_anim, log = mmd_env
    (static_anim / "Dance #1.vmd").write_bytes(b"vmd")
    (user_anim / "Wave.vmd").write_bytes(b"vmd")
    _make_bad_file(static_anim, b".vmd")
    _make_bad_file(user_anim, b".vmd")
    # A folder is not an animation, so it must not count as a skipped one.
    _make_bad_file(user_anim, b"-folder.vmd", is_dir=True)

    with _client(mmd_router) as client:
        response = client.get("/api/model/mmd/animations")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert sorted(a["filename"] for a in body["animations"]) == ["Dance #1.vmd", "Wave.vmd"]
    assert "跳过 2 个文件名不是合法 UTF-8 的 VMD 动画" in log.messages
    _assert_logs_have_no_paths(log, tmp_path)


def test_mmd_delete_list_skips_non_utf8_entries(mmd_env, tmp_path):
    _, user_anim, log = mmd_env
    (user_anim / "Wave.vmd").write_bytes(b"vmd")
    _make_bad_file(user_anim, b".vmd")
    _make_bad_file(user_anim, b"-folder.vmd", is_dir=True)

    with _client(mmd_router) as client:
        response = client.get("/api/model/mmd/animations/list")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert [a["filename"] for a in body["animations"]] == ["Wave.vmd"]
    assert "跳过 1 个文件名不是合法 UTF-8 的 VMD 动画" in log.messages
    _assert_logs_have_no_paths(log, tmp_path)


def test_vrm_animations_skip_non_utf8_entries(vrm_env, tmp_path):
    static_anim, user_anim, log = vrm_env
    (static_anim / "Idle #1.vrma.gz").write_bytes(b"gz")
    (user_anim / "Wave.vrma").write_bytes(b"vrma")
    (user_anim / "Pose.vrm").write_bytes(b"vrm")
    _make_bad_file(static_anim, b".vrma.gz")
    _make_bad_file(user_anim, b".vrma")
    _make_bad_file(user_anim, b".vrm")
    _make_bad_file(user_anim, b"-folder.vrma", is_dir=True)
    _make_bad_file(user_anim, b"-folder.vrm", is_dir=True)

    with _client(vrm_router) as client:
        response = client.get("/api/model/vrm/animations")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert sorted(a["filename"] for a in body["animations"]) == ["Idle #1.vrma.gz", "Pose.vrm", "Wave.vrma"]
    assert "跳过 3 个文件名不是合法 UTF-8 的 VRM 动画" in log.messages
    _assert_logs_have_no_paths(log, tmp_path)


def _fail_for(name, method, monkeypatch):
    """Make ``Path.<method>`` raise a path-carrying PermissionError for ``name`` only."""
    real = getattr(Path, method)

    def flaky(self, *args, **kwargs):
        if self.name == name:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, method, flaky)


@pytest.mark.parametrize("router_name, endpoint, extension", [
    ("mmd", "/api/model/mmd/animations", ".vmd"),
    ("mmd", "/api/model/mmd/animations/list", ".vmd"),
    ("vrm", "/api/model/vrm/animations", ".vrma"),
])
def test_unreadable_animation_file_is_counted_without_logging_paths(
    router_name, endpoint, extension, mmd_env, vrm_env, tmp_path, monkeypatch,
):
    _, user_anim, log = mmd_env if router_name == "mmd" else vrm_env
    (user_anim / f"Wave{extension}").write_bytes(b"x")
    (user_anim / f"Broken{extension}").write_bytes(b"x")
    _fail_for(f"Broken{extension}", "is_file", monkeypatch)

    with _client(mmd_router if router_name == "mmd" else vrm_router) as client:
        response = client.get(endpoint)

    assert response.status_code == 200
    assert [a["filename"] for a in response.json()["animations"]] == [f"Wave{extension}"]
    label = "VMD" if router_name == "mmd" else "VRM"
    assert f"跳过 1 个处理失败的 {label} 动画文件" in log.messages
    _assert_logs_have_no_paths(log, tmp_path)


@pytest.mark.parametrize("router_name, endpoint, method", [
    ("mmd", "/api/model/mmd/animations", "iterdir"),
    ("mmd", "/api/model/mmd/animations/list", "iterdir"),
    ("vrm", "/api/model/vrm/animations", "iterdir"),
])
def test_unreadable_animation_directory_does_not_log_paths(
    router_name, endpoint, method, mmd_env, vrm_env, tmp_path, monkeypatch,
):
    _, _, log = mmd_env if router_name == "mmd" else vrm_env
    _fail_for("animation", method, monkeypatch)

    with _client(mmd_router if router_name == "mmd" else vrm_router) as client:
        response = client.get(endpoint)

    assert response.status_code == 200
    assert response.json()["animations"] == []
    assert any(message.startswith("处理动画目录失败") for message in log.messages)
    _assert_logs_have_no_paths(log, tmp_path)


@pytest.mark.parametrize("router_name, endpoint", [
    ("mmd", "/api/model/mmd/animations"),
    ("mmd", "/api/model/mmd/animations/list"),
    ("vrm", "/api/model/vrm/animations"),
])
def test_unexpected_failure_does_not_log_paths(router_name, endpoint, tmp_path, monkeypatch):
    router_module = mmd_router if router_name == "mmd" else vrm_router
    log = _RecordingLogger()
    monkeypatch.setattr(router_module, "logger", log)
    secret = str(tmp_path / "config.json")

    def broken_config():
        raise PermissionError(13, "Permission denied", secret)

    monkeypatch.setattr(router_module, "get_config_manager", broken_config)

    with _client(router_module) as client:
        response = client.get(endpoint)

    assert response.status_code == 500
    assert log.messages
    _assert_logs_have_no_paths(log, tmp_path)


@pytest.mark.parametrize("endpoint, broken_dir, expected", [
    ("/api/model/mmd/animations", "static", ["Wave.vmd"]),
    ("/api/model/mmd/animations/list", "user", []),
])
def test_mmd_animation_dir_check_failure_is_tolerated(
    endpoint, broken_dir, expected, mmd_env, tmp_path, monkeypatch,
):
    static_anim, user_anim, log = mmd_env
    (user_anim / "Wave.vmd").write_bytes(b"vmd")
    target = static_anim if broken_dir == "static" else user_anim
    real_exists = Path.exists

    def flaky_exists(self, *args, **kwargs):
        if self == target:
            raise PermissionError(13, "Permission denied", str(self))
        return real_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", flaky_exists)

    with _client(mmd_router) as client:
        response = client.get(endpoint)

    assert response.status_code == 200
    assert [a["filename"] for a in response.json()["animations"]] == expected
    assert "检查动画目录失败: PermissionError" in log.messages
    _assert_logs_have_no_paths(log, tmp_path)
