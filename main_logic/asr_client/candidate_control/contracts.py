"""Immutable fences for an asynchronous candidate rejection transaction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..endpointing.detector import DetectorCandidateKey


class CandidateRejectionOutcome(Enum):
    """Result of a fail-open candidate rejection attempt."""

    APPLIED = "applied"
    STALE = "stale"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CandidateRejectionRequest:
    """Full runtime fence for one detector-authoritative candidate."""

    session_epoch: int
    audio_generation: int
    transport_generation: int
    turn_id: int
    candidate: DetectorCandidateKey
    profile_generation: str
    filter_generation: str

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
        for name in ("profile_generation", "filter_generation"):
            value = getattr(self, name)
            if type(value) is not str or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
