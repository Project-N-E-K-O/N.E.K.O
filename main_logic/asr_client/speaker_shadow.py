"""ASR-private candidate identity for provider-neutral speaker verification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class SpeakerShadowCandidateKey:
    """Identity private to Independent ASR candidate observation."""

    detector_epoch: int
    shadow_generation: int
    scope: Literal["provider_pause", "smart_turn_turn"]
    candidate_generation: int = 0


__all__ = ["SpeakerShadowCandidateKey"]
