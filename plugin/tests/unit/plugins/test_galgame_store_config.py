from __future__ import annotations

from types import SimpleNamespace

from plugin.plugins.galgame_plugin import GalgamePlugin
from plugin.plugins.galgame_plugin.models import (
    STORE_LLM_VISION_ENABLED,
    STORE_LLM_VISION_MAX_IMAGE_PX,
    STORE_OCR_BACKEND_SELECTION,
    STORE_OCR_CAPTURE_BACKEND,
    STORE_OCR_POLL_INTERVAL_SECONDS,
    STORE_OCR_SCREEN_TEMPLATES,
    STORE_OCR_TRIGGER_MODE,
    STORE_READER_MODE,
)
from plugin.plugins.galgame_plugin.service import build_config
from plugin.plugins.galgame_plugin.store import GalgameStore


class _MemoryStore:
    enabled = True

    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def _read_value(self, key: str, default: object) -> object:
        return self.values.get(key, default)

    def _write_value(self, key: str, value: object) -> None:
        self.values[key] = value


def test_galgame_store_config_overrides_keep_missing_distinct_from_false() -> None:
    backing = _MemoryStore()
    store = GalgameStore(backing, SimpleNamespace(warning=lambda *_: None))

    missing = store.load_config_overrides()
    assert missing[STORE_LLM_VISION_ENABLED] is None
    assert missing[STORE_READER_MODE] is None

    store.persist_config_override(STORE_LLM_VISION_ENABLED, False)
    store.persist_config_override(STORE_READER_MODE, "ocr_reader")

    loaded = store.load_config_overrides()
    assert loaded[STORE_LLM_VISION_ENABLED] is False
    assert loaded[STORE_READER_MODE] == "ocr_reader"


def test_galgame_config_overrides_apply_valid_values_and_ignore_invalid() -> None:
    backing = _MemoryStore()
    backing.values.update(
        {
            STORE_READER_MODE: "ocr_reader",
            STORE_OCR_BACKEND_SELECTION: "rapidocr",
            STORE_OCR_CAPTURE_BACKEND: "dxcam",
            STORE_OCR_POLL_INTERVAL_SECONDS: 0.25,
            STORE_OCR_TRIGGER_MODE: "after_advance",
            STORE_LLM_VISION_ENABLED: False,
            STORE_LLM_VISION_MAX_IMAGE_PX: 1024,
            STORE_OCR_SCREEN_TEMPLATES: [{"id": "title", "stage": "title_stage"}],
        }
    )
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
        _persist=GalgameStore(backing, SimpleNamespace(warning=lambda *_: None)),
    )

    GalgamePlugin._apply_config_overrides_from_store(plugin)

    assert plugin._cfg.reader.reader_mode == "ocr_reader"
    assert plugin._cfg.ocr_reader.ocr_reader_backend_selection == "rapidocr"
    assert plugin._cfg.ocr_reader.ocr_reader_capture_backend == "dxcam"
    assert plugin._cfg.ocr_reader.ocr_reader_poll_interval_seconds == 0.25
    assert plugin._cfg.ocr_reader.ocr_reader_trigger_mode == "after_advance"
    assert plugin._cfg.llm.llm_vision_enabled is False
    assert plugin._cfg.llm.llm_vision_max_image_px == 1024
    assert plugin._cfg.ocr_reader.ocr_reader_screen_templates == [
        {"id": "title", "stage": "title_stage"}
    ]

    backing.values[STORE_READER_MODE] = "bad"
    backing.values[STORE_OCR_BACKEND_SELECTION] = "bad"
    GalgamePlugin._apply_config_overrides_from_store(plugin)

    assert plugin._cfg.reader.reader_mode == "ocr_reader"
    assert plugin._cfg.ocr_reader.ocr_reader_backend_selection == "rapidocr"
