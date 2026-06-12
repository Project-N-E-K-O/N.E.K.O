# Ported from claudian/src/providers/claude/modelOptions.ts
# Original author: Claudian contributors
# License: MIT

"""
Claude model options.
"""

from __future__ import annotations

from typing import Any, Dict, List


# Available model options
MODEL_OPTIONS = [
    {"id": "claude-sonnet-4-20250514", "label": "Claude Sonnet 4", "default": True},
    {"id": "claude-opus-4-20250514", "label": "Claude Opus 4"},
    {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5"},
]

# Effort level options
EFFORT_OPTIONS = [
    {"id": "low", "label": "Low"},
    {"id": "medium", "label": "Medium"},
    {"id": "high", "label": "High", "default": True},
    {"id": "max", "label": "Max"},
]


def get_model_options() -> List[Dict[str, Any]]:
    """Get available model options."""
    return MODEL_OPTIONS.copy()


def get_effort_options() -> List[Dict[str, Any]]:
    """Get effort level options."""
    return EFFORT_OPTIONS.copy()
