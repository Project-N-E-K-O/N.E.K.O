"""Display-only character discovery and trusted/legacy host lifecycle contracts."""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from main_routers.game_router import runtime
from tests.node_harness import run_node_script


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_exposes_png_path_without_private_config(monkeypatch, tmp_path):
    from main_routers import characters_router
    from unittest.mock import AsyncMock

    data = {"当前猫娘": "Example", "猫娘": {"Example": {
        "_reserved": {"avatar": {"model_type": "pngtuber", "pngtuber": {
            "idle_image": "/user_pngtuber/example/idle.png", "mirror": True,
            "talking_image": "/user_pngtuber/example/talk.png",
        }}}, "prompt": "private",
    }}}
    (tmp_path / "example").mkdir()
    (tmp_path / "example" / "idle.png").touch()
    monkeypatch.setattr(runtime, "get_config_manager", lambda: SimpleNamespace(
        load_characters=lambda: data, pngtuber_dir=tmp_path))
    monkeypatch.setattr(runtime, "_load_game_character_prompt_locale", AsyncMock(return_value=("en", True)))
    monkeypatch.setattr(characters_router, "get_current_live2d_model", AsyncMock(return_value=None))
    result = await runtime.game_character("example-game")
    assert result["pngtuber_path"] == "/user_pngtuber/example/idle.png"
    assert "talking_image" not in json.dumps(result)
    assert "private" not in json.dumps(result)
    for raw, expected in [("example/idle.png", "/user_pngtuber/example/idle.png"),
                          ("example\\idle.png", "/user_pngtuber/example/idle.png"),
                          ("missing.png", ""), ("../idle.png", ""),
                          ("//example.invalid/idle.png", ""), ("idle.txt", "")]:
        data["猫娘"]["Example"]["_reserved"]["avatar"]["pngtuber"]["idle_image"] = raw
        assert (await runtime.game_character("example-game"))["pngtuber_path"] == expected
    data["猫娘"]["Example"]["_reserved"]["avatar"]["pngtuber"] = {"idle_image": {"bad": "shape"}}
    assert (await runtime.game_character("example-game"))["pngtuber_path"] == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_resolves_mmd_against_static_and_user_directories(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    from main_routers import characters_router

    model = {"model_path": "example/avatar.pmx"}
    data = {"当前猫娘": "Example", "猫娘": {"Example": {
        "_reserved": {"avatar": {"model_type": "live2d", "mmd": model}},
    }}}
    user_dir = tmp_path / "user"
    static_dir = tmp_path / "static" / "mmd"
    (user_dir / "example").mkdir(parents=True)
    (static_dir / "example").mkdir(parents=True)
    (user_dir / "example" / "avatar.pmx").touch()
    monkeypatch.setattr(runtime, "get_config_manager", lambda: SimpleNamespace(
        load_characters=lambda: data, project_root=tmp_path, mmd_dir=user_dir))
    monkeypatch.setattr(runtime, "_load_game_character_prompt_locale", AsyncMock(return_value=("en", True)))
    monkeypatch.setattr(characters_router, "get_current_live2d_model", AsyncMock(return_value=None))
    assert (await runtime.game_character("sdk-avatar"))["mmd_path"] == "/user_mmd/example/avatar.pmx"
    (static_dir / "example" / "avatar.pmx").touch()
    assert (await runtime.game_character("sdk-avatar"))["mmd_path"] == "/static/mmd/example/avatar.pmx"
    for raw in ["missing.pmx", "../outside.pmx", None, {}]:
        model["model_path"] = raw
        assert (await runtime.game_character("sdk-avatar"))["mmd_path"] == ""
    character = data["猫娘"]["Example"]
    character["mmd"] = "example/avatar.pmx"
    assert (await runtime.game_character("sdk-avatar"))["mmd_path"] == "/static/mmd/example/avatar.pmx"
    character.pop("mmd")
    model["model_path"] = ""
    character.update(model_type="mmd", model_path="example/avatar.pmx")
    result = await runtime.game_character("sdk-avatar")
    assert result["model_type"] == "mmd"
    assert (await runtime.game_character("sdk-avatar"))["mmd_path"] == "/static/mmd/example/avatar.pmx"
    character.update(model_type="live3d", live3d_sub_type="mmd")
    result = await runtime.game_character("sdk-avatar")
    assert (result["model_type"], result["live3d_sub_type"]) == ("live3d", "mmd")
    assert result["mmd_path"] == "/static/mmd/example/avatar.pmx"
    for malformed in [None, "legacy", []]:
        character["_reserved"]["avatar"]["mmd"] = malformed
        assert (await runtime.game_character("sdk-avatar"))["mmd_path"] == "/static/mmd/example/avatar.pmx"
        character["mmd"] = "example/avatar.pmx"
        assert (await runtime.game_character("sdk-avatar"))["mmd_path"] == "/static/mmd/example/avatar.pmx"
        character.pop("mmd")

    character.pop("model_type")
    character.pop("live3d_sub_type")
    character["_reserved"]["avatar"].update(
        model_type="live3d", live3d_sub_type="mmd", mmd={"model_path": "example/avatar.pmx"},
    )
    result = await runtime.game_character("sdk-avatar")
    assert (result["model_type"], result["live3d_sub_type"]) == ("live3d", "mmd")
    assert result["mmd_path"] == "/static/mmd/example/avatar.pmx"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_names_use_existing_registry_without_private_fields(monkeypatch):
    data = {"当前猫娘": "Neko", "猫娘": {"Neko": {"prompt": "private"}, "Other": {"api_key": "secret"}}}
    monkeypatch.setattr(runtime, "get_config_manager", lambda: SimpleNamespace(load_characters=lambda: data))
    assert await runtime.game_character_names("example-game") == {"names": ["Neko", "Other"]}
    data["猫娘"] = {"New": {"memory": "private"}}
    assert await runtime.game_character_names("example-game") == {"names": ["New"]}


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("name", [" Example", "Example ", "\tExample\n", "\u3000Example\u00a0"])
async def test_names_reject_noncanonical_keys_without_renaming(monkeypatch, name):
    data = {"当前猫娘": name, "猫娘": {name: {}, "Example": {}}}
    monkeypatch.setattr(runtime, "get_config_manager", lambda: SimpleNamespace(load_characters=lambda: data))
    with pytest.raises(HTTPException) as error:
        await runtime.game_character_names("example-game")
    assert error.value.status_code == 422
    assert list(data["猫娘"]) == [name, "Example"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_legacy_vrm_and_live2d_paths(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock
    from starlette.responses import JSONResponse
    from main_routers import characters_router

    character = {"model_type": "live3d", "live3d_sub_type": "vrm", "vrm": "example.vrm"}
    (tmp_path / "example.vrm").touch()
    data = {"当前猫娘": "Example", "猫娘": {"Example": character}}
    monkeypatch.setattr(runtime, "get_config_manager", lambda: SimpleNamespace(
        load_characters=lambda: data, project_root=tmp_path, vrm_dir=tmp_path))
    monkeypatch.setattr(runtime, "_load_game_character_prompt_locale", AsyncMock(return_value=("en", True)))
    canonical = AsyncMock(return_value=JSONResponse({"model_info": {"path": "/user_live2d/example.model3.json"}}))
    monkeypatch.setattr(characters_router, "get_current_live2d_model", canonical)
    assert (await runtime.game_character("sdk-avatar"))["vrm_path"] == "/user_vrm/example.vrm"
    for malformed in [None, [], "legacy"]:
        character["_reserved"] = {"avatar": malformed}
        assert (await runtime.game_character("sdk-avatar"))["vrm_path"] == "/user_vrm/example.vrm"
    character["_reserved"] = {"avatar": {"vrm": {"model_path": ""}, "live2d": None}}
    assert (await runtime.game_character("sdk-avatar"))["vrm_path"] == "", "explicit empty must win over legacy"
    character.update(model_type="live2d", live2d="example.model3.json")
    assert (await runtime.game_character("sdk-avatar"))["live2d_path"] == "/user_live2d/example.model3.json"
    canonical.assert_awaited_with("Example")


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("model_type", ["live2d", "", "vrm", "mmd", "pngtuber", "live3d"])
@pytest.mark.parametrize("global_fallback", [True, False])
async def test_character_does_not_offer_global_live2d_as_owned_fallback(monkeypatch, model_type, global_fallback):
    from unittest.mock import AsyncMock
    from starlette.responses import JSONResponse
    from main_routers import characters_router

    character = {"_reserved": {"avatar": {"model_type": model_type, "live3d_sub_type": "vrm"}}}
    data = {"当前猫娘": "Example", "猫娘": {"Example": character}}
    monkeypatch.setattr(runtime, "get_config_manager", lambda: SimpleNamespace(load_characters=lambda: data))
    monkeypatch.setattr(runtime, "_load_game_character_prompt_locale", AsyncMock(return_value=("en", True)))
    path = "/static/live2d/example/model.model3.json"
    canonical = AsyncMock(return_value=JSONResponse({"success": True, "model_info": {
        "path": path, "is_fallback": global_fallback,
    }}))
    monkeypatch.setattr(characters_router, "get_current_live2d_model", canonical)
    result = await runtime.game_character("sdk-avatar")
    expected = "" if global_fallback and model_type not in ("", "live2d") else path
    assert result["live2d_path"] == expected
    canonical.assert_awaited_once_with("Example")


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("nekos, status", [
    ({str(i): {} for i in range(257)}, 413), ({"x" * 129: {}}, 422), ({"🐈" * 129: {}}, 422),
])
async def test_names_reject_unbounded_results(monkeypatch, nekos, status):
    monkeypatch.setattr(runtime, "get_config_manager", lambda: SimpleNamespace(load_characters=lambda: {"猫娘": nekos}))
    with pytest.raises(HTTPException) as error:
        await runtime.game_character_names("example-game")
    assert error.value.status_code == status


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("length", [65, 128])
async def test_names_count_unicode_code_points(monkeypatch, length):
    name = "🐈" * length
    monkeypatch.setattr(runtime, "get_config_manager", lambda: SimpleNamespace(
        load_characters=lambda: {"猫娘": {name: {"prompt": "private"}}},
    ))
    assert await runtime.game_character_names("example-game") == {"names": [name]}


@pytest.mark.unit
@pytest.mark.parametrize("suite, marker", [
    ("avatar_discovery", "avatar discovery runtime test passed"),
    ("context_memory", "context and memory runtime test passed"),
    ("same_origin_host", "same-origin host runtime test passed"),
    ("lifecycle", "lifecycle runtime test passed"),
    ("voice_state", "voice state runtime regression passed"),
    ("sdk", "mini-game SDK runtime test passed"),
])
def test_avatar_and_request_lifecycle_runtime(suite, marker):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    script = Path(__file__).resolve().parents[1] / "frontend" / f"test_neko_minigame_{suite}_runtime.js"
    result = run_node_script(node, f"require({json.dumps(str(script))});", timeout=30,
                             capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker in result.stdout
