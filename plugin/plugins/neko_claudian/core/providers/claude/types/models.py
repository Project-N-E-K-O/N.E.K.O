# Ported from claudian/src/providers/claude/types/models.ts
# Original author: Claudian contributors
# License: MIT

"""
Claude model types.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class EffortLevel:
    """Effort level configuration."""
    level: str = "high"  # "low" | "medium" | "high" | "max"

    def resolve(self, model: str) -> str:
        """Resolve effort level for a model."""
        # Models that support effort levels
        if "sonnet" in model or "opus" in model:
            return self.level
        return "high"


def resolve_effort_level(model: str, settings_effort: Optional[str]) -> Optional[str]:
    """Resolve the effort level for a model."""
    if not settings_effort:
        return None

    # Models that support effort levels
    if "sonnet" in model or "opus" in model:
        return settings_effort

    return None
