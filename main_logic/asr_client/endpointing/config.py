"""Configuration for the local Silero and Smart Turn endpointing runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class SmartTurnConfig:
    """Internal defaults; this change does not expose a user-facing setting."""

    enabled: bool = False
    evaluation_threshold: float = 0.5
    # Slightly stricter than classic defaults: ignore brief clicks/noise and
    # only open a turn after sustained speech. Override via NEKO_VAD_*.
    candidate_silence_ms: int = 400
    onset_probability: float = 0.55
    offset_probability: float = 0.35
    minimum_speech_ms: int = 350
    max_audio_seconds: int = 8
    inference_error_limit: int = 3

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


def speech_gate_smart_turn_config(*, enabled: bool = True) -> SmartTurnConfig:
    """Build SmartTurnConfig tuned for 'only recognize real speech'."""

    onset = _env_float("NEKO_VAD_ONSET", 0.55)
    offset = _env_float("NEKO_VAD_OFFSET", 0.35)
    if offset >= onset:
        offset = max(0.0, onset - 0.15)
    return SmartTurnConfig(
        enabled=enabled,
        evaluation_threshold=_env_float("NEKO_VAD_EVAL_THRESHOLD", 0.5),
        candidate_silence_ms=_env_int("NEKO_VAD_SILENCE_MS", 400),
        onset_probability=onset,
        offset_probability=offset,
        minimum_speech_ms=_env_int("NEKO_VAD_MIN_SPEECH_MS", 350),
        max_audio_seconds=_env_int("NEKO_VAD_MAX_AUDIO_SECONDS", 8),
    )
