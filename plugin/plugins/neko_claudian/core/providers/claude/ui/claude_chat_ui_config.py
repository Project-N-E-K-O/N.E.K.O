# Ported from claudian/src/providers/claude/ui/ClaudeChatUIConfig.ts
# Original author: Claudian contributors
# License: MIT

"""
Claude chat UI configuration.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ClaudeChatUIConfig:
    """Claude chat UI configuration.

    Ported from ClaudeChatUIConfig.ts
    """

    @classmethod
    def owns_model(cls, model: str, settings: Dict[str, Any]) -> bool:
        """Check if a model belongs to Claude provider."""
        claude_models = [
            "claude-sonnet-4-20250514",
            "claude-opus-4-20250514",
            "claude-haiku-4-5-20251001",
        ]
        return model in claude_models or model.startswith("claude-")
