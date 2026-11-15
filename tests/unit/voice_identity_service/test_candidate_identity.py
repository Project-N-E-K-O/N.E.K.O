from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from main_logic.voice_identity_service.candidate_identity import (
    CandidateAudio, CandidateBatch, CandidateBinding, CandidateIdentitySelector,
    CandidateSelection,
)
from main_logic.voice_identity_service.candidate_calibration import CandidateCalibrationContract
from main_logic.voice_identity_service.prewire_gate.contracts import (
    PrewireDecisionState as State, PrewireIntervalIdentity, PrewireIntervalSpec,
    PrewireStreamKey, SampleRange,
)
from main_logic.voice_identity_service.prewire_gate.decision import (
    CalibratedIdentityEvidence, CalibratedIdentityOutcome,
)
from main_logic.voice_identity_service.prewire_gate.scheduler import (
    ControlledScoringScheduler, ScoringWindowPlan,
)

pytestmark = pytest.mark.runtime


def binding():
    return CandidateBinding(PrewireStreamKey("session", 1), "profile", "scorer", "config", "tfmap", "tse-reference", "a" * 64, "b" * 64)


def spec(start=0, segment=1):
    full = SampleRange(start, start + 8)
    identity = PrewireIntervalIdentity(binding().stream, segment, full, "profile", "scorer", "config")
    return PrewireIntervalSpec(identity, full, SampleRange(start, start + 6), SampleRange(start, start + 4), False, False, False)


def audio(candidate_id="one", value=100, *, start=0, bound=None):
    return CandidateAudio(bound or binding(), candidate_id, SampleRange(start, start + 8), value.to_bytes(2, "little", signed=True) * 8)


class ResearchClassifier:
    """Explicit injected fixture; no registered/released package claim."""

    def __init__(self, bound=None):
        self.bound = bound or binding()
        bound = self.bound
        self.contract = CandidateCalibrationContract(bound.model_generation, bound.separator_generation, bound.reference_generation, bound.preprocessing_generation, bound.config_generation, bound.scoring_parameters_digest, bound.calibration_digest, (8,), ((8, 0, 6),))

    def require_candidate_support(self, bound, *, sample_counts, decision_sample_ranges):
        self.contract.require_candidate_support(bound, sample_counts=sample_counts, decision_sample_ranges=decision_sample_ranges)
        if bound != self.bound or any(count != 8 for count in sample_counts):
            raise ValueError("fixture_candidate_calibration_mismatch")
        if decision_sample_ranges and decision_sample_ranges != ((8, 0, 6),):
            raise ValueError("fixture_candidate_layout_mismatch")

    def classify(self, observation):
        outcome = CalibratedIdentityOutcome.OWNER if observation.raw_similarity > .7 else (
            CalibratedIdentityOutcome.NONOWNER if observation.raw_similarity < .3 else CalibratedIdentityOutcome.UNCERTAIN
        )
        return CalibratedIdentityEvidence(outcome, "research_fixture")


class Scorer:
    def __init__(self):
        self.calls = []

    def score(self, pcm16, sample_rate_hz):
        assert sample_rate_hz == 16000
        self.calls.append(pcm16)
        value = int.from_bytes(pcm16[:2], "little", signed=True)
        return .9 if value > 0 else (.1 if value < 0 else .5)


def selector(backend=None, *, classifier=None, deadline=1, capacity=32):
    backend = backend or Scorer()
    scheduler = ControlledScoringScheduler(backend, window_plan=ScoringWindowPlan((8,)), max_outstanding_jobs=2, max_buffered_pcm_bytes=32, deadline_seconds=deadline, close_timeout_seconds=.1)
    return CandidateIdentitySelector(binding(), scheduler=scheduler, classifier=classifier or ResearchClassifier(), deadline_seconds=deadline, max_buffered_pcm_bytes=capacity), backend


@pytest.mark.asyncio
@pytest.mark.parametrize("values,state,chosen", [
    ((), State.DROP, None), ((100,), State.KEEP, "0"), ((-100,), State.DROP, None),
    ((0,), State.UNCERTAIN, None), ((100, -100), State.KEEP, "0"),
    ((-100, 100), State.KEEP, "1"), ((100, 100), State.UNCERTAIN, None),
    ((100, 0), State.UNCERTAIN, None), ((-100, -100), State.DROP, None),
])
async def test_selection_requires_unique_owner_and_scores_actual_candidate(values, state, chosen):
    target, backend = selector()
    candidates = tuple(audio(str(index), value) for index, value in enumerate(values))
    try:
        result = await target.select(CandidateBatch(binding(), spec(), candidates))
        assert result.decision is state
        assert backend.calls == [candidate.pcm16 for candidate in candidates]
        assert (result.selected.candidate_id if result.selected else None) == chosen
        assert result.commit_pcm16 == (result.selected.pcm16[:8] if chosen is not None else b"")
        result.validate_for(spec(), binding())
    finally:
        await target.close()


@pytest.mark.asyncio
async def test_anonymous_channel_permutation_is_reidentified_each_interval():
    target, backend = selector()
    try:
        first = await target.select(CandidateBatch(binding(), spec(), (audio("a", -100), audio("b", 100))))
        second_spec = spec(4, 2)
        second = await target.select(CandidateBatch(binding(), second_spec, (audio("a", 200, start=4), audio("b", -100, start=4))))
        assert first.selected.candidate_id == "b"
        assert second.selected.candidate_id == "a"
        assert first.commit_pcm16 != second.commit_pcm16
        assert len(backend.calls) == 4
    finally:
        await target.close()


@pytest.mark.asyncio
async def test_same_channel_id_changed_pcm_changes_identity_decision():
    target, backend = selector()
    try:
        owner = await target.select(CandidateBatch(binding(), spec(), (audio("same", 100),)))
        guest = await target.select(CandidateBatch(binding(), spec(4, 2), (audio("same", -100, start=4),)))
        assert owner.decision is State.KEEP
        assert guest.decision is State.DROP
        assert owner.evidence[0].content_digest != guest.evidence[0].content_digest
        assert guest.commit_pcm16 == b""
    finally:
        await target.close()


def test_invalid_candidate_contracts_and_late_selection_rebinding_rejected():
    with pytest.raises(ValueError):
        CandidateAudio(binding(), "one", SampleRange(0, 8), b"\x01\x00" * 7)
    with pytest.raises(ValueError):
        CandidateBatch(binding(), spec(), (audio("a"), audio("b"), audio("c")))
    with pytest.raises(ValueError):
        CandidateBatch(binding(), spec(), (audio("a"), audio("a")))
    with pytest.raises(ValueError):
        CandidateBatch(binding(), spec(), (audio(start=1),))
    with pytest.raises(ValueError):
        CandidateBatch(binding(), spec(), (audio(bound=replace(binding(), separator_generation="other")),))


@pytest.mark.parametrize("field,value", [
    ("profile_generation", "new"), ("model_generation", "new"),
    ("config_generation", "new"), ("separator_generation", "new"),
    ("reference_generation", "new"), ("scoring_parameters_digest", "c" * 64),
    ("calibration_digest", "d" * 64), ("stream", PrewireStreamKey("session", 2)),
    ("preprocessing_generation", "different-frontend"),
])
@pytest.mark.asyncio
async def test_selection_cannot_authorize_changed_identity_or_acoustic_version(field, value):
    target, _ = selector()
    try:
        result = await target.select(CandidateBatch(binding(), spec(), (audio(),)))
        with pytest.raises(ValueError):
            result.validate_for(spec(), replace(binding(), **{field: value}))
        with pytest.raises(ValueError):
            result.validate_for(spec(4, 2), binding())
        with pytest.raises(ValueError):
            replace(result, selected=audio(value=200))
    finally:
        await target.close()


@pytest.mark.asyncio
async def test_capacity_rejection_never_starts_scoring():
    target, backend = selector(capacity=16)
    try:
        result = await target.select(CandidateBatch(binding(), spec(), (audio("a"), audio("b"))))
        assert result.decision is State.UNAVAILABLE
        assert result.reason == "candidate_pcm_capacity"
        assert result.commit_pcm16 == b""
        assert backend.calls == []
    finally:
        await target.close()


def test_raw_classifier_has_no_candidate_admission_contract():
    class RawClassifier:
        def classify(self, observation):
            raise AssertionError("raw classifier must never execute")
    with pytest.raises(AttributeError):
        selector(classifier=RawClassifier())


@pytest.mark.asyncio
async def test_retirement_during_await_prevents_late_owner_audio():
    class Blocked:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
        async def score_async(self, pcm16, sample_rate_hz):
            self.started.set()
            await self.release.wait()
            return .9
    backend = Blocked()
    target, _ = selector(backend)
    pending = asyncio.create_task(target.select(CandidateBatch(binding(), spec(), (audio(),))))
    try:
        await backend.started.wait()
        await target.close()
        backend.release.set()
        result = await pending
        assert result.decision is State.STALE
        assert result.commit_pcm16 == b""
    finally:
        backend.release.set()
        await target.close()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_timeout_and_cancel_release_receipt_without_owner_fallback():
    class Blocked:
        async def score_async(self, pcm16, sample_rate_hz):
            await asyncio.Event().wait()
    target, _ = selector(Blocked(), deadline=.01)
    try:
        result = await target.select(CandidateBatch(binding(), spec(), (audio(),)))
        assert result.decision is State.UNAVAILABLE
        assert result.commit_pcm16 == b""
    finally:
        await target.close()


@pytest.mark.asyncio
async def test_inflight_and_queued_pcm_share_one_capacity_budget():
    class Blocked:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
        async def score_async(self, pcm16, sample_rate_hz):
            self.started.set()
            await self.release.wait()
            return .9
    backend = Blocked()
    target, _ = selector(backend)
    pending = asyncio.create_task(target.select(CandidateBatch(binding(), spec(), (audio("a"), audio("b")))))
    try:
        await backend.started.wait()
        result = await target.select(CandidateBatch(binding(), spec(4, 2), (audio(start=4),)))
        assert result.decision is State.UNAVAILABLE
        assert result.reason == "candidate_pcm_capacity"
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert target._retained_bytes == 0
        assert target._pending_batches == 0
    finally:
        backend.release.set()
        await target.close()
        await asyncio.gather(pending, return_exceptions=True)
