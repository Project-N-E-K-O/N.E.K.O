"""Bounded identification of actual separated audio before ASR delivery.

Candidate numbers are anonymous, local to a scoring interval.  This module
does not select the first channel, consult mixed-audio scores, invent a
calibration package, or authorize a larger interval than the supplied spec.
The caller owns the single delivery ledger and cross-window confirmation.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from typing import Protocol

from .prewire_gate.contracts import (
    SAMPLE_RATE_HZ,
    PrewireDecisionState,
    PrewireIntervalSpec,
    PrewireStreamKey,
    SampleRange,
)
from .prewire_gate.decision import (
    PrewireEvidenceClassifier,
    PrewireQualitySummary,
    PrewireScoreObservation,
    StrictPrewireEvidencePolicy,
)
from .prewire_gate.scheduler import (
    ControlledScoringScheduler,
    RawSampleRange,
    ScoreRequest,
    ScoreResultStatus,
    ScoringIdentity,
    SchedulerError,
)


from .prewire_gate.candidate_contracts import (
    CandidateAudio, CandidateBatch, CandidateBinding, CandidateEvidence, CandidateSelection,
)


class CandidateEvidenceClassifier(PrewireEvidenceClassifier, Protocol):
    def require_candidate_support(
        self,
        binding: CandidateBinding,
        *,
        sample_counts: tuple[int, ...],
        decision_sample_ranges: tuple[tuple[int, int, int], ...],
    ) -> None: ...


class CandidateIdentitySelector:
    """Own one bounded scoring scheduler; serialize candidate batches.

Admission requires an explicit candidate calibration contract. Existing raw
or terminal classifiers do not implement that contract and cannot silently
authorize separated PCM. Tests may inject an explicitly research classifier.
"""

    def __init__(
        self,
        binding: CandidateBinding,
        *,
        scheduler: ControlledScoringScheduler,
        classifier: CandidateEvidenceClassifier,
        deadline_seconds: float,
        max_buffered_pcm_bytes: int,
        required_consistent_observations: int = 1,
    ) -> None:
        if type(binding) is not CandidateBinding:
            raise ValueError("binding must be CandidateBinding")
        if type(deadline_seconds) not in {int, float} or not math.isfinite(deadline_seconds) or deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be finite and positive")
        if type(max_buffered_pcm_bytes) is not int or max_buffered_pcm_bytes <= 0:
            raise ValueError("max_buffered_pcm_bytes must be positive")
        # A missing method is a preparation error, not implicit raw support.
        classifier.require_candidate_support(binding, sample_counts=scheduler.window_plan.sample_counts, decision_sample_ranges=())
        self.binding = binding
        self._scheduler = scheduler
        self._classifier = classifier
        self._policy = StrictPrewireEvidencePolicy(classifier, required_consistent_observations=required_consistent_observations)
        self._deadline_seconds = deadline_seconds
        self._max_bytes = max_buffered_pcm_bytes
        self._lock = asyncio.Lock()
        self._closed = False
        self._epoch = 0
        self._retained_bytes = 0
        self._pending_batches = 0

    async def select(self, batch: CandidateBatch) -> CandidateSelection:
        if type(batch) is not CandidateBatch or batch.binding != self.binding:
            raise ValueError("candidate_batch_identity_mismatch")
        retained_bytes = sum(len(candidate.pcm16) for candidate in batch.candidates)
        if self._closed:
            return CandidateSelection(self.binding, batch.spec, PrewireDecisionState.STALE, "candidate_selector_retired", ())
        if self._retained_bytes + retained_bytes > self._max_bytes or self._pending_batches >= 2:
            return CandidateSelection(self.binding, batch.spec, PrewireDecisionState.UNAVAILABLE, "candidate_pcm_capacity", ())
        spec = batch.spec
        layout = (spec.scoring_range.sample_count, spec.decision_range.start - spec.scoring_range.start, spec.decision_range.end - spec.scoring_range.start)
        self._classifier.require_candidate_support(self.binding, sample_counts=(spec.scoring_range.sample_count,), decision_sample_ranges=(layout,))
        deadline = time.monotonic() + self._deadline_seconds
        epoch = self._epoch
        self._retained_bytes += retained_bytes
        self._pending_batches += 1
        acquired = False
        evidence: list[CandidateEvidence] = []
        try:
            try:
                await asyncio.wait_for(self._lock.acquire(), timeout=max(0, deadline - time.monotonic()))
                acquired = True
            except TimeoutError:
                return CandidateSelection(self.binding, spec, PrewireDecisionState.UNAVAILABLE, "candidate_deadline", ())
            if self._closed or epoch != self._epoch:
                return CandidateSelection(self.binding, spec, PrewireDecisionState.STALE, "candidate_selector_retired", ())
            for candidate in batch.candidates:
                item = await self._score_candidate(spec, candidate, deadline)
                if self._closed or epoch != self._epoch:
                    return CandidateSelection(self.binding, spec, PrewireDecisionState.STALE, "candidate_selector_retired", tuple(evidence))
                evidence.append(item)
                if item.decision in {PrewireDecisionState.UNAVAILABLE, PrewireDecisionState.STALE}:
                    return CandidateSelection(self.binding, spec, item.decision, item.reason, tuple(evidence))
            owners = [item for item in evidence if item.decision is PrewireDecisionState.KEEP]
            if len(owners) == 1 and all(item.decision in {PrewireDecisionState.KEEP, PrewireDecisionState.DROP} for item in evidence):
                selected = next(candidate for candidate in batch.candidates if candidate.candidate_id == owners[0].candidate_id)
                return CandidateSelection(self.binding, spec, PrewireDecisionState.KEEP, "unique_candidate_owner", tuple(evidence), selected)
            if owners or any(item.decision is PrewireDecisionState.UNCERTAIN for item in evidence):
                return CandidateSelection(self.binding, spec, PrewireDecisionState.UNCERTAIN, "candidate_identity_ambiguous", tuple(evidence))
            return CandidateSelection(self.binding, spec, PrewireDecisionState.DROP, "no_candidate_owner", tuple(evidence))
        finally:
            if acquired:
                self._lock.release()
            self._retained_bytes -= retained_bytes
            self._pending_batches -= 1

    async def _score_candidate(self, spec: PrewireIntervalSpec, candidate: CandidateAudio, deadline: float) -> CandidateEvidence:
        binding = self.binding
        # Include acoustic versions and immutable content in the receipt fence;
        # observations retain actual scorer/config generations for calibration.
        acoustic_version = hashlib.sha256(repr((binding, candidate.candidate_id, candidate.content_digest)).encode()).hexdigest()
        identity = ScoringIdentity(binding.stream.session_id, binding.stream.ingress_generation, binding.profile_generation, binding.model_generation, acoustic_version)
        sample_range = RawSampleRange(candidate.original_range.start, candidate.original_range.end)
        receipt = None
        try:
            if deadline <= time.monotonic():
                return CandidateEvidence(candidate.candidate_id, candidate.content_digest, None, PrewireDecisionState.UNAVAILABLE, "candidate_deadline")
            receipt = self._scheduler.submit(ScoreRequest(f"{spec.identity.segment_id}:{candidate.candidate_id}:{candidate.content_digest}", identity, sample_range, candidate.pcm16, SAMPLE_RATE_HZ))
            result = await asyncio.wait_for(self._scheduler.await_result(receipt), timeout=max(0, deadline - time.monotonic()))
            result = result.validate_identity(identity, sample_range)
            if result.status is not ScoreResultStatus.COMPLETED or result.score is None:
                state = PrewireDecisionState.STALE if result.status is ScoreResultStatus.STALE else PrewireDecisionState.UNAVAILABLE
                return CandidateEvidence(candidate.candidate_id, candidate.content_digest, None, state, f"candidate_scoring_{result.status.value}:{result.error_code or 'unknown'}")
            observation = PrewireScoreObservation(candidate.original_range, spec.decision_range, result.score, candidate.quality, binding.profile_generation, binding.model_generation, binding.config_generation, binding.scoring_parameters_digest)
            decision = self._policy.decide(spec.decision_range, (observation,), event_ended=spec.event_ended, boundary_trusted=spec.boundary_trusted, independent_event=spec.independent_event, deadline_expired=True)
            return CandidateEvidence(candidate.candidate_id, candidate.content_digest, result.score, decision.state, decision.reason)
        except (TimeoutError, SchedulerError, ValueError) as exc:
            return CandidateEvidence(candidate.candidate_id, candidate.content_digest, None, PrewireDecisionState.UNAVAILABLE, f"candidate_scoring_failed:{type(exc).__name__}")
        finally:
            if receipt is not None:
                self._scheduler.abandon(receipt)

    @property
    def retirement_confirmed(self) -> bool:
        return self._closed and self._scheduler.retirement_confirmed

    async def close(self) -> None:
        self._closed = True
        self._epoch += 1
        await self._scheduler.close()
