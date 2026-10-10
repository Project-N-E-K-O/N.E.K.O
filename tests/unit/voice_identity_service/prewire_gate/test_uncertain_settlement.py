"""Completed uncertainty is a local gap, never a fabricated endpoint."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace

import pytest

from main_logic.voice_identity_service.prewire_gate.contracts import (
    PrewireCommitStage,
    PrewireContractError,
    PrewireDecisionState,
    PrewireIntervalIdentity,
    PrewireIntervalRecord,
    PrewireIntervalSpec,
    PrewireStreamKey,
    SampleRange,
)
from main_logic.voice_identity_service.prewire_gate.decision import (
    CalibratedIdentityEvidence,
    CalibratedIdentityOutcome,
)
from main_logic.voice_identity_service.prewire_gate.gate import (
    PrewireAudioEvent,
    PrewireGapEvent,
    PrewireGate,
    PrewireGateIdentityError,
)
from main_logic.voice_identity_service.prewire_gate.ledger import (
    PrewireCapacityError,
    PrewireIdentityError,
    PrewireIntervalLedger,
    PrewireTransitionError,
)
from main_logic.voice_identity_service.prewire_gate.scheduler import (
    ControlledScoringScheduler,
    ScoringWindowPlan,
)

pytestmark = pytest.mark.runtime


def _spec(segment: int, start: int = 0) -> PrewireIntervalSpec:
    original = SampleRange(start, start + 8)
    commit = SampleRange(start, start + 4)
    return PrewireIntervalSpec(
        PrewireIntervalIdentity(
            PrewireStreamKey("settlement", 1), segment, original,
            "profile", "model", "config",
        ),
        original, commit, commit, False, False, False,
    )


def _scored_ledger(*, capacity: int = 8):
    ledger = PrewireIntervalLedger(capacity=capacity)
    spec = _spec(1)
    ledger.open_stream(spec.identity.stream, original_cursor=0)
    ledger.add(spec)
    ledger.record_score(spec.identity, score=0.4, scoring_parameters_digest="a" * 64)
    ledger.decide(spec.identity, PrewireDecisionState.UNCERTAIN, reason="uncertain")
    return ledger, spec


@pytest.mark.parametrize("decision", list(PrewireDecisionState))
def test_only_uncertain_record_can_be_finalized(decision) -> None:
    kwargs = dict(
        spec=_spec(1), decision=decision, score=0.4,
        scoring_parameters_digest="a" * 64, gap_finalized=True,
    )
    if decision is PrewireDecisionState.UNCERTAIN:
        assert PrewireIntervalRecord(**kwargs).gap_finalized
    else:
        with pytest.raises(PrewireContractError, match="only an uncertain"):
            PrewireIntervalRecord(**kwargs)


def test_finalized_gap_requires_score_and_has_no_delivery_stage() -> None:
    with pytest.raises(PrewireContractError, match="require score"):
        PrewireIntervalRecord(
            _spec(1), decision=PrewireDecisionState.UNCERTAIN, gap_finalized=True,
        )
    ledger, spec = _scored_ledger()
    record = ledger.finalize_uncertain(spec.identity)
    with pytest.raises(PrewireContractError, match="only an uncertain"):
        replace(record, commit_stage=PrewireCommitStage.ENQUEUED)
    with pytest.raises(PrewireContractError, match="must be bool"):
        replace(record, gap_finalized=1)


def test_finalization_preserves_identity_evidence_and_unblocks_later_keep() -> None:
    ledger, first = _scored_ledger()
    second = _spec(2, 4)
    ledger.add(second)
    ledger.record_score(second.identity, score=0.8, scoring_parameters_digest="a" * 64)
    ledger.decide(second.identity, PrewireDecisionState.KEEP, reason="owner")
    assert ledger.plan_contiguous(first.identity.stream).record_identities == ()

    record = ledger.finalize_uncertain(first.identity)
    assert ledger.finalize_uncertain(first.identity) is record
    assert record.spec is first
    assert record.decision is PrewireDecisionState.UNCERTAIN
    assert not record.spec.event_ended
    assert record.score == 0.4
    assert record.scoring_parameters_digest == "a" * 64
    assert record.decision_reason == "uncertain"
    plan = ledger.plan_contiguous(first.identity.stream)
    assert plan.record_identities == (first.identity, second.identity)
    assert plan.gaps[0].decision is PrewireDecisionState.UNCERTAIN
    assert plan.releases[0].asr_range == SampleRange(0, 4)
    assert ledger.release_cursor(first.identity.stream) == 0
    ledger.claim_enqueued(plan)
    assert ledger.release_cursor(first.identity.stream) == 8
    assert ledger.asr_cursor(first.identity.stream) == 4
    assert ledger.get(first.identity).original_to_asr is None
    with pytest.raises(PrewireTransitionError):
        ledger.claim_enqueued(plan)
    with pytest.raises(PrewireTransitionError):
        ledger.finalize_uncertain(first.identity)


@pytest.mark.parametrize("action", ["score", "keep", "stale", "uncertain"])
def test_finalized_gap_is_immutable_before_claim(action) -> None:
    ledger, spec = _scored_ledger()
    record = ledger.finalize_uncertain(spec.identity)
    with pytest.raises(PrewireTransitionError, match="finalized gap is immutable"):
        if action == "score":
            ledger.record_score(spec.identity, score=0.9, scoring_parameters_digest="b" * 64)
        else:
            ledger.decide(spec.identity, PrewireDecisionState(action), reason="late")
    assert ledger.get(spec.identity) is record


@pytest.mark.parametrize("decision", [PrewireDecisionState.PENDING, PrewireDecisionState.KEEP, PrewireDecisionState.DROP, PrewireDecisionState.UNAVAILABLE, PrewireDecisionState.STALE])
def test_finalization_rejects_non_uncertain_judgments(decision) -> None:
    ledger = PrewireIntervalLedger()
    spec = _spec(1)
    ledger.open_stream(spec.identity.stream, original_cursor=0)
    ledger.add(spec)
    if decision is not PrewireDecisionState.PENDING:
        ledger.record_score(spec.identity, score=0.8, scoring_parameters_digest="a" * 64)
        ledger.decide(spec.identity, decision, reason="test")
    with pytest.raises(PrewireTransitionError, match="only an unconsumed"):
        ledger.finalize_uncertain(spec.identity)
    assert ledger.release_cursor(spec.identity.stream) == 0


def test_gap_capacity_releases_only_after_claim_and_replay_is_rejected() -> None:
    ledger, first = _scored_ledger(capacity=1)
    ledger.finalize_uncertain(first.identity)
    second = _spec(2, 4)
    with pytest.raises(PrewireCapacityError):
        ledger.add(second)
    plan = ledger.plan_contiguous(first.identity.stream)
    ledger.claim_gaps(plan)
    assert ledger.release_cursor(first.identity.stream) == 4
    assert ledger.asr_cursor(first.identity.stream) == 0
    ledger.add(second)
    assert ledger.get(first.identity) is None
    with pytest.raises(PrewireIdentityError):
        ledger.add(first)


class _Backend:
    def __init__(self, *, blocked: bool = False) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not blocked:
            self.release.set()

    async def score_async(self, pcm16: bytes, sample_rate_hz: int) -> float:
        self.started.set()
        await self.release.wait()
        return 0.8


class _Classifier:
    def __init__(self) -> None:
        self.calls = 0

    def classify(self, observation) -> CalibratedIdentityEvidence:
        self.calls += 1
        outcome = (
            CalibratedIdentityOutcome.UNCERTAIN if self.calls == 1
            else CalibratedIdentityOutcome.OWNER
        )
        return CalibratedIdentityEvidence(outcome, "test_calibration")


def _gate(backend: _Backend, *, classifier=None) -> PrewireGate:
    scheduler = ControlledScoringScheduler(
        backend, window_plan=ScoringWindowPlan((8,)),
        max_outstanding_jobs=8, max_buffered_pcm_bytes=1_000,
        deadline_seconds=1.0, close_timeout_seconds=0.1,
    )
    gate = PrewireGate(
        scheduler, classifier=classifier or _Classifier(), window_samples=8,
        step_samples=4, guard_samples=0, max_held_pcm_bytes=1_000,
        scoring_parameters_digest="a" * 64, required_consistent_observations=1,
    )
    gate.open_stream(
        _spec(1).identity.stream, profile_generation="profile",
        model_generation="model", config_generation="config",
    )
    return gate


@pytest.mark.asyncio
async def test_gate_settles_only_completed_uncertainty_and_preserves_audio_order() -> None:
    backend = _Backend()
    gate = _gate(backend)
    first, second = _spec(1), _spec(2, 4)
    pcm = b"".join(sample.to_bytes(2, "little") for sample in range(12))
    try:
        gate.append_pcm(first.identity.stream, start_sample=0, pcm16=pcm)
        first_submission = gate.submit_interval(first)
        second_submission = gate.submit_interval(second)
        assert await gate.resolve(first_submission) is None
        assert await gate.resolve(second_submission) is None
        plan = gate.finalize_uncertain(first_submission)
        assert gate.finalize_uncertain(first_submission) is plan
        assert plan is not None
        assert [type(event) for event in plan.events] == [PrewireGapEvent, PrewireAudioEvent]
        assert plan.events[0].decision is PrewireDecisionState.UNCERTAIN
        assert plan.events[1].pcm16 == pcm[8:16]
        record = gate.get_interval_record(first.identity)
        assert record.gap_finalized and not record.spec.event_ended
        with pytest.raises(FrozenInstanceError):
            record.gap_finalized = False
        assert gate.get_interval_record(replace(first.identity, profile_generation="old")) is None
        gate.claim(plan)
        assert gate.held_pcm_bytes == 8
        assert gate.pending_interval_count == 0
        with pytest.raises(PrewireGateIdentityError):
            gate.finalize_uncertain(first_submission)
        with pytest.raises(PrewireGateIdentityError):
            gate.claim(plan)
        assert gate.get_interval_record(first.identity) is record
    finally:
        await gate.close()
    assert gate.get_interval_record(first.identity) is record


@pytest.mark.asyncio
async def test_pending_resolution_cannot_be_finalized_and_invalidation_fences_late_result() -> None:
    backend = _Backend(blocked=True)
    gate = _gate(backend)
    spec = _spec(1)
    resolving = None
    try:
        gate.append_pcm(spec.identity.stream, start_sample=0, pcm16=b"\x01\x00" * 8)
        submission = gate.submit_interval(spec)
        with pytest.raises(PrewireTransitionError, match="not_complete"):
            gate.finalize_uncertain(submission)
        resolving = asyncio.create_task(gate.resolve(submission))
        await asyncio.wait_for(backend.started.wait(), timeout=0.5)
        with pytest.raises(PrewireTransitionError, match="not_complete"):
            gate.finalize_uncertain(submission)
        gaps = gate.invalidate_stream(spec.identity.stream, reason="profile_changed")
        backend.release.set()
        assert await resolving is None
        with pytest.raises(PrewireGateIdentityError):
            gate.finalize_uncertain(submission)
        assert len(gaps) == 1 and gaps[0].decision is PrewireDecisionState.STALE
        assert gate.held_pcm_bytes == 0 and gate.pending_interval_count == 0
    finally:
        backend.release.set()
        if resolving is not None:
            await resolving
        await gate.close()


@pytest.mark.asyncio
async def test_completed_owner_is_not_eligible_for_uncertain_finalization() -> None:
    backend = _Backend()
    gate = _gate(backend)
    try:
        gate.append_pcm(_spec(1).identity.stream, start_sample=0, pcm16=b"\x01\x00" * 12)
        first = gate.submit_interval(_spec(1))
        second = gate.submit_interval(_spec(2, 4))
        await gate.resolve(first)
        await gate.resolve(second)
        with pytest.raises(PrewireTransitionError, match="only an unconsumed"):
            gate.finalize_uncertain(second)
        assert gate.get_interval_record(second.identity).decision is PrewireDecisionState.KEEP
        assert not gate.get_interval_record(second.identity).gap_finalized
    finally:
        await gate.close()


@pytest.mark.asyncio
async def test_classifier_failure_is_unavailable_and_cannot_be_finalized_as_uncertain() -> None:
    class FailingClassifier:
        def classify(self, observation):
            raise ValueError("classifier unavailable")

    gate = _gate(_Backend(), classifier=FailingClassifier())
    spec = _spec(1)
    try:
        gate.append_pcm(spec.identity.stream, start_sample=0, pcm16=b"\x01\x00" * 8)
        submission = gate.submit_interval(spec)
        assert await gate.resolve(submission) is None
        with pytest.raises(PrewireTransitionError, match="only an unconsumed"):
            gate.finalize_uncertain(submission)
        record = gate.get_interval_record(spec.identity)
        assert record.decision is PrewireDecisionState.UNAVAILABLE
        assert record.decision_reason == "calibration_execution_failed"
        assert not record.gap_finalized
        assert gate.held_pcm_bytes == 16
    finally:
        await gate.close()
