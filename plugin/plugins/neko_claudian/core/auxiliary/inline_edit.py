# Ported from claudian/src/providers/claude/auxiliary/ClaudeInlineEditService.ts
# Original author: Claudian contributors
# License: MIT

"""
InlineEditService — Inline code editing using AI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class InlineEditRequest:
    """Request for inline edit."""
    file_path: str = ""
    selection: str = ""
    instruction: str = ""
    context: str = ""
    language: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filePath": self.file_path,
            "selection": self.selection,
            "instruction": self.instruction,
            "context": self.context,
            "language": self.language,
        }


@dataclass
class InlineEditResult:
    """Result of inline edit."""
    success: bool = False
    edited_text: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"success": self.success}
        if self.edited_text:
            out["editedText"] = self.edited_text
        if self.error:
            out["error"] = self.error
        return out


# System prompt for inline edit
INLINE_EDIT_SYSTEM_PROMPT = """You are an inline code editor. You will be given a code selection and an instruction.
Your task is to edit the code according to the instruction.

Rules:
- Output ONLY the edited code
- No explanations or markdown
- Preserve indentation and formatting
- If the instruction is unclear, make your best judgment"""


def build_inline_edit_prompt(request: InlineEditRequest) -> str:
    """Build the prompt for inline edit."""
    parts = []
    if request.file_path:
        parts.append(f"File: {request.file_path}")
    if request.language:
        parts.append(f"Language: {request.language}")
    if request.context:
        parts.append(f"\nContext:\n```\n{request.context}\n```")
    parts.append(f"\nSelection to edit:\n```\n{request.selection}\n```")
    parts.append(f"\nInstruction: {request.instruction}")
    return "\n".join(parts)


def parse_inline_edit_response(response: str) -> InlineEditResult:
    """Parse the inline edit response."""
    if not response or not response.strip():
        return InlineEditResult(success=False, error="Empty response")

    # Remove markdown code blocks if present
    text = response.strip()
    if text.startswith("```"):
        # Find the end of the opening code block
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        # Remove closing code block
        if text.endswith("```"):
            text = text[:-3].rstrip()

    return InlineEditResult(success=True, edited_text=text)


class InlineEditService:
    """Inline code editing using AI.

    Ported from ClaudeInlineEditService.ts
    """

    def __init__(self, get_agent_service: Callable[[], Any]):
        self._get_agent_service = get_agent_service
        self._session_id: Optional[str] = None

    def reset_conversation(self) -> None:
        """Reset the conversation state."""
        self._session_id = None

    async def edit_text(self, request: InlineEditRequest) -> InlineEditResult:
        """Edit text inline.

        Args:
            request: The edit request

        Returns:
            InlineEditResult with the edited text
        """
        self._session_id = None
        prompt = build_inline_edit_prompt(request)
        return await self._send_message(prompt)

    async def continue_conversation(self, message: str) -> InlineEditResult:
        """Continue an existing conversation.

        Args:
            message: Follow-up message

        Returns:
            InlineEditResult
        """
        if not self._session_id:
            return InlineEditResult(success=False, error="No active conversation")

        return await self._send_message(message)

    async def _send_message(self, prompt: str) -> InlineEditResult:
        """Send a message to the AI.

        Args:
            prompt: The prompt to send

        Returns:
            InlineEditResult
        """
        agent_service = self._get_agent_service()
        if not agent_service:
            return InlineEditResult(success=False, error="Agent service not available")

        try:
            # Use cold start query for inline edit
            # For now, return a placeholder
            return InlineEditResult(
                success=False,
                error="Inline edit not yet implemented",
            )
        except Exception as e:
            return InlineEditResult(success=False, error=str(e))

    def cancel(self) -> None:
        """Cancel the current operation."""
        pass
