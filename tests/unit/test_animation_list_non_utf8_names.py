"""Animation lists must survive file names that are not valid UTF-8.

On Linux a file extracted from a GBK / Shift-JIS archive keeps its raw bytes,
and Python hands its name back with lone surrogates. One such ``.vmd`` /
``.vrma`` used to break JSON rendering and fail the whole list with a 500.
The lists now skip those entries and log only how many were skipped, never a
file system path. All names here are synthetic and live under ``tmp_path``.
"""

import os
import sys
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from main_routers import mmd_router, vrm_router


pytestmark = pytest.mark.unit

# "你好世界" in GBK: valid bytes on disk, invalid as UTF-8.
BAD_NAME_BYTES = "你好世界".encode("gbk")
BAD_NAME = BAD_NAME_BYTES.decode("utf-8", "surrogateescape")


def _make_bad_file(directory, suffix: bytes):
    """Create ``directory/<GBK bytes><suffix>``; skip where the filesystem refuses raw bytes."""
    if sys.platform == "win32":
        pytest.skip("Windows file names are UTF-16; non-UTF-8 byte names cannot occur")
    target = os.path.join(os.fsencode(directory), BAD_NAME_BYTES + suffix)
    try:
        with open(target, "wb") as file_obj:
            file_obj.write(b"x")
    except OSError as exc:  # e.g. APFS rejects invalid UTF-8 names
        pytest.skip(f"filesystem rejects non-UTF-8 names: {exc}")


class _RecordingLogger:
    """Stands in for the module logger and keeps every formatted message."""

    def __init__(self):
        self.messages = []

    def _record(self, msg, *args, **_kwargs):
        self.messages.append(str(msg) % args if args else str(msg))

    debug = info = warning = error = exception = critical = _record


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

    with _client(mmd_router) as client:
        response = client.get("/api/model/mmd/animations")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert sorted(a["filename"] for a in body["animations"]) == ["Dance #1.vmd", "Wave.vmd"]
    assert any("2" in message and "UTF-8" in message for message in log.messages)
    _assert_logs_have_no_paths(log, tmp_path)


def test_mmd_delete_list_skips_non_utf8_entries(mmd_env, tmp_path):
    _, user_anim, log = mmd_env
    (user_anim / "Wave.vmd").write_bytes(b"vmd")
    _make_bad_file(user_anim, b".vmd")

    with _client(mmd_router) as client:
        response = client.get("/api/model/mmd/animations/list")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert [a["filename"] for a in body["animations"]] == ["Wave.vmd"]
    assert any("1" in message and "UTF-8" in message for message in log.messages)
    _assert_logs_have_no_paths(log, tmp_path)


def test_vrm_animations_skip_non_utf8_entries(vrm_env, tmp_path):
    static_anim, user_anim, log = vrm_env
    (static_anim / "Idle #1.vrma.gz").write_bytes(b"gz")
    (user_anim / "Wave.vrma").write_bytes(b"vrma")
    (user_anim / "Pose.vrm").write_bytes(b"vrm")
    _make_bad_file(static_anim, b".vrma.gz")
    _make_bad_file(user_anim, b".vrma")
    _make_bad_file(user_anim, b".vrm")

    with _client(vrm_router) as client:
        response = client.get("/api/model/vrm/animations")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert sorted(a["filename"] for a in body["animations"]) == ["Idle #1.vrma.gz", "Pose.vrm", "Wave.vrma"]
    assert any("3" in message and "UTF-8" in message for message in log.messages)
    _assert_logs_have_no_paths(log, tmp_path)


def test_vrm_animation_file_errors_do_not_log_paths(vrm_env, tmp_path, monkeypatch):
    _, user_anim, log = vrm_env
    (user_anim / "Wave.vrma").write_bytes(b"vrma")
    (user_anim / "Broken.vrma").write_bytes(b"vrma")
    real_stat = type(user_anim).stat

    def flaky_stat(self, *args, **kwargs):
        if self.name == "Broken.vrma" and not kwargs and not args:
            raise PermissionError(13, "Permission denied", str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(type(user_anim), "stat", flaky_stat)

    with _client(vrm_router) as client:
        response = client.get("/api/model/vrm/animations")

    assert response.status_code == 200
    assert [a["filename"] for a in response.json()["animations"]] == ["Wave.vrma"]
    _assert_logs_have_no_paths(log, tmp_path)
