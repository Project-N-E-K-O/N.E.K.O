from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugin.plugins.study_companion.models import StudyConfig
from plugin.plugins.study_companion.study_ocr_pipeline import (
    CAPTURE_BACKEND_DXCAM,
    StudyCaptureProfile,
    StudyOcrPipeline,
)

pytestmark = pytest.mark.unit


class _Logger:
    def warning(self, *args: object, **kwargs: object) -> None:
        return None


class _Backend:
    def __init__(self, result: Any) -> None:
        self.result = result

    def extract_text(self, image: Any) -> Any:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _Capture:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[tuple[Any, StudyCaptureProfile]] = []

    def capture_frame(self, target: Any, profile: StudyCaptureProfile) -> Any:
        self.calls.append((target, profile))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_ocr_pipeline_disabled_none_image_and_backend_failure_paths() -> None:
    disabled = StudyOcrPipeline(logger=_Logger(), config=StudyConfig(ocr_enabled=False))
    failing = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(),
        ocr_backend=_Backend(RuntimeError("boom")),
    )

    assert disabled.capture_snapshot().status == "disabled"
    assert disabled.snapshot_from_image(None).status == "empty"
    failed = failing.snapshot_from_image("image", backend_name="fake")
    assert failed.status == "ocr_failed"
    assert failed.diagnostic == "boom"


def test_ocr_pipeline_normalizes_strings_dicts_objects_and_join_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        StudyOcrPipeline,
        "_join_segments",
        staticmethod(lambda parts: "|".join(parts)),
    )
    item = SimpleNamespace(text="object text", to_dict=lambda: {"text": "object text", "box": [1]})

    assert StudyOcrPipeline._normalize_ocr_output("  text  ") == ("text", [])
    text, boxes = StudyOcrPipeline._normalize_ocr_output(
        [{"text": "dict text"}, item, "raw"]
    )

    assert text == "dict text|object text|raw"
    assert boxes == [{"text": "dict text"}, {"text": "object text", "box": [1]}]


def test_ocr_pipeline_capture_target_uses_profile_and_resets_backends_on_config_update() -> None:
    capture = _Capture("frame")
    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(ocr_left_inset_ratio=0.2, ocr_capture_backend=CAPTURE_BACKEND_DXCAM),
        ocr_backend=_Backend([{"text": "hello"}, {"text": "world"}]),
        capture_backend=capture,
    )

    snapshot = pipeline.capture_snapshot(target={"hwnd": 1})
    pipeline.update_config(StudyConfig(ocr_backend_selection="rapidocr"))

    assert snapshot.status == "ok"
    assert "hello" in snapshot.text
    assert capture.calls[0][1].left_inset_ratio == 0.2
    assert pipeline._ocr_backend is None
    assert pipeline._capture_backend is None


def test_ocr_pipeline_capture_target_failure_and_fullscreen_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    target_pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(),
        ocr_backend=_Backend(""),
        capture_backend=_Capture(RuntimeError("capture failed")),
    )
    fullscreen_pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(),
        ocr_backend=_Backend(""),
    )
    monkeypatch.setattr(
        StudyOcrPipeline,
        "_capture_fullscreen",
        staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("screen denied"))),
    )

    assert target_pipeline.capture_snapshot(target="window").status == "capture_failed"
    fullscreen = fullscreen_pipeline.capture_snapshot()
    assert fullscreen.status == "capture_failed"
    assert "screen denied" in fullscreen.diagnostic
