# Ported from claudian/src/core/mcp/McpConfigParser.ts
# Original author: Claudian contributors
# License: MIT

"""
McpConfigParser — Parse MCP server configurations from JSON.

Supports multiple formats for pasting/importing MCP configs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _is_valid_mcp_server_config(obj: Any) -> bool:
    """Check if an object is a valid MCP server config."""
    if not isinstance(obj, dict):
        return False

    # Check for stdio (command required)
    if obj.get("command") and isinstance(obj["command"], str):
        return True

    # Check for sse/http (url required)
    if obj.get("url") and isinstance(obj["url"], str):
        return True

    return False


@dataclass
class ParsedMcpConfig:
    """Result of parsing MCP config."""
    servers: List[Dict[str, Any]] = field(default_factory=list)
    needs_name: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "servers": self.servers,
            "needsName": self.needs_name,
        }


def parse_clipboard_config(json_str: str) -> ParsedMcpConfig:
    """Parse pasted JSON (supports multiple formats).

    Formats supported:
    1. Full Claude Code format: { "mcpServers": { "name": {...} } }
    2. Single server with name: { "name": { "command": "..." } }
    3. Single server without name: { "command": "..." }
    4. Multiple named servers: { "server1": {...}, "server2": {...} }

    Ported from McpConfigParser.ts parseClipboardConfig.
    """
    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError("Invalid JSON") from e

    if not isinstance(parsed, dict):
        raise ValueError("Invalid JSON object")

    # Format 1: Full Claude Code format
    # { "mcpServers": { "server-name": { "command": "...", ... } } }
    mcp_servers = parsed.get("mcpServers")
    if isinstance(mcp_servers, dict):
        servers = []
        for name, config in mcp_servers.items():
            if _is_valid_mcp_server_config(config):
                servers.append({"name": name, "config": config})

        if not servers:
            raise ValueError("No valid server configs found in mcpServers")

        return ParsedMcpConfig(servers=servers, needs_name=False)

    # Format 2: Single server config without name
    # { "command": "...", "args": [...] } or { "type": "sse", "url": "..." }
    if _is_valid_mcp_server_config(parsed):
        return ParsedMcpConfig(
            servers=[{"name": "", "config": parsed}],
            needs_name=True,
        )

    # Format 3: Single named server
    # { "server-name": { "command": "...", ... } }
    entries = list(parsed.items())
    if len(entries) == 1:
        name, config = entries[0]
        if _is_valid_mcp_server_config(config):
            return ParsedMcpConfig(
                servers=[{"name": name, "config": config}],
                needs_name=False,
            )

    # Format 4: Multiple named servers (without mcpServers wrapper)
    # { "server1": {...}, "server2": {...} }
    servers = []
    for name, config in entries:
        if _is_valid_mcp_server_config(config):
            servers.append({"name": name, "config": config})

    if servers:
        return ParsedMcpConfig(servers=servers, needs_name=False)

    raise ValueError("Invalid MCP configuration format")


def try_parse_clipboard_config(text: str) -> Optional[ParsedMcpConfig]:
    """Try to parse clipboard content as MCP config.

    Returns None if not valid MCP config.

    Ported from McpConfigParser.ts tryParseClipboardConfig.
    """
    trimmed = text.strip()
    if not trimmed.startswith("{"):
        return None

    try:
        return parse_clipboard_config(trimmed)
    except (ValueError, json.JSONDecodeError):
        return None
