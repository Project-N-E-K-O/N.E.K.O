# Ported from claudian/src/core/tools/toolResultContent.ts
# Original author: Claudian contributors
# License: MIT

"""
Tool result content extraction utilities.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def extract_tool_result_content(
    content: Any,
    fallback_indent: int = 2,
) -> str:
    """Extract string content from a tool result.

    Handles various formats:
    - Direct string
    - Content blocks array
    - Object with content/text fields

    Ported from toolResultContent.ts extractToolResultContent.
    """
    if content is None:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, (int, float, bool)):
        return str(content)

    if isinstance(content, list):
        # Content blocks array
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)

    if isinstance(content, dict):
        # Object with content/text fields
        text = content.get("text") or content.get("content")
        if isinstance(text, str):
            return text

        # Try to serialize
        try:
            return json.dumps(content, indent=fallback_indent)
        except (TypeError, ValueError):
            return str(content)

    return str(content)


def is_blocked_tool_result(content: str, is_error: bool = False) -> bool:
    """Check if a tool result indicates a blocked action.

    Ported from toolResultContent.ts isBlockedToolResult.
    """
    if is_error:
        return False

    if not content:
        return False

    blocked_indicators = [
        "Permission denied",
        "Access denied",
        "Not allowed",
        "Blocked",
        "User denied",
        "User cancelled",
    ]

    content_lower = content.lower()
    return any(indicator.lower() in content_lower for indicator in blocked_indicators)
