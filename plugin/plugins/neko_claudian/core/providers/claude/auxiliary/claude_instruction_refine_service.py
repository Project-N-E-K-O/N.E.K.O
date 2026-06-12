# Ported from claudian/src/providers/claude/auxiliary/ClaudeInstructionRefineService.ts
# Original author: Claudian contributors
# License: MIT

"""
Claude-specific instruction refine service.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from ....auxiliary.instruction_refine import InstructionRefineResult

logger = logging.getLogger(__name__)


class ClaudeInstructionRefineService:
    """Claude-specific instruction refinement.

    Ported from ClaudeInstructionRefineService.ts
    """

    def __init__(self, get_agent_service: Callable[[], Any]):
        self._get_agent_service = get_agent_service

    async def refine_instruction(
        self,
        instruction: str,
        existing_prompt: str = "",
    ) -> InstructionRefineResult:
        """Refine an instruction."""
        from ....auxiliary.instruction_refine import InstructionRefineService

        service = InstructionRefineService(self._get_agent_service)
        return await service.refine_instruction(instruction, existing_prompt)

    async def continue_conversation(self, response: str) -> InstructionRefineResult:
        """Continue refinement conversation."""
        from ....auxiliary.instruction_refine import InstructionRefineService

        service = InstructionRefineService(self._get_agent_service)
        return await service.continue_conversation(response)

    def reset_conversation(self) -> None:
        """Reset conversation."""
        pass

    def cancel(self) -> None:
        """Cancel current operation."""
        pass
