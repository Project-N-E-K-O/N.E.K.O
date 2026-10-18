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

pytestmark = pytest.mark.unit_fast


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


@pytest.mark.parametrize("bad", ["../evil", "a/b", "a\\b"])
def test_physics_profile_rejects_path_syntax(client, config_dir, bad):
    response = client.get(f"/api/model/vrm/physics_profile/{bad}")
    assert response.status_code in (400, 404)


@pytest.mark.parametrize("name", ["My Avatar", "semi;colon", "小春", "model(1)"])
def test_physics_profile_accepts_every_valid_vrm_stem(client, config_dir, name):
    stored = {"version": 1, "model": name, "colliders": [], "chains": []}
    (config_dir / f"{name}_physics.json").write_text(json.dumps(stored), encoding="utf-8")
    response = client.get(f"/api/model/vrm/physics_profile/{name}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["source"] == "file"
    assert payload["config"]["model"] == name


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
    assert vrm_router._get_physics_config_path("..") is None


def test_physics_config_path_matches_the_model_name_validator(tmp_path, monkeypatch):
    class _Mgr:
        project_root = tmp_path

    monkeypatch.setattr(vrm_router, "get_config_manager", lambda: _Mgr())
    for name in ["My Avatar", "model(1)", "小春", "with space"]:
        assert vrm_router._is_valid_vrm_model_name(name) is True
        resolved = vrm_router._get_physics_config_path(name)
        assert resolved is not None, name
        assert resolved.name == f"{name}_physics.json"


def test_physics_endpoint_differs_from_emotion_endpoint(client, config_dir):
    emotion = client.get("/api/model/vrm/emotion_mapping/sister1.0")
    physics = client.get("/api/model/vrm/physics_profile/sister1.0")
    assert emotion.status_code == 200
    assert physics.status_code == 200
    assert emotion.json()["config"] != physics.json()["config"]


def test_cleanup_removes_both_configs_when_no_model_remains(tmp_path, monkeypatch):
    class _Mgr:
        project_root = tmp_path
        vrm_dir = tmp_path / "user_vrm"

    _Mgr.vrm_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(vrm_router, "get_config_manager", lambda: _Mgr())

    async def _no_workshop():
        return {"success": True, "items": []}

    monkeypatch.setattr(vrm_router, "get_subscribed_workshop_items", _no_workshop)

    config_dir = tmp_path / "static" / "vrm" / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    emotion = config_dir / "sister1.0_emotion.json"
    physics = config_dir / "sister1.0_physics.json"
    emotion.write_text("{}", encoding="utf-8")
    physics.write_text("{}", encoding="utf-8")

    import asyncio

    asyncio.run(vrm_router._cleanup_vrm_model_configs("sister1.0"))

    assert not emotion.exists()
    assert not physics.exists()


def test_cleanup_keeps_configs_while_a_same_stem_model_remains(tmp_path, monkeypatch):
    class _Mgr:
        project_root = tmp_path
        vrm_dir = tmp_path / "user_vrm"

    _Mgr.vrm_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "static" / "vrm").mkdir(parents=True, exist_ok=True)
    survivor = tmp_path / "static" / "vrm" / "sister1.0.vrm"
    survivor.write_bytes(b"vrm")
    monkeypatch.setattr(vrm_router, "get_config_manager", lambda: _Mgr())

    async def _no_workshop():
        return {"success": True, "items": []}

    monkeypatch.setattr(vrm_router, "get_subscribed_workshop_items", _no_workshop)

    config_dir = tmp_path / "static" / "vrm" / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    physics = config_dir / "sister1.0_physics.json"
    physics.write_text("{}", encoding="utf-8")

    import asyncio

    asyncio.run(vrm_router._cleanup_vrm_model_configs("sister1.0"))

    assert physics.exists()

def test_physics_profile_accepts_a_utf8_bom(client, config_dir):
    stored = {"colliders": [], "chains": [{"bones": ["Hair"], "stiffness": 0.5}]}
    (config_dir / "sister1.0_physics.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps(stored).encode("utf-8")
    )
    payload = client.get("/api/model/vrm/physics_profile/sister1.0").json()
    assert payload["source"] == "file"
    assert payload["config"] == stored


def test_physics_profile_non_object_json_falls_back(client, config_dir):
    (config_dir / "sister1.0_physics.json").write_text("[1, 2]", encoding="utf-8")
    payload = client.get("/api/model/vrm/physics_profile/sister1.0").json()
    assert payload["success"] is True
    assert payload["config"] is None
    assert payload["source"] == "corrupt"


def test_physics_profile_read_does_not_create_the_config_dir(tmp_path, monkeypatch):
    class _Mgr:
        project_root = tmp_path

    monkeypatch.setattr(vrm_router, "get_config_manager", lambda: _Mgr())
    app = FastAPI()
    app.include_router(vrm_router.router)
    payload = TestClient(app).get("/api/model/vrm/physics_profile/sister1.0").json()
    assert payload["source"] == "absent"
    assert not (tmp_path / "static" / "vrm" / "configs").exists()


def test_physics_profile_utf16_file_falls_back_instead_of_500(client, config_dir):
    (config_dir / "sister1.0_physics.json").write_bytes("{}".encode("utf-16"))
    response = client.get("/api/model/vrm/physics_profile/sister1.0")
    assert response.status_code == 200
    assert response.json() == {"success": True, "config": None, "source": "corrupt"}


def test_physics_profile_non_finite_numbers_become_null(client, config_dir):
    (config_dir / "sister1.0_physics.json").write_text(
        '{"colliders": [{"bone": "Head", "radius": NaN}],'
        ' "chains": [{"bones": ["Hair"], "stiffness": 0.5, "drag_force": Infinity, "hit_radius": 1e400}]}',
        encoding="utf-8",
    )
    response = client.get("/api/model/vrm/physics_profile/sister1.0")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "file"
    assert payload["config"] == {
        "colliders": [{"bone": "Head", "radius": None}],
        "chains": [{"bones": ["Hair"], "stiffness": 0.5, "drag_force": None, "hit_radius": None}],
    }


def test_cleanup_skips_workshop_lookup_when_no_config_exists(tmp_path, monkeypatch):
    class _Mgr:
        project_root = tmp_path
        vrm_dir = tmp_path / "user_vrm"

    _Mgr.vrm_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(vrm_router, "get_config_manager", lambda: _Mgr())
    calls = []

    async def _workshop():
        calls.append(1)
        return {"success": True, "items": []}

    monkeypatch.setattr(vrm_router, "get_subscribed_workshop_items", _workshop)

    import asyncio

    asyncio.run(vrm_router._cleanup_vrm_model_configs("sister1.0"))
    assert calls == []
