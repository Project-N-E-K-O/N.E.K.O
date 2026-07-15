from __future__ import annotations

from concurrent.futures import Future
import subprocess
import sys
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from plugin.plugins.study_companion.models import AwarenessConfig, OcrSnapshot, StudyConfig
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
    backend = _Backend([{"text": "hello"}, {"text": "world"}])
    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(ocr_left_inset_ratio=0.2, ocr_capture_backend=CAPTURE_BACKEND_DXCAM),
        ocr_backend=backend,
        capture_backend=capture,
    )

    snapshot = pipeline.capture_snapshot(target={"hwnd": 1})
    pipeline.update_config(StudyConfig(ocr_backend_selection="rapidocr"))

    assert snapshot.status == "ok"
    assert "hello" in snapshot.text
    assert capture.calls[0][1].left_inset_ratio == 0.2
    assert pipeline._ocr_backend is backend
    assert pipeline._owns_ocr_backend is False
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
    assert snapshot.app_type == "code_editor"
    assert snapshot.activity_type == ""
    assert activity is not None
    assert activity.classify_method == "title"
    assert capture.calls


def test_ocr_pipeline_lightweight_jpeg_keeps_shrinking_until_limit() -> None:
    image = Image.effect_noise((1600, 900), 120).convert("RGB")

    raw = StudyOcrPipeline._encode_lightweight_jpeg(image, max_bytes=10_240)

    assert len(raw) <= 10_240


def test_ocr_pipeline_lightweight_jpeg_disables_optimize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = Image.new("RGB", (800, 600), "white")
    original_save = Image.Image.save
    optimize_values: list[object] = []

    def save_spy(self, fp, format=None, **params):  # noqa: ANN001
        optimize_values.append(params.get("optimize"))
        return original_save(self, fp, format=format, **params)

    monkeypatch.setattr(Image.Image, "save", save_spy)

    raw = StudyOcrPipeline._encode_lightweight_jpeg(image, max_bytes=20_000)

    assert raw
    assert optimize_values
    assert set(optimize_values) == {False}


def test_ocr_pipeline_imagehash_module_import_is_optional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline_module, "imagehash", None, raising=False)

    assert StudyOcrPipeline._calculate_thumbnail_phash(
        Image.new("RGB", (16, 16), "white")
    ) is None


def test_study_plugin_registration_import_does_not_load_numpy() -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import plugin.plugins.study_companion; "
                "raise SystemExit(1 if 'numpy' in sys.modules else 0)"
            ),
        ],
        check=True,
    )


def test_ocr_pipeline_capture_lightweight_allows_missing_imagehash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline_module, "imagehash", None, raising=False)
    image = Image.new("RGB", (640, 360), "white")
    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(
            awareness=AwarenessConfig(
                classify_mode="title_first",
                image_max_bytes=50_000,
            )
        ),
        ocr_backend=_Backend(RuntimeError("ocr should not run")),
        capture_backend=_Capture(image),
    )

    snapshot = pipeline.capture_lightweight(
        target={"hwnd": 1, "title": "main.py - Visual Studio Code"}
    )

    assert snapshot.status == "ok"
    assert snapshot.thumbnail_phash == ""
    assert snapshot.has_content_change is False
    assert "phash=" in snapshot.diagnostic


def test_ocr_pipeline_capture_lightweight_cancels_timed_out_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = Image.new("RGB", (640, 360), "white")
    futures: list[_TimeoutFuture] = []

    class _TimeoutFuture:
        def __init__(self) -> None:
            self.cancelled = False

        def result(self, timeout: float | None = None) -> object:
            raise TimeoutError(f"timed out after {timeout}")

        def cancel(self) -> bool:
            self.cancelled = True
            return True

    class _TimeoutExecutor:
        def submit(self, fn, /, *args: Any, **kwargs: Any) -> _TimeoutFuture:  # noqa: ANN001
            del fn, args, kwargs
            future = _TimeoutFuture()
            futures.append(future)
            return future

    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(
            awareness=AwarenessConfig(classify_mode="ocr_text", image_max_bytes=50_000)
        ),
        ocr_backend=_Backend("Question: text"),
        capture_backend=_Capture(image),
    )
    monkeypatch.setattr(pipeline, "_executor", _TimeoutExecutor())

    snapshot = pipeline.capture_lightweight(target={"hwnd": 1, "title": "Quiz"})

    assert snapshot.status == "ok"
    assert len(futures) == 2
    assert all(future.cancelled for future in futures)
    assert pipeline._executor is None


def test_ocr_pipeline_capture_lightweight_reports_backend_resolve_failure() -> None:
    image = Image.new("RGB", (640, 360), "white")

    class _FailingResolvePipeline(StudyOcrPipeline):
        def _resolve_ocr_backend(self):
            raise RuntimeError("backend missing")

    pipeline = _FailingResolvePipeline(
        logger=_Logger(),
        config=StudyConfig(
            awareness=AwarenessConfig(classify_mode="ocr_text", image_max_bytes=50_000)
        ),
        capture_backend=_Capture(image),
    )

    snapshot = pipeline.capture_lightweight(target={"hwnd": 1, "title": "Quiz"})

    assert snapshot.status == "ok"
    assert "ocr_status=ocr_failed" in snapshot.diagnostic
    assert "backend missing" in snapshot.diagnostic


def test_ocr_pipeline_capture_lightweight_reports_sync_jpeg_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = Image.new("RGB", (640, 360), "white")
    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(awareness=AwarenessConfig(image_max_bytes=50_000)),
        capture_backend=_Capture(image),
    )

    def fail_encode(_image: Image.Image, *, max_bytes: int) -> bytes:
        del max_bytes
        raise RuntimeError("jpeg encode failed hard")

    monkeypatch.setattr(
        StudyOcrPipeline,
        "_encode_lightweight_jpeg",
        staticmethod(fail_encode),
    )

    snapshot = pipeline.capture_lightweight(target={"hwnd": 1, "title": "Quiz"})

    assert snapshot.status == "capture_failed"
    assert "jpeg encode failed hard" in snapshot.diagnostic


def test_ocr_pipeline_vision_snapshot_ttl_is_extended() -> None:
    assert pipeline_module._VISION_SNAPSHOT_TTL_SECONDS == 30.0


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
    assert snapshot.app_type == "web_page"
    assert snapshot.activity_type == "question"
    assert snapshot.ocr_text_snippet == "Question: Why does this happen?"
    assert activity is not None
    assert activity.classify_method == "both"
    assert activity.activity_type == "question"


def test_ocr_pipeline_capture_lightweight_skips_worker_vision_snapshot() -> None:
    image = Image.new("RGB", (800, 600), "white")

    class _TrackingPipeline(StudyOcrPipeline):
        def __init__(self) -> None:
            super().__init__(
                logger=_Logger(),
                config=StudyConfig(
                    awareness=AwarenessConfig(
                        classify_mode="ocr_text",
                        image_max_bytes=80_000,
                    )
                ),
                ocr_backend=_Backend("Question: text"),
                capture_backend=_Capture(image),
            )
            self.skip_values: list[bool] = []

        def _extract_image(
            self,
            image: Any,
            *,
            backend_name: str,
            _skip_vision_snapshot: bool = False,
        ) -> OcrSnapshot:
            self.skip_values.append(_skip_vision_snapshot)
            return OcrSnapshot(
                text="Question: text",
                status="ok",
                backend=backend_name,
            )

    pipeline = _TrackingPipeline()

    snapshot = pipeline.capture_lightweight(target={"hwnd": 1, "title": "Quiz"})

    assert snapshot.status == "ok"
    assert pipeline.skip_values == [True]


def test_ocr_pipeline_reuses_executor_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = Image.new("RGB", (800, 600), "white")
    created: list[_FakeExecutor] = []

    class _FakeExecutor:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.shutdown_waits: list[bool] = []
            created.append(self)

        def submit(self, fn, /, *args: Any, **kwargs: Any):  # noqa: ANN001
            future: Future[Any] = Future()
            try:
                future.set_result(fn(*args, **kwargs))
            except Exception as exc:
                future.set_exception(exc)
            return future

        def shutdown(self, *, wait: bool = True) -> None:
            self.shutdown_waits.append(wait)

    monkeypatch.setattr(pipeline_module, "ThreadPoolExecutor", _FakeExecutor)
    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(
            awareness=AwarenessConfig(classify_mode="ocr_text", image_max_bytes=80_000)
        ),
        ocr_backend=_Backend("Question: text"),
        capture_backend=_Capture(image),
    )

    assert pipeline.capture_lightweight(target={"hwnd": 1, "title": "Quiz"}).status == "ok"
    assert pipeline.capture_lightweight(target={"hwnd": 1, "title": "Quiz"}).status == "ok"
    pipeline.close()

    assert len(created) == 1
    assert created[0].shutdown_waits == [True]


def test_ocr_pipeline_close_drains_work_before_releasing_owned_backend() -> None:
    image = Image.new("RGB", (800, 600), "white")
    extraction_started = threading.Event()
    release_extraction = threading.Event()

    class _ClosableBackend(_Backend):
        def __init__(self) -> None:
            super().__init__("Question: text")
            self.close_calls = 0

        def extract_text(self, image: Any) -> Any:
            extraction_started.set()
            assert release_extraction.wait(timeout=2.0)
            return super().extract_text(image)

        def close(self) -> None:
            self.close_calls += 1

    backend = _ClosableBackend()
    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(
            awareness=AwarenessConfig(classify_mode="ocr_text", image_max_bytes=80_000)
        ),
        ocr_backend=backend,
        capture_backend=_Capture(image),
    )
    pipeline._owns_ocr_backend = True
    capture_thread = threading.Thread(
        target=pipeline.capture_lightweight,
        kwargs={"target": {"hwnd": 1, "title": "Quiz"}},
    )
    capture_thread.start()
    assert extraction_started.wait(timeout=2.0)

    close_thread = threading.Thread(target=pipeline.close)
    close_thread.start()
    close_thread.join(timeout=0.05)

    assert close_thread.is_alive()
    assert backend.close_calls == 0

    release_extraction.set()
    capture_thread.join(timeout=2.0)
    close_thread.join(timeout=2.0)

    assert not capture_thread.is_alive()
    assert not close_thread.is_alive()
    assert backend.close_calls == 1
    assert pipeline._ocr_backend is None
    with pytest.raises(RuntimeError, match="pipeline is closed"):
        pipeline._resolve_ocr_backend()


def test_ocr_pipeline_close_releases_only_owned_backend() -> None:
    class _ClosableBackend:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    external = _ClosableBackend()
    external_pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(),
        ocr_backend=external,
    )
    external_pipeline.close()
    assert external.close_calls == 0

    owned = _ClosableBackend()
    owned_pipeline = StudyOcrPipeline(logger=_Logger(), config=StudyConfig())
    owned_pipeline._ocr_backend = owned
    owned_pipeline._owns_ocr_backend = True
    owned_pipeline.close()
    owned_pipeline.close()

    assert owned.close_calls == 1


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
    assert first.has_content_change is True
    assert second.status == "ok"
    assert second.has_content_change is False

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
