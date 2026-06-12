# Ported from claudian/src/providers/claude/registration.ts
# Original author: Claudian contributors
# License: MIT

"""
Claude provider registration.
"""

from __future__ import annotations

from typing import Any, Dict

from ..registry import ProviderRegistry
from .capabilities import get_claude_capabilities


def register_claude_provider() -> None:
    """Register the Claude provider."""
    ProviderRegistry.register("claude", {
        "id": "claude",
        "name": "Claude",
        "capabilities": get_claude_capabilities(),
    })
