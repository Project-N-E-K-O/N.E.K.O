# Ported from claudian/src/utils/markdown.ts
# Original author: Claudian contributors
# License: MIT

"""
Markdown utilities.
"""

from __future__ import annotations

import re


def append_markdown_snippet(existing: str, snippet: str) -> str:
    """Append a markdown snippet to existing text."""
    if not existing:
        return snippet
    if not snippet:
        return existing

    # Add newline separator if needed
    if not existing.endswith("\n"):
        existing += "\n"
    return existing + snippet


def extract_code_blocks(text: str) -> list[str]:
    """Extract code blocks from markdown."""
    pattern = r"```(?:\w+)?\n(.*?)```"
    return re.findall(pattern, text, re.DOTALL)


def strip_markdown(text: str) -> str:
    """Strip markdown formatting."""
    # Remove code blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Remove inline code
    text = re.sub(r"`[^`]+`", "", text)
    # Remove headers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove bold/italic
    text = re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", text)
    return text.strip()
