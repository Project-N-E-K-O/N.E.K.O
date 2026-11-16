"""Complete parameter snapshots survive the preferences API and later layout saves."""

import asyncio
import json

import pytest
from starlette.requests import Request

from main_routers.config_router import preferences as preferences_router
from utils import preferences


@pytest.fixture
def isolated_preferences(monkeypatch, tmp_path):
    path = tmp_path / "user_preferences.json"

    class Config:
        def get_config_path(self, _name):
            return path

        get_runtime_config_path = get_config_path

        def ensure_config_directory(self):
            path.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(preferences, "_config_manager", Config())
    monkeypatch.setattr(preferences, "assert_cloudsave_writable", lambda *a, **k: None)
    return path


def save_request(payload):
    async def receive():
        return {"type": "http.request", "body": json.dumps(payload).encode(), "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/preferences"}, receive)


def test_explicit_reset_survives_api_reload_and_later_position_save(isolated_preferences):
    path = "/workshop/123/avatar.model3.json"
    position = {"x": 10, "y": 20}
    scale = {"x": 1, "y": 1}
    assert preferences.update_model_preferences(path, position, scale, {"ParamAngleX": 12})
    result = asyncio.run(preferences_router.save_preferences(save_request({
        "model_path": path, "position": position, "scale": scale,
        "parameters": {}, "parameters_mode": "replace",
    })))
    assert result["success"] is True
    saved = asyncio.run(preferences_router.get_preferences())[0]
    assert saved["parameters"] == {}
    assert saved["parameters_mode"] == "replace"

    # Dragging/zooming omits parameters and must preserve the explicit reset.
    assert preferences.update_model_preferences(path, {"x": 30, "y": 40}, scale)
    saved = preferences.load_user_preferences()[0]
    assert saved["parameters"] == {}
    assert saved["parameters_mode"] == "replace"
    assert saved["position"] == {"x": 30, "y": 40}

    assert preferences.update_model_preferences(path, position, scale, {"ParamAngleY": 3})
    saved = json.loads(isolated_preferences.read_text(encoding="utf-8"))[0]
    assert saved["parameters"] == {"ParamAngleY": 3}
    assert saved["parameters_mode"] == "replace"


def test_legacy_parameter_saves_do_not_acquire_snapshot_semantics(isolated_preferences):
    assert preferences.update_model_preferences("legacy.model3.json", {"x": 0, "y": 0},
                                               {"x": 1, "y": 1}, {"ParamAngleX": 3})
    assert "parameters_mode" not in preferences.load_user_preferences()[0]


@pytest.mark.parametrize("extra", [
    {"parameters_mode": "replace"},
    {"parameters": {}, "parameters_mode": "unknown"},
    {"parameters": {}, "parameters_mode": False},
    {"parameters": [], "parameters_mode": "replace"},
])
def test_invalid_parameter_snapshot_is_rejected_without_writing(isolated_preferences, extra):
    result = asyncio.run(preferences_router.save_preferences(save_request({
        "model_path": "avatar.model3.json", "position": {"x": 0, "y": 0},
        "scale": {"x": 1, "y": 1}, **extra,
    })))
    assert result["success"] is False
    assert not isolated_preferences.exists()
