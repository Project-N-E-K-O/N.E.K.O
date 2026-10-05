"""Canonical SmartTurn evaluation and completion reason contracts."""

from __future__ import annotations

from typing import Literal, TypeAlias


EvaluationReason: TypeAlias = Literal[
    "candidate_pause",
    "periodic_no_vad",
    "strict_retry",
]
CompletionReason: TypeAlias = EvaluationReason | Literal["semantic_timeout"]

EVALUATION_REASONS = frozenset(
    {"candidate_pause", "periodic_no_vad", "strict_retry"}
)
COMPLETE_REASONS = EVALUATION_REASONS | {"semantic_timeout"}
