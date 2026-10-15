"""Strict evidence policy for local audio before it reaches an ASR provider.

The policy is deliberately independent from buffering and transport.  It never
turns a missing calibration package, a failed score, or a single ambiguous
observation into permission to upload audio.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Protocol

from .contracts import (
    ENDED_MICRO_EVENT_MAX_SAMPLES,
    PrewireDecisionState,
    SampleRange,
)


class CalibratedIdentityOutcome(str, Enum):
    OWNER = "owner"
    NONOWNER = "nonowner"
    UNCERTAIN = "uncertain"
    UNSUPPORTED = "unsupported"
    FAILURE = "failure"


@dataclass(frozen=True, slots=True)
class PrewireQualitySummary:
    """Reproducible scalar quality evidence; no PCM or embedding is retained."""

    speech_samples: int | None = None
    rms: float | None = None
    peak: float | None = None
    near_silence: float | None = None
    clipping: float | None = None
    continuous: bool = True

    def __post_init__(self) -> None:
        if self.speech_samples is not None and (
            type(self.speech_samples) is not int or self.speech_samples < 0
        ):
            raise ValueError("speech_samples must be a non-negative integer")
        for name in ("rms", "peak", "near_silence", "clipping"):
            value = getattr(self, name)
            if value is None:
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise ValueError(f"{name} must be within [0, 1]")
        if type(self.continuous) is not bool:
            raise ValueError("continuous must be bool")


@dataclass(frozen=True, slots=True)
class PrewireScoreObservation:
    """One score bound to its real range and immutable scoring parameters."""

    scoring_range: SampleRange
    decision_range: SampleRange
    raw_similarity: float
    quality: PrewireQualitySummary
    profile_generation: str
    model_generation: str
    config_generation: str
    parameters_digest: str

    def __post_init__(self) -> None:
        if not self.scoring_range.contains(self.decision_range):
            raise ValueError("scoring range must contain its decision range")
        if (
            isinstance(self.raw_similarity, bool)
            or not isinstance(self.raw_similarity, (int, float))
            or not math.isfinite(float(self.raw_similarity))
            or not -1.0 <= float(self.raw_similarity) <= 1.0
        ):
            raise ValueError("raw_similarity must be within [-1, 1]")
        for name in (
            "profile_generation",
            "model_generation",
            "config_generation",
        ):
            value = getattr(self, name)
            if type(value) is not str or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if (
            type(self.parameters_digest) is not str
            or len(self.parameters_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.parameters_digest
            )
        ):
            raise ValueError("parameters_digest must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class CalibratedIdentityEvidence:
    outcome: CalibratedIdentityOutcome
    reason: str

    def __post_init__(self) -> None:
        if type(self.outcome) is not CalibratedIdentityOutcome:
            raise TypeError("outcome must be CalibratedIdentityOutcome")
        if type(self.reason) is not str or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")


class PrewireEvidenceClassifier(Protocol):
    """Versioned calibration layer supplied by the voice identity service."""

    def classify(
        self, observation: PrewireScoreObservation
    ) -> CalibratedIdentityEvidence: ...


@dataclass(frozen=True, slots=True)
class PrewirePolicyDecision:
    state: PrewireDecisionState
    reason: str
    parameters_digest: str | None = None
    used_ended_micro_event_rule: bool = False


class StrictPrewireEvidencePolicy:
    """Resolve one decision range without deriving identity from duration."""

    def __init__(
        self,
        classifier: PrewireEvidenceClassifier | None,
        *,
        required_consistent_observations: int,
    ) -> None:
        if (
            type(required_consistent_observations) is not int
            or required_consistent_observations <= 0
        ):
            raise ValueError("required_consistent_observations must be positive")
        self._classifier = classifier
        self._required = required_consistent_observations

    def decide(
        self,
        decision_range: SampleRange,
        observations: tuple[PrewireScoreObservation, ...],
        *,
        event_ended: bool,
        boundary_trusted: bool,
        independent_event: bool,
        deadline_expired: bool = False,
    ) -> PrewirePolicyDecision:
        for name, value in (
            ("event_ended", event_ended),
            ("boundary_trusted", boundary_trusted),
            ("independent_event", independent_event),
            ("deadline_expired", deadline_expired),
        ):
            if type(value) is not bool:
                raise TypeError(f"{name} must be bool")
        if (
            event_ended
            and boundary_trusted
            and independent_event
            and decision_range.sample_count < ENDED_MICRO_EVENT_MAX_SAMPLES
        ):
            return PrewirePolicyDecision(
                PrewireDecisionState.DROP,
                "ended_trusted_micro_event",
                used_ended_micro_event_rule=True,
            )
        if not observations:
            return PrewirePolicyDecision(
                PrewireDecisionState.UNAVAILABLE
                if deadline_expired or event_ended
                else PrewireDecisionState.PENDING,
                "identity_evidence_missing",
            )
        if self._classifier is None:
            return PrewirePolicyDecision(
                PrewireDecisionState.UNAVAILABLE,
                "calibration_package_unavailable",
            )

        expected_versions: tuple[str, str, str, str] | None = None
        calibrated: list[CalibratedIdentityOutcome] = []
        seen_scoring_ranges: set[SampleRange] = set()
        for observation in observations:
            if observation.decision_range != decision_range:
                return PrewirePolicyDecision(
                    PrewireDecisionState.STALE,
                    "decision_range_mismatch",
                )
            if not observation.quality.continuous:
                return PrewirePolicyDecision(
                    PrewireDecisionState.STALE,
                    "discontinuous_scoring_audio",
                )
            versions = (
                observation.profile_generation,
                observation.model_generation,
                observation.config_generation,
                observation.parameters_digest,
            )
            if expected_versions is None:
                expected_versions = versions
            elif versions != expected_versions:
                return PrewirePolicyDecision(
                    PrewireDecisionState.STALE,
                    "scoring_version_changed",
                )
            if observation.scoring_range in seen_scoring_ranges:
                continue
            seen_scoring_ranges.add(observation.scoring_range)
            try:
                evidence = self._classifier.classify(observation)
            except Exception:
                return PrewirePolicyDecision(
                    PrewireDecisionState.UNAVAILABLE,
                    "calibration_execution_failed",
                )
            if evidence.outcome in {
                CalibratedIdentityOutcome.FAILURE,
                CalibratedIdentityOutcome.UNSUPPORTED,
            }:
                return PrewirePolicyDecision(
                    PrewireDecisionState.UNAVAILABLE,
                    evidence.reason,
                )
            calibrated.append(evidence.outcome)

        assert expected_versions is not None
        digest = expected_versions[3]
        owner_count = calibrated.count(CalibratedIdentityOutcome.OWNER)
        nonowner_count = calibrated.count(CalibratedIdentityOutcome.NONOWNER)
        if owner_count and nonowner_count:
            return PrewirePolicyDecision(
                PrewireDecisionState.UNCERTAIN,
                "conflicting_identity_evidence",
                digest,
            )
        if owner_count >= self._required:
            return PrewirePolicyDecision(
                PrewireDecisionState.KEEP,
                "consistent_owner_evidence",
                digest,
            )
        if nonowner_count >= self._required:
            return PrewirePolicyDecision(
                PrewireDecisionState.DROP,
                "consistent_nonowner_evidence",
                digest,
            )
        return PrewirePolicyDecision(
            PrewireDecisionState.UNCERTAIN
            if deadline_expired or event_ended
            else PrewireDecisionState.PENDING,
            "additional_identity_evidence_required",
            digest,
        )


__all__ = [
    "CalibratedIdentityEvidence",
    "CalibratedIdentityOutcome",
    "PrewireEvidenceClassifier",
    "PrewirePolicyDecision",
    "PrewireQualitySummary",
    "PrewireScoreObservation",
    "StrictPrewireEvidencePolicy",
]
