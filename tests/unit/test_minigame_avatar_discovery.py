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
async def test_names_use_existing_registry_without_private_fields(monkeypatch):
    data = {"当前猫娘": "Neko", "猫娘": {"Neko": {"prompt": "private"}, "Other": {"api_key": "secret"}}}
    monkeypatch.setattr(runtime, "get_config_manager", lambda: SimpleNamespace(load_characters=lambda: data))
    assert await runtime.game_character_names("example-game") == {"names": ["Neko", "Other"]}
    data["猫娘"] = {"New": {"memory": "private"}}
    assert await runtime.game_character_names("example-game") == {"names": ["New"]}


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("nekos, status", [({str(i): {} for i in range(257)}, 413), ({"x" * 129: {}}, 422)])
async def test_names_reject_unbounded_results(monkeypatch, nekos, status):
    monkeypatch.setattr(runtime, "get_config_manager", lambda: SimpleNamespace(load_characters=lambda: {"猫娘": nekos}))
    with pytest.raises(HTTPException) as error:
        await runtime.game_character_names("example-game")
    assert error.value.status_code == status


@pytest.mark.unit
def test_avatar_discovery_runtime():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    script = Path(__file__).resolve().parents[1] / "frontend" / "test_neko_minigame_avatar_discovery_runtime.js"
    result = run_node_script(node, f"require({json.dumps(str(script))});", timeout=30,
                             capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "avatar discovery runtime test passed" in result.stdout
