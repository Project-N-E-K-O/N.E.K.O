from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from plugin.plugins.galgame_plugin import GalgamePlugin
from plugin.plugins.galgame_plugin.models import (
    STORE_CONTEXT_SNAPSHOT,
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
                    "backend_selection": "tesseract",
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


def _valid_snapshot(
    *,
    game_id: str = "demo.alpha",
    scene_id: str = "scene-a",
    summary_seed: str = "scene summary",
    stable_line_ids: list[str] | None = None,
    saved_at: float = 1_700_000_000.0,
) -> dict[str, object]:
    return {
        "scene_id": scene_id,
        "game_id": game_id,
        "route_id": "route-1",
        "summary_seed": summary_seed,
        "stable_line_ids": stable_line_ids or ["line-1", "line-2"],
        "saved_at": saved_at,
    }


def test_persist_context_snapshot_round_trip(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    snapshot = _valid_snapshot()

    assert store.persist_context_snapshot(snapshot) is True

    loaded = store.load_context_snapshot(
        current_game_id="demo.alpha",
        max_age_seconds=0.0,
        require_game_id=True,
    )
    assert loaded["scene_id"] == "scene-a"
    assert loaded["summary_seed"] == "scene summary"
    assert loaded["stable_line_ids"] == ["line-1", "line-2"]
    assert loaded["game_id"] == "demo.alpha"


def test_persist_context_snapshot_rejects_empty_game_id_by_default(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    snapshot = _valid_snapshot(game_id="")

    assert store.persist_context_snapshot(snapshot) is False
    # And explicit allow path lets it through.
    assert store.persist_context_snapshot(snapshot, require_game_id=False) is True


def test_load_context_snapshot_rejects_mismatched_game_id(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.persist_context_snapshot(_valid_snapshot(game_id="demo.alpha"))

    loaded = store.load_context_snapshot(
        current_game_id="demo.beta",
        max_age_seconds=0.0,
    )
    assert loaded == {}


def test_load_context_snapshot_rejects_mismatched_game_id_even_when_optional(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.persist_context_snapshot(_valid_snapshot(game_id="demo.alpha"))

    loaded = store.load_context_snapshot(
        current_game_id="demo.beta",
        max_age_seconds=0.0,
        require_game_id=False,
    )

    assert loaded == {}


def test_load_context_snapshot_rejects_bound_snapshot_for_unbound_session(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.persist_context_snapshot(_valid_snapshot(game_id="demo.alpha"))

    loaded = store.load_context_snapshot(
        current_game_id="",
        max_age_seconds=0.0,
        require_game_id=False,
    )

    assert loaded == {}


def test_load_context_snapshot_returns_empty_when_expired(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.persist_context_snapshot(_valid_snapshot(saved_at=1_000.0))

    loaded = store.load_context_snapshot(
        current_game_id="demo.alpha",
        max_age_seconds=60.0,
        now=10_000.0,
    )
    assert loaded == {}


def test_load_context_snapshot_returns_empty_for_missing_or_corrupt(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    # No record yet
    assert store.load_context_snapshot(current_game_id="x") == {}

    # Corrupt raw value (non-dict)
    store.persist_config_override(STORE_CONTEXT_SNAPSHOT, "not-a-dict")
    assert store.load_context_snapshot(current_game_id="x") == {}

    # Empty-ish payload (no scene_id / summary / line_ids)
    store.persist_config_override(STORE_CONTEXT_SNAPSHOT, {"game_id": "x"})
    assert store.load_context_snapshot(current_game_id="x") == {}


def test_clear_context_snapshot_resets_storage(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.persist_context_snapshot(_valid_snapshot())
    store.clear_context_snapshot()
    assert store.load_context_snapshot(current_game_id="demo.alpha") == {}


def test_build_config_parses_phase3_llm_fields() -> None:
    config = build_config(
        {
            "llm": {
                "llm_explain_cache_ttl_seconds": 12.0,
                "llm_choice_cache_ttl_seconds": 1.5,
                "llm_near_match_cache_enabled": True,
                "llm_near_match_cache_ttl_seconds": 20.0,
                "context_persist_enabled": True,
                "context_persist_max_age_seconds": 7200.0,
                "context_persist_require_game_id": False,
            }
        }
    )
    assert config.llm.llm_explain_cache_ttl_seconds == 12.0
    assert config.llm.llm_choice_cache_ttl_seconds == 1.5
    assert config.llm.llm_near_match_cache_enabled is True
    assert config.llm.llm_near_match_cache_ttl_seconds == 20.0
    assert config.llm.context_persist_enabled is True
    assert config.llm.context_persist_max_age_seconds == 7200.0
    assert config.llm.context_persist_require_game_id is False


def test_build_config_phase3_defaults_preserve_old_behaviour() -> None:
    config = build_config({})
    assert config.llm.llm_explain_cache_ttl_seconds == 8.0
    assert config.llm.llm_choice_cache_ttl_seconds == 4.0
    assert config.llm.llm_near_match_cache_enabled is False
    assert config.llm.llm_near_match_cache_ttl_seconds == 15.0
    assert config.llm.context_persist_enabled is False
    assert config.llm.context_persist_max_age_seconds == 3600.0
    assert config.llm.context_persist_require_game_id is True
