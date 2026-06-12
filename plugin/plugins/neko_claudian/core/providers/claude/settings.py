# Ported from claudian/src/providers/claude/settings.ts
# Original author: Claudian contributors
# License: MIT

"""
Claude provider settings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ClaudeSettings:
    """Claude-specific settings."""
    model: str = "claude-sonnet-4-20250514"
    effort_level: str = "high"
    service_tier: str = "auto"
    thinking_budget: str = "10000"
    permission_mode: str = "normal"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "effortLevel": self.effort_level,
            "serviceTier": self.service_tier,
            "thinkingBudget": self.thinking_budget,
            "permissionMode": self.permission_mode,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ClaudeSettings:
        return cls(
            model=data.get("model", "claude-sonnet-4-20250514"),
            effort_level=data.get("effortLevel", "high"),
            service_tier=data.get("serviceTier", "auto"),
            thinking_budget=data.get("thinkingBudget", "10000"),
            permission_mode=data.get("permissionMode", "normal"),
        )
