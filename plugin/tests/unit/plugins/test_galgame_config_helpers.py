from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.plugins.galgame_plugin import GalgamePluginConfigService
from plugin.plugins.galgame_plugin.install_tasks import install_task_state_path
from plugin.plugins.galgame_plugin.models import (
    GalgameConfig,
    STORE_LLM_VISION_ENABLED,
    STORE_LLM_VISION_MAX_IMAGE_PX,
    STORE_OCR_BACKEND_SELECTION,
    STORE_OCR_CAPTURE_BACKEND,
    STORE_OCR_POLL_INTERVAL_SECONDS,
    STORE_OCR_SCREEN_TEMPLATES,
    STORE_OCR_TRIGGER_MODE,
    STORE_READER_MODE,
    json_copy,
)


pytestmark = pytest.mark.plugin_unit


def test_config_service_persists_runtime_overrides_to_store() -> None:
    writes: list[tuple[str, object]] = []
    service = GalgamePluginConfigService(
        SimpleNamespace(
            _persist=SimpleNamespace(
                persist_config_override=lambda key, value: writes.append((key, value))
            )
        )
    )

    templates = [{"id": "title", "stage": "title_stage"}]
    service.persist_ocr_backend_selection(
        backend_selection="rapidocr",
        capture_backend="dxcam",
    )
    service.persist_reader_mode(reader_mode="ocr_reader")
    service.persist_ocr_timing(poll_interval_seconds=0.5, trigger_mode="after_advance")
    service.persist_llm_vision(vision_enabled=False, vision_max_image_px=1024)
    service.persist_ocr_screen_templates(templates)

    assert writes == [
        (STORE_OCR_BACKEND_SELECTION, "rapidocr"),
        (STORE_OCR_CAPTURE_BACKEND, "dxcam"),
        (STORE_READER_MODE, "ocr_reader"),
        (STORE_OCR_POLL_INTERVAL_SECONDS, 0.5),
        (STORE_OCR_TRIGGER_MODE, "after_advance"),
        (STORE_LLM_VISION_ENABLED, False),
        (STORE_LLM_VISION_MAX_IMAGE_PX, 1024),
        (STORE_OCR_SCREEN_TEMPLATES, [{"id": "title", "stage": "title_stage"}]),
    ]


def test_install_task_state_path_rejects_path_traversal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    with pytest.raises(ValueError, match="invalid task_id"):
        install_task_state_path("../outside", kind="textractor")

    with pytest.raises(ValueError, match="invalid task_id"):
        install_task_state_path(r"..\outside", kind="textractor")


def test_json_copy_fast_path_preserves_copy_semantics() -> None:
    shallow = {"status": "active", "count": 1, "enabled": True}
    shallow_copy = json_copy(shallow)

    assert shallow_copy == shallow
    assert shallow_copy is not shallow

    nested = {"items": [{"text": "hello"}]}
    nested_copy = json_copy(nested)
    nested_copy["items"][0]["text"] = "changed"

    assert nested["items"][0]["text"] == "hello"


def test_galgame_config_groups_fields_and_keeps_flat_compatibility(tmp_path: Path) -> None:
    cfg = GalgameConfig(
        bridge_root=tmp_path / "bridge",
        llm_target_entry_ref="entry-1",
        ocr_reader_enabled=True,
        ocr_reader_backend_selection="tesseract",
        ocr_reader_screen_templates=[{"id": "demo", "stage": "title_stage"}],
        ocr_reader_screen_awareness_model_enabled=True,
        ocr_reader_screen_awareness_model_path="screen-model.json",
        memory_reader_hook_codes=["/HSN-4@1234"],
    )

    assert len(fields(GalgameConfig)) == 7
    assert cfg.bridge.bridge_root == tmp_path / "bridge"
    assert cfg.bridge_root == tmp_path / "bridge"
    assert cfg.llm.llm_target_entry_ref == "entry-1"
    assert cfg.ocr_reader.ocr_reader_enabled is True
    assert cfg.ocr_reader_enabled is True
    assert cfg.ocr_reader.ocr_reader_screen_templates == [{"id": "demo", "stage": "title_stage"}]
    assert cfg.ocr_reader.ocr_reader_screen_awareness_model_enabled is True
    assert cfg.ocr_reader_screen_awareness_model_path == "screen-model.json"
    assert cfg.memory_reader.memory_reader_hook_codes == ["/HSN-4@1234"]

    cfg.ocr_reader_backend_selection = "rapidocr"

    assert cfg.ocr_reader.ocr_reader_backend_selection == "rapidocr"
