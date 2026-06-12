# Ported from claudian/src/providers/claude/auxiliary/ClaudeInlineEditService.ts
# Original author: Claudian contributors
# License: MIT

"""
Claude-specific inline edit service.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from ....auxiliary.inline_edit import InlineEditRequest, InlineEditResult

logger = logging.getLogger(__name__)


class ClaudeInlineEditService:
    """Claude-specific inline edit.

    Ported from ClaudeInlineEditService.ts
    """

    def __init__(self, get_agent_service: Callable[[], Any]):
        self._get_agent_service = get_agent_service

    async def edit_text(self, request: InlineEditRequest) -> InlineEditResult:
        """Edit text inline."""
        from ....auxiliary.inline_edit import InlineEditService

        service = InlineEditService(self._get_agent_service)
        return await service.edit_text(request)

    def reset_conversation(self) -> None:
        """Reset conversation state."""
        pass

    def cancel(self) -> None:
        """Cancel current operation."""
        pass
