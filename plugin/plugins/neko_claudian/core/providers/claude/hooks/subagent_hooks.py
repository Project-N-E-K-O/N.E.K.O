# Ported from claudian/src/providers/claude/hooks/SubagentHooks.ts
# Original author: Claudian contributors
# License: MIT

"""
Subagent hooks for Claude provider.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class SubagentHooks:
    """Hooks for subagent lifecycle events.

    Ported from providers/claude/hooks/SubagentHooks.ts
    """

    def __init__(self):
        self._on_spawn: Optional[Callable] = None
        self._on_complete: Optional[Callable] = None
        self._on_error: Optional[Callable] = None

    def set_on_spawn(self, callback: Callable) -> None:
        """Set the spawn callback."""
        self._on_spawn = callback

    def set_on_complete(self, callback: Callable) -> None:
        """Set the complete callback."""
        self._on_complete = callback

    def set_on_error(self, callback: Callable) -> None:
        """Set the error callback."""
        self._on_error = callback

    async def handle_spawn(self, agent_id: str, input_data: Dict[str, Any]) -> None:
        """Handle a subagent spawn event."""
        if self._on_spawn:
            await self._on_spawn(agent_id, input_data)

    async def handle_complete(self, agent_id: str, result: Any) -> None:
        """Handle a subagent complete event."""
        if self._on_complete:
            await self._on_complete(agent_id, result)

    async def handle_error(self, agent_id: str, error: Exception) -> None:
        """Handle a subagent error event."""
        if self._on_error:
            await self._on_error(agent_id, error)
