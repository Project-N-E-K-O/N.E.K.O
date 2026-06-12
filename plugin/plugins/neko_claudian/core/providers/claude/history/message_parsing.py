# Ported from claudian/src/providers/claude/history/sdkMessageParsing.ts
# Original author: Claudian contributors
# License: MIT

"""
Message parsing utilities for Claude history.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def parse_sdk_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """Parse an SDK message into a normalized format."""
    role = message.get("role", "user")
    content = message.get("content", "")

    if isinstance(content, str):
        return {"role": role, "content": content}

    if isinstance(content, list):
        # Extract text from content blocks
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    text_parts.append(f"[Tool: {block.get('name', 'unknown')}]")

        return {"role": role, "content": "\n".join(text_parts)}

    return {"role": role, "content": str(content)}


def extract_agent_id_from_tool_use_result(result: Any) -> Optional[str]:
    """Extract agent ID from a tool use result."""
    if not isinstance(result, dict):
        return None

    # Check direct fields
    for key in ("agent_id", "agentId"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value

    # Check in content
    content = result.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                text = block.get("text", "")
                if "agent_id" in text or "agentId" in text:
                    # Try to extract from JSON-like text
                    import re
                    match = re.search(r'"agent_?[Ii]d"\s*:\s*"([^"]+)"', text)
                    if match:
                        return match.group(1)

    return None


def resolve_tool_use_result_status(
    result: Any,
    fallback: str = "completed",
) -> str:
    """Resolve the status from a tool use result."""
    if not isinstance(result, dict):
        return fallback

    status = result.get("retrieval_status") or result.get("status")
    if isinstance(status, str):
        if status.lower() == "error":
            return "error"
        if status.lower() in ("completed", "success"):
            return "completed"

    return fallback


def extract_xml_tag(payload: str, tag_name: str) -> Optional[str]:
    """Extract content of an XML tag from payload."""
    import re
    pattern = rf"<{re.escape(tag_name)}>(.*?)</{re.escape(tag_name)}>"
    match = re.search(pattern, payload, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None
