"""Configuration for the local Silero and Smart Turn endpointing runtime."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SmartTurnConfig:
    """Internal defaults; this change does not expose a user-facing setting."""

    enabled: bool = False
    evaluation_threshold: float = 0.5
    candidate_silence_ms: int = 300
    onset_probability: float = 0.5
    offset_probability: float = 0.35
    minimum_speech_ms: int = 200
    max_audio_seconds: int = 8
    inference_error_limit: int = 3
    candidate_complete_confirmation_seconds: float = 1.0

    def __post_init__(self) -> None:
        for name in ("evaluation_threshold", "onset_probability", "offset_probability"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be within [0, 1]")
        if self.offset_probability >= self.onset_probability:
            raise ValueError("offset_probability must be below onset_probability")
        if self.candidate_silence_ms <= 0 or self.minimum_speech_ms <= 0:
            raise ValueError("speech and silence durations must be positive")
        if self.max_audio_seconds <= 0:
            raise ValueError("max_audio_seconds must be positive")
        if self.inference_error_limit <= 0:
            raise ValueError("inference_error_limit must be positive")
        if (
            not math.isfinite(self.candidate_complete_confirmation_seconds)
            or self.candidate_complete_confirmation_seconds < 0
        ):
            raise ValueError(
                "candidate_complete_confirmation_seconds must be finite and non-negative"
            )
