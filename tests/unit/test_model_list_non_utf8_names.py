"""Model lists must survive file names that are not valid UTF-8 (#3230).

On Linux a folder extracted from a GBK / Shift-JIS archive keeps its raw bytes,
and Python hands it back with lone surrogates. One such entry used to raise
during URL quoting or JSON rendering and empty the whole list. All names here
are synthetic and live under ``tmp_path``.
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from main_routers import live2d_router, mmd_router, vrm_router
from utils.url_utils import drop_non_utf8_entries, is_utf8_text


pytestmark = pytest.mark.unit

# "你好世界" in GBK: valid bytes on disk, invalid as UTF-8.
BAD_NAME_BYTES = "你好世界".encode("gbk")
BAD_NAME = BAD_NAME_BYTES.decode("utf-8", "surrogateescape")


def _make_bad_path(directory, suffix: bytes = b"", *, is_dir: bool = False):
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
    return os.fsdecode(target)


def test_helpers_detect_surrogate_escaped_names():
    assert is_utf8_text("猫娘 #1%.vrm")
    assert not is_utf8_text(BAD_NAME)
    kept, skipped = drop_non_utf8_entries([
        {"name": "ok", "path": "/user_live2d/ok/ok.model3.json", "size": 1},
        {"name": BAD_NAME, "path": "/user_live2d/x/x.model3.json"},
        {"name": "x", "path": f"/user_live2d/{BAD_NAME}/x.model3.json"},
    ])
    assert [entry["name"] for entry in kept] == ["ok"]
    assert skipped == 2


@pytest.fixture
def live2d_client(tmp_path, monkeypatch):
    live2d_dir = tmp_path / "live2d"
    live2d_dir.mkdir()
    vrm_dir = tmp_path / "vrm"
    vrm_dir.mkdir()
    workshop_root = tmp_path / "workshop"
    workshop_root.mkdir()
    config = SimpleNamespace(
        live2d_dir=live2d_dir,
        readable_live2d_dir=None,
        vrm_dir=vrm_dir,
        ensure_live2d_directory=lambda: True,
        ensure_vrm_directory=lambda: True,
    )
    monkeypatch.setattr(live2d_router, "get_config_manager", lambda: config)
    monkeypatch.setattr(live2d_router, "get_workshop_path", lambda: str(workshop_root))
    app = FastAPI()
    app.include_router(live2d_router.router)
    with TestClient(app) as client:
        yield client, config, workshop_root


def test_live2d_models_keep_valid_entries_next_to_non_utf8_ones(live2d_client, monkeypatch):
    client, _, workshop_root = live2d_client
    monkeypatch.setattr(live2d_router, "find_models", lambda: [
        {"name": "Good #1", "display_name": "Good #1",
         "path": "/user_live2d/Good #1/Good #1.model3.json", "source": "documents"},
        {"name": BAD_NAME, "display_name": BAD_NAME,
         "path": f"/user_live2d/{BAD_NAME}/{BAD_NAME}.model3.json", "source": "documents"},
    ])
    item = workshop_root / "123"
    item.mkdir()
    (item / "Shop.model3.json").write_text("{}", encoding="utf-8")
    _make_bad_path(item, b".model3.json")
    bad_subdir = _make_bad_path(item, is_dir=True)
    with open(os.path.join(os.fsencode(bad_subdir), BAD_NAME_BYTES + b".model3.json"), "wb") as file_obj:
        file_obj.write(b"{}")
    monkeypatch.setattr(live2d_router, "get_subscribed_workshop_items", AsyncMock(return_value={
        "success": True, "items": [{"installedFolder": str(item), "publishedFileId": "123"}],
    }))

    full = client.get("/api/live2d/models")
    assert full.status_code == 200
    assert sorted(model["path"] for model in full.json()) == [
        "/user_live2d/Good%20%231/Good%20%231.model3.json",
        "/workshop/123/Shop.model3.json",
    ]
    simple = client.get("/api/live2d/models", params={"simple": True}).json()
    assert simple["success"] is True
    assert sorted(simple["models"]) == ["Good #1", "Shop"]


def test_live2d_user_models_skip_non_utf8_entries(live2d_client):
    client, config, _ = live2d_client
    good = config.live2d_dir / "Good"
    good.mkdir()
    (good / "Good.model3.json").write_text("{}", encoding="utf-8")
    bad = _make_bad_path(config.live2d_dir, is_dir=True)
    with open(os.path.join(os.fsencode(bad), b"a.model3.json"), "wb") as file_obj:
        file_obj.write(b"{}")
    (config.vrm_dir / "Avatar.vrm").write_bytes(b"glTF")
    _make_bad_path(config.vrm_dir, b".vrm")

    response = client.get("/api/live2d/user_models")
    assert response.status_code == 200
    assert sorted(model["name"] for model in response.json()["models"]) == ["Avatar", "Good"]


def test_vrm_models_skip_non_utf8_entries(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    static_vrm = project_root / "static" / "vrm"
    static_vrm.mkdir(parents=True)
    user_dir = tmp_path / "user_vrm"
    user_dir.mkdir()
    item = tmp_path / "workshop" / "123"
    (item / "nested").mkdir(parents=True)
    (user_dir / "猫娘 #1%.vrm").write_bytes(b"glTF")
    (item / "Shop.vrm").write_bytes(b"glTF")
    for directory in (static_vrm, user_dir, item, item / "nested"):
        _make_bad_path(directory, b".vrm")
    bad_subdir = _make_bad_path(item, is_dir=True)
    with open(os.path.join(os.fsencode(bad_subdir), b"a.vrm"), "wb") as file_obj:
        file_obj.write(b"glTF")
    config = SimpleNamespace(project_root=project_root, vrm_dir=user_dir, ensure_vrm_directory=lambda: True)
    monkeypatch.setattr(vrm_router, "get_config_manager", lambda: config)
    monkeypatch.setattr(vrm_router, "get_subscribed_workshop_items", AsyncMock(return_value={
        "success": True, "items": [{"installedFolder": str(item), "publishedFileId": "123"}],
    }))
    app = FastAPI()
    app.include_router(vrm_router.router)
    with TestClient(app) as client:
        response = client.get("/api/model/vrm/models")
    assert response.status_code == 200
    assert sorted(model["url"] for model in response.json()["models"]) == [
        "/user_vrm/%E7%8C%AB%E5%A8%98%20%231%25.vrm",
        "/workshop/123/Shop.vrm",
    ]


def test_mmd_models_skip_non_utf8_entries(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    static_mmd = project_root / "static" / "mmd"
    static_mmd.mkdir(parents=True)
    mmd_dir = tmp_path / "mmd"
    (mmd_dir / "Good").mkdir(parents=True)
    (mmd_dir / "Good" / "Good.pmx").write_bytes(b"pmx")
    item = tmp_path / "workshop" / "123"
    item.mkdir(parents=True)
    (item / "Shop.pmx").write_bytes(b"pmx")
    for directory in (static_mmd, mmd_dir / "Good", item):
        _make_bad_path(directory, b".pmx")
    _make_bad_path(mmd_dir, is_dir=True)  # would be listed as a broken top-level folder
    config = SimpleNamespace(project_root=project_root, mmd_dir=mmd_dir, mmd_animation_dir=mmd_dir / "animation")
    monkeypatch.setattr(mmd_router, "get_config_manager", lambda: config)
    monkeypatch.setattr(mmd_router, "get_subscribed_workshop_items", AsyncMock(return_value={
        "success": True, "items": [{"installedFolder": str(item), "publishedFileId": "123"}],
    }))
    app = FastAPI()
    app.include_router(mmd_router.router)
    with TestClient(app) as client:
        response = client.get("/api/model/mmd/models")
    assert response.status_code == 200
    assert sorted(model["url"] for model in response.json()["models"]) == [
        "/user_mmd/Good/Good.pmx",
        "/workshop/123/Shop.pmx",
    ]
