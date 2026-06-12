# Ported from claudian/src/core/prompt/titleGeneration.ts
# Original author: Claudian contributors
# License: MIT

"""
Title generation prompts.
"""

from __future__ import annotations

TITLE_GENERATION_SYSTEM_PROMPT = """You are a title generator. Generate a short, descriptive title for the given conversation.

Rules:
- Maximum 50 characters
- No quotes or special characters
- Be concise and descriptive
- Output ONLY the title, nothing else"""


def get_title_generation_system_prompt() -> str:
    """Get the title generation system prompt."""
    return TITLE_GENERATION_SYSTEM_PROMPT


def build_title_generation_prompt(user_message: str) -> str:
    """Build the title generation prompt."""
    truncated = user_message[:500] if len(user_message) > 500 else user_message
    return f'User\'s request:\n"""\n{truncated}\n"""\n\nGenerate a title for this conversation:'


def parse_title_response(response: str) -> str | None:
    """Parse the title from the response."""
    trimmed = response.strip()
    if not trimmed:
        return None

    # Remove quotes
    title = trimmed
    if (title.startswith('"') and title.endswith('"')) or \
       (title.startswith("'") and title.endswith("'")):
        title = title[1:-1]

    # Remove trailing punctuation
    import re
    title = re.sub(r'[.!?:;,]+$', '', title)

    # Truncate
    if len(title) > 50:
        title = title[:47] + "..."

    return title if title else None
