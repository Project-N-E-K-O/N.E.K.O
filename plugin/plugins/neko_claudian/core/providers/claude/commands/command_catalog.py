# Ported from claudian/src/providers/claude/commands/ClaudeCommandCatalog.ts
# Original author: Claudian contributors
# License: MIT

"""
Command catalog for Claude provider.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ClaudeCommandCatalog:
    """Command catalog for Claude provider.

    Ported from providers/claude/commands/ClaudeCommandCatalog.ts
    """

    def __init__(self):
        self._commands: List[Dict[str, Any]] = []
        self._runtime_commands: List[Dict[str, Any]] = []

    def set_runtime_commands(self, commands: List[Dict[str, Any]]) -> None:
        """Set runtime commands from SDK."""
        self._runtime_commands = commands

    def get_runtime_commands(self) -> List[Dict[str, Any]]:
        """Get runtime commands."""
        return self._runtime_commands

    def get_all_commands(self) -> List[Dict[str, Any]]:
        """Get all commands (static + runtime)."""
        return self._commands + self._runtime_commands

    def get_command(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a command by name."""
        for cmd in self.get_all_commands():
            if cmd.get("name") == name:
                return cmd
        return None

    def get_dropdown_config(self) -> Dict[str, Any]:
        """Get dropdown configuration."""
        return {
            "commands": self.get_all_commands(),
        }

    def list_dropdown_entries(self, include_builtins: bool = True) -> List[Dict[str, Any]]:
        """List entries for dropdown display."""
        entries = []
        for cmd in self.get_all_commands():
            if not include_builtins and cmd.get("source") == "builtin":
                continue
            entries.append({
                "id": cmd.get("id", cmd.get("name", "")),
                "name": cmd.get("name", ""),
                "description": cmd.get("description", ""),
                "content": cmd.get("content", ""),
            })
        return entries
