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
async def test_character_exposes_png_path_without_private_config(monkeypatch):
    from main_routers import characters_router
    from unittest.mock import AsyncMock

    data = {"当前猫娘": "Example", "猫娘": {"Example": {
        "_reserved": {"avatar": {"model_type": "pngtuber", "pngtuber": {
            "idle_image": "/user_pngtuber/example/idle.png", "mirror": True,
            "talking_image": "/user_pngtuber/example/talk.png",
        }}}, "prompt": "private",
    }}}
    monkeypatch.setattr(runtime, "get_config_manager", lambda: SimpleNamespace(load_characters=lambda: data))
    monkeypatch.setattr(runtime, "_load_game_character_prompt_locale", AsyncMock(return_value=("en", True)))
    monkeypatch.setattr(characters_router, "get_current_live2d_model", AsyncMock(return_value=None))
    result = await runtime.game_character("example-game")
    assert result["pngtuber_path"] == "/user_pngtuber/example/idle.png"
    assert "talking_image" not in json.dumps(result)
    assert "private" not in json.dumps(result)
    data["猫娘"]["Example"]["_reserved"]["avatar"]["pngtuber"] = {"idle_image": {"bad": "shape"}}
    assert (await runtime.game_character("example-game"))["pngtuber_path"] == ""


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
