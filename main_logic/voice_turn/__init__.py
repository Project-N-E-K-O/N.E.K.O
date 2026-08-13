"""Provider-neutral voice input and turn-detection contracts."""

from .contracts import (
    AsrTurnCapabilities,
    EvaluationStatus,
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
    "SpeechActivityEvent",
    "TurnDecision",
    "TurnDetector",
    "TurnEvaluation",
    "build_turn_detector_if_required",
    "requires_external_turn_detector",
]
