from __future__ import annotations

import asyncio
from dataclasses import replace
import threading

import pytest

from main_logic.voice_identity_service.prewire_gate import (
    CalibratedIdentityEvidence,
    CalibratedIdentityOutcome,
    ControlledScoringScheduler,
    PrewireAudioEvent,
    PrewireDecisionState,
    PrewireEndEvent,
    PrewireGapEvent,
    PrewireGate,
    PrewireGateRangeError,
    PrewireIntervalIdentity,
    PrewireIntervalSpec,
    PrewireStreamKey,
    RawSampleRange,
    SampleRange,
    ScoreRequest,
    ScoreResultStatus,
    ScoringIdentity,
    ScoringWindowPlan,
    UnknownReceiptError,
)

pytestmark = pytest.mark.runtime


class Backend:
    def __init__(self, score=0.8):
        self.calls = []
        self.score = score

    async def score_async(self, pcm16, sample_rate_hz):
        self.calls.append(pcm16)
        return self.score


class Classifier:
    def classify(self, observation):
        return CalibratedIdentityEvidence(
            CalibratedIdentityOutcome.OWNER, "test_calibration"
        )


def make_gate(*, window=8, step=4, guard=0, capacity=8, lengths=None, backend=None):
    scheduler = ControlledScoringScheduler(
        backend or Backend(),
        window_plan=ScoringWindowPlan(lengths or (window,)),
        max_outstanding_jobs=capacity,
        max_buffered_pcm_bytes=200_000,
        deadline_seconds=1.0,
        close_timeout_seconds=0.02,
    )
    gate = PrewireGate(
        scheduler,
        classifier=Classifier(),
        window_samples=window,
        step_samples=step,
        guard_samples=guard,
        max_held_pcm_bytes=200_000,
        scoring_parameters_digest="a" * 64,
        required_consistent_observations=1,
    )
    return gate, scheduler


def opened(gate, generation=1):
    stream = PrewireStreamKey("session", generation)
    gate.open_stream(
        stream, profile_generation="p", model_generation="m", config_generation="c"
    )
    return stream


def spec(stream, number, scoring, decision, commit, ended=False):
    identity = PrewireIntervalIdentity(stream, number, scoring, "p", "m", "c")
    return PrewireIntervalSpec(identity, scoring, decision, commit, ended, False, False)


def request(key):
    return ScoreRequest(
        key,
        ScoringIdentity("session", 1, "p", "m", "c"),
        RawSampleRange(0, 8),
        b"\x01\x00" * 8,
        16_000,
    )


@pytest.mark.asyncio
async def test_invalidation_releases_unresolved_scoring_capacity():
    gate, scheduler = make_gate(capacity=2)
    try:
        for generation in range(1, 33):
            stream = opened(gate, generation)
            gate.append_pcm(stream, start_sample=0, pcm16=b"\x01\x00" * 8)
            gate.submit_interval(
                spec(
                    stream,
                    1,
                    SampleRange(0, 8),
                    SampleRange(0, 4),
                    SampleRange(0, 4),
                    True,
                )
            )
            gate.invalidate_stream(stream)
            assert scheduler.outstanding_jobs == scheduler.queued_jobs == 0
            assert scheduler.buffered_pcm_bytes == gate.held_pcm_bytes == 0
        stream = opened(gate, 33)
        gate.append_pcm(stream, start_sample=0, pcm16=b"\x01\x00" * 8)
        submission = gate.submit_interval(
            spec(
                stream, 1, SampleRange(0, 8), SampleRange(0, 4), SampleRange(0, 4), True
            )
        )
        plan = await gate.resolve(submission)
        assert isinstance(plan.events[0], PrewireAudioEvent)
        assert scheduler.outstanding_jobs == 0
    finally:
        await gate.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["capacity", "backend", "score_range"])
async def test_partial_scoring_failure_releases_all_receipts(failure):
    class FailedBackend(Backend):
        async def score_async(self, pcm16, sample_rate_hz):
            raise RuntimeError("model failure")

    backend = FailedBackend() if failure == "backend" else Backend(2.0)
    gate, scheduler = make_gate(
        capacity=1 if failure == "capacity" else 2, backend=backend
    )
    stream = opened(gate)
    original = SampleRange(0, 8)
    try:
        gate.append_pcm(stream, start_sample=0, pcm16=b"\x01\x00" * 8)
        submission = gate.submit_interval(
            spec(stream, 1, original, SampleRange(0, 4), SampleRange(0, 4), True),
            scoring_ranges=(original, original),
        )
        plan = await gate.resolve(submission)
        assert isinstance(plan.events[0], PrewireGapEvent)
        assert plan.events[0].decision is PrewireDecisionState.UNAVAILABLE
        assert scheduler.outstanding_jobs == scheduler.queued_jobs == 0
        assert scheduler.buffered_pcm_bytes == 0
        receipt = scheduler.submit(request("after_failure"))
        await scheduler.await_result(receipt)
    finally:
        await gate.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("tail_supported", [False, True])
@pytest.mark.parametrize("samples", [8_000, 24_000, 29_000])
async def test_window_planner_tail_finishes_without_padding_or_implicit_keep(
    tail_supported, samples
):
    backend = Backend()
    lengths = (5_000, 8_000, 16_000) if tail_supported else (16_000,)
    gate, scheduler = make_gate(
        window=16_000, step=8_000, lengths=lengths, backend=backend
    )
    stream = opened(gate)
    pcm = b"".join(sample.to_bytes(2, "little") for sample in range(samples))
    gate.append_pcm(stream, start_sample=0, pcm16=pcm)
    planner = gate.new_planner()
    live = planner.add_samples(samples)
    tails = planner.finish_event()
    assert planner.finish_event() == ()
    assert all(tail.commit_range.sample_count <= 8_000 for tail in tails)
    try:
        for number, planned in enumerate(live + tails, 1):
            gate.submit_interval(
                spec(
                    stream,
                    number,
                    planned.scoring_range,
                    planned.decision_range,
                    planned.commit_range,
                    number > len(live),
                )
            )
        plan = await gate.finish_stream(stream)
        assert len(plan.events) == len(live) + len(tails)
        for event, planned in zip(plan.events, live + tails, strict=True):
            assert event.original_range == planned.commit_range
            if planned in tails and not tail_supported:
                assert isinstance(event, PrewireGapEvent)
                assert event.decision is PrewireDecisionState.UNAVAILABLE
                assert event.reason == "scoring_window_unsupported"
            else:
                assert isinstance(event, PrewireAudioEvent)
                assert (
                    event.pcm16
                    == pcm[
                        planned.commit_range.start * 2 : planned.commit_range.end * 2
                    ]
                )
        scored = live + tails if tail_supported else live
        assert backend.calls == [
            pcm[p.scoring_range.start * 2 : p.scoring_range.end * 2] for p in scored
        ]
        gate.claim(plan)
        assert isinstance(await gate.finish_stream(stream), PrewireEndEvent)
        assert gate.held_pcm_bytes == scheduler.buffered_pcm_bytes == 0
        assert scheduler.outstanding_jobs == 0
        assert planner.add_samples(16_000)[0].commit_range.start == samples
    finally:
        await gate.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("window", "step", "guard"), [(8, 4, 0), (8, 4, 4), (8, 6, 2), (8, 3, 4)]
)
async def test_accepted_guard_configuration_can_resolve_its_plan(window, step, guard):
    gate, _ = make_gate(window=window, step=step, guard=guard)
    stream = opened(gate)
    gate.append_pcm(stream, start_sample=0, pcm16=b"\x01\x00" * window)
    planned = gate.new_planner().add_samples(window)[0]
    interval = spec(
        stream, 1, planned.scoring_range, planned.decision_range, planned.commit_range
    )
    try:
        if guard:
            with pytest.raises(PrewireGateRangeError):
                gate.submit_interval(
                    replace(interval, decision_range=interval.commit_range)
                )
        plan = await gate.resolve(gate.submit_interval(interval))
        assert isinstance(plan.events[0], PrewireAudioEvent)
        assert plan.events[0].original_range == SampleRange(0, step)
    finally:
        await gate.close()


class BlockedBackend:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def score(self, pcm16, sample_rate_hz):
        self.calls += 1
        self.started.set()
        assert self.release.wait(5), "test backend was not released"
        return 0.8


@pytest.mark.asyncio
@pytest.mark.parametrize("abandon_active", [False, True])
async def test_queued_deadlines_preserve_physical_serialization(abandon_active):
    backend = BlockedBackend()
    scheduler = ControlledScoringScheduler(
        backend,
        window_plan=ScoringWindowPlan((8,)),
        max_outstanding_jobs=2,
        max_buffered_pcm_bytes=100,
        deadline_seconds=0.03,
        close_timeout_seconds=0.01,
    )
    first = scheduler.submit(request("first"))
    try:
        assert await asyncio.to_thread(backend.started.wait, 1)
        physical_task = scheduler._active_score_task
        if abandon_active:
            assert scheduler.abandon(first)
            assert not scheduler.abandon(first)
            with pytest.raises(UnknownReceiptError):
                await scheduler.await_result(first)
        else:
            assert (
                await scheduler.await_result(first)
            ).status is ScoreResultStatus.TIMED_OUT
        for number in range(4):
            queued = scheduler.submit(request(f"queued-{number}"))
            result = await asyncio.wait_for(scheduler.await_result(queued), 1)
            assert result.status is ScoreResultStatus.TIMED_OUT
            assert result.error_code == "deadline_expired_in_queue"
            assert scheduler.queued_jobs == 0
            assert scheduler.outstanding_jobs == 1
            assert scheduler.buffered_pcm_bytes == 16
            assert backend.calls == 1
        backend.release.set()
        await asyncio.wait_for(asyncio.shield(physical_task), 1)
        fresh = scheduler.submit(request("after_physical_return"))
        assert (
            await scheduler.await_result(fresh)
        ).status is ScoreResultStatus.COMPLETED
        assert backend.calls == 2
    finally:
        backend.release.set()
        await scheduler.close()


@pytest.mark.asyncio
async def test_abandon_respects_receipt_identity_and_existing_awaiter():
    backend = BlockedBackend()
    gate, scheduler = make_gate(backend=backend)
    receipt = scheduler.submit(request("owned"))
    try:
        assert await asyncio.to_thread(backend.started.wait, 1)
        assert not scheduler.abandon(replace(receipt, scheduler_id="foreign"))
        waiter = asyncio.create_task(scheduler.await_result(receipt))
        ready = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_soon(ready.set_result, None)
        await ready
        assert scheduler.abandon(receipt)
        assert (await waiter).status is ScoreResultStatus.CANCELLED
        assert scheduler.outstanding_jobs == 1
        assert scheduler.buffered_pcm_bytes == 16
    finally:
        backend.release.set()
        await gate.close()


@pytest.mark.asyncio
async def test_cancelled_gate_waiter_does_not_abandon_shared_resolution():
    backend = BlockedBackend()
    gate, scheduler = make_gate(backend=backend)
    stream = opened(gate)
    gate.append_pcm(stream, start_sample=0, pcm16=b"\x01\x00" * 8)
    submission = gate.submit_interval(
        spec(stream, 1, SampleRange(0, 8), SampleRange(0, 4), SampleRange(0, 4), True)
    )
    waiter = asyncio.create_task(gate.resolve(submission))
    try:
        assert await asyncio.to_thread(backend.started.wait, 1)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert scheduler.outstanding_jobs == 1
        backend.release.set()
        plan = await gate.resolve(submission)
        assert isinstance(plan.events[0], PrewireAudioEvent)
        assert scheduler.outstanding_jobs == 0
        assert backend.calls == 1
        gate.claim(plan)
        assert await gate.resolve(submission) is None
    finally:
        backend.release.set()
        await gate.close()
