# Ported from claudian/src/providers/claude/modelLabels.ts
# Original author: Claudian contributors
# License: MIT

"""
Claude model labels.
"""

from __future__ import annotations

from typing import Dict


# Model display labels
MODEL_LABELS: Dict[str, str] = {
    "claude-sonnet-4-20250514": "Claude Sonnet 4",
    "claude-opus-4-20250514": "Claude Opus 4",
    "claude-haiku-4-5-20251001": "Claude Haiku 4.5",
}


def get_model_label(model: str) -> str:
    """Get display label for a model."""
    return MODEL_LABELS.get(model, model)
