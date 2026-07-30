from __future__ import annotations

import asyncio
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


_CLOSE_STEPS = (
    "_stop_foreground_advance_monitor",
    "_shutdown_capture_worker",
    "_drain_inflight_capture_workers",
    "_release_rapidocr_backend",
)


@pytest.mark.parametrize("exploding_step", list(_CLOSE_STEPS))
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

    for name in _CLOSE_STEPS:
        setattr(manager, name, types.MethodType(_step(name), manager))

    manager.close()

    assert done == list(_CLOSE_STEPS), f"{exploding_step} 抛错后其余步骤仍必须跑到"
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
    for name in _CLOSE_STEPS:
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

    def _drain(self, futures, **_kwargs) -> list:
        del self
        calls.append(("drain", None))
        return list(futures)

    manager._stop_foreground_advance_monitor = types.MethodType(_stop, manager)
    manager._shutdown_capture_worker = types.MethodType(_shutdown, manager)
    manager._drain_inflight_capture_workers = types.MethodType(_drain, manager)

    with manager as active:
        assert active is manager

    assert calls == [("stop", 1.0), ("shutdown", None), ("drain", None)]


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

    def _drain(self, futures, **_kwargs) -> list:
        del self
        calls.append(("drain", list(futures)))
        return []

    manager._stop_foreground_advance_monitor = types.MethodType(_stop, manager)
    manager._shutdown_capture_worker = types.MethodType(_shutdown, manager)
    manager._drain_inflight_capture_workers = types.MethodType(_drain, manager)

    manager.close()

    # _shutdown_capture_worker 抛错时拿不到在飞清单，drain 仍要跑（独立守卫），
    # 只是无事可等。
    assert calls == [("stop", 1.0), ("shutdown", None), ("drain", [])]


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


class _FakeRapidOcrBackend:
    """Stand-in for the real backend: only tracks whether close() has run."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _teardown_manager(backend: _FakeRapidOcrBackend) -> OcrReaderManager:
    manager = object.__new__(OcrReaderManager)
    manager._logger = _NullLogger()
    manager._capture_worker_lock = threading.Lock()
    manager._capture_executor = None
    manager._capture_future = None
    manager._capture_future_started_at = 0.0
    manager._capture_future_timed_out = False
    manager._abandoned_capture_workers = []
    manager._rapidocr_backend_cache = backend
    manager._rapidocr_backend_cache_key = ("a", "b", "c", "d", "e")
    manager.vision_classifier = None
    manager._writer = types.SimpleNamespace(session_id="")
    manager._attached_window = None
    manager._stop_foreground_advance_monitor = types.MethodType(
        lambda self, **_kwargs: None,
        manager,
    )
    return manager


def _submit_blocked_capture(
    manager: OcrReaderManager,
    capture,
) -> Future[OcrExtractionResult]:
    manager._capture_and_extract_text = capture
    return manager._submit_capture_worker(
        DetectedGameWindow(hwnd=1, width=800, height=600),
        OcrCaptureProfile(),
        SelectedOcrBackendPlan(),
        True,
        True,
    )


def test_shutdown_capture_worker_reports_only_uncancellable_futures_as_inflight() -> None:
    """Report the worker that is already running; cancelled ones need no wait."""
    manager = _teardown_manager(_FakeRapidOcrBackend())

    worker_started = threading.Event()
    release_worker = threading.Event()

    def _blocked_capture(*_args, **_kwargs) -> OcrExtractionResult:
        worker_started.set()
        release_worker.wait(timeout=5.0)
        return OcrExtractionResult(text="done")

    running_future = _submit_blocked_capture(manager, _blocked_capture)
    assert worker_started.wait(timeout=1.0)

    queued_executor = ThreadPoolExecutor(max_workers=1)
    queued_future: Future[OcrExtractionResult] = Future()
    finished_executor = ThreadPoolExecutor(max_workers=1)
    finished_future: Future[OcrExtractionResult] = Future()
    finished_future.set_result(OcrExtractionResult(text="already done"))
    manager._abandoned_capture_workers = [
        (queued_executor, queued_future),
        (finished_executor, finished_future),
    ]

    try:
        inflight = manager._shutdown_capture_worker()

        assert inflight == [running_future], "只有取消不掉的在飞任务需要等"
        assert queued_future.cancelled(), "还在排队的任务应该被取消而不是被等"
    finally:
        release_worker.set()
        running_future.result(timeout=5.0)
        queued_executor.shutdown(wait=True)
        finished_executor.shutdown(wait=True)


def test_shutdown_capture_worker_itself_never_waits_for_running_worker() -> None:
    """Only teardown waits. Hot-path worker rotation calls this too."""
    manager = _teardown_manager(_FakeRapidOcrBackend())

    worker_started = threading.Event()
    release_worker = threading.Event()

    def _blocked_capture(*_args, **_kwargs) -> OcrExtractionResult:
        worker_started.set()
        release_worker.wait(timeout=5.0)
        return OcrExtractionResult(text="done")

    running_future = _submit_blocked_capture(manager, _blocked_capture)
    assert worker_started.wait(timeout=1.0)

    drain_calls: list[object] = []

    def _record_drain(self, futures, **_kwargs) -> list:
        del self
        drain_calls.append(futures)
        return []

    manager._drain_inflight_capture_workers = types.MethodType(_record_drain, manager)

    try:
        started_at = time.monotonic()
        manager._shutdown_capture_worker()
        elapsed = time.monotonic() - started_at

        assert elapsed < 0.5, f"_shutdown_capture_worker 阻塞了 {elapsed:.2f}s"
        assert drain_calls == [], "等待只能发生在收尾路径，不能塞进这个函数"
    finally:
        release_worker.set()
        running_future.result(timeout=5.0)


def test_close_releases_rapidocr_backend_only_after_inflight_capture_finishes() -> None:
    """close() must not free the backend while a capture still holds its runtime."""
    backend = _FakeRapidOcrBackend()
    manager = _teardown_manager(backend)

    worker_started = threading.Event()
    release_worker = threading.Event()
    backend_closed_seen_by_worker: list[bool] = []

    def _blocked_capture(*_args, **_kwargs) -> OcrExtractionResult:
        worker_started.set()
        release_worker.wait(timeout=5.0)
        backend_closed_seen_by_worker.append(backend.closed)
        return OcrExtractionResult(text="done")

    running_future = _submit_blocked_capture(manager, _blocked_capture)
    assert worker_started.wait(timeout=1.0)

    releaser = threading.Timer(0.15, release_worker.set)
    releaser.start()
    try:
        manager.close()
    finally:
        releaser.cancel()
        release_worker.set()
        running_future.result(timeout=5.0)

    assert backend_closed_seen_by_worker == [False], (
        "worker 跑完之前 backend 就被 close 了 —— 释放没有等在飞任务"
    )
    assert backend.closed is True, "等完之后重依赖仍然必须释放"


def test_async_shutdown_releases_rapidocr_backend_only_after_inflight_capture_finishes() -> None:
    """Plugin unload goes through async shutdown(); it waits just like close()."""
    backend = _FakeRapidOcrBackend()
    manager = _teardown_manager(backend)

    worker_started = threading.Event()
    release_worker = threading.Event()
    backend_closed_seen_by_worker: list[bool] = []

    def _blocked_capture(*_args, **_kwargs) -> OcrExtractionResult:
        worker_started.set()
        release_worker.wait(timeout=5.0)
        backend_closed_seen_by_worker.append(backend.closed)
        return OcrExtractionResult(text="done")

    running_future = _submit_blocked_capture(manager, _blocked_capture)
    assert worker_started.wait(timeout=1.0)

    releaser = threading.Timer(0.15, release_worker.set)
    releaser.start()
    try:
        asyncio.run(manager.shutdown())
    finally:
        releaser.cancel()
        release_worker.set()
        running_future.result(timeout=5.0)

    assert backend_closed_seen_by_worker == [False], (
        "worker 跑完之前 backend 就被 close 了 —— 释放没有等在飞任务"
    )
    assert backend.closed is True


def test_close_gives_up_on_stuck_capture_after_bounded_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stuck worker cannot be killed, so teardown must move on at the deadline."""
    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.ocr_reader._OCR_SHUTDOWN_CAPTURE_DRAIN_TIMEOUT_SECONDS",
        0.05,
    )
    backend = _FakeRapidOcrBackend()
    manager = _teardown_manager(backend)
    warnings: list[str] = []

    class _RecordingLogger(_NullLogger):
        def warning(self, message: str, *_args) -> None:
            warnings.append(str(message))

    manager._logger = _RecordingLogger()

    worker_started = threading.Event()
    release_worker = threading.Event()

    def _stuck_capture(*_args, **_kwargs) -> OcrExtractionResult:
        worker_started.set()
        release_worker.wait(timeout=10.0)
        return OcrExtractionResult(text="finally")

    running_future = _submit_blocked_capture(manager, _stuck_capture)
    assert worker_started.wait(timeout=1.0)

    try:
        started_at = time.monotonic()
        manager.close()
        elapsed = time.monotonic() - started_at

        assert elapsed < 2.0, f"卡死的 worker 把 close() 拖了 {elapsed:.2f}s"
        assert backend.closed is True, "等不到也必须继续释放重依赖"
        assert any("in-flight capture worker" in message for message in warnings), (
            "放弃等待必须留下 warning"
        )
    finally:
        release_worker.set()
        running_future.result(timeout=5.0)


_ASYNC_SHUTDOWN_STEPS = (
    "_stop_foreground_advance_monitor",
    "_shutdown_capture_worker",
    "_drain_inflight_capture_workers",
    "_release_rapidocr_backend",
)


def _async_shutdown_manager() -> tuple[OcrReaderManager, dict]:
    manager = object.__new__(OcrReaderManager)
    manager._logger = _NullLogger()
    observed: dict = {"closed_classifier": [], "ended_sessions": []}

    class _Classifier:
        def close(self) -> None:
            observed["closed_classifier"].append(True)

    manager.vision_classifier = _Classifier()
    manager._writer = types.SimpleNamespace(
        session_id="session-1",
        end_session=lambda ts: observed["ended_sessions"].append(ts),
    )
    manager._ocr_lang_detector = types.SimpleNamespace(reset=lambda **_kwargs: None)
    manager._time_fn = lambda: 0.0
    manager._attached_window = object()
    return manager, observed


@pytest.mark.parametrize("exploding_step", list(_ASYNC_SHUTDOWN_STEPS))
def test_async_shutdown_releases_every_resource_despite_one_failure(
    exploding_step: str,
) -> None:
    """Plugin unload is the production teardown path; one bad step must not skip the rest."""
    manager, observed = _async_shutdown_manager()
    done: list[str] = []
    still_running: Future[OcrExtractionResult] = Future()

    def _step(name: str):
        def _run(self, *_args, **_kwargs):
            del self
            done.append(name)
            if name == exploding_step:
                raise RuntimeError(f"{name} exploded")
            if name == "_shutdown_capture_worker":
                return [still_running]
            return []
        return _run

    for name in _ASYNC_SHUTDOWN_STEPS:
        setattr(manager, name, types.MethodType(_step(name), manager))

    asyncio.run(manager.shutdown())

    expected = list(_ASYNC_SHUTDOWN_STEPS)
    if exploding_step == "_shutdown_capture_worker":
        # 拿不到在飞清单就没有东西可等，跳过 drain 是对的；其余步骤照跑。
        expected.remove("_drain_inflight_capture_workers")
    assert done == expected, f"{exploding_step} 抛错后其余步骤仍必须跑到"
    assert observed["closed_classifier"] == [True], "classifier 必须被关掉"
    assert manager.vision_classifier is None, "classifier 引用必须摘掉"
    assert len(observed["ended_sessions"]) == 1, "writer session 必须收掉"
    assert manager._attached_window is None

    still_running.cancel()


def test_async_shutdown_finishes_cleanup_when_cancelled_mid_drain() -> None:
    """Cancelling the unload during the drain must not strand the heavy deps."""
    manager, observed = _async_shutdown_manager()
    released: list[str] = []
    drain_entered = threading.Event()
    still_running: Future[OcrExtractionResult] = Future()

    manager._stop_foreground_advance_monitor = types.MethodType(
        lambda self, **_kwargs: None,
        manager,
    )
    manager._shutdown_capture_worker = types.MethodType(
        lambda self: [still_running],
        manager,
    )

    def _slow_drain(self, _futures, **_kwargs) -> list:
        del self
        drain_entered.set()
        time.sleep(0.3)
        return []

    manager._drain_inflight_capture_workers = types.MethodType(_slow_drain, manager)
    manager._release_rapidocr_backend = types.MethodType(
        lambda self: released.append("backend"),
        manager,
    )

    async def _cancel_during_drain() -> None:
        task = asyncio.create_task(manager.shutdown())
        await asyncio.to_thread(drain_entered.wait, 2.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_cancel_during_drain())

    assert released == ["backend"], "取消发生在 drain 期间，重依赖仍然必须被释放"
    assert observed["closed_classifier"] == [True]
    assert manager.vision_classifier is None
    assert len(observed["ended_sessions"]) == 1
    assert manager._attached_window is None

    still_running.cancel()


def test_drain_timeout_leaves_room_for_the_rest_of_plugin_shutdown() -> None:
    """The host joins the child on the same budget; eating it trades cleanup for a kill."""
    from plugin.plugins.galgame_plugin import ocr_runtime_types as runtime_types

    drain_timeout = runtime_types._OCR_SHUTDOWN_CAPTURE_DRAIN_TIMEOUT_SECONDS
    host_budget = runtime_types._PLUGIN_SHUTDOWN_TIMEOUT

    assert drain_timeout > 0.0
    assert drain_timeout <= host_budget * 0.5, (
        f"drain 上限 {drain_timeout}s 吃掉了宿主优雅关闭预算 {host_budget}s 的一半以上，"
        "backend / classifier / writer 的收尾会来不及跑就被 terminate"
    )


def test_drain_inflight_capture_workers_skips_wait_when_nothing_is_running() -> None:
    manager = _teardown_manager(_FakeRapidOcrBackend())
    finished: Future[OcrExtractionResult] = Future()
    finished.set_result(OcrExtractionResult(text="done"))

    started_at = time.monotonic()
    pending = manager._drain_inflight_capture_workers([finished], timeout=30.0)

    assert pending == []
    assert time.monotonic() - started_at < 0.5, "没有在飞任务时不该等待"
