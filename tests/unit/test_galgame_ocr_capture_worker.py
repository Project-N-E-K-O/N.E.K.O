from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import threading
import time
import types

import pytest

from plugin.plugins.galgame_plugin.ocr_reader import (
    _CaptureStillRunning,
    _CaptureTimedOut,
    DetectedGameWindow,
    OcrCaptureProfile,
    OcrExtractionResult,
    OcrReaderManager,
    SelectedOcrBackendPlan,
)


class _NullLogger:
    def debug(self, *_args, **_kwargs) -> None:
        pass

    def warning(self, *_args, **_kwargs) -> None:
        pass


class _ExplodingLogger(_NullLogger):
    def warning(self, *_args, **_kwargs) -> None:
        raise RuntimeError("logger failed")


@pytest.mark.parametrize("exploding_step", [
    "_stop_foreground_advance_monitor",
    "_shutdown_capture_worker",
    "_release_rapidocr_backend",
])
def test_ocr_reader_manager_close_releases_every_resource_despite_one_failure(
    exploding_step: str,
) -> None:
    """No teardown step may take another one down with it.

    close() swallows failures so callers can tear down unconditionally — which
    means a shared guard is worse than none: one failing step would skip the
    rest while the caller still sees a clean close and keeps live threads,
    executors, or a classifier around.
    """
    manager = object.__new__(OcrReaderManager)
    manager._logger = _NullLogger()
    done: list[str] = []
    closed_classifier: list[bool] = []

    class _Classifier:
        def close(self) -> None:
            closed_classifier.append(True)

    manager.vision_classifier = _Classifier()

    def _step(name: str):
        def _run(self, *_args, **_kwargs) -> None:
            del self
            done.append(name)
            if name == exploding_step:
                raise RuntimeError(f"{name} exploded")
        return _run

    for name in (
        "_stop_foreground_advance_monitor",
        "_shutdown_capture_worker",
        "_release_rapidocr_backend",
    ):
        setattr(manager, name, types.MethodType(_step(name), manager))

    manager.close()

    assert done == [
        "_stop_foreground_advance_monitor",
        "_shutdown_capture_worker",
        "_release_rapidocr_backend",
    ], f"{exploding_step} 抛错后其余步骤仍必须跑到"
    assert closed_classifier == [True], "classifier 必须被关掉"
    assert manager.vision_classifier is None, "classifier 引用必须摘掉"


def test_ocr_reader_manager_close_drops_classifier_reference_when_its_close_raises() -> None:
    """A classifier whose own close() raises must still be let go of."""
    manager = object.__new__(OcrReaderManager)
    manager._logger = _NullLogger()

    class _Classifier:
        def close(self) -> None:
            raise RuntimeError("classifier close exploded")

    manager.vision_classifier = _Classifier()
    for name in (
        "_stop_foreground_advance_monitor",
        "_shutdown_capture_worker",
        "_release_rapidocr_backend",
    ):
        setattr(manager, name, types.MethodType(lambda self, *a, **k: None, manager))

    manager.close()

    assert manager.vision_classifier is None


def test_ocr_reader_manager_context_manager_closes_capture_resources() -> None:
    manager = object.__new__(OcrReaderManager)
    manager._logger = _NullLogger()
    calls: list[tuple[str, float | None]] = []

    def _stop(self, *, join_timeout: float = 1.0) -> None:
        calls.append(("stop", join_timeout))

    def _shutdown(self) -> None:
        calls.append(("shutdown", None))

    manager._stop_foreground_advance_monitor = types.MethodType(_stop, manager)
    manager._shutdown_capture_worker = types.MethodType(_shutdown, manager)

    with manager as active:
        assert active is manager

    assert calls == [("stop", 1.0), ("shutdown", None)]


def test_ocr_reader_manager_close_swallows_shutdown_errors() -> None:
    manager = object.__new__(OcrReaderManager)
    manager._logger = _ExplodingLogger()
    calls: list[tuple[str, float | None]] = []

    def _stop(self, *, join_timeout: float = 1.0) -> None:
        del self
        calls.append(("stop", join_timeout))
        raise TypeError("legacy stop rejected timeout")

    def _shutdown(self) -> None:
        del self
        calls.append(("shutdown", None))
        raise RuntimeError("shutdown failed")

    manager._stop_foreground_advance_monitor = types.MethodType(_stop, manager)
    manager._shutdown_capture_worker = types.MethodType(_shutdown, manager)

    manager.close()

    assert calls == [("stop", 1.0), ("shutdown", None)]


def test_stop_foreground_advance_monitor_does_not_retry_without_timeout() -> None:
    manager = object.__new__(OcrReaderManager)
    calls: list[dict[str, float]] = []

    class _LegacyMonitor:
        def stop(self, **kwargs) -> None:
            calls.append(kwargs)
            raise TypeError("legacy stop rejected timeout")

    manager._wheel_monitor = _LegacyMonitor()
    manager._runtime = types.SimpleNamespace(
        foreground_advance_monitor_running=True,
        foreground_advance_last_seq=9,
    )

    with pytest.raises(TypeError):
        manager._stop_foreground_advance_monitor(join_timeout=0.2)

    assert calls == [{"join_timeout": 0.2}]


def test_timed_out_capture_does_not_replace_running_executor_during_recovery_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.ocr_reader._OCR_CAPTURE_TIMEOUT_SECONDS",
        12.0,
    )
    manager = object.__new__(OcrReaderManager)
    manager._logger = _NullLogger()
    manager._capture_worker_lock = threading.Lock()
    manager._capture_executor = None
    manager._capture_future = None
    manager._capture_future_started_at = 0.0
    manager._capture_future_timed_out = False
    manager._abandoned_capture_workers = []

    worker_started = threading.Event()
    release_worker = threading.Event()

    def _blocked_capture(*_args, **_kwargs) -> OcrExtractionResult:
        worker_started.set()
        release_worker.wait(timeout=5.0)
        return OcrExtractionResult(text="done")

    manager._capture_and_extract_text = _blocked_capture

    target = DetectedGameWindow(hwnd=1, width=800, height=600)
    profile = OcrCaptureProfile()
    backend_plan = SelectedOcrBackendPlan()

    first_future = manager._submit_capture_worker(
        target,
        profile,
        backend_plan,
        True,
        True,
    )
    assert worker_started.wait(timeout=1.0)
    first_executor = manager._capture_executor

    manager._capture_future_started_at = time.monotonic() - 18.0
    manager._capture_future_timed_out = True

    with pytest.raises(_CaptureStillRunning, match="accumulating blocked OCR threads"):
        manager._submit_capture_worker(
            target,
            profile,
            backend_plan,
            True,
            True,
        )

    assert manager._capture_executor is first_executor
    assert manager._capture_future is first_future
    assert not first_future.done()

    release_worker.set()
    try:
        assert first_future.result(timeout=1.0).text == "done"
    finally:
        manager._shutdown_capture_worker()


def test_timed_out_running_capture_is_abandoned_after_recovery_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.ocr_reader._OCR_CAPTURE_TIMEOUT_SECONDS",
        12.0,
    )
    manager = object.__new__(OcrReaderManager)
    manager._logger = _NullLogger()
    manager._capture_worker_lock = threading.Lock()
    manager._capture_executor = None
    manager._capture_future = None
    manager._capture_future_started_at = 0.0
    manager._capture_future_timed_out = False
    manager._abandoned_capture_workers = []

    worker_started = threading.Event()
    release_worker = threading.Event()
    capture_calls = 0
    capture_calls_lock = threading.Lock()

    def _capture(*_args, **_kwargs) -> OcrExtractionResult:
        nonlocal capture_calls
        with capture_calls_lock:
            capture_calls += 1
            call_number = capture_calls
        if call_number == 1:
            worker_started.set()
            release_worker.wait(timeout=5.0)
            return OcrExtractionResult(text="stale")
        return OcrExtractionResult(text="recovered")

    manager._capture_and_extract_text = _capture

    target = DetectedGameWindow(hwnd=1, width=800, height=600)
    profile = OcrCaptureProfile()
    backend_plan = SelectedOcrBackendPlan()

    first_future = manager._submit_capture_worker(
        target,
        profile,
        backend_plan,
        True,
        True,
    )
    assert worker_started.wait(timeout=1.0)
    first_executor = manager._capture_executor

    manager._capture_future_started_at = time.monotonic() - 30.0
    manager._capture_future_timed_out = True

    second_future = manager._submit_capture_worker(
        target,
        profile,
        backend_plan,
        True,
        True,
    )

    assert manager._capture_executor is not first_executor
    assert manager._capture_future is second_future
    assert manager._abandoned_capture_workers == [(first_executor, first_future)]
    assert not first_future.done()
    assert second_future.result(timeout=1.0).text == "recovered"

    release_worker.set()
    try:
        assert first_future.result(timeout=1.0).text == "stale"
    finally:
        manager._shutdown_capture_worker()


def test_timed_out_capture_is_retained_when_recovery_limit_is_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.ocr_reader._OCR_CAPTURE_TIMEOUT_SECONDS",
        12.0,
    )
    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.ocr_manager_capture._OCR_MAX_ABANDONED_CAPTURE_WORKERS",
        1,
    )
    manager = object.__new__(OcrReaderManager)
    manager._logger = _NullLogger()
    manager._capture_worker_lock = threading.Lock()
    manager._capture_executor = None
    manager._capture_future = None
    manager._capture_future_started_at = 0.0
    manager._capture_future_timed_out = False
    old_executor = ThreadPoolExecutor(max_workers=1)
    old_future: Future[OcrExtractionResult] = Future()
    manager._abandoned_capture_workers = [(old_executor, old_future)]

    worker_started = threading.Event()
    release_worker = threading.Event()

    def _blocked_capture(*_args, **_kwargs) -> OcrExtractionResult:
        worker_started.set()
        release_worker.wait(timeout=5.0)
        return OcrExtractionResult(text="stale")

    manager._capture_and_extract_text = _blocked_capture

    target = DetectedGameWindow(hwnd=1, width=800, height=600)
    profile = OcrCaptureProfile()
    backend_plan = SelectedOcrBackendPlan()

    first_future = manager._submit_capture_worker(
        target,
        profile,
        backend_plan,
        True,
        True,
    )
    assert worker_started.wait(timeout=1.0)
    first_executor = manager._capture_executor

    manager._capture_future_started_at = time.monotonic() - 30.0
    manager._capture_future_timed_out = True

    with pytest.raises(_CaptureTimedOut, match="recovery limit"):
        manager._submit_capture_worker(
            target,
            profile,
            backend_plan,
            True,
            True,
        )

    assert manager._abandoned_capture_workers == [(old_executor, old_future)]
    assert manager._capture_executor is first_executor
    assert manager._capture_future is first_future
    assert manager._capture_future_timed_out is True

    release_worker.set()
    try:
        assert first_future.result(timeout=1.0).text == "stale"
    finally:
        manager._shutdown_capture_worker()
