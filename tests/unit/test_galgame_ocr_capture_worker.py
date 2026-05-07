from __future__ import annotations

import threading
import time

import pytest

from plugin.plugins.galgame_plugin.ocr_reader import (
    _CaptureStillRunning,
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
