# Ported from claudian/src/providers/claude/app/workspaceServices.ts
# Original author: Claudian contributors
# License: MIT

"""
Claude workspace services.
"""

from __future__ import annotations

from typing import Any, Optional


class ClaudeWorkspaceServices:
    """Claude workspace services.

    Ported from workspaceServices.ts
    """

    def __init__(self, workspace_path: str):
        self._workspace_path = workspace_path

    @property
    def workspace_path(self) -> str:
        return self._workspace_path

    def get_mcp_config_path(self) -> str:
        """Get MCP config file path."""
        import os
        return os.path.join(self._workspace_path, ".mcp.json")
