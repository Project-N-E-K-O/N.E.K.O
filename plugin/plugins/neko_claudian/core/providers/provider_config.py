# Ported from claudian/src/core/providers/providerConfig.ts
# Original author: Claudian contributors
# License: MIT

"""
Provider configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ProviderConfig:
    """Provider configuration."""
    id: str = "claude"
    name: str = "Claude"
    enabled: bool = True
    model: str = "claude-sonnet-4-20250514"
    effort_level: str = "high"
    service_tier: str = "auto"
    thinking_budget: str = "10000"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "model": self.model,
            "effortLevel": self.effort_level,
            "serviceTier": self.service_tier,
            "thinkingBudget": self.thinking_budget,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ProviderConfig:
        return cls(
            id=data.get("id", "claude"),
            name=data.get("name", "Claude"),
            enabled=data.get("enabled", True),
            model=data.get("model", "claude-sonnet-4-20250514"),
            effort_level=data.get("effortLevel", "high"),
            service_tier=data.get("serviceTier", "auto"),
            thinking_budget=data.get("thinkingBudget", "10000"),
        )
