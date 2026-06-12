# Ported from claudian/src/core/providers/modelRouting.ts
# Original author: Claudian contributors
# License: MIT

"""
Model routing — Route queries to appropriate models.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


# Default model aliases
MODEL_ALIASES = {
    "sonnet": "claude-sonnet-4-20250514",
    "opus": "claude-opus-4-20250514",
    "haiku": "claude-haiku-4-5-20251001",
}


def resolve_model(model: str, settings: Optional[Dict[str, Any]] = None) -> str:
    """Resolve a model name to its full form."""
    # Check aliases first
    if model in MODEL_ALIASES:
        return MODEL_ALIASES[model]

    # Check custom aliases from settings
    if settings:
        custom_aliases = settings.get("customModelAliases", {})
        if model in custom_aliases:
            return custom_aliases[model]

    return model


def get_default_model() -> str:
    """Get the default model."""
    return "claude-sonnet-4-20250514"
