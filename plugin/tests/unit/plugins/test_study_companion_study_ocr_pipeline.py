from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from plugin.plugins.study_companion.models import AwarenessConfig, StudyConfig
from plugin.plugins.study_companion import study_ocr_pipeline as pipeline_module
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


def test_ocr_pipeline_capture_lightweight_title_first_skips_ocr_and_limits_jpeg() -> None:
    image = Image.new("RGB", (1600, 900), "white")
    capture = _Capture(image)
    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(
            awareness=AwarenessConfig(
                classify_mode="title_first",
                image_max_bytes=50_000,
            )
        ),
        ocr_backend=_Backend(RuntimeError("ocr should not run")),
        capture_backend=capture,
    )

    snapshot = pipeline.capture_lightweight(
        target={"hwnd": 1, "title": "main.py - Visual Studio Code"}
    )
    activity = snapshot.to_activity_snapshot()

    assert snapshot.status == "ok"
    assert snapshot.jpeg_bytes is not None
    assert len(snapshot.jpeg_bytes) <= 50_000
    assert snapshot.jpeg_base64
    assert snapshot.jpeg_metadata["limit_bytes"] == 50_000
    assert snapshot.jpeg_metadata["encoded_bytes"] == len(snapshot.jpeg_bytes)
    assert snapshot.jpeg_metadata["attempts"] >= 1
    assert snapshot.app_type == "unknown"
    assert snapshot.activity_type == ""
    assert activity is not None
    assert activity.classify_method == "title"
    assert capture.calls


def test_ocr_pipeline_lightweight_jpeg_keeps_shrinking_until_limit() -> None:
    image = Image.effect_noise((1600, 900), 120).convert("RGB")
    metadata: dict[str, int | float] = {}

    raw = StudyOcrPipeline._encode_lightweight_jpeg(
        image,
        max_bytes=10_240,
        metadata=metadata,
    )

    assert len(raw) <= 10_240
    assert metadata["attempts"] > 1
    assert metadata["encoded_bytes"] == len(raw)
    assert metadata["final_quality"] <= 72


def test_ocr_pipeline_capture_lightweight_ocr_mode_writes_activity_type() -> None:
    image = Image.new("RGB", (800, 600), "white")
    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(
            awareness=AwarenessConfig(classify_mode="ocr_text", image_max_bytes=80_000)
        ),
        ocr_backend=_Backend("Question: Why does this happen?"),
        capture_backend=_Capture(image),
    )

    snapshot = pipeline.capture_lightweight(
        target={"hwnd": 1, "title": "Quiz - Google Chrome"}
    )
    activity = snapshot.to_activity_snapshot()

    assert snapshot.status == "ok"
    assert snapshot.app_type == "unknown"
    assert snapshot.activity_type == "question"
    assert snapshot.ocr_text_snippet == "Question: Why does this happen?"
    assert activity is not None
    assert activity.classify_method == "both"
    assert activity.activity_type == "question"


def test_ocr_pipeline_capture_lightweight_empty_without_semantic_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = Image.new("RGB", (800, 600), "white")
    monkeypatch.setattr(StudyOcrPipeline, "_get_active_window_title", staticmethod(lambda: ""))
    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(
            awareness=AwarenessConfig(classify_mode="ocr_text", image_max_bytes=80_000)
        ),
        ocr_backend=_Backend(""),
        capture_backend=_Capture(image),
    )

    snapshot = pipeline.capture_lightweight(target={"hwnd": 1, "title": ""})

    assert snapshot.status == "empty"
    assert snapshot.jpeg_bytes is not None
    assert snapshot.activity_type == "idle"
    assert snapshot.ocr_text_snippet == ""


def test_ocr_pipeline_capture_lightweight_ocr_failure_falls_back_to_title() -> None:
    image = Image.new("RGB", (800, 600), "white")
    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(
            awareness=AwarenessConfig(classify_mode="ocr_text", image_max_bytes=80_000)
        ),
        ocr_backend=_Backend(RuntimeError("ocr broken")),
        capture_backend=_Capture(image),
    )

    snapshot = pipeline.capture_lightweight(
        target={"hwnd": 1, "title": "Quiz - Google Chrome"}
    )
    activity = snapshot.to_activity_snapshot()

    assert snapshot.status == "ok"
    assert snapshot.classify_method == "title"
    assert snapshot.activity_type == "question"
    assert "ocr_failed" in snapshot.diagnostic
    assert snapshot.jpeg_bytes is not None
    assert activity is not None
    assert activity.classify_method == "title"
    assert activity.activity_type == "question"


def test_ocr_pipeline_capture_lightweight_content_change_and_failure_paths() -> None:
    image = Image.new("RGB", (640, 360), "white")
    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(),
        ocr_backend=_Backend(""),
        capture_backend=_Capture(image),
    )

    first = pipeline.capture_lightweight(target={"hwnd": 1, "title": "Notes - Obsidian"})
    second = pipeline.capture_lightweight(target={"hwnd": 1, "title": "Notes - Obsidian"})

    assert first.status == "ok"
    assert not hasattr(first, "has_content" + "_change")
    assert not hasattr(first, "thumbnail_" + "phash")
    assert second.status == "ok"
    assert not hasattr(second, "has_content" + "_change")

    failing = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(),
        ocr_backend=_Backend(""),
        capture_backend=_Capture(RuntimeError("capture failed")),
    )
    failed = failing.capture_lightweight(target={"hwnd": 1, "title": "Broken"})
    assert failed.status == "capture_failed"
    assert "capture failed" in failed.diagnostic


def test_ocr_pipeline_macos_active_window_title_prefers_window_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_run(cmd, **_kwargs):
        script = cmd[-1]
        calls.append(script)
        if "first window" in script:
            return SimpleNamespace(returncode=0, stdout="Lesson - Safari\n")
        return SimpleNamespace(returncode=0, stdout="Safari\n")

    monkeypatch.setattr(pipeline_module.sys, "platform", "darwin")
    monkeypatch.setattr(pipeline_module.subprocess, "run", fake_run)

    assert StudyOcrPipeline._get_active_window_title() == "Lesson - Safari"
    assert "first window" in calls[0]


def test_ocr_pipeline_macos_active_window_title_falls_back_to_app_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd, **_kwargs):
        script = cmd[-1]
        if "first window" in script:
            return SimpleNamespace(returncode=1, stdout="")
        return SimpleNamespace(returncode=0, stdout="Safari\n")

    monkeypatch.setattr(pipeline_module.sys, "platform", "darwin")
    monkeypatch.setattr(pipeline_module.subprocess, "run", fake_run)

    assert StudyOcrPipeline._get_active_window_title() == "Safari"


def test_ocr_pipeline_linux_active_window_title_uses_xdotool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="Lesson - Firefox\n")

    monkeypatch.setattr(pipeline_module.sys, "platform", "linux")
    monkeypatch.setattr(pipeline_module.subprocess, "run", fake_run)

    assert StudyOcrPipeline._get_active_window_title() == "Lesson - Firefox"
    assert calls == [["xdotool", "getactivewindow", "getwindowname"]]
