# Ported from claudian/src/core/prompt/inlineEdit.ts
# Original author: Claudian contributors
# License: MIT

"""
Inline edit prompts.
"""

from __future__ import annotations

INLINE_EDIT_SYSTEM_PROMPT = """You are an inline code editor. You will be given code and an instruction to edit it.

Rules:
- Output ONLY the edited code
- No explanations or markdown code blocks
- Preserve indentation and formatting
- Make minimal changes to satisfy the instruction
- If the instruction is ambiguous, make your best judgment"""


def get_inline_edit_system_prompt() -> str:
    """Get the inline edit system prompt."""
    return INLINE_EDIT_SYSTEM_PROMPT


def build_inline_edit_prompt(
    selection: str,
    instruction: str,
    context: str = "",
    language: str = "",
    file_path: str = "",
) -> str:
    """Build the inline edit prompt."""
    parts = []
    if file_path:
        parts.append(f"File: {file_path}")
    if language:
        parts.append(f"Language: {language}")
    if context:
        parts.append(f"\nContext:\n```\n{context}\n```")
    parts.append(f"\nSelection to edit:\n```\n{selection}\n```")
    parts.append(f"\nInstruction: {instruction}")
    return "\n".join(parts)


def parse_inline_edit_response(response: str) -> str:
    """Parse the inline edit response."""
    text = response.strip()

    # Remove markdown code blocks if present
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3].rstrip()

    return text
