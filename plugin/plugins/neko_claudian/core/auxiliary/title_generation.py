# Ported from claudian/src/providers/claude/auxiliary/ClaudeTitleGenerationService.ts
# Original author: Claudian contributors
# License: MIT

"""
TitleGenerationService — Generate conversation titles using AI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class TitleGenerationResult:
    """Result of title generation."""
    success: bool = False
    title: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"success": self.success}
        if self.title:
            out["title"] = self.title
        if self.error:
            out["error"] = self.error
        return out


# Callback type
TitleGenerationCallback = Callable[[str, TitleGenerationResult], Awaitable[None]]

# System prompt for title generation
TITLE_GENERATION_SYSTEM_PROMPT = """You are a title generator. Generate a short, descriptive title for the given conversation.
Rules:
- Maximum 50 characters
- No quotes or special characters
- Be concise and descriptive
- Output ONLY the title, nothing else"""


class TitleGenerationService:
    """Generate conversation titles using AI.

    Ported from ClaudeTitleGenerationService.ts
    """

    def __init__(self, get_agent_service: Callable[[], Any]):
        self._get_agent_service = get_agent_service
        self._active_generations: Dict[str, bool] = {}

    async def generate_title(
        self,
        conversation_id: str,
        user_message: str,
        callback: TitleGenerationCallback,
    ) -> None:
        """Generate a title for a conversation.

        Args:
            conversation_id: ID of the conversation
            user_message: First user message
            callback: Callback with the result
        """
        # Cancel any existing generation
        self._active_generations[conversation_id] = True

        truncated = self._truncate_text(user_message, 500)
        prompt = f'User\'s request:\n"""\n{truncated}\n"""\n\nGenerate a title for this conversation:'

        try:
            # Use cold start query for title generation
            agent_service = self._get_agent_service()
            if not agent_service:
                await self._safe_callback(callback, conversation_id, TitleGenerationResult(
                    success=False,
                    error="Agent service not available",
                ))
                return

            # For now, use a simple title extraction
            title = self._extract_simple_title(user_message)

            if title:
                await self._safe_callback(callback, conversation_id, TitleGenerationResult(
                    success=True,
                    title=title,
                ))
            else:
                await self._safe_callback(callback, conversation_id, TitleGenerationResult(
                    success=False,
                    error="Failed to generate title",
                ))

        except Exception as e:
            await self._safe_callback(callback, conversation_id, TitleGenerationResult(
                success=False,
                error=str(e),
            ))
        finally:
            self._active_generations.pop(conversation_id, None)

    def cancel(self) -> None:
        """Cancel all active generations."""
        self._active_generations.clear()

    def _extract_simple_title(self, text: str) -> Optional[str]:
        """Extract a simple title from text."""
        # Take first line or first 50 chars
        first_line = text.split("\n")[0].strip()
        if not first_line:
            return None

        # Remove common prefixes
        for prefix in ["#", "##", "###", ">", "-"]:
            if first_line.startswith(prefix):
                first_line = first_line[len(prefix):].strip()

        # Truncate
        if len(first_line) > 50:
            first_line = first_line[:47] + "..."

        return first_line if first_line else None

    def _truncate_text(self, text: str, max_length: int) -> str:
        """Truncate text to max length."""
        if len(text) <= max_length:
            return text
        return text[:max_length] + "..."

    async def _safe_callback(
        self,
        callback: TitleGenerationCallback,
        conversation_id: str,
        result: TitleGenerationResult,
    ) -> None:
        """Safely call callback, ignoring errors."""
        try:
            await callback(conversation_id, result)
        except Exception:
            pass
