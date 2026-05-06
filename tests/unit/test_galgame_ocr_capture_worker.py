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


def test_timed_out_capture_does_not_replace_running_executor() -> None:
    manager = object.__new__(OcrReaderManager)
    manager._logger = _NullLogger()
    manager._capture_worker_lock = threading.Lock()
    manager._capture_executor = None
    manager._capture_future = None
    manager._capture_future_started_at = 0.0
    manager._capture_future_timed_out = False

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

    manager._capture_future_started_at = time.monotonic() - 30.0
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
