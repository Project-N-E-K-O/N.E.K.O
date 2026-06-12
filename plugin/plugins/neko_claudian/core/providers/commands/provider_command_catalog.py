# Ported from claudian/src/core/providers/commands/ProviderCommandCatalog.ts
# Original author: Claudian contributors
# License: MIT

"""
Provider command catalog.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class ProviderCommandCatalog:
    """Catalog for provider commands.

    Ported from ProviderCommandCatalog.ts
    """

    def __init__(self):
        self._commands: List[Dict[str, Any]] = []
        self._runtime_commands: List[Dict[str, Any]] = []

    def set_runtime_commands(self, commands: List[Dict[str, Any]]) -> None:
        """Set runtime commands."""
        self._runtime_commands = commands

    def get_all_commands(self) -> List[Dict[str, Any]]:
        """Get all commands."""
        return self._commands + self._runtime_commands

    def get_dropdown_config(self) -> Dict[str, Any]:
        """Get dropdown configuration."""
        return {"commands": self.get_all_commands()}

    def list_dropdown_entries(self, include_builtins: bool = True) -> List[Dict[str, Any]]:
        """List entries for dropdown."""
        return [
            {
                "id": cmd.get("id", cmd.get("name", "")),
                "name": cmd.get("name", ""),
                "description": cmd.get("description", ""),
            }
            for cmd in self.get_all_commands()
        ]
