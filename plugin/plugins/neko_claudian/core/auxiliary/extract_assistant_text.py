# Ported from claudian/src/providers/claude/auxiliary/extractAssistantText.ts
# Original author: Claudian contributors
# License: MIT

"""
Extract assistant text from messages.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def extract_assistant_text(messages: List[Dict[str, Any]]) -> Optional[str]:
    """Extract the last assistant text from messages.

    Ported from extractAssistantText.ts
    """
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue

        # Check content field
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

        # Check content blocks
        content_blocks = msg.get("contentBlocks") or msg.get("content_blocks")
        if isinstance(content_blocks, list):
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("content")
                    if isinstance(text, str) and text.strip():
                        return text.strip()

    return None


def extract_all_assistant_text(messages: List[Dict[str, Any]]) -> List[str]:
    """Extract all assistant text from messages."""
    texts = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue

        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            texts.append(content.strip())

    return texts
