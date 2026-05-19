from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.plugins.galgame_plugin import GalgamePlugin
from plugin.plugins.galgame_plugin.character_profile import CharacterProfileManager
from plugin.plugins.galgame_plugin.state import build_initial_state
from plugin.plugins.galgame_plugin.models import (
    ADVANCE_SPEED_MEDIUM,
    MODE_COMPANION,
)


pytestmark = pytest.mark.plugin_unit


def _plugin_with_character_profiles() -> GalgamePlugin:
    plugin = GalgamePlugin.__new__(GalgamePlugin)
    plugin._cfg = SimpleNamespace()
    plugin._state = build_initial_state(
        mode=MODE_COMPANION,
        push_notifications=True,
        advance_speed=ADVANCE_SPEED_MEDIUM,
    )
    plugin._state_lock = threading.Lock()
    plugin._state_dirty = True
    plugin._cached_snapshot = None
    plugin._character_profile_manager = CharacterProfileManager(
        data_dir=Path(__file__).parents[3] / "plugins" / "galgame_plugin" / "character_data"
    )
    plugin._persist = SimpleNamespace(
        persist_config_override=lambda *_args, **_kwargs: None
    )
    plugin.logger = SimpleNamespace(
        warning=lambda *_args, **_kwargs: None,
        info=lambda *_args, **_kwargs: None,
    )
    return plugin


@pytest.mark.asyncio
async def test_character_list_auto_matches_ocr_window_title_without_bound_game(
) -> None:
    plugin = _plugin_with_character_profiles()
    plugin._state.ocr_reader_runtime = {
        "status": "active",
        "game_id": "ocr-unknown",
        "window_title": "千恋＊万花",
        "process_name": "unknown.exe",
    }

    result = await plugin.galgame_get_character_list()

    assert result.is_ok()
    payload = result.value
    assert payload["profile_game_id"] == "senren_banka"
    assert payload["match_reason"] == "window_title_contains"
    assert [item["name"] for item in payload["characters"]] == ["叢雨"]


@pytest.mark.asyncio
async def test_fixed_character_mode_auto_loads_profiles_without_bound_game(
) -> None:
    plugin = _plugin_with_character_profiles()
    plugin._state.memory_reader_runtime = {
        "status": "active",
        "game_id": "mem-unknown",
        "process_name": "SenrenBanka.exe",
    }

    result = await plugin.galgame_set_character_mode(
        mode="fixed",
        character_name="叢雨",
    )

    assert result.is_ok()
    assert plugin._state.character_mode == "fixed"
    assert plugin._state.character_fixed_name == "叢雨"
    assert plugin._state.character_profile_game_id == "senren_banka"


@pytest.mark.asyncio
async def test_character_list_reports_empty_when_no_profile_matches(
) -> None:
    plugin = _plugin_with_character_profiles()
    plugin._state.ocr_reader_runtime = {
        "status": "active",
        "game_id": "ocr-unknown",
        "window_title": "Unrelated Game",
        "process_name": "unrelated.exe",
    }

    result = await plugin.galgame_get_character_list()

    assert result.is_ok()
    payload = result.value
    assert payload["profile_game_id"] == ""
    assert payload["characters"] == []
