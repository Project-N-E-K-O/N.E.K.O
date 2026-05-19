from __future__ import annotations

from pathlib import Path
import threading
import time
from types import SimpleNamespace

from plugin.plugins.galgame_plugin import GalgamePlugin
from plugin.plugins.galgame_plugin.models import (
    STORE_CHARACTER_FIXED_NAME,
    STORE_CHARACTER_MODE,
    STORE_CHARACTER_PROFILE_VERSION,
    STORE_CHARACTER_PROFILES,
    STORE_CONTEXT_SNAPSHOT,
    STORE_CROSS_SCENE_MEMORY,
    STORE_CHARACTER_RUNTIME_STATE,
    STORE_KEYS,
    STORE_LLM_VISION_ENABLED,
    STORE_LLM_VISION_MAX_IMAGE_PX,
    STORE_OCR_BACKEND_SELECTION,
    STORE_OCR_CAPTURE_BACKEND,
    STORE_OCR_FAST_LOOP_ENABLED,
    STORE_OCR_POLL_INTERVAL_SECONDS,
    STORE_OCR_SCREEN_TEMPLATES,
    STORE_OCR_TRIGGER_MODE,
    STORE_READER_MODE,
    STORE_RAPIDOCR_AUTO_DETECT_LANG,
    STORE_RAPIDOCR_AUTO_DETECT_LAST_LANG,
    STORE_RAPIDOCR_LANG_TYPE,
)
from plugin.plugins.galgame_plugin.service import build_config
from plugin.plugins.galgame_plugin.store import GalgameStore


def _logger() -> SimpleNamespace:
    return SimpleNamespace(warning=lambda *_, **__: None)


def _store_path(tmp_path: Path) -> Path:
    return tmp_path / "galgame-store.json"


def _make_store(tmp_path: Path) -> GalgameStore:
    return GalgameStore(_store_path(tmp_path), _logger())


def test_galgame_store_config_overrides_keep_missing_distinct_from_false(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    missing = store.load_config_overrides()
    assert missing[STORE_LLM_VISION_ENABLED] is None
    assert missing[STORE_READER_MODE] is None
    assert missing[STORE_OCR_FAST_LOOP_ENABLED] is None

    store.persist_config_override(STORE_LLM_VISION_ENABLED, False)
    store.persist_config_override(STORE_READER_MODE, "ocr_reader")
    store.persist_config_override(STORE_OCR_FAST_LOOP_ENABLED, False)
    store.persist_config_override(STORE_RAPIDOCR_AUTO_DETECT_LAST_LANG, "japan")

    loaded = store.load_config_overrides()
    assert loaded[STORE_LLM_VISION_ENABLED] is False
    assert loaded[STORE_READER_MODE] == "ocr_reader"
    assert loaded[STORE_OCR_FAST_LOOP_ENABLED] is False
    assert loaded[STORE_RAPIDOCR_AUTO_DETECT_LAST_LANG] == "japan"


def test_galgame_store_config_overrides_coerce_rapidocr_auto_detect_bool(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    missing = store.load_config_overrides()
    assert missing[STORE_RAPIDOCR_AUTO_DETECT_LANG] is None

    for raw, expected in [(1, True), (0, False), ("true", True), ("false", False)]:
        store.persist_config_override(STORE_RAPIDOCR_AUTO_DETECT_LANG, raw)
        loaded = store.load_config_overrides()
        assert loaded[STORE_RAPIDOCR_AUTO_DETECT_LANG] is expected


def test_galgame_store_config_overrides_normalize_rapidocr_lang_values(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    store.persist_config_override(STORE_RAPIDOCR_LANG_TYPE, " Japan ")
    store.persist_config_override(STORE_RAPIDOCR_AUTO_DETECT_LAST_LANG, "KOREAN")

    loaded = store.load_config_overrides()
    assert loaded[STORE_RAPIDOCR_LANG_TYPE] == "japan"
    assert loaded[STORE_RAPIDOCR_AUTO_DETECT_LAST_LANG] == "korean"


def test_galgame_config_overrides_apply_valid_values_and_ignore_invalid(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    for key, value in {
        STORE_READER_MODE: "ocr_reader",
        STORE_OCR_BACKEND_SELECTION: "rapidocr",
        STORE_OCR_CAPTURE_BACKEND: "dxcam",
        STORE_OCR_POLL_INTERVAL_SECONDS: 0.25,
        STORE_OCR_TRIGGER_MODE: "after_advance",
        STORE_OCR_FAST_LOOP_ENABLED: False,
        STORE_LLM_VISION_ENABLED: False,
        STORE_LLM_VISION_MAX_IMAGE_PX: 1024,
        STORE_OCR_SCREEN_TEMPLATES: [{"id": "title", "stage": "title_stage"}],
        STORE_RAPIDOCR_LANG_TYPE: "korean",
        STORE_RAPIDOCR_AUTO_DETECT_LANG: False,
        STORE_RAPIDOCR_AUTO_DETECT_LAST_LANG: "japan",
    }.items():
        store.persist_config_override(key, value)

    plugin = SimpleNamespace(
        _cfg=build_config(
            {
                "galgame": {"reader_mode": "auto"},
                "ocr_reader": {
                    "backend_selection": "auto",
                    "capture_backend": "smart",
                    "poll_interval_seconds": 2.0,
                    "trigger_mode": "interval",
                },
                "llm": {"vision_enabled": True, "vision_max_image_px": 768},
            }
        ),
        _persist=store,
    )

    GalgamePlugin._apply_config_overrides_from_store(plugin)

    assert plugin._cfg.reader.reader_mode == "ocr_reader"
    assert plugin._cfg.ocr_reader.ocr_reader_backend_selection == "rapidocr"
    assert plugin._cfg.ocr_reader.ocr_reader_capture_backend == "dxcam"
    assert plugin._cfg.ocr_reader.ocr_reader_poll_interval_seconds == 0.25
    assert plugin._cfg.ocr_reader.ocr_reader_trigger_mode == "after_advance"
    assert plugin._cfg.ocr_reader.ocr_reader_fast_loop_enabled is False
    assert plugin._cfg.llm.llm_vision_enabled is False
    assert plugin._cfg.llm.llm_vision_max_image_px == 1024
    assert plugin._cfg.ocr_reader.ocr_reader_screen_templates == [
        {"id": "title", "stage": "title_stage"}
    ]
    assert plugin._cfg.rapidocr.rapidocr_lang_type == "korean"
    assert plugin._cfg.rapidocr.rapidocr_auto_detect_lang is False
    assert plugin._cfg.rapidocr.rapidocr_auto_detect_last_lang == "japan"

    store.persist_config_override(STORE_READER_MODE, "bad")
    store.persist_config_override(STORE_OCR_BACKEND_SELECTION, "bad")
    GalgamePlugin._apply_config_overrides_from_store(plugin)

    assert plugin._cfg.reader.reader_mode == "ocr_reader"
    assert plugin._cfg.ocr_reader.ocr_reader_backend_selection == "rapidocr"


def test_galgame_store_reads_refresh_from_disk_after_first_load(tmp_path: Path) -> None:
    backing = _store_path(tmp_path)
    first = GalgameStore(backing, _logger())
    second = GalgameStore(backing, _logger())

    assert first.load_config_overrides().get(STORE_READER_MODE) is None

    second.persist_config_override(STORE_READER_MODE, "auto")
    assert first.load_config_overrides()[STORE_READER_MODE] == "auto"

    second.persist_config_override(STORE_READER_MODE, "ocr_reader")

    assert first.load_config_overrides()[STORE_READER_MODE] == "ocr_reader"


def test_galgame_store_context_snapshot_round_trips_and_checks_game_id(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    snapshot = {
        "scene_id": "scene-a",
        "game_id": "game-a",
        "route_id": "route-a",
        "summary_seed": "summary",
        "stable_line_ids": ["line-1", "line-2"],
        "saved_at": time.time(),
    }

    store.persist_context_snapshot(snapshot)

    loaded = store.load_context_snapshot(current_game_id="game-a")
    mismatch = store.load_context_snapshot(current_game_id="game-b")

    assert loaded["scene_id"] == "scene-a"
    assert loaded["summary_seed"] == "summary"
    assert loaded["stable_line_ids"] == ["line-1", "line-2"]
    assert mismatch == {}


def test_galgame_store_context_snapshot_strict_load_rejects_empty_game_id_and_expires(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)

    store.persist_context_snapshot(
        {"game_id": "", "summary_seed": "no game", "saved_at": time.time()}
    )
    assert store.load_context_snapshot(current_game_id="") == {}
    assert (
        store.load_context_snapshot(current_game_id="", require_game_id=False)[
            "summary_seed"
        ]
        == "no game"
    )

    store.persist_context_snapshot(
        {
            "game_id": "game-a",
            "summary_seed": "old",
            "saved_at": time.time() - 100,
        }
    )

    assert store.load_context_snapshot(
        current_game_id="game-a",
        max_age_seconds=1,
    ) == {}

    store.persist_config_override(STORE_CONTEXT_SNAPSHOT, {"game_id": "game-a"})
    assert store.load_context_snapshot(
        current_game_id="game-a",
        max_age_seconds=3600,
    ) == {}


def test_galgame_store_context_snapshot_can_disable_game_id_requirement(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    store.persist_context_snapshot(
        {
            "game_id": "game-a",
            "summary_seed": "summary",
            "saved_at": time.time(),
        }
    )

    assert store.load_context_snapshot(
        current_game_id="",
        require_game_id=False,
    )["game_id"] == "game-a"


def test_galgame_store_restores_host_play_mode_values(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    profile = {"叢雨": {"identity": "刀灵"}}
    runtime = {"叢雨": {"current_emotion": "平静"}}
    memory = {"plot_threads": [{"thread": "intro"}]}
    for key, value in {
        STORE_CHARACTER_PROFILES: profile,
        STORE_CHARACTER_PROFILE_VERSION: "2026-05-18",
        STORE_CHARACTER_MODE: "fixed",
        STORE_CHARACTER_FIXED_NAME: "叢雨",
        STORE_CROSS_SCENE_MEMORY: memory,
        STORE_CHARACTER_RUNTIME_STATE: runtime,
    }.items():
        store.persist_config_override(key, value)

    restored, warnings = store.load()

    assert warnings == []
    assert restored[STORE_CHARACTER_PROFILES] == profile
    assert restored[STORE_CHARACTER_PROFILE_VERSION] == "2026-05-18"
    assert restored[STORE_CHARACTER_MODE] == "fixed"
    assert restored[STORE_CHARACTER_FIXED_NAME] == "叢雨"
    assert restored[STORE_CROSS_SCENE_MEMORY] == memory
    assert restored[STORE_CHARACTER_RUNTIME_STATE] == runtime


def test_galgame_store_rejects_invalid_host_play_mode_values(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    for key, value in {
        STORE_CHARACTER_PROFILES: [],
        STORE_CHARACTER_PROFILE_VERSION: 123,
        STORE_CHARACTER_MODE: "dynamic",
        STORE_CHARACTER_FIXED_NAME: [],
        STORE_CROSS_SCENE_MEMORY: [],
        STORE_CHARACTER_RUNTIME_STATE: [],
    }.items():
        store.persist_config_override(key, value)

    restored, warnings = store.load()

    assert restored[STORE_CHARACTER_PROFILES] == {}
    assert restored[STORE_CHARACTER_PROFILE_VERSION] == ""
    assert restored[STORE_CHARACTER_MODE] == "off"
    assert restored[STORE_CHARACTER_FIXED_NAME] == ""
    assert restored[STORE_CROSS_SCENE_MEMORY] == {}
    assert restored[STORE_CHARACTER_RUNTIME_STATE] == {}
    assert any("character_mode" in warning for warning in warnings)


def test_galgame_store_keys_include_host_play_mode_keys() -> None:
    assert {
        STORE_CHARACTER_PROFILES,
        STORE_CHARACTER_PROFILE_VERSION,
        STORE_CHARACTER_MODE,
        STORE_CHARACTER_FIXED_NAME,
        STORE_CROSS_SCENE_MEMORY,
        STORE_CHARACTER_RUNTIME_STATE,
    }.issubset(set(STORE_KEYS))


def test_galgame_snapshot_state_redacts_context_snapshot_by_default() -> None:
    plugin = SimpleNamespace(
        _state=SimpleNamespace(
            bound_game_id="game-a",
            available_game_ids=[],
            mode="companion",
            push_notifications=True,
            advance_speed="medium",
            active_game_id="game-a",
            active_session_id="session-a",
            active_session_meta={},
            active_data_source="bridge_sdk",
            latest_snapshot={},
            history_events=[],
            history_lines=[],
            history_observed_lines=[],
            history_choices=[],
            screen_type="",
            screen_ui_elements=[],
            screen_confidence=0.0,
            screen_debug={},
            dedupe_window=[],
            line_buffer=b"",
            stream_reset_pending=False,
            last_error={},
            next_poll_at_monotonic=0.0,
            current_connection_state="idle",
            events_byte_offset=0,
            events_file_size=0,
            last_seq=0,
            last_seen_data_monotonic=0.0,
            warmup_session_id="",
            memory_reader_runtime={},
            memory_reader_target={},
            ocr_reader_runtime={},
            ocr_capture_profiles={},
            ocr_window_target={},
            context_snapshot={
                "scene_id": "scene-a",
                "game_id": "game-a",
                "route_id": "route-a",
                "summary_seed": "private summary",
                "stable_line_ids": ["line-1"],
                "saved_at": 123.0,
            },
            character_profiles={},
            active_scene_characters=[],
            character_profile_version="",
            character_profile_game_id="",
            character_profile_match_reason="",
            character_mode="off",
            character_fixed_name="",
            character_mode_stale=False,
            cross_scene_memory={},
            character_runtime_state={},
            last_push_seq=0,
            plugin_error="",
            dependency_status={},
        ),
        _state_lock=threading.Lock(),
        _state_dirty=True,
        _cached_snapshot=None,
    )

    public = GalgamePlugin._snapshot_state(plugin)
    private = GalgamePlugin._snapshot_state(plugin, include_private_context=True)

    assert "summary_seed" not in public["context_snapshot"]
    assert public["context_snapshot"]["summary_seed_chars"] == len("private summary")
    assert private["context_snapshot"]["summary_seed"] == "private summary"
