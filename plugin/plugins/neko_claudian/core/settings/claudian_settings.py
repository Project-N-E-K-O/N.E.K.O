# Ported from claudian/src/core/settings/ClaudianSettings.ts
# Original author: Claudian contributors
# License: MIT

"""
Claudian settings management.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ClaudianSettings:
    """Application settings.

    Ported from ClaudianSettings.ts
    """
    # User preferences
    user_name: str = ""

    # Security
    permission_mode: str = "normal"  # "yolo" | "plan" | "normal"

    # Model & thinking
    model: str = "claude-sonnet-4-20250514"
    thinking_budget: str = "10000"
    effort_level: str = "high"
    service_tier: str = "auto"
    enable_auto_title_generation: bool = True
    title_generation_model: str = ""

    # Content settings
    system_prompt: str = ""
    persistent_external_context_paths: List[str] = field(default_factory=list)

    # UI settings
    locale: str = "en"
    max_tabs: int = 5
    enable_auto_scroll: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "userName": self.user_name,
            "permissionMode": self.permission_mode,
            "model": self.model,
            "thinkingBudget": self.thinking_budget,
            "effortLevel": self.effort_level,
            "serviceTier": self.service_tier,
            "enableAutoTitleGeneration": self.enable_auto_title_generation,
            "titleGenerationModel": self.title_generation_model,
            "systemPrompt": self.system_prompt,
            "persistentExternalContextPaths": self.persistent_external_context_paths,
            "locale": self.locale,
            "maxTabs": self.max_tabs,
            "enableAutoScroll": self.enable_auto_scroll,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ClaudianSettings:
        return cls(
            user_name=data.get("userName", ""),
            permission_mode=data.get("permissionMode", "normal"),
            model=data.get("model", "claude-sonnet-4-20250514"),
            thinking_budget=data.get("thinkingBudget", "10000"),
            effort_level=data.get("effortLevel", "high"),
            service_tier=data.get("serviceTier", "auto"),
            enable_auto_title_generation=data.get("enableAutoTitleGeneration", True),
            title_generation_model=data.get("titleGenerationModel", ""),
            system_prompt=data.get("systemPrompt", ""),
            persistent_external_context_paths=data.get("persistentExternalContextPaths", []),
            locale=data.get("locale", "en"),
            max_tabs=data.get("maxTabs", 5),
            enable_auto_scroll=data.get("enableAutoScroll", True),
        )
