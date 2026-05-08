from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from plugin.plugins.galgame_plugin import GalgamePlugin
from plugin.plugins.galgame_plugin.models import (
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


def _make_store(tmp_path: Path) -> GalgameStore:
    return GalgameStore(tmp_path / "galgame-store.json", SimpleNamespace(warning=lambda *_: None))


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
