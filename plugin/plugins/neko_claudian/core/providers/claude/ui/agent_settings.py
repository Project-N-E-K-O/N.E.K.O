# Ported from claudian/src/providers/claude/ui/AgentSettings.ts
# Original author: Claudian contributors
# License: MIT

"""
Agent settings UI helpers.
"""

from __future__ import annotations

from typing import Any, Dict, List


def get_agent_settings_config() -> Dict[str, Any]:
    """Get agent settings configuration."""
    return {
        "fields": [
            {"name": "model", "type": "select", "label": "Model"},
            {"name": "effortLevel", "type": "select", "label": "Effort Level"},
            {"name": "permissionMode", "type": "select", "label": "Permission Mode"},
        ]
    }
