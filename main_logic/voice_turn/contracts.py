"""Stable contracts for provider-neutral semantic turn detection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from collections.abc import Callable
from typing import Protocol, runtime_checkable


class TurnDecision(Enum):
    """A semantic judgment only; timeout and provider commits live elsewhere."""

    INCOMPLETE = "incomplete"
    COMPLETE = "complete"


class EvaluationStatus(Enum):
    """Execution status kept separate from the semantic decision."""

    OK = "ok"
    UNAVAILABLE = "unavailable"
    ERROR = "error"
    STALE = "stale"


class SpeechActivityEvent(Enum):
    """Cheap VAD events; none of these commits an ASR turn."""

    NONE = "none"
    SPEECH_STARTED = "speech_started"
    CANDIDATE_PAUSE = "candidate_pause"
    SPEECH_RESUMED = "speech_resumed"


@dataclass(frozen=True, slots=True)
class TurnEvaluation:
    status: EvaluationStatus
    decision: TurnDecision | None
    probability: float | None
    generation: int
    activity_seq: int
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status is EvaluationStatus.OK:
            if self.decision is None or self.probability is None:
                raise ValueError("OK evaluations require a decision and probability")
            if not 0.0 <= self.probability <= 1.0:
                raise ValueError("probability must be within [0, 1]")
        elif self.decision is not None or self.probability is not None:
            raise ValueError("non-OK evaluations must not carry a semantic result")


@runtime_checkable
class TurnDetector(Protocol):
    """Contract consumed by the future ASR Controller."""

    async def on_speech_started(self) -> None: ...

    async def evaluate(self, audio_tail: bytes) -> TurnEvaluation: ...

    async def reset(self) -> None: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class AsrTurnCapabilities:
    """Only the capability needed to choose an endpoint authority."""

    semantic_endpoint: bool


def requires_external_turn_detector(capabilities: AsrTurnCapabilities) -> bool:
    """Return false for Soniox-like providers with authoritative endpoints."""

    return not capabilities.semantic_endpoint


def build_turn_detector_if_required(
    capabilities: AsrTurnCapabilities, factory: Callable[[], TurnDetector]
) -> TurnDetector | None:
    """Construct only for providers without an authoritative semantic endpoint."""

    if not requires_external_turn_detector(capabilities):
        return None
    return factory()


@dataclass(frozen=True, slots=True)
class SmartTurnConfig:
    """Internal defaults; this PR does not expose a user-facing setting."""

    enabled: bool = False
    evaluation_threshold: float = 0.5
    candidate_silence_ms: int = 300
    onset_probability: float = 0.5
    offset_probability: float = 0.35
    minimum_speech_ms: int = 200
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
