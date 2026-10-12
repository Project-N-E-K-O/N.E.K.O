import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main_routers import vrm_router


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(vrm_router.router)
    return TestClient(app)


@pytest.fixture()
def config_dir(tmp_path, monkeypatch):
    class _Mgr:
        project_root = tmp_path

    monkeypatch.setattr(vrm_router, "get_config_manager", lambda: _Mgr())
    directory = tmp_path / "static" / "vrm" / "configs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def test_physics_profile_absent_returns_null_config(client, config_dir):
    response = client.get("/api/model/vrm/physics_profile/sister1.0")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["config"] is None
    assert payload["source"] == "absent"


def test_physics_profile_returns_stored_config(client, config_dir):
    stored = {
        "version": 1,
        "model": "sister1.0",
        "generated_by": "neko-vrm-physics-tuner",
        "colliders": [
            {"bone": "J_Bip_C_Head", "shape": "sphere", "radius": 0.083, "offset": [0, 0, 0], "tail": None}
        ],
        "chains": [
            {
                "name": "hair_front:hair_front",
                "bones": ["hair_front_01", "hair_front_02"],
                "stiffness": 0.9,
                "drag_force": 0.4,
                "gravity_power": 0.05,
                "gravity_dir": [0, -1, 0],
                "hit_radius": 0.02,
                "center": None,
                "collider_bones": ["J_Bip_C_Head"],
            }
        ],
    }
    (config_dir / "sister1.0_physics.json").write_text(json.dumps(stored), encoding="utf-8")

    response = client.get("/api/model/vrm/physics_profile/sister1.0")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["source"] == "file"
    assert payload["config"]["colliders"][0]["bone"] == "J_Bip_C_Head"
    assert payload["config"]["chains"][0]["stiffness"] == 0.9
    assert payload["config"]["chains"][0]["bones"] == ["hair_front_01", "hair_front_02"]


def test_physics_profile_corrupt_json_falls_back(client, config_dir):
    (config_dir / "broken_physics.json").write_text("{not valid json", encoding="utf-8")
    response = client.get("/api/model/vrm/physics_profile/broken")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["config"] is None
    assert payload["source"] == "corrupt"


@pytest.mark.parametrize("bad", ["../evil", "a/b", "with space", "semi;colon"])
def test_physics_profile_rejects_unsafe_names(client, config_dir, bad):
    response = client.get(f"/api/model/vrm/physics_profile/{bad}")
    assert response.status_code in (400, 404)


def test_physics_profile_accepts_unicode_names(client, config_dir):
    stored = {"version": 1, "model": "小春", "colliders": [], "chains": []}
    (config_dir / "小春_physics.json").write_text(json.dumps(stored), encoding="utf-8")
    response = client.get("/api/model/vrm/physics_profile/小春")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["config"]["model"] == "小春"


def test_physics_config_path_is_inside_config_dir(tmp_path, monkeypatch):
    class _Mgr:
        project_root = tmp_path

    monkeypatch.setattr(vrm_router, "get_config_manager", lambda: _Mgr())
    resolved = vrm_router._get_physics_config_path("sister1.0")
    assert resolved is not None
    assert resolved.name == "sister1.0_physics.json"
    assert resolved.parent.name == "configs"


def test_physics_config_path_rejects_traversal(tmp_path, monkeypatch):
    class _Mgr:
        project_root = tmp_path

    monkeypatch.setattr(vrm_router, "get_config_manager", lambda: _Mgr())
    assert vrm_router._get_physics_config_path("../escape") is None
    assert vrm_router._get_physics_config_path("") is None


def test_physics_endpoint_differs_from_emotion_endpoint(client, config_dir):
    emotion = client.get("/api/model/vrm/emotion_mapping/sister1.0")
    physics = client.get("/api/model/vrm/physics_profile/sister1.0")
    assert emotion.status_code == 200
    assert physics.status_code == 200
    assert emotion.json()["config"] != physics.json()["config"]
