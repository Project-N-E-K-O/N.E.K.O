from unittest.mock import AsyncMock
import json

import pytest

from main_routers.characters_router import live2d_models as api


@pytest.mark.asyncio
async def test_live2d_outfit_updates_only_the_bound_character_and_can_be_cleared(monkeypatch):
    model_ref = "mao_pro/mao_pro.model3.json"
    avatar = {"model_type": "live2d", "live2d": {"model_path": model_ref}}
    characters = {"猫娘": {"A": {"_reserved": {"avatar": avatar}}, "B": {"voice_id": "unchanged"}}}
    manager = AsyncMock()
    manager.aload_characters.return_value = characters
    monkeypatch.setattr(api, "get_config_manager", lambda: manager)
    choices = {"version": 1, "wardrobe": "mao-hoodie-v1", "print": "stars", "color": "#8070aa", "hat": False}
    update = api.Live2DOutfitUpdate(model_ref=model_ref, outfit=choices)
    assert (await api.get_live2d_outfit("A"))["outfit"] is None
    assert await api.save_live2d_outfit("A", update) == {"success": True}
    assert (await api.get_live2d_outfit("A"))["outfit"] == choices
    for model_type in ("live3d", "vrm", "pngtuber"):
        avatar["model_type"] = model_type
        assert (await api.get_live2d_outfit("A"))["wardrobe"] is None
        assert (await api.save_live2d_outfit("A", update)).status_code == 409
        assert avatar["live2d"]["outfit"] == choices
    avatar["model_type"] = "live2d"
    avatar["live2d"]["model_path"] = "unrelated.model3.json"
    assert (await api.save_live2d_outfit("A", update)).status_code == 409
    avatar["live2d"]["model_path"] = model_ref
    await api.save_live2d_outfit("A", api.Live2DOutfitUpdate(model_ref=model_ref, outfit=None))
    assert (await api.get_live2d_outfit("A"))["outfit"] is None
    assert manager.asave_characters.await_count == 2
    assert characters["猫娘"]["B"] == {"voice_id": "unchanged"}


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix, keeps_outfit", [("/static", False), ("/user_live2d", True)])
async def test_parameter_file_excludes_only_builtin_wardrobe_controls(tmp_path, monkeypatch, prefix, keeps_outfit):
    from main_routers import live2d_router

    monkeypatch.setattr(live2d_router, "find_model_directory", lambda _: (str(tmp_path), prefix))
    request = AsyncMock()
    request.json.return_value = {"parameters": {"ParamOutfitHat": 1, "ParamCheek": 0.25}}
    assert (await live2d_router.save_model_parameters("mao_pro", request))["success"]
    saved = json.loads((tmp_path / "parameters.json").read_text())
    assert saved["ParamCheek"] == 0.25
    assert ("ParamOutfitHat" in saved) is keeps_outfit
