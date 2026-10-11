"""Physical retirement regressions using real delegates and controlled barriers."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from main_logic.voice_identity_service.prewire_gate.scheduler import (
    ControlledScoringScheduler,
    RawSampleRange,
    SchedulerCapacityError,
    SchedulerClosedError,
    ScoreRequest,
    ScoreResultStatus,
    ScorerCapabilities,
    ScoringIdentity,
    ScoringWindowPlan,
)

pytestmark = pytest.mark.runtime


def _request(name: str) -> ScoreRequest:
    return ScoreRequest(
        name,
        ScoringIdentity("session", 1, "profile", "model", "config"),
        RawSampleRange(0, 4),
        b"\x01\x00" * 4,
        16_000,
    )


def _scheduler(backend, *, deadline: float = 1.0) -> ControlledScoringScheduler:
    return ControlledScoringScheduler(
        backend,
        window_plan=ScoringWindowPlan((4,)),
        max_outstanding_jobs=4,
        max_buffered_pcm_bytes=32,
        deadline_seconds=deadline,
        close_timeout_seconds=0.01,
    )


class _ThreadBackend:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.returned = threading.Event()

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        self.started.set()
        try:
            assert self.release.wait(2.0), "test failed to release native delegate"
            return 0.99
        finally:
            self.returned.set()


class _DelegatingAsyncBackend(_ThreadBackend):
    async def score_async(self, pcm16: bytes, sample_rate_hz: int) -> float:
        return await asyncio.to_thread(self.score, pcm16, sample_rate_hz)


class _OwnedDelegatingAsyncBackend(_DelegatingAsyncBackend):
    @property
    def retirement_confirmed(self) -> bool:
        return self.returned.is_set()


async def _settle_tasks(scheduler: ControlledScoringScheduler) -> None:
    tasks = set(scheduler._physical_tasks)
    if scheduler._worker is not None:
        tasks.add(scheduler._worker)
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 0.5)


@pytest.mark.asyncio
async def test_close_retains_sync_thread_owner_across_repeated_close() -> None:
    backend = _ThreadBackend()
    scheduler = _scheduler(backend)
    receipt = scheduler.submit(_request("native"))
    try:
        assert await asyncio.to_thread(backend.started.wait, 0.5)
        await asyncio.wait_for(scheduler.close(), 0.2)
        await asyncio.wait_for(scheduler.close(), 0.2)
        result = await scheduler.await_result(receipt)
        assert result.status is ScoreResultStatus.CANCELLED
        assert result.score is None
        assert not scheduler.retirement_confirmed
        assert not backend.returned.is_set()
        with pytest.raises(SchedulerClosedError):
            scheduler.submit(_request("late"))
        backend.release.set()
        await _settle_tasks(scheduler)
        assert backend.returned.is_set()
        assert scheduler.retirement_confirmed
    finally:
        backend.release.set()
        await scheduler.close()
        await _settle_tasks(scheduler)


@pytest.mark.asyncio
@pytest.mark.parametrize("abandon", [False, True])
@pytest.mark.parametrize("capacity", ["jobs", "pcm"])
async def test_consumed_or_abandoned_receipt_keeps_physical_capacity(abandon, capacity) -> None:
    backend = _ThreadBackend()
    scheduler = ControlledScoringScheduler(
        backend,
        window_plan=ScoringWindowPlan((4,)),
        max_outstanding_jobs=1 if capacity == "jobs" else 4,
        max_buffered_pcm_bytes=8 if capacity == "pcm" else 32,
        deadline_seconds=1.0,
        close_timeout_seconds=0.01,
    )
    receipt = scheduler.submit(_request("physical"))
    try:
        assert await asyncio.to_thread(backend.started.wait, 0.5)
        physical = scheduler._active_score_task
        if abandon:
            assert scheduler.abandon(receipt)
        else:
            assert scheduler.cancel(receipt)
            assert (await scheduler.await_result(receipt)).status is ScoreResultStatus.CANCELLED
        assert scheduler.outstanding_jobs == 1
        assert scheduler.buffered_pcm_bytes == 8
        with pytest.raises(SchedulerCapacityError, match=(
            "outstanding_job_capacity" if capacity == "jobs" else "pcm_buffer_capacity"
        )):
            scheduler.submit(_request("cannot-fit-while-thread-alive"))
        backend.release.set()
        await asyncio.wait_for(asyncio.shield(physical), 0.5)
        assert scheduler.outstanding_jobs == scheduler.buffered_pcm_bytes == 0
        result = await scheduler.await_result(scheduler.submit(_request("after-exit")))
        assert result.status is ScoreResultStatus.COMPLETED
    finally:
        backend.release.set()
        await scheduler.close()
        await _settle_tasks(scheduler)


@pytest.mark.asyncio
@pytest.mark.parametrize("has_owner", [False, True])
async def test_cancelled_async_wrapper_requires_delegated_owner_exit(has_owner) -> None:
    backend = (_OwnedDelegatingAsyncBackend if has_owner else _DelegatingAsyncBackend)()
    scheduler = _scheduler(backend)
    receipt = scheduler.submit(_request("delegate"))
    try:
        assert await asyncio.to_thread(backend.started.wait, 0.5)
        await asyncio.wait_for(scheduler.close(), 0.2)
        await _settle_tasks(scheduler)
        assert (await scheduler.await_result(receipt)).status is ScoreResultStatus.CANCELLED
        # The asyncio wrapper has already exited; its real native delegate has not.
        assert not scheduler.retirement_confirmed
        assert not backend.returned.is_set()
        assert scheduler.outstanding_jobs == 1
        assert scheduler.buffered_pcm_bytes == 8
        backend.release.set()
        assert await asyncio.to_thread(backend.returned.wait, 0.5)
        assert scheduler.retirement_confirmed is has_owner
        assert scheduler.outstanding_jobs == (0 if has_owner else 1)
        assert scheduler.buffered_pcm_bytes == (0 if has_owner else 8)
    finally:
        backend.release.set()
        await scheduler.close()
        await _settle_tasks(scheduler)
        assert await asyncio.to_thread(backend.returned.wait, 0.5)


@pytest.mark.asyncio
async def test_cancelled_async_delegate_fences_queued_work_before_wrapper_exit() -> None:
    backend = _OwnedDelegatingAsyncBackend()
    scheduler = _scheduler(backend)
    active = scheduler.submit(_request("active"))
    queued = scheduler.submit(_request("queued"))
    try:
        assert await asyncio.to_thread(backend.started.wait, 0.5)
        assert scheduler.cancel(active)
        assert (await scheduler.await_result(active)).status is ScoreResultStatus.CANCELLED
        result = await asyncio.wait_for(scheduler.await_result(queued), 0.2)
        assert result.status is ScoreResultStatus.CANCELLED
        assert result.error_code == "scheduler_fenced"
        assert not scheduler.retirement_confirmed
        with pytest.raises(SchedulerClosedError):
            scheduler.submit(_request("cannot-overlap-native"))
        backend.release.set()
        assert await asyncio.to_thread(backend.returned.wait, 0.5)
        await _settle_tasks(scheduler)
        assert scheduler.retirement_confirmed
    finally:
        backend.release.set()
        await scheduler.close()
        await _settle_tasks(scheduler)


@pytest.mark.asyncio
@pytest.mark.parametrize("proof", [1, "true", "raises"])
async def test_owner_exit_proof_is_explicit_and_faults_fail_closed(proof) -> None:
    class Backend(_DelegatingAsyncBackend):
        @property
        def retirement_confirmed(self):
            if proof == "raises":
                raise RuntimeError("owner unavailable")
            return proof

    backend = Backend()
    scheduler = _scheduler(backend)
    receipt = scheduler.submit(_request("owner-fault"))
    try:
        assert await asyncio.to_thread(backend.started.wait, 0.5)
        await asyncio.wait_for(scheduler.close(), 0.2)
        await scheduler.await_result(receipt)
        backend.release.set()
        assert await asyncio.to_thread(backend.returned.wait, 0.5)
        await _settle_tasks(scheduler)
        assert not scheduler.retirement_confirmed
        assert scheduler.outstanding_jobs == 1
        assert scheduler.buffered_pcm_bytes == 8
    finally:
        backend.release.set()
        await scheduler.close()
        await _settle_tasks(scheduler)


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_proof", [False, None, 1, "raises"])
async def test_normal_async_return_cannot_override_declared_unfinished_owner(pending_proof) -> None:
    class Backend(_ThreadBackend):
        native_task = None

        @property
        def retirement_confirmed(self):
            if self.returned.is_set():
                return True
            if pending_proof == "raises":
                raise RuntimeError("owner unavailable")
            return pending_proof

        async def score_async(self, pcm16, sample_rate_hz):
            self.native_task = asyncio.create_task(asyncio.to_thread(self.score, pcm16, sample_rate_hz))
            assert await asyncio.to_thread(self.started.wait, 0.5)
            # The wrapper returns normally while its declared owner still has
            # a real execution. Task.done() must not override that evidence.
            return 0.9

    backend = Backend()
    scheduler = _scheduler(backend)
    first = scheduler.submit(_request("normal-but-unretired"))
    queued = scheduler.submit(_request("cannot-overlap"))
    try:
        result = await scheduler.await_result(first)
        assert result.status is ScoreResultStatus.FAILED
        assert result.error_code == "backend_retirement_unconfirmed"
        assert result.score is None
        assert (await scheduler.await_result(queued)).status is ScoreResultStatus.CANCELLED
        await _settle_tasks(scheduler)
        assert not scheduler.retirement_confirmed
        assert scheduler.outstanding_jobs == 1
        assert scheduler.buffered_pcm_bytes == 8
        with pytest.raises(SchedulerClosedError):
            scheduler.submit(_request("still-fenced"))
        backend.release.set()
        await asyncio.wait_for(backend.native_task, 0.5)
        assert scheduler.retirement_confirmed
        assert scheduler.outstanding_jobs == scheduler.buffered_pcm_bytes == 0
    finally:
        backend.release.set()
        await scheduler.close()
        if backend.native_task is not None:
            await asyncio.wait_for(backend.native_task, 0.5)
        await _settle_tasks(scheduler)


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_declared_property_attribute_error_is_not_missing_owner(asynchronous) -> None:
    class Backend:
        owner_recovered = False

        @property
        def retirement_confirmed(self):
            if not self.owner_recovered:
                raise AttributeError("declared owner evidence cannot be read")
            return True

        def score(self, pcm16, sample_rate_hz):
            return 0.9

    backend = Backend()
    if asynchronous:
        async def score_async(pcm16, sample_rate_hz):
            return backend.score(pcm16, sample_rate_hz)
        backend.score_async = score_async
    scheduler = _scheduler(backend)
    receipt = scheduler.submit(_request("descriptor-fault"))
    try:
        result = await scheduler.await_result(receipt)
        assert result.status is ScoreResultStatus.FAILED
        assert result.error_code == "backend_retirement_unconfirmed"
        await _settle_tasks(scheduler)
        assert not scheduler.retirement_confirmed
        assert scheduler.outstanding_jobs == 1
        assert scheduler.buffered_pcm_bytes == 8
        backend.owner_recovered = True
        assert scheduler.retirement_confirmed
        assert scheduler.outstanding_jobs == scheduler.buffered_pcm_bytes == 0
    finally:
        await scheduler.close()
        await _settle_tasks(scheduler)


@pytest.mark.asyncio
async def test_sync_normal_return_keeps_declared_native_owner_and_fences_queue() -> None:
    class Backend(_ThreadBackend):
        native = None
        calls = 0

        @property
        def retirement_confirmed(self):
            return self.returned.is_set()

        def score(self, pcm16, sample_rate_hz):
            self.calls += 1
            if self.native is None:
                def delegated():
                    super(Backend, self).score(pcm16, sample_rate_hz)
                self.native = threading.Thread(target=delegated)
                self.native.start()
                assert self.started.wait(0.5)
            return 0.9

    backend = Backend()
    scheduler = _scheduler(backend)
    first = scheduler.submit(_request("sync-native-owner"))
    queued = scheduler.submit(_request("cannot-overlap"))
    try:
        result = await scheduler.await_result(first)
        assert result.status is ScoreResultStatus.FAILED
        assert result.error_code == "backend_retirement_unconfirmed"
        assert (await scheduler.await_result(queued)).status is ScoreResultStatus.CANCELLED
        await _settle_tasks(scheduler)
        assert backend.calls == 1
        assert not backend.returned.is_set()
        assert scheduler.outstanding_jobs == 1
        assert scheduler.buffered_pcm_bytes == 8
        assert not scheduler.retirement_confirmed
        backend.release.set()
        await asyncio.to_thread(backend.native.join, 0.5)
        assert not backend.native.is_alive()
        assert scheduler.retirement_confirmed
        assert scheduler.outstanding_jobs == scheduler.buffered_pcm_bytes == 0
    finally:
        backend.release.set()
        if backend.native is not None:
            await asyncio.to_thread(backend.native.join, 0.5)
        await scheduler.close()
        await _settle_tasks(scheduler)


@pytest.mark.asyncio
async def test_normal_sync_completion_without_declared_owner_remains_available() -> None:
    scheduler = _scheduler(SimpleNamespace(score=lambda pcm16, rate: 0.9))
    try:
        for name in ("normal", "followup"):
            result = await scheduler.await_result(scheduler.submit(_request(name)))
            assert result.status is ScoreResultStatus.COMPLETED
            assert result.score == 0.9
        await scheduler.close()
        assert scheduler.retirement_confirmed
        assert scheduler.outstanding_jobs == scheduler.buffered_pcm_bytes == 0
    finally:
        await scheduler.close()


@pytest.mark.asyncio
async def test_uncooperative_async_exit_is_required_even_with_positive_owner() -> None:
    started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def score_async(pcm16, sample_rate_hz):
        started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
        return 0.9

    scheduler = _scheduler(SimpleNamespace(score_async=score_async, retirement_confirmed=True))
    receipt = scheduler.submit(_request("uncooperative"))
    try:
        await asyncio.wait_for(started.wait(), 0.5)
        await asyncio.wait_for(scheduler.close(), 0.2)
        await asyncio.wait_for(cancelled.wait(), 0.5)
        assert not scheduler.retirement_confirmed
        assert (await scheduler.await_result(receipt)).score is None
        release.set()
        await _settle_tasks(scheduler)
        assert scheduler.retirement_confirmed
    finally:
        release.set()
        await scheduler.close()
        await _settle_tasks(scheduler)


@pytest.mark.asyncio
async def test_new_queued_input_does_not_renew_first_queue_deadline() -> None:
    backend = _ThreadBackend()
    now = [10.0]
    scheduler = ControlledScoringScheduler(
        backend,
        window_plan=ScoringWindowPlan((4,)),
        max_outstanding_jobs=4,
        max_buffered_pcm_bytes=32,
        deadline_seconds=0.1,
        close_timeout_seconds=0.01,
        clock=lambda: now[0],
    )
    active = scheduler.submit(_request("active"))
    queued = scheduler.submit(_request("queued"))
    try:
        assert await asyncio.to_thread(backend.started.wait, 0.5)
        now[0] += 0.05
        later = scheduler.submit(_request("later"))
        assert scheduler.reprioritize(queued)
        assert queued.absolute_deadline == 10.1
        assert later.absolute_deadline == 10.15
        queued_result = await asyncio.wait_for(scheduler.await_result(queued), 0.5)
        assert queued_result.status is ScoreResultStatus.TIMED_OUT
        assert queued_result.error_code == "deadline_expired_in_queue"
        assert not backend.returned.is_set()
        assert (await scheduler.await_result(active)).status is ScoreResultStatus.TIMED_OUT
    finally:
        backend.release.set()
        await scheduler.close()
        await _settle_tasks(scheduler)
        await scheduler.await_result(later)


@pytest.mark.asyncio
async def test_normal_async_completion_needs_no_cancellation_owner() -> None:
    async def score_async(pcm16, sample_rate_hz):
        return 0.9

    scheduler = _scheduler(SimpleNamespace(score_async=score_async))
    try:
        result = await scheduler.await_result(scheduler.submit(_request("normal")))
        assert result.status is ScoreResultStatus.COMPLETED
        assert result.score == 0.9
        await scheduler.close()
        assert scheduler.retirement_confirmed
    finally:
        await scheduler.close()


@pytest.mark.asyncio
async def test_backend_cancellation_remains_a_distinct_failure() -> None:
    async def score_async(pcm16, sample_rate_hz):
        raise asyncio.CancelledError

    scheduler = _scheduler(SimpleNamespace(score_async=score_async))
    first = scheduler.submit(_request("backend-cancelled"))
    queued = scheduler.submit(_request("cannot-overlap"))
    try:
        result = await scheduler.await_result(first)
        assert result.status is ScoreResultStatus.FAILED
        assert result.error_code == "backend_cancelled"
        assert result.score is None
        assert (await scheduler.await_result(queued)).status is ScoreResultStatus.CANCELLED
        with pytest.raises(SchedulerClosedError):
            scheduler.submit(_request("still-fenced"))
        assert not scheduler.retirement_confirmed
    finally:
        await scheduler.close()
        await _settle_tasks(scheduler)


@pytest.mark.parametrize("counts", [(14_400,), (24_000, 26_400)])
def test_declared_capability_rejects_unsupported_windows(counts) -> None:
    capability = ScorerCapabilities(16_000, 24_000, (24_000,), "model")
    with pytest.raises(ValueError, match="outside declared"):
        capability.require_support(counts, model_generation="model")


def test_declared_supported_window_is_accepted_without_changing_thresholds() -> None:
    capability = ScorerCapabilities(16_000, 24_000, (24_000, 26_400), "model")
    capability.require_support((24_000, 26_400), model_generation="model")


@pytest.mark.parametrize("rate,generation", [(8_000, "model"), (16_000, "old")])
def test_capability_must_match_runtime_rate_and_generation(rate, generation) -> None:
    capability = ScorerCapabilities(rate, 24_000, (24_000,), generation)
    with pytest.raises(ValueError, match="does not match"):
        capability.require_support((24_000,), model_generation="model")


@pytest.mark.parametrize("changes", [
    {"sample_rate_hz": True}, {"sample_rate_hz": 0},
    {"minimum_samples": True}, {"minimum_samples": 0},
    {"supported_sample_counts": ()}, {"supported_sample_counts": [24_000]},
    {"supported_sample_counts": (24_000, 24_000)},
    {"supported_sample_counts": (14_400,)},
    {"supported_sample_counts": ([],)},
    {"model_generation": ""}, {"model_generation": 1},
])
def test_capability_contract_rejects_invalid_declarations(changes) -> None:
    values = dict(sample_rate_hz=16_000, minimum_samples=24_000,
                  supported_sample_counts=(24_000,), model_generation="model")
    values.update(changes)
    with pytest.raises(ValueError):
        ScorerCapabilities(**values)
