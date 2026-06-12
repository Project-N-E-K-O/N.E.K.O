# Ported from claudian/src/utils/mcp.ts
# Original author: Claudian contributors
# License: MIT

"""
MCP utilities.
"""

from __future__ import annotations

import re
from typing import Set


def extract_mcp_mentions(text: str, server_names: Set[str]) -> Set[str]:
    """Extract @mentions for MCP servers from text."""
    mentions: Set[str] = set()

    for name in server_names:
        pattern = rf"@{re.escape(name)}\b"
        if re.search(pattern, text):
            mentions.add(name)

    return mentions


def transform_mcp_mentions(text: str, server_names: Set[str]) -> str:
    """Append ' MCP' after each valid @mention."""
    result = text
    for name in server_names:
        pattern = rf"@{re.escape(name)}\b"
        result = re.sub(pattern, f"@{name} MCP", result)
    return result
