# Ported from claudian/src/utils/context.ts
# Original author: Claudian contributors
# License: MIT

"""
Context utilities.
"""

from __future__ import annotations

from typing import List, Optional


def extract_user_display_content(content: str) -> Optional[str]:
    """Extract display content from user message."""
    if not content:
        return None

    # Remove system prompt prefix if present
    if content.startswith("[System:"):
        end = content.find("]")
        if end != -1:
            content = content[end + 1:].strip()

    return content if content else None


def append_context_files(text: str, files: List[str]) -> str:
    """Append context files to text."""
    if not files:
        return text

    parts = [text]
    for file_path in files:
        parts.append(f"\n\n@{file_path}")

    return "".join(parts)
