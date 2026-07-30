"""Provider-neutral voice turn detection primitives.

This package deliberately has no ASR provider, Core, or Omni dependencies.
Smart Turn is an optional semantic endpoint helper for ASR providers that do
not already expose an authoritative semantic endpoint.
"""

from .audio_buffer import Pcm16RingBuffer
from .contracts import (
    AsrTurnCapabilities,
    EvaluationStatus,
    SmartTurnConfig,
    SpeechActivityEvent,
    TurnDecision,
    TurnDetector,
    TurnEvaluation,
    build_turn_detector_if_required,
    requires_external_turn_detector,
)

__all__ = [
    "AsrTurnCapabilities",
    "EvaluationStatus",
    "Pcm16RingBuffer",
    "SmartTurnConfig",
    "SpeechActivityEvent",
    "TurnDecision",
    "TurnDetector",
    "TurnEvaluation",
    "build_turn_detector_if_required",
    "requires_external_turn_detector",
]
