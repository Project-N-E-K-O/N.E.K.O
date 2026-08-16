"""Immutable fences for one fail-open ASR candidate rejection transaction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..endpointing.detector import DetectorCandidateKey


class CandidateRejectionOutcome(StrEnum):
    """Externally meaningful result of a candidate rejection attempt."""

    STALE = "stale"
    APPLIED = "applied"
    APPLIED_CLEANUP_DEGRADED = "applied_cleanup_degraded"


@dataclass(frozen=True, slots=True)
class CandidateRejectionRequest:
    """Complete runtime and activation identity for one exact candidate."""

    session_epoch: int
    audio_generation: int
    transport_generation: int
    turn_id: int
    candidate: DetectorCandidateKey
    activation_generation: str

    def __post_init__(self) -> None:
        for name in (
            "session_epoch",
            "audio_generation",
            "transport_generation",
            "turn_id",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if type(self.candidate) is not DetectorCandidateKey:
            raise TypeError("candidate must be DetectorCandidateKey")
        if (
            type(self.activation_generation) is not str
            or not self.activation_generation.strip()
        ):
            raise ValueError("activation_generation must be a non-empty string")


__all__ = ["CandidateRejectionOutcome", "CandidateRejectionRequest"]
