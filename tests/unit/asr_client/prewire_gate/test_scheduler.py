from __future__ import annotations

import asyncio
import threading
import time

import pytest

from main_logic.asr_client.prewire_gate.scheduler import (
    ControlledScoringScheduler,
    RawSampleRange,
    SchedulerCapacityError,
    SchedulerClosedError,
    SchedulerError,
    UnknownReceiptError,
    ScoreRequest,
    ScoreResultStatus,
    ScoringIdentity,
    ScoringWindowPlan,
)


def _identity(
    *,
    session: str = "session",
    ingress: int = 1,
    profile: str = "profile-2",
    model: str = "model-3",
    config: str = "config-4",
) -> ScoringIdentity:
    return ScoringIdentity(session, ingress, profile, model, config)


def _request(
    request_id: str,
    *,
    start: int = 0,
    samples: int = 4,
    identity: ScoringIdentity | None = None,
) -> ScoreRequest:
    return ScoreRequest(
        request_id,
        identity or _identity(),
        RawSampleRange(start, start + samples),
        b"\x01\x00" * samples,
        16_000,
    )


def _scheduler(backend, **changes) -> ControlledScoringScheduler:
    values = {
        "window_plan": ScoringWindowPlan((4, 6, 720, 8_000, 16_000)),
        "max_outstanding_jobs": 4,
        "max_buffered_pcm_bytes": 1_000_000,
        "deadline_seconds": 1.0,
        "close_timeout_seconds": 0.02,
    }
    values.update(changes)
    return ControlledScoringScheduler(backend, **values)


class _RecordingBackend:
    def __init__(self, *, score: float = 0.75, delay: float = 0.0) -> None:
        self.value = score
        self.delay = delay
        self.calls: list[tuple[int, int]] = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.calls.append((len(pcm16) // 2, sample_rate_hz))
            if self.delay:
                time.sleep(self.delay)
            return self.value
        finally:
            with self.lock:
                self.active -= 1


class _BlockedBackend:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        self.started.set()
        self.release.wait(2.0)
        return 0.5


class _AsyncRecordingBackend:
    def __init__(self, *, score: float = 0.8, delay: float = 0.0) -> None:
        self.value = score
        self.delay = delay
        self.calls: list[tuple[int, int]] = []
        self.active = 0
        self.max_active = 0

    async def score_async(self, pcm16: bytes, sample_rate_hz: int) -> float:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            self.calls.append((len(pcm16) // 2, sample_rate_hz))
            if self.delay:
                await asyncio.sleep(self.delay)
            return self.value
        finally:
            self.active -= 1


class _AsyncLateAfterCancellationBackend:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.returned = asyncio.Event()

    async def score_async(self, pcm16: bytes, sample_rate_hz: int) -> float:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            await self.release.wait()
        self.returned.set()
        return 0.99


class _AsyncUncooperativeBackend:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.returned = asyncio.Event()
        self.calls = 0
        self.active = 0
        self.max_active = 0

    async def score_async(self, pcm16: bytes, sample_rate_hz: int) -> float:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
        finally:
            self.active -= 1
            self.returned.set()
        return 0.91


@pytest.mark.asyncio
async def test_single_background_channel_does_not_block_submission() -> None:
    backend = _RecordingBackend(delay=0.02)
    scheduler = _scheduler(backend)
    first = scheduler.submit(_request("first"))
    second = scheduler.submit(_request("second", start=10))

    first_result, second_result = await asyncio.gather(
        scheduler.await_result(first), scheduler.await_result(second)
    )

    assert first_result.status is ScoreResultStatus.COMPLETED
    assert second_result.status is ScoreResultStatus.COMPLETED
    assert backend.calls == [(4, 16_000), (4, 16_000)]
    assert backend.max_active == 1
    await scheduler.close()


@pytest.mark.asyncio
async def test_async_backend_uses_one_serial_execution_channel() -> None:
    backend = _AsyncRecordingBackend(delay=0.01)
    scheduler = _scheduler(backend)
    receipts = [
        scheduler.submit(_request(f"async-{index}", start=index * 10))
        for index in range(3)
    ]

    results = await asyncio.gather(
        *(scheduler.await_result(receipt) for receipt in receipts)
    )

    assert [result.status for result in results] == [ScoreResultStatus.COMPLETED] * 3
    assert [result.score for result in results] == [0.8, 0.8, 0.8]
    assert backend.max_active == 1
    await scheduler.close()


@pytest.mark.asyncio
async def test_async_backend_is_preferred_when_sync_score_also_exists() -> None:
    class _DualBackend(_AsyncRecordingBackend):
        def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
            raise AssertionError("sync score must not run on the event loop")

    backend = _DualBackend()
    scheduler = _scheduler(backend)
    receipt = scheduler.submit(_request("prefer-async"))

    result = await scheduler.await_result(receipt)

    assert result.status is ScoreResultStatus.COMPLETED
    assert backend.calls == [(4, 16_000)]
    await scheduler.close()


def test_request_binds_pcm_to_exact_raw_sample_range() -> None:
    with pytest.raises(ValueError, match="exactly match"):
        ScoreRequest(
            "bad", _identity(), RawSampleRange(10, 15), b"\x00\x00" * 4, 16_000
        )


@pytest.mark.asyncio
async def test_outstanding_and_pcm_capacity_are_bounded() -> None:
    backend = _RecordingBackend()
    scheduler = _scheduler(backend, max_outstanding_jobs=1)
    first = scheduler.submit(_request("first"))
    with pytest.raises(SchedulerCapacityError, match="outstanding"):
        scheduler.submit(_request("second", start=10))
    assert scheduler.cancel(first)
    await scheduler.await_result(first)
    await scheduler.close()

    scheduler = _scheduler(backend, max_buffered_pcm_bytes=7)
    with pytest.raises(SchedulerCapacityError, match="pcm_buffer"):
        scheduler.submit(_request("too-large"))
    await scheduler.close()


@pytest.mark.asyncio
async def test_reprioritize_never_renews_absolute_deadline() -> None:
    now = [10.0]
    backend = _RecordingBackend()
    scheduler = _scheduler(backend, deadline_seconds=2.0, clock=lambda: now[0])
    first = scheduler.submit(_request("first"))
    second = scheduler.submit(_request("second", start=10))
    original_deadline = second.absolute_deadline

    now[0] = 11.5
    assert scheduler.reprioritize(second)
    assert second.absolute_deadline == original_deadline == 12.0
    now[0] = 12.1
    second_result = await scheduler.await_result(second)
    first_result = await scheduler.await_result(first)

    assert second_result.status is ScoreResultStatus.TIMED_OUT
    assert first_result.status is ScoreResultStatus.TIMED_OUT
    assert backend.calls == []
    await scheduler.close()


class _FailingBackend:
    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        raise RuntimeError("private backend detail")


class _LateFailOnceBackend:
    def __init__(self) -> None:
        self.calls = 0

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        self.calls += 1
        if self.calls == 1:
            time.sleep(0.15)
            raise RuntimeError("late failure")
        return 0.6


@pytest.mark.asyncio
async def test_backend_exception_is_a_distinct_failed_result() -> None:
    scheduler = _scheduler(_FailingBackend())
    receipt = scheduler.submit(_request("failure"))
    result = await scheduler.await_result(receipt)
    assert result.status is ScoreResultStatus.FAILED
    assert result.error_code == "backend_exception"
    assert result.score is None
    await scheduler.close()


@pytest.mark.asyncio
async def test_queued_cancellation_is_distinct_and_never_scores() -> None:
    backend = _BlockedBackend()
    scheduler = _scheduler(backend)
    active = scheduler.submit(_request("active"))
    queued = scheduler.submit(_request("queued", start=10))
    await asyncio.to_thread(backend.started.wait, 1.0)

    assert scheduler.cancel(queued)
    assert scheduler.queued_jobs == 0
    cancelled = await scheduler.await_result(queued)
    assert cancelled.status is ScoreResultStatus.CANCELLED
    backend.release.set()
    assert (await scheduler.await_result(active)).status is ScoreResultStatus.COMPLETED
    await scheduler.close()


@pytest.mark.asyncio
async def test_execution_timeout_is_reported_before_late_score_finishes() -> None:
    backend = _BlockedBackend()
    scheduler = _scheduler(backend, deadline_seconds=0.01)
    receipt = scheduler.submit(_request("timeout"))

    result = await scheduler.await_result(receipt)
    assert result.status is ScoreResultStatus.TIMED_OUT
    assert result.error_code == "execution_deadline_expired"
    backend.release.set()
    await scheduler.close()


@pytest.mark.asyncio
async def test_async_timeout_cancels_and_isolates_late_result() -> None:
    backend = _AsyncLateAfterCancellationBackend()
    scheduler = _scheduler(
        backend,
        deadline_seconds=0.01,
        close_timeout_seconds=0.01,
    )
    receipt = scheduler.submit(_request("async-timeout"))
    await backend.started.wait()

    result = await scheduler.await_result(receipt)

    assert result.status is ScoreResultStatus.TIMED_OUT
    assert result.error_code == "execution_deadline_expired"
    await asyncio.wait_for(backend.cancelled.wait(), 0.2)
    backend.release.set()
    await asyncio.wait_for(backend.returned.wait(), 0.2)
    assert result.score is None
    await scheduler.close()


@pytest.mark.asyncio
async def test_active_async_cancel_requests_cancellation_and_ignores_late_score() -> (
    None
):
    backend = _AsyncLateAfterCancellationBackend()
    scheduler = _scheduler(backend, close_timeout_seconds=0.01)
    receipt = scheduler.submit(_request("async-cancel"))
    await backend.started.wait()

    assert scheduler.cancel(receipt)
    result = await scheduler.await_result(receipt)

    assert result.status is ScoreResultStatus.CANCELLED
    assert result.error_code == "cancelled_by_caller"
    await asyncio.wait_for(backend.cancelled.wait(), 0.2)
    backend.release.set()
    await asyncio.wait_for(backend.returned.wait(), 0.2)
    assert result.score is None
    await scheduler.close()


@pytest.mark.parametrize("trigger", ["timeout", "cancel"])
@pytest.mark.asyncio
async def test_uncooperative_async_backend_fences_scheduler_without_overlap(
    trigger: str,
) -> None:
    backend = _AsyncUncooperativeBackend()
    scheduler = _scheduler(
        backend,
        deadline_seconds=0.01 if trigger == "timeout" else 1.0,
        close_timeout_seconds=0.01,
    )
    active = scheduler.submit(_request("uncooperative-active"))
    queued = scheduler.submit(_request("must-not-run", start=10))
    await backend.started.wait()
    if trigger == "cancel":
        assert scheduler.cancel(active)

    active_result = await scheduler.await_result(active)
    queued_result = await asyncio.wait_for(scheduler.await_result(queued), 0.2)

    assert active_result.status is (
        ScoreResultStatus.TIMED_OUT
        if trigger == "timeout"
        else ScoreResultStatus.CANCELLED
    )
    assert queued_result.status is ScoreResultStatus.CANCELLED
    assert queued_result.error_code == "scheduler_fenced"
    assert backend.calls == 1
    assert backend.max_active == 1
    with pytest.raises(SchedulerClosedError):
        scheduler.submit(_request("rejected-after-fence", start=20))

    backend.release.set()
    await asyncio.wait_for(backend.returned.wait(), 0.2)
    await scheduler.close()


@pytest.mark.asyncio
async def test_late_backend_exception_after_timeout_does_not_kill_worker() -> None:
    backend = _LateFailOnceBackend()
    scheduler = _scheduler(backend, deadline_seconds=0.1)
    first = scheduler.submit(_request("late-failure"))
    assert (await scheduler.await_result(first)).status is ScoreResultStatus.TIMED_OUT
    await asyncio.sleep(0.17)
    second = scheduler.submit(_request("next", start=10))
    assert (await scheduler.await_result(second)).status is ScoreResultStatus.COMPLETED
    assert backend.calls == 2
    await scheduler.close()


@pytest.mark.asyncio
async def test_identity_receipt_can_fence_stale_result_after_await() -> None:
    scheduler = _scheduler(_RecordingBackend())
    receipt = scheduler.submit(_request("identity"))
    result = await scheduler.await_result(receipt)

    current = result.validate_identity(_identity(ingress=2), receipt.sample_range)
    assert current.status is ScoreResultStatus.STALE
    assert current.score is None
    assert current.error_code == "identity_or_sample_range_changed"
    await scheduler.close()


@pytest.mark.parametrize(
    "changed",
    [
        _identity(session="other-session"),
        _identity(ingress=2),
        _identity(profile="other-profile"),
        _identity(model="other-model"),
        _identity(config="other-config"),
    ],
)
@pytest.mark.asyncio
async def test_every_identity_generation_participates_in_stale_fence(changed) -> None:
    scheduler = _scheduler(_RecordingBackend())
    receipt = scheduler.submit(_request("generation-fence"))
    result = await scheduler.await_result(receipt)
    fenced = result.validate_identity(changed, receipt.sample_range)
    assert fenced.status is ScoreResultStatus.STALE
    await scheduler.close()


@pytest.mark.parametrize(
    "values",
    [
        ("session", 0, "profile", "model", "config"),
        ("session", True, "profile", "model", "config"),
        ("session", 1, "", "model", "config"),
        ("session", 1, "profile", 3, "config"),
        ("session", 1, "profile", "model", ""),
    ],
)
def test_identity_contract_rejects_lossy_or_empty_generations(values) -> None:
    with pytest.raises(ValueError):
        ScoringIdentity(*values)


@pytest.mark.asyncio
async def test_same_request_id_with_different_ranges_is_never_reused() -> None:
    backend = _RecordingBackend()
    scheduler = _scheduler(backend)
    first = scheduler.submit(_request("same", start=0, samples=4))
    second = scheduler.submit(_request("same", start=4, samples=6))
    results = await asyncio.gather(
        scheduler.await_result(first), scheduler.await_result(second)
    )

    assert [result.score for result in results] == [0.75, 0.75]
    assert first.sample_range != second.sample_range
    assert backend.calls == [(4, 16_000), (6, 16_000)]
    await scheduler.close()


@pytest.mark.asyncio
async def test_receipt_from_another_scheduler_is_never_accepted() -> None:
    first_scheduler = _scheduler(_RecordingBackend())
    second_scheduler = _scheduler(_RecordingBackend())
    foreign = first_scheduler.submit(_request("same"))
    second_scheduler.submit(_request("same"))
    with pytest.raises(UnknownReceiptError):
        await second_scheduler.await_result(foreign)
    await first_scheduler.await_result(foreign)
    await first_scheduler.close()
    await second_scheduler.close()


@pytest.mark.asyncio
async def test_close_is_bounded_and_isolates_late_active_result() -> None:
    backend = _BlockedBackend()
    scheduler = _scheduler(backend, close_timeout_seconds=0.01)
    receipt = scheduler.submit(_request("late"))
    await asyncio.to_thread(backend.started.wait, 1.0)

    started = time.monotonic()
    await scheduler.close()
    elapsed = time.monotonic() - started
    result = await scheduler.await_result(receipt)

    assert elapsed < 0.2
    assert result.status is ScoreResultStatus.CANCELLED
    assert result.error_code == "scheduler_closed"
    backend.release.set()


def test_window_plan_is_injected_without_accuracy_defaults() -> None:
    plan = ScoringWindowPlan((720, 2_400, 8_000))
    scheduler = _scheduler(_RecordingBackend(), window_plan=plan)
    assert scheduler.window_plan is plan


@pytest.mark.asyncio
async def test_request_must_use_an_injected_window_size() -> None:
    scheduler = _scheduler(_RecordingBackend(), window_plan=ScoringWindowPlan((4, 720)))
    with pytest.raises(SchedulerError, match="sample_range_not_in_window_plan"):
        scheduler.submit(_request("unscheduled", samples=6))
    await scheduler.close()
