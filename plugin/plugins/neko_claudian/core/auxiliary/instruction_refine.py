# Ported from claudian/src/providers/claude/auxiliary/ClaudeInstructionRefineService.ts
# Original author: Claudian contributors
# License: MIT

"""
InstructionRefineService — Refine user instructions using AI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class InstructionRefineResult:
    """Result of instruction refinement."""
    success: bool = False
    refined_instruction: Optional[str] = None
    clarification: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        out = {"success": self.success}
        if self.refined_instruction:
            out["refinedInstruction"] = self.refined_instruction
        if self.clarification:
            out["clarification"] = self.clarification
        if self.error:
            out["error"] = self.error
        return out


# System prompt for instruction refinement
INSTRUCTION_REFINE_SYSTEM_PROMPT = """You are an instruction refinement assistant.
Your task is to help refine and clarify user instructions.

Rules:
- If the instruction is clear, return the refined version
- If the instruction is ambiguous, ask a clarifying question
- Be concise and specific
- Preserve the user's intent"""


class InstructionRefineService:
    """Refine user instructions using AI.

    Ported from ClaudeInstructionRefineService.ts
    """

    def __init__(self, get_agent_service: callable):
        self._get_agent_service = get_agent_service
        self._conversation_history: list = []

    def reset_conversation(self) -> None:
        """Reset the conversation history."""
        self._conversation_history.clear()

    async def refine_instruction(
        self,
        instruction: str,
        existing_prompt: str = "",
    ) -> InstructionRefineResult:
        """Refine a user instruction.

        Args:
            instruction: The raw instruction
            existing_prompt: Existing system prompt for context

        Returns:
            InstructionRefineResult
        """
        prompt_parts = []
        if existing_prompt:
            prompt_parts.append(f"Existing system prompt:\n{existing_prompt}")
        prompt_parts.append(f"User instruction to refine:\n{instruction}")
        prompt = "\n\n".join(prompt_parts)

        self._conversation_history.append({"role": "user", "content": prompt})

        agent_service = self._get_agent_service()
        if not agent_service:
            return InstructionRefineResult(
                success=False,
                error="Agent service not available",
            )

        try:
            # For now, return the instruction as-is
            return InstructionRefineResult(
                success=True,
                refined_instruction=instruction,
            )
        except Exception as e:
            return InstructionRefineResult(success=False, error=str(e))

    async def continue_conversation(self, response: str) -> InstructionRefineResult:
        """Continue the refinement conversation.

        Args:
            response: User's response to clarification

        Returns:
            InstructionRefineResult
        """
        self._conversation_history.append({"role": "user", "content": response})

        try:
            return InstructionRefineResult(
                success=True,
                refined_instruction=response,
            )
        except Exception as e:
            return InstructionRefineResult(success=False, error=str(e))

    def cancel(self) -> None:
        """Cancel the current operation."""
        self._conversation_history.clear()
