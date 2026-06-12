# Ported from claudian/src/providers/claude/capabilities.ts
# Original author: Claudian contributors
# License: MIT

"""
Claude provider capabilities.
"""

from __future__ import annotations

from typing import Any, Dict


# Claude capabilities
CLAUDE_CAPABILITIES = {
    "supportsStreaming": True,
    "supportsTools": True,
    "supportsImages": True,
    "supportsThinking": True,
    "supportsMcp": True,
    "supportsRewind": True,
    "supportsPlanMode": True,
    "supportsFork": True,
    "supportsNativeHistory": True,
    "supportsProviderCommands": True,
    "supportsTurnSteer": True,
    "supportsSubagents": True,
    "maxContextTokens": 200000,
    "supportedModels": [
        "claude-sonnet-4-20250514",
        "claude-opus-4-20250514",
        "claude-haiku-4-5-20251001",
    ],
}


def get_claude_capabilities() -> Dict[str, Any]:
    """Get Claude provider capabilities."""
    return CLAUDE_CAPABILITIES.copy()
