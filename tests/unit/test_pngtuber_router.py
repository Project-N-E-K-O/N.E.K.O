from pathlib import Path
import copy
import importlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers.pngtuber_router import router
from main_routers.shared_state import init_shared_state
from utils.config_manager import get_config_manager, get_reserved


characters_router_module = importlib.import_module("main_routers.characters_router")


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_MODEL_DIR = PROJECT_ROOT / "static" / "pngtuber-test" / "sample-pngtuber-model"
CATGIRL_BUCKET = "\u732b\u5a18"
TEST_CATGIRL_NAME = "\u6d4b\u8bd5\u89d2\u8272"


class DummyRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class DummyConfigManager:
    def __init__(self):
        self.characters = {
            CATGIRL_BUCKET: {
                TEST_CATGIRL_NAME: {
                    "_reserved": {
                        "avatar": {
                            "model_type": "live2d",
                            "live2d": {"model_path": "mao_pro/mao_pro.model3.json"},
                        }
                    }
                }
            }
        }
        self.saved_characters = None

    async def aload_characters(self, character_json_path=None):
        return copy.deepcopy(self.characters)

    async def asave_characters(self, characters, character_json_path=None):
        self.saved_characters = copy.deepcopy(characters)
        self.characters = copy.deepcopy(characters)


@pytest.fixture
def pngtuber_client(clean_user_data_dir):
    init_shared_state({}, None, None, get_config_manager(), None)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _open_multipart_folder_files(folder: Path):
    files = []
    opened = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(folder.parent).as_posix()
        handle = path.open("rb")
        opened.append(handle)
        files.append(("files", (rel, handle, "application/octet-stream")))
    return files, opened


def test_upload_pngtuber_folder_creates_importable_model(pngtuber_client):
    cm = get_config_manager()
    imported_dir = cm.pngtuber_dir / "Sample_PNGTuber_Test_Model"
    if imported_dir.exists():
        import shutil
        shutil.rmtree(imported_dir)

    files, opened = _open_multipart_folder_files(SAMPLE_MODEL_DIR)
    try:
        response = pngtuber_client.post("/api/model/pngtuber/upload_model", files=files)
    finally:
        for handle in opened:
            handle.close()

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    assert data["model_type"] == "pngtuber"
    assert data["folder"] == "Sample_PNGTuber_Test_Model"
    assert data["pngtuber"]["idle_image"] == "/user_pngtuber/Sample_PNGTuber_Test_Model/idle.gif"
    assert data["pngtuber"]["talking_image"] == "/user_pngtuber/Sample_PNGTuber_Test_Model/talking.gif"
    assert data["pngtuber"]["click_image"] == "/user_pngtuber/Sample_PNGTuber_Test_Model/talking.gif"
    assert data["pngtuber"]["drag_image"] == "/static/assets/neko-idle/cat-idle-cat-move-1.gif"

    assert (imported_dir / "model.json").exists()
    assert (imported_dir / "idle.gif").exists()
    assert (imported_dir / "talking.gif").exists()

    list_response = pngtuber_client.get("/api/model/pngtuber/models")
    assert list_response.status_code == 200
    models = list_response.json()["models"]
    assert any(m["folder"] == "Sample_PNGTuber_Test_Model" for m in models)


@pytest.mark.asyncio
async def test_uploaded_pngtuber_config_can_be_saved_to_character(pngtuber_client, monkeypatch):
    cm = get_config_manager()
    imported_dir = cm.pngtuber_dir / "Sample_PNGTuber_Test_Model"
    if imported_dir.exists():
        import shutil
        shutil.rmtree(imported_dir)

    files, opened = _open_multipart_folder_files(SAMPLE_MODEL_DIR)
    try:
        upload_response = pngtuber_client.post("/api/model/pngtuber/upload_model", files=files)
    finally:
        for handle in opened:
            handle.close()

    assert upload_response.status_code == 200, upload_response.text
    uploaded = upload_response.json()
    assert uploaded["success"] is True

    config_manager = DummyConfigManager()

    async def _noop_initialize():
        return None

    async def _noop_init_one(name, *, is_new=False):
        return None

    monkeypatch.setattr(characters_router_module, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr(characters_router_module, "get_initialize_character_data", lambda: _noop_initialize)
    monkeypatch.setattr(characters_router_module, "get_init_one_catgirl", lambda: _noop_init_one)

    save_response = await characters_router_module.update_catgirl_l2d(
        TEST_CATGIRL_NAME,
        DummyRequest({"model_type": "pngtuber", "pngtuber": uploaded["pngtuber"]}),
    )
    body = json.loads(save_response.body)

    assert save_response.status_code == 200
    assert body["success"] is True
    catgirl = config_manager.saved_characters[CATGIRL_BUCKET][TEST_CATGIRL_NAME]
    pngtuber = get_reserved(catgirl, "avatar", "pngtuber")
    assert get_reserved(catgirl, "avatar", "model_type") == "pngtuber"
    assert get_reserved(catgirl, "avatar", "live3d_sub_type") == ""
    assert pngtuber["idle_image"] == "/user_pngtuber/Sample_PNGTuber_Test_Model/idle.gif"
    assert pngtuber["talking_image"] == "/user_pngtuber/Sample_PNGTuber_Test_Model/talking.gif"
    assert pngtuber["click_image"] == "/user_pngtuber/Sample_PNGTuber_Test_Model/talking.gif"
    assert pngtuber["drag_image"] == "/static/assets/neko-idle/cat-idle-cat-move-1.gif"


def test_upload_pngtuber_folder_requires_model_json(pngtuber_client, tmp_path):
    bad_dir = tmp_path / "bad-pngtuber"
    bad_dir.mkdir()
    image = bad_dir / "idle.png"
    image.write_bytes(b"fake")

    files = []
    handle = image.open("rb")
    try:
        files.append(("files", ("bad-pngtuber/idle.png", handle, "image/png")))
        response = pngtuber_client.post("/api/model/pngtuber/upload_model", files=files)
    finally:
        handle.close()

    assert response.status_code == 400
    assert "model.json" in response.json()["error"]


def test_sample_pngtuber_model_documents_gif_presets():
    manifest = json.loads((SAMPLE_MODEL_DIR / "model.json").read_text(encoding="utf-8"))

    assert manifest["model_type"] == "pngtuber"
    assert manifest["capabilities"]["gif_variant_presets"] is True
    assert manifest["gif_presets"]["cat2_static_assets"] == {
        "idle_image": "/static/assets/neko-idle/cat-idle-cat2.gif",
        "talking_image": "/static/assets/neko-idle/cat-idle-cat2-click.gif",
        "click_image": "/static/assets/neko-idle/cat-idle-cat2-click.gif",
        "drag_image": "/static/assets/neko-idle/cat-idle-cat-move-2.gif",
    }
    assert manifest["gif_presets"]["cat3_static_assets"] == {
        "idle_image": "/static/assets/neko-idle/cat-idle-cat3.gif",
        "talking_image": "/static/assets/neko-idle/cat-idle-cat3-click.gif",
        "click_image": "/static/assets/neko-idle/cat-idle-cat3-click.gif",
        "drag_image": "/static/assets/neko-idle/cat-idle-cat-move-3.gif",
    }
    assert manifest["gif_presets"]["action_gif_examples"]["happy_image"] == "/static/assets/neko-idle/cat-idle-cat4-1.gif"
