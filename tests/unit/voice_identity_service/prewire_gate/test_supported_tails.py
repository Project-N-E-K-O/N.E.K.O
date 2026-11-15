"""Real-range tail scheduling; fixture scores make no acoustic accuracy claim."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace

import pytest

from main_logic.voice_identity_service.prewire_gate import (
    CalibratedIdentityEvidence,
    CalibratedIdentityOutcome,
    ControlledScoringScheduler,
    PrewireAudioEvent,
    PrewireCommitStage,
    PrewireDecisionState,
    PrewireGapEvent,
    PrewireGate,
    PrewireIntervalIdentity,
    PrewireIntervalSpec,
    PrewireStreamKey,
    PrewireWindowPlanner,
    SampleRange,
    ScoringWindowPlan,
)

pytestmark = pytest.mark.runtime


class RecordingBackend:
    def __init__(self):
        self.calls = []

    async def score_async(self, pcm16, sample_rate_hz):
        assert sample_rate_hz == 16_000
        self.calls.append(pcm16)
        return 0.8


class OwnerClassifier:
    def classify(self, observation):
        return CalibratedIdentityEvidence(CalibratedIdentityOutcome.OWNER, "fixture")


def make_gate(backend, classifier=None, lengths=(4, 8)):
    scheduler = ControlledScoringScheduler(
        backend,
        window_plan=ScoringWindowPlan(lengths),
        max_outstanding_jobs=8,
        max_buffered_pcm_bytes=1_000,
        deadline_seconds=1,
        close_timeout_seconds=0.05,
    )
    gate = PrewireGate(
        scheduler,
        classifier=classifier or OwnerClassifier(),
        window_samples=8,
        step_samples=4,
        guard_samples=2,
        max_held_pcm_bytes=1_000,
        scoring_parameters_digest="a" * 64,
        required_consistent_observations=1,
    )
    stream = PrewireStreamKey("tail", 1)
    gate.open_stream(stream, profile_generation="p", model_generation="m", config_generation="c")
    return gate, scheduler, stream


def submit(gate, stream, planned, segment):
    identity = PrewireIntervalIdentity(stream, segment, planned.scoring_range, "p", "m", "c")
    return gate.submit_interval(PrewireIntervalSpec(
        identity, planned.scoring_range, planned.decision_range, planned.commit_range,
        True, False, False,
    ))


@pytest.mark.parametrize("window_ms", [900, 450, 350, 250])
def test_candidate_window_and_tail_use_only_uncommitted_real_samples(window_ms):
    window = window_ms * 16
    planner = PrewireWindowPlanner(
        window_samples=window, step_samples=2_400, guard_samples=1_600,
        scoring_sample_counts=tuple(count for count in (4_000, 5_600, 7_200, 14_400) if count <= window),
    )
    live = planner.add_samples(window + 160)
    tails = planner.finish_event()
    assert len(live) == 1
    cursor = live[-1].commit_range.end
    for planned in tails:
        assert planned.commit_range.start == cursor
        assert planned.scoring_range.start == cursor
        assert planned.scoring_range.end <= window + 160
        assert planned.scoring_range.contains(planned.decision_range)
        assert planned.decision_range.contains(planned.commit_range)
        assert planned.commit_range.sample_count <= 2_400
        cursor = planned.commit_range.end
    assert cursor == window + 160
    assert planner.finish_event() == ()


def test_tail_plan_selects_largest_supported_length_and_truncates_guard():
    planner = PrewireWindowPlanner(
        window_samples=12, step_samples=4, guard_samples=2,
        scoring_sample_counts=(3, 6, 12),
    )
    assert planner.add_samples(11) == ()
    tails = planner.finish_event()
    assert [tail.scoring_range for tail in tails] == [
        SampleRange(0, 6), SampleRange(4, 10), SampleRange(8, 11),
    ]
    assert [tail.commit_range for tail in tails] == [
        SampleRange(0, 4), SampleRange(4, 8), SampleRange(8, 11),
    ]
    assert tails[-1].decision_range == SampleRange(8, 11)


@pytest.mark.parametrize("counts", [(), (8, 4), (4, 4, 8), (True, 8), (0, 8), [4, 8]])
def test_planner_rejects_noncanonical_scoring_plan(counts):
    with pytest.raises(ValueError):
        PrewireWindowPlanner(window_samples=8, step_samples=4, guard_samples=0, scoring_sample_counts=counts)


def test_planner_requires_live_window_in_explicit_plan():
    with pytest.raises(ValueError, match="contain window_samples"):
        PrewireWindowPlanner(window_samples=8, step_samples=4, guard_samples=0, scoring_sample_counts=(4,))


@pytest.mark.asyncio
async def test_supported_tail_is_scored_once_without_padding_and_short_rest_is_gap():
    backend = RecordingBackend()
    gate, scheduler, stream = make_gate(backend, lengths=(6, 8))
    pcm = b"".join(sample.to_bytes(2, "little") for sample in range(1, 8))
    gate.append_pcm(stream, start_sample=0, pcm16=pcm)
    planner = gate.new_planner()
    assert planner.add_samples(7) == ()
    tails = planner.finish_event()
    assert [tail.scoring_range for tail in tails] == [SampleRange(0, 6), SampleRange(4, 7)]
    try:
        for index, planned in enumerate(tails, 1):
            submit(gate, stream, planned, index)
        plan = await gate.finish_stream(stream)
        assert isinstance(plan.events[0], PrewireAudioEvent)
        assert plan.events[0].pcm16 == pcm[:8]
        assert isinstance(plan.events[1], PrewireGapEvent)
        assert plan.events[1].original_range == SampleRange(4, 7)
        assert plan.events[1].reason == "scoring_window_unsupported"
        assert backend.calls == [pcm[:12]]
        gate.claim(plan)
        assert (await gate.finish_stream(stream)).original_cursor == 7
        assert gate.held_pcm_bytes == scheduler.buffered_pcm_bytes == 0
    finally:
        await gate.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["backend", "calibration"])
async def test_supported_length_is_not_permission_when_tail_evidence_fails(failure):
    class FailingBackend(RecordingBackend):
        async def score_async(self, pcm16, sample_rate_hz):
            raise RuntimeError("model failure")

    class UnsupportedClassifier:
        def classify(self, observation):
            return CalibratedIdentityEvidence(CalibratedIdentityOutcome.UNSUPPORTED, "tail_not_calibrated")

    backend = FailingBackend() if failure == "backend" else RecordingBackend()
    gate, _, stream = make_gate(backend, UnsupportedClassifier() if failure == "calibration" else None)
    gate.append_pcm(stream, start_sample=0, pcm16=b"\x01\x00" * 4)
    planner = gate.new_planner()
    planner.add_samples(4)
    try:
        plan = await gate.resolve(submit(gate, stream, planner.finish_event()[0], 1))
        assert isinstance(plan.events[0], PrewireGapEvent)
        assert plan.events[0].decision is PrewireDecisionState.UNAVAILABLE
        assert plan.events[0].reason != "ended_trusted_micro_event"
    finally:
        await gate.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_waiter", [False, True])
async def test_late_tail_score_cannot_release_invalidated_stream(cancel_waiter):
    class BlockedBackend(RecordingBackend):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.exited = asyncio.Event()

        @property
        def retirement_confirmed(self):
            return self.exited.is_set()

        async def score_async(self, pcm16, sample_rate_hz):
            self.calls.append(pcm16)
            self.started.set()
            try:
                await self.release.wait()
                return 0.8
            finally:
                self.exited.set()

    backend = BlockedBackend()
    gate, scheduler, stream = make_gate(backend)
    gate.append_pcm(stream, start_sample=0, pcm16=b"\x01\x00" * 4)
    planner = gate.new_planner()
    planner.add_samples(4)
    submission = submit(gate, stream, planner.finish_event()[0], 1)
    waiter = asyncio.create_task(gate.resolve(submission))
    physical_task = None
    try:
        await asyncio.wait_for(backend.started.wait(), 1)
        physical_task = scheduler._active_score_task
        assert physical_task is not None
        if cancel_waiter:
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        invalidated = gate.invalidate_stream(stream)
        assert all(not isinstance(event, PrewireAudioEvent) for event in invalidated)
        assert scheduler.outstanding_jobs == 1
        backend.release.set()
        await asyncio.wait_for(asyncio.gather(physical_task, return_exceptions=True), 1)
        assert backend.retirement_confirmed
        if not cancel_waiter:
            assert await waiter is None
        assert await gate.resolve(submission) is None
        assert scheduler.outstanding_jobs == 0
        assert gate.held_pcm_bytes == 0
    finally:
        backend.release.set()
        if physical_task is not None:
            await asyncio.gather(physical_task, return_exceptions=True)
        await asyncio.gather(waiter, return_exceptions=True)
        await gate.close()


@pytest.mark.asyncio
async def test_reverse_tail_resolution_preserves_original_delivery_order():
    backend = RecordingBackend()
    gate, _, stream = make_gate(backend, lengths=(6, 8))
    pcm = b"\x01\x00" * 7
    gate.append_pcm(stream, start_sample=0, pcm16=pcm)
    planner = gate.new_planner()
    planner.add_samples(7)
    submissions = [submit(gate, stream, tail, index) for index, tail in enumerate(planner.finish_event(), 1)]
    try:
        # The unsupported later interval must not jump the unresolved prefix.
        assert await gate.resolve(submissions[1]) is None
        plan = await gate.resolve(submissions[0])
        assert [event.original_range for event in plan.events] == [SampleRange(0, 4), SampleRange(4, 7)]
        assert [event.kind for event in plan.events] == ["audio", "gap"]
        gate.claim(plan)
        assert await gate.resolve(submissions[0]) is None
        assert backend.calls == [pcm[:12]]
    finally:
        await gate.close()


@pytest.mark.asyncio
async def test_prior_owner_window_does_not_authorize_nonowner_tail():
    class SwitchingClassifier:
        def classify(self, observation):
            outcome = (
                CalibratedIdentityOutcome.OWNER if observation.scoring_range.start == 0
                else CalibratedIdentityOutcome.NONOWNER
            )
            return CalibratedIdentityEvidence(outcome, "fresh_range_evidence")

    backend = RecordingBackend()
    gate, _, stream = make_gate(backend, SwitchingClassifier())
    pcm = b"\x01\x00" * 4 + b"\x02\x00" * 4
    gate.append_pcm(stream, start_sample=0, pcm16=pcm)
    planner = gate.new_planner()
    live = planner.add_samples(8)[0]
    tail = planner.finish_event()[0]
    live_identity = PrewireIntervalIdentity(stream, 1, live.scoring_range, "p", "m", "c")
    live_submission = gate.submit_interval(PrewireIntervalSpec(
        live_identity, live.scoring_range, live.decision_range, live.commit_range,
        False, False, False,
    ))
    try:
        first = await gate.resolve(live_submission)
        assert isinstance(first.events[0], PrewireAudioEvent)
        gate.claim(first)
        last = await gate.resolve(submit(gate, stream, tail, 2))
        assert len(last.events) == 1
        assert isinstance(last.events[0], PrewireGapEvent)
        assert last.events[0].original_range == SampleRange(4, 8)
        assert last.events[0].decision is PrewireDecisionState.DROP
        assert backend.calls == [pcm, pcm[8:]]
    finally:
        await gate.close()


@pytest.mark.asyncio
async def test_interval_record_lookup_is_immutable_exact_and_survives_retirement():
    gate, _, stream = make_gate(RecordingBackend())
    gate.append_pcm(stream, start_sample=0, pcm16=b"\x01\x00" * 4)
    planner = gate.new_planner()
    planner.add_samples(4)
    submission = submit(gate, stream, planner.finish_event()[0], 1)
    try:
        assert gate.get_interval_record(replace(submission.identity, config_generation="other")) is None
        plan = await gate.resolve(submission)
        gate.claim(plan)
        record = gate.get_interval_record(submission.identity)
        assert record.commit_stage is PrewireCommitStage.ENQUEUED
        with pytest.raises(FrozenInstanceError):
            record.commit_stage = PrewireCommitStage.WRITTEN
        await gate.finish_stream(stream)
        assert gate.get_interval_record(submission.identity) is record
        updated = gate.advance_delivery(
            submission.identity, expected=PrewireCommitStage.ENQUEUED,
            next_stage=PrewireCommitStage.WRITTEN,
        )
        assert gate.get_interval_record(submission.identity) is updated
        assert record.commit_stage is PrewireCommitStage.ENQUEUED
    finally:
        await gate.close()
