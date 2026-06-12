# Ported from claudian/src/providers/claude/security/ClaudePermissionUpdates.ts
# Original author: Claudian contributors
# License: MIT

"""
Claude-specific permission updates.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def build_claude_permission_updates(
    tool_name: str,
    input_data: Dict[str, Any],
    decision: str,
) -> List[Dict[str, Any]]:
    """Build permission updates for Claude SDK.

    Ported from ClaudePermissionUpdates.ts
    """
    updates = []

    if decision == "allow":
        # Single-use permission
        pass

    elif decision == "allow-always":
        # Add persistent allow rule
        updates.append({
            "type": "addRules",
            "rules": [{"tool": tool_name, "behavior": "allow"}],
            "destination": "session",
        })

    elif decision == "deny":
        # Add deny rule
        updates.append({
            "type": "addRules",
            "rules": [{"tool": tool_name, "behavior": "deny"}],
            "destination": "session",
        })

    return updates


def build_mode_update(mode: str) -> Dict[str, Any]:
    """Build a permission mode update."""
    return {
        "type": "setMode",
        "mode": mode,
        "destination": "session",
    }
