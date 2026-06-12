# Ported from claudian/src/providers/claude/auxiliary/ClaudeTitleGenerationService.ts
# Original author: Claudian contributors
# License: MIT

"""
Claude-specific title generation service.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from ....auxiliary.title_generation import TitleGenerationResult

logger = logging.getLogger(__name__)


class ClaudeTitleGenerationService:
    """Claude-specific title generation.

    Ported from ClaudeTitleGenerationService.ts
    """

    def __init__(self, get_agent_service: Callable[[], Any]):
        self._get_agent_service = get_agent_service

    async def generate_title(
        self,
        conversation_id: str,
        user_message: str,
        callback: Callable,
    ) -> None:
        """Generate a title for a conversation."""
        from ....auxiliary.title_generation import TitleGenerationService

        service = TitleGenerationService(self._get_agent_service)
        await service.generate_title(conversation_id, user_message, callback)

    def cancel(self) -> None:
        """Cancel active generations."""
        pass
