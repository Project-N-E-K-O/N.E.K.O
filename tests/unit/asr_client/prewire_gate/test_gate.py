from __future__ import annotations

import asyncio
from dataclasses import dataclass
import threading

import pytest

from main_logic.asr_client.prewire_gate.contracts import (
    PrewireDecisionState,
    PrewireIntervalIdentity,
    PrewireIntervalSpec,
    PrewireStreamKey,
    SampleRange,
)
from main_logic.asr_client.prewire_gate.decision import (
    CalibratedIdentityEvidence,
    CalibratedIdentityOutcome,
    PrewireScoreObservation,
)
from main_logic.asr_client.prewire_gate.gate import (
    PrewireAudioEvent,
    PrewireEndEvent,
    PrewireGapEvent,
    PrewireGate,
    PrewireGateCapacityError,
    PrewireGateIdentityError,
    PrewireWindowPlanner,
)
from main_logic.asr_client.prewire_gate.scheduler import (
    ControlledScoringScheduler,
    ScoringWindowPlan,
)


@dataclass
class _Classifier:
    outcome: CalibratedIdentityOutcome

    def classify(
        self, observation: PrewireScoreObservation
    ) -> CalibratedIdentityEvidence:
        return CalibratedIdentityEvidence(self.outcome, "test_calibration")


class _ScoreBackend:
    def __init__(self, score: float = 0.8) -> None:
        self.score_value = score
        self.calls: list[bytes] = []

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        assert sample_rate_hz == 16_000
        self.calls.append(pcm16)
        return self.score_value


class _BlockedBackend:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        self.started.set()
        self.release.wait(1.0)
        return 0.8


def _scheduler(backend, *, jobs: int = 8, deadline: float = 1.0):
    return ControlledScoringScheduler(
        backend,
        window_plan=ScoringWindowPlan((8,)),
        max_outstanding_jobs=jobs,
        max_buffered_pcm_bytes=1_000,
        deadline_seconds=deadline,
        close_timeout_seconds=0.02,
    )


def _identity(
    segment: int,
    original: SampleRange,
    *,
    stream: PrewireStreamKey | None = None,
) -> PrewireIntervalIdentity:
    return PrewireIntervalIdentity(
        stream or PrewireStreamKey("session", 1),
        segment,
        original,
        "profile-v1",
        "model-v1",
        "config-v1",
    )


def _spec(
    segment: int,
    *,
    start: int = 0,
    ended: bool = True,
    trusted: bool = False,
    independent: bool = False,
) -> PrewireIntervalSpec:
    original = SampleRange(start, start + 8)
    commit = SampleRange(start, start + 4)
    return PrewireIntervalSpec(
        _identity(segment, original),
        original,
        commit,
        commit,
        ended,
        trusted,
        independent,
    )


def _gate(
    backend,
    *,
    classifier=_Classifier(CalibratedIdentityOutcome.OWNER),
    held_bytes: int = 1_000,
    jobs: int = 8,
    deadline: float = 1.0,
) -> PrewireGate:
    gate = PrewireGate(
        _scheduler(backend, jobs=jobs, deadline=deadline),
        classifier=classifier,
        window_samples=8,
        step_samples=4,
        guard_samples=0,
        max_held_pcm_bytes=held_bytes,
        scoring_parameters_digest="a" * 64,
        required_consistent_observations=1,
    )
    gate.open_stream(
        PrewireStreamKey("session", 1),
        profile_generation="profile-v1",
        model_generation="model-v1",
        config_generation="config-v1",
    )
    return gate


def test_window_planning_is_independent_of_transport_chunking() -> None:
    one = PrewireWindowPlanner(window_samples=8, step_samples=4, guard_samples=2)
    split = PrewireWindowPlanner(window_samples=8, step_samples=4, guard_samples=2)

    expected = one.add_samples(13)
    actual = split.add_samples(2) + split.add_samples(3) + split.add_samples(8)

    assert actual == expected
    assert [plan.commit_range for plan in actual] == [
        SampleRange(0, 4),
        SampleRange(4, 8),
    ]
    assert one.finish_event() == split.finish_event()


@pytest.mark.asyncio
async def test_submit_is_nonawaiting_and_keep_releases_only_original_commit_pcm() -> (
    None
):
    backend = _ScoreBackend()
    gate = _gate(backend)
    pcm = b"".join(sample.to_bytes(2, "little") for sample in range(8))

    gate.append_pcm(PrewireStreamKey("session", 1), start_sample=0, pcm16=pcm)
    submission = gate.submit_interval(_spec(1))
    assert gate.held_pcm_bytes == len(pcm)

    plan = await gate.resolve(submission)

    assert plan is not None
    assert len(plan.events) == 1
    audio = plan.events[0]
    assert isinstance(audio, PrewireAudioEvent)
    assert audio.original_range == SampleRange(0, 4)
    assert audio.asr_range == SampleRange(0, 4)
    assert audio.pcm16 == pcm[:8]
    assert backend.calls == [pcm]
    assert gate.held_pcm_bytes == len(pcm)
    gate.claim(plan)
    assert gate.held_pcm_bytes == len(pcm) - 8
    await gate.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("classifier", "expected"),
    [
        (_Classifier(CalibratedIdentityOutcome.NONOWNER), PrewireDecisionState.DROP),
        (
            _Classifier(CalibratedIdentityOutcome.UNCERTAIN),
            PrewireDecisionState.UNCERTAIN,
        ),
        (None, PrewireDecisionState.UNAVAILABLE),
    ],
)
async def test_non_keep_decisions_are_explicit_gaps(classifier, expected) -> None:
    gate = _gate(_ScoreBackend(), classifier=classifier)
    gate.append_pcm(
        PrewireStreamKey("session", 1), start_sample=0, pcm16=b"\x01\x00" * 8
    )
    submission = gate.submit_interval(_spec(1))

    plan = await gate.resolve(submission)

    assert plan is not None
    assert len(plan.events) == 1
    assert isinstance(plan.events[0], PrewireGapEvent)
    assert plan.events[0].decision is expected
    gate.claim(plan)
    assert gate.held_pcm_bytes == 8
    await gate.close()


@pytest.mark.asyncio
async def test_pcm_capacity_backpressures_before_accepting_audio() -> None:
    gate = _gate(_ScoreBackend(), held_bytes=8)

    with pytest.raises(PrewireGateCapacityError, match="local_pcm_capacity"):
        gate.append_pcm(
            PrewireStreamKey("session", 1),
            start_sample=0,
            pcm16=b"\x01\x00" * 8,
        )
    assert gate.held_pcm_bytes == 0
    await gate.close()


@pytest.mark.asyncio
async def test_queue_capacity_failure_cancels_partial_work_and_emits_gap() -> None:
    gate = _gate(_ScoreBackend(), jobs=1)
    spec = _spec(1)
    gate.append_pcm(
        PrewireStreamKey("session", 1), start_sample=0, pcm16=b"\x01\x00" * 8
    )

    submission = gate.submit_interval(
        spec,
        scoring_ranges=(spec.scoring_range, spec.scoring_range),
    )
    plan = await gate.resolve(submission)

    assert plan is not None
    assert len(plan.events) == 1
    assert isinstance(plan.events[0], PrewireGapEvent)
    assert plan.events[0].decision is PrewireDecisionState.UNAVAILABLE
    assert plan.events[0].reason.startswith("scoring_not_queued")
    gate.claim(plan)
    await gate.close()


@pytest.mark.asyncio
async def test_scoring_timeout_never_releases_audio() -> None:
    backend = _BlockedBackend()
    gate = _gate(backend, deadline=0.01)
    gate.append_pcm(
        PrewireStreamKey("session", 1), start_sample=0, pcm16=b"\x01\x00" * 8
    )
    submission = gate.submit_interval(_spec(1))

    plan = await gate.resolve(submission)
    backend.release.set()

    assert plan is not None
    assert isinstance(plan.events[0], PrewireGapEvent)
    assert plan.events[0].decision is PrewireDecisionState.UNAVAILABLE
    assert plan.events[0].reason.startswith("scoring_timed_out")
    gate.claim(plan)
    await gate.close()


@pytest.mark.asyncio
async def test_invalidation_fences_late_score_and_accounts_one_gap() -> None:
    backend = _BlockedBackend()
    gate = _gate(backend)
    gate.append_pcm(
        PrewireStreamKey("session", 1), start_sample=0, pcm16=b"\x01\x00" * 8
    )
    submission = gate.submit_interval(_spec(1))
    resolving = asyncio.create_task(gate.resolve(submission))
    await asyncio.to_thread(backend.started.wait, 0.5)

    invalidated = gate.invalidate_stream(
        PrewireStreamKey("session", 1), reason="profile_reloaded"
    )
    backend.release.set()
    late = await resolving

    combined = invalidated + (() if late is None else late.events)
    assert len(combined) == 1
    assert isinstance(combined[0], PrewireGapEvent)
    assert combined[0].decision is PrewireDecisionState.STALE
    assert combined[0].reason == "profile_reloaded"
    assert gate.held_pcm_bytes == 0
    assert gate.pending_interval_count == 0
    await gate.close()


@pytest.mark.asyncio
async def test_overlapping_windows_share_pcm_and_new_revision_replaces_old_plan() -> (
    None
):
    gate = _gate(_ScoreBackend())
    stream = PrewireStreamKey("session", 1)
    pcm = b"\x01\x00" * 12
    gate.append_pcm(stream, start_sample=0, pcm16=pcm)
    first = gate.submit_interval(_spec(1))
    second = gate.submit_interval(_spec(2, start=4))

    assert gate.held_pcm_bytes == len(pcm)
    first_plan = await gate.resolve(first)
    assert first_plan is not None
    second_plan = await gate.resolve(second)

    assert second_plan is not None
    assert second_plan is not first_plan
    assert len(second_plan.events) == 2
    with pytest.raises(PrewireGateIdentityError, match="stale"):
        gate.claim(first_plan)
    gate.claim(second_plan)
    assert gate.pending_interval_count == 0
    assert gate.held_pcm_bytes == 8
    await gate.close()


def test_invalidation_clears_retained_pcm_in_place() -> None:
    gate = _gate(_ScoreBackend(), classifier=None)
    stream = PrewireStreamKey("session", 1)
    gate.append_pcm(stream, start_sample=0, pcm16=b"\x7f\x00" * 8)
    retained = gate._streams[stream].pcm16

    gate.invalidate_stream(stream)

    assert retained == bytearray()
    assert gate.held_pcm_bytes == 0


@pytest.mark.asyncio
async def test_ended_micro_rule_never_applies_to_ordinary_opening() -> None:
    gate = _gate(_ScoreBackend(), classifier=None)
    micro_pcm = b"\x01\x00" * 8
    gate.append_pcm(PrewireStreamKey("session", 1), start_sample=0, pcm16=micro_pcm)
    ordinary = gate.submit_interval(
        _spec(1, ended=True, trusted=False, independent=False)
    )
    plan = await gate.resolve(ordinary)

    assert plan is not None
    assert len(plan.events) == 1
    assert isinstance(plan.events[0], PrewireGapEvent)
    assert plan.events[0].decision is PrewireDecisionState.UNAVAILABLE
    assert plan.events[0].reason != "ended_trusted_micro_event"
    gate.claim(plan)
    await gate.close()


@pytest.mark.asyncio
async def test_unended_first_200ms_stays_held_instead_of_becoming_a_gap() -> None:
    gate = _gate(_ScoreBackend(), classifier=None)
    gate.append_pcm(
        PrewireStreamKey("session", 1), start_sample=0, pcm16=b"\x01\x00" * 8
    )
    submission = gate.submit_interval(_spec(1, ended=False))

    plan = await gate.resolve(submission)

    assert plan is None
    assert gate.held_pcm_bytes == 16
    gaps = gate.invalidate_stream(PrewireStreamKey("session", 1))
    assert len(gaps) == 1
    assert gaps[0].decision is PrewireDecisionState.STALE


@pytest.mark.asyncio
async def test_finish_emits_end_after_accounted_audio() -> None:
    gate = _gate(_ScoreBackend())
    gate.append_pcm(
        PrewireStreamKey("session", 1), start_sample=0, pcm16=b"\x01\x00" * 8
    )
    gate.submit_interval(_spec(1))
    tail_range = SampleRange(4, 8)
    gate.submit_interval(
        PrewireIntervalSpec(
            _identity(2, tail_range),
            tail_range,
            tail_range,
            tail_range,
            True,
            True,
            True,
        )
    )

    plan = await gate.finish_stream(PrewireStreamKey("session", 1))
    assert not isinstance(plan, PrewireEndEvent)
    assert [event.kind for event in plan.events] == ["audio", "gap"]
    gate.claim(plan)
    end = await gate.finish_stream(PrewireStreamKey("session", 1))

    assert isinstance(end, PrewireEndEvent)
    assert end.original_cursor == 8
    assert end.asr_cursor == 4
    assert gate.held_pcm_bytes == 0
    await gate.close()
