from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from _galgame_character_data import CHARACTER_DATA_DIR
from plugin.plugins.galgame_plugin import GalgamePlugin
from plugin.plugins.galgame_plugin.character_profile import CharacterProfileManager
from plugin.plugins.galgame_plugin.game_llm_agent import GameLLMAgent
from plugin.plugins.galgame_plugin.state import build_initial_state
from plugin.plugins.galgame_plugin.models import (
    ADVANCE_SPEED_MEDIUM,
    MODE_COMPANION,
    STORE_CHARACTER_FIXED_NAME,
    STORE_CHARACTER_MODE,
    STORE_CHARACTER_PROFILE_VERSION,
    STORE_CHARACTER_PROFILES,
    STORE_CHARACTER_RUNTIME_STATE,
    STORE_CROSS_SCENE_MEMORY,
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
        data_dir=CHARACTER_DATA_DIR
    )
    plugin._persist = SimpleNamespace(
        persist_config_override=lambda *_args, **_kwargs: None
    )
    plugin.logger = SimpleNamespace(
        warning=lambda *_args, **_kwargs: None,
        info=lambda *_args, **_kwargs: None,
    )
    return plugin


def _plugin_with_persist_writes() -> tuple[GalgamePlugin, list[tuple[str, object]]]:
    plugin = _plugin_with_character_profiles()
    writes: list[tuple[str, object]] = []
    plugin._persist = SimpleNamespace(
        persist_config_override=lambda key, value: writes.append((key, value))
    )
    return plugin, writes


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


@pytest.mark.asyncio
async def test_character_list_loads_user_only_profile_for_bound_game(
    tmp_path,
) -> None:
    plugin = _plugin_with_character_profiles()
    plugin._character_profile_manager = CharacterProfileManager(data_dir=tmp_path)
    plugin._state.bound_game_id = "custom_game"
    (tmp_path / "custom_game.user.json").write_text(
        json.dumps(
            {
                "game_id": "custom_game",
                "last_updated": "2026-05-20",
                "characters": {
                    "雪乃": {
                        "identity": "user-only profile",
                        "character_voice": {
                            "core_traits": [
                                {
                                    "trait": "自定义性格",
                                    "speech_effect": "自定义语调",
                                }
                            ]
                        },
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = await plugin.galgame_get_character_list()

    assert result.is_ok()
    payload = result.value
    assert payload["profile_game_id"] == "custom_game"
    assert payload["match_reason"] == "exact_game_id"
    assert [item["name"] for item in payload["characters"]] == ["雪乃"]


@pytest.mark.asyncio
async def test_imported_user_profiles_survive_empty_preset_placeholder(
    tmp_path,
) -> None:
    plugin = _plugin_with_character_profiles()
    plugin._character_profile_manager = CharacterProfileManager(data_dir=tmp_path)
    plugin._state.bound_game_id = "custom_game"
    (tmp_path / "custom_game.json").write_text(
        json.dumps({"game_id": "custom_game", "characters": {}}),
        encoding="utf-8",
    )
    source = tmp_path / "import.json"
    source.write_text(
        json.dumps(
            {
                "game_id": "custom_game",
                "last_updated": "2026-05-20",
                "characters": {
                    "自定义角色": {
                        "identity": "用户导入的自定义角色",
                        "character_voice": {
                            "core_traits": [
                                {
                                    "trait": "谨慎",
                                    "speech_effect": "先确认事实再回答",
                                }
                            ],
                        },
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    import_result = await plugin.galgame_import_character_data(
        file_path=str(source),
        game_id="custom_game",
    )
    list_result = await plugin.galgame_get_character_list()

    assert import_result.is_ok()
    assert list_result.is_ok()
    payload = list_result.value
    assert payload["profile_game_id"] == "custom_game"
    assert [item["name"] for item in payload["characters"]] == ["自定义角色"]


@pytest.mark.asyncio
async def test_available_game_ids_do_not_fallback_match_profiles() -> None:
    plugin = _plugin_with_character_profiles()
    plugin._state.available_game_ids = ["missing_game"]
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


@pytest.mark.asyncio
async def test_character_list_preserves_restored_fixed_mode_before_detection(
) -> None:
    plugin, writes = _plugin_with_persist_writes()
    plugin._state.character_profiles = {
        "叢雨": {
            "identity": "刀灵少女",
            "character_voice": {
                "core_traits": [
                    {"trait": "骄傲", "speech_effect": "用古风口吻说话"}
                ],
            },
        }
    }
    plugin._state.character_profile_game_id = "senren_banka"
    plugin._state.character_profile_match_reason = "window_title_contains"
    plugin._state.character_mode = "fixed"
    plugin._state.character_fixed_name = "叢雨"
    plugin._state.character_mode_stale = False

    result = await plugin.galgame_get_character_list()

    assert result.is_ok()
    payload = result.value
    assert payload["profile_game_id"] == "senren_banka"
    assert [item["name"] for item in payload["characters"]] == ["叢雨"]
    assert plugin._state.character_profiles
    assert plugin._state.character_mode == "fixed"
    assert plugin._state.character_fixed_name == "叢雨"
    assert (STORE_CHARACTER_MODE, "off") not in writes
    assert (STORE_CHARACTER_FIXED_NAME, "") not in writes


def test_commit_state_persists_strategy_memory_fields() -> None:
    plugin, writes = _plugin_with_persist_writes()
    payload = plugin._snapshot_state(include_private_context=True)
    payload["cross_scene_memory"] = {
        "characters": {"鍙㈤洦": {"arc": "new arc", "confidence": 0.8}}
    }
    payload["character_runtime_state"] = {
        "鍙㈤洦": {
            "game_id": "senren_banka",
            "current_emotion": "guarded",
        }
    }

    plugin._commit_state(payload)

    assert (STORE_CROSS_SCENE_MEMORY, payload["cross_scene_memory"]) in writes
    assert (
        STORE_CHARACTER_RUNTIME_STATE,
        payload["character_runtime_state"],
    ) in writes


def test_scene_change_cross_scene_memory_update_persists_immediately() -> None:
    plugin, writes = _plugin_with_persist_writes()
    plugin._state.character_runtime_state = {
        "Yukino": {
            "arc_stage": "route guard",
            "current_emotion": "focused",
        }
    }
    agent = SimpleNamespace(
        _plugin=plugin,
        _scene_memory=[{"summary": "Yukino protects the secret route."}],
        _push_seq_counter=17,
        _cross_scene_memory_dirty=False,
        logger=plugin.logger,
    )

    GameLLMAgent._maybe_update_cross_scene_memory(
        agent,
        {},
        scene_id="scene-b",
        route_id="route-a",
    )

    persisted = [
        value for key, value in writes if key == STORE_CROSS_SCENE_MEMORY
    ]
    assert persisted
    assert persisted[-1] == plugin._state.cross_scene_memory
    assert persisted[-1]["characters"]["Yukino"]["last_key_event"] == (
        "Yukino protects the secret route."
    )
    assert plugin._cached_snapshot is None
    assert agent._cross_scene_memory_dirty is True


def test_activate_character_profiles_rebuilds_runtime_for_new_game() -> None:
    plugin, writes = _plugin_with_persist_writes()
    profile_name = next(
        iter(
            plugin._character_profile_manager.load_game_profiles("senren_banka")[
                "profiles"
            ]
        )
    )
    plugin._state.character_profile_game_id = "other_game"
    plugin._state.character_runtime_state = {
        profile_name: {
            "game_id": "other_game",
            "current_emotion": "stale from another game",
        }
    }

    load = plugin._activate_character_profiles("senren_banka")

    assert load["profiles"]
    runtime = plugin._state.character_runtime_state[profile_name]
    assert runtime["game_id"] == "senren_banka"
    assert runtime["current_emotion"] != "stale from another game"
    assert (STORE_CHARACTER_RUNTIME_STATE, plugin._state.character_runtime_state) in writes


@pytest.mark.asyncio
async def test_bind_game_clears_persisted_character_profile_state() -> None:
    plugin, writes = _plugin_with_persist_writes()
    plugin._state.available_game_ids = ["missing_game"]
    plugin._state.character_profiles = {"鍙㈤洦": {"identity": "old"}}
    plugin._state.character_profile_version = "old-version"
    plugin._state.character_profile_game_id = "senren_banka"
    plugin._state.character_profile_match_reason = "exact_game_id"
    plugin._state.character_runtime_state = {
        "鍙㈤洦": {"game_id": "senren_banka", "current_emotion": "old"}
    }
    plugin._state.character_mode = "fixed"
    plugin._state.character_fixed_name = next(iter(plugin._state.character_profiles))
    plugin._state.character_mode_stale = True
    plugin._config_service = SimpleNamespace(
        persist_preferences=lambda **_kwargs: None
    )

    async def _poll_bridge(**_kwargs):
        return None

    plugin._poll_bridge = _poll_bridge

    result = await plugin.galgame_bind_game(game_id="missing_game")

    assert result.is_ok()
    assert plugin._state.character_profiles == {}
    assert plugin._state.character_profile_version == ""
    assert plugin._state.character_profile_game_id == ""
    assert plugin._state.character_profile_match_reason == ""
    assert plugin._state.character_runtime_state == {}
    assert plugin._state.character_mode == "off"
    assert plugin._state.character_fixed_name == ""
    assert plugin._state.character_mode_stale is False
    assert (STORE_CHARACTER_PROFILES, {}) in writes
    assert (STORE_CHARACTER_PROFILE_VERSION, "") in writes
    assert (STORE_CHARACTER_RUNTIME_STATE, {}) in writes
    assert (STORE_CHARACTER_MODE, "off") in writes
    assert (STORE_CHARACTER_FIXED_NAME, "") in writes
