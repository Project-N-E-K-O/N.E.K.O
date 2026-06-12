# Ported from claudian/src/core/providers/commands/hiddenCommands.ts
# Original author: Claudian contributors
# License: MIT

"""
Hidden commands management.
"""

from __future__ import annotations

from typing import Dict, List, Set


class HiddenCommands:
    """Manage hidden commands per provider.

    Ported from hiddenCommands.ts
    """

    def __init__(self):
        self._hidden: Dict[str, Set[str]] = {}

    def is_hidden(self, provider_id: str, command_id: str) -> bool:
        """Check if a command is hidden."""
        return command_id in self._hidden.get(provider_id, set())

    def hide(self, provider_id: str, command_id: str) -> None:
        """Hide a command."""
        if provider_id not in self._hidden:
            self._hidden[provider_id] = set()
        self._hidden[provider_id].add(command_id)

    def unhide(self, provider_id: str, command_id: str) -> None:
        """Unhide a command."""
        if provider_id in self._hidden:
            self._hidden[provider_id].discard(command_id)

    def get_hidden(self, provider_id: str) -> List[str]:
        """Get all hidden commands for a provider."""
        return list(self._hidden.get(provider_id, set()))

    def load(self, data: Dict[str, List[str]]) -> None:
        """Load hidden commands from data."""
        for provider_id, commands in data.items():
            self._hidden[provider_id] = set(commands)

    def save(self) -> Dict[str, List[str]]:
        """Save hidden commands to data."""
        return {k: list(v) for k, v in self._hidden.items()}
