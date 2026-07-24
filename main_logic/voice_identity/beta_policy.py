"""Versioned, fail-open Owner voice filtering policy for the beta."""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from .contracts import SpeakerShadowObservation


OwnerCandidateScope = Literal["provider_pause", "smart_turn_turn"]


class OwnerVoiceBetaDecision(Enum):
    """Provider-neutral authority returned by the beta policy."""

    FORWARD = "forward"
    REJECT_CURRENT_CANDIDATE = "reject_current_candidate"


@dataclass(frozen=True, slots=True)
class OwnerVoiceBetaConfig:
    """Immutable beta-v1 parameters; these are not production thresholds."""

    version: str = "beta-v1"
    similarity_threshold: float = 0.40
    first_observation_ms: int = 1_500
    second_observation_ms: int = 3_000
    candidate_capacity: int = 256

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("version must not be empty")
        if (
            not math.isfinite(self.similarity_threshold)
            or not -1.0 <= self.similarity_threshold <= 1.0
        ):
            raise ValueError("similarity_threshold must be within [-1, 1]")
        if self.first_observation_ms <= 0:
            raise ValueError("first_observation_ms must be positive")
        if self.second_observation_ms <= self.first_observation_ms:
            raise ValueError(
                "second_observation_ms must be greater than first_observation_ms"
            )
        if self.candidate_capacity <= 0:
            raise ValueError("candidate_capacity must be positive")


@dataclass(frozen=True, slots=True)
class OwnerVoiceCandidateIdentity:
    """Full identity required before a candidate-local decision may execute."""

    session_id: str
    detector_epoch: int
    candidate_generation: int
    candidate_scope: OwnerCandidateScope
    profile_revision: int

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("session_id must not be empty")
        if self.detector_epoch < 0:
            raise ValueError("detector_epoch must not be negative")
        if self.candidate_generation < 0:
            raise ValueError("candidate_generation must not be negative")
        if self.candidate_scope not in {"provider_pause", "smart_turn_turn"}:
            raise ValueError("candidate_scope is invalid")
        if self.profile_revision < 0:
            raise ValueError("profile_revision must not be negative")

    def matches_observation(self, observation: SpeakerShadowObservation) -> bool:
        candidate = observation.candidate
        return bool(
            getattr(candidate, "detector_epoch", None) == self.detector_epoch
            and getattr(candidate, "shadow_generation", None)
            == self.candidate_generation
            and getattr(candidate, "scope", None) == self.candidate_scope
        )


@dataclass(frozen=True, slots=True)
class OwnerVoiceDecisionRecord:
    """One observation result; it carries no Provider-specific command."""

    identity: OwnerVoiceCandidateIdentity
    decision: OwnerVoiceBetaDecision
    config_version: str
    observed_audio_ms: int
    reason: str


class OwnerVoiceBetaPolicy:
    """Require two stable, clearly low observations and otherwise forward."""

    def __init__(self, config: OwnerVoiceBetaConfig | None = None) -> None:
        self._config = config or OwnerVoiceBetaConfig()
        self._first_low: OrderedDict[OwnerVoiceCandidateIdentity, bool] = OrderedDict()
        self._observation_count = 0
        self._forward_count = 0
        self._hypothetical_reject_count = 0

    @property
    def config(self) -> OwnerVoiceBetaConfig:
        return self._config

    def observe(
        self,
        observation: SpeakerShadowObservation,
        *,
        identity: OwnerVoiceCandidateIdentity,
        enabled: bool,
        active_profile_revision: int | None,
    ) -> OwnerVoiceDecisionRecord:
        """Record a hypothetical decision without changing audio execution."""

        self._observation_count += 1
        audio_ms = observation.audio_ms
        if not enabled:
            self._first_low.pop(identity, None)
            return self._forward(identity, audio_ms, "filter_disabled")
        if active_profile_revision != identity.profile_revision:
            self._first_low.pop(identity, None)
            return self._forward(identity, audio_ms, "profile_revision_changed")
        if not identity.matches_observation(observation):
            self._first_low.pop(identity, None)
            return self._forward(identity, audio_ms, "candidate_identity_mismatch")
        similarity = observation.similarity
        if (
            not isinstance(audio_ms, int)
            or isinstance(audio_ms, bool)
            or audio_ms < 0
            or not isinstance(similarity, (int, float))
            or isinstance(similarity, bool)
            or not math.isfinite(float(similarity))
            or not -1.0 <= float(similarity) <= 1.0
        ):
            self._first_low.pop(identity, None)
            return self._forward(identity, 0, "invalid_observation")
        if audio_ms < self._config.first_observation_ms:
            return self._forward(identity, audio_ms, "insufficient_audio")
        clearly_low = float(similarity) < self._config.similarity_threshold
        if audio_ms < self._config.second_observation_ms:
            if clearly_low:
                self._remember_first_low(identity)
                return self._forward(
                    identity,
                    audio_ms,
                    "awaiting_second_low_observation",
                )
            self._first_low.pop(identity, None)
            return self._forward(identity, audio_ms, "owner_or_uncertain")

        first_was_low = self._first_low.pop(identity, False)
        if first_was_low and clearly_low:
            self._hypothetical_reject_count += 1
            return OwnerVoiceDecisionRecord(
                identity=identity,
                decision=OwnerVoiceBetaDecision.REJECT_CURRENT_CANDIDATE,
                config_version=self._config.version,
                observed_audio_ms=audio_ms,
                reason="stable_clear_mismatch",
            )
        return self._forward(identity, audio_ms, "owner_or_uncertain")

    def forget_candidate(self, identity: OwnerVoiceCandidateIdentity) -> None:
        self._first_low.pop(identity, None)

    def reset(self) -> None:
        self._first_low.clear()

    def snapshot(self) -> dict[str, int | str | float]:
        return {
            "config_version": self._config.version,
            "similarity_threshold": self._config.similarity_threshold,
            "pending_candidate_count": len(self._first_low),
            "observation_count": self._observation_count,
            "forward_count": self._forward_count,
            "hypothetical_reject_count": self._hypothetical_reject_count,
        }

    def _remember_first_low(self, identity: OwnerVoiceCandidateIdentity) -> None:
        self._first_low.pop(identity, None)
        self._first_low[identity] = True
        while len(self._first_low) > self._config.candidate_capacity:
            self._first_low.popitem(last=False)

    def _forward(
        self,
        identity: OwnerVoiceCandidateIdentity,
        audio_ms: int,
        reason: str,
    ) -> OwnerVoiceDecisionRecord:
        self._forward_count += 1
        return OwnerVoiceDecisionRecord(
            identity=identity,
            decision=OwnerVoiceBetaDecision.FORWARD,
            config_version=self._config.version,
            observed_audio_ms=max(0, int(audio_ms or 0)),
            reason=reason,
        )


__all__ = [
    "OwnerCandidateScope",
    "OwnerVoiceBetaConfig",
    "OwnerVoiceBetaDecision",
    "OwnerVoiceBetaPolicy",
    "OwnerVoiceCandidateIdentity",
    "OwnerVoiceDecisionRecord",
]
