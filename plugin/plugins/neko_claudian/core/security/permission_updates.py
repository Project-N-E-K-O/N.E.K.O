# Ported from claudian/src/core/security/PermissionUpdates.ts
# Original author: Claudian contributors
# License: MIT

"""
PermissionUpdates — Build permission update payloads for SDK.

Handles building permission updates based on approval decisions.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def build_permission_updates(
    tool_name: str,
    input_data: Dict[str, Any],
    decision: str,
    suggestions: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Build permission update payloads based on approval decision.

    Ported from PermissionUpdates.ts buildPermissionUpdates.

    Args:
        tool_name: Name of the tool
        input_data: Tool input data
        decision: "allow", "allow-always", or "deny"
        suggestions: Optional suggestions from the SDK

    Returns:
        List of permission update payloads
    """
    updates: List[Dict[str, Any]] = []

    if decision == "allow":
        # Single-use permission - no persistent update needed
        pass

    elif decision == "allow-always":
        # Add a persistent allow rule
        updates.append({
            "type": "addRules",
            "rules": [
                {
                    "tool": tool_name,
                    "behavior": "allow",
                }
            ],
            "destination": "session",
        })

    elif decision == "deny":
        # Add a deny rule for this specific invocation
        updates.append({
            "type": "addRules",
            "rules": [
                {
                    "tool": tool_name,
                    "behavior": "deny",
                }
            ],
            "destination": "session",
        })

    # Process suggestions if provided
    if suggestions:
        for suggestion in suggestions:
            if suggestion.get("type") == "addRules":
                updates.append(suggestion)

    return updates


def build_mode_update(mode: str) -> Dict[str, Any]:
    """Build a permission mode update payload.

    Args:
        mode: Permission mode ("default", "acceptEdits", "bypassPermissions", "plan")

    Returns:
        Permission mode update payload
    """
    return {
        "type": "setMode",
        "mode": mode,
        "destination": "session",
    }
