"""Build theater system messages that carry the N.E.K.O prompt watermark."""

from __future__ import annotations

from config.prompts.prompts_theater import THEATER_SYSTEM_PROMPT_WATERMARK
from utils.llm_client import SystemMessage


def unmark_theater_system_prompt(content: str) -> str:
    """Return the prompt text without the watermark, for code that edits it."""
    return str(content).replace(THEATER_SYSTEM_PROMPT_WATERMARK, "")


def mark_theater_system_prompt(content: str) -> str:
    """Return the prompt with exactly one trailing watermark.

    Repair and discovery requests extend an earlier system prompt, so a mark
    already inside the text is moved to the end instead of repeated.
    """
    return unmark_theater_system_prompt(content).rstrip() + THEATER_SYSTEM_PROMPT_WATERMARK


def theater_system_message(content: str) -> SystemMessage:
    """Build the system message every theater model request starts with."""
    return SystemMessage(content=mark_theater_system_prompt(content))
