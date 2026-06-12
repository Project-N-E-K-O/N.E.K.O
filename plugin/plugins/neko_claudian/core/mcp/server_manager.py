# Ported from claudian/src/core/mcp/McpServerManager.ts
# Original author: Claudian contributors
# License: MIT

"""
McpServerManager — Manage MCP server configurations.

Handles loading, enabling/disabling, and querying MCP servers.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Set

logger = logging.getLogger(__name__)


@dataclass
class ManagedMcpServer:
    """Managed MCP server configuration."""
    name: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    context_saving: bool = True
    disabled_tools: List[str] = field(default_factory=list)
    description: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "name": self.name,
            "config": self.config,
            "enabled": self.enabled,
            "contextSaving": self.context_saving,
        }
        if self.disabled_tools:
            out["disabledTools"] = self.disabled_tools
        if self.description:
            out["description"] = self.description
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ManagedMcpServer:
        return cls(
            name=data.get("name", ""),
            config=data.get("config", {}),
            enabled=data.get("enabled", True),
            context_saving=data.get("contextSaving", True),
            disabled_tools=data.get("disabledTools", []),
            description=data.get("description"),
        )


class McpStorageAdapter(Protocol):
    """Storage interface for loading MCP servers."""
    async def load(self) -> List[ManagedMcpServer]:
        ...


class McpServerManager:
    """Manage MCP server configurations.

    Ported from McpServerManager.ts
    """

    def __init__(self, storage: Optional[McpStorageAdapter] = None):
        self._servers: List[ManagedMcpServer] = []
        self._storage = storage

    async def load_servers(self) -> None:
        """Load servers from storage."""
        if self._storage:
            self._servers = await self._storage.load()

    def get_servers(self) -> List[ManagedMcpServer]:
        """Get all servers."""
        return self._servers

    def get_enabled_count(self) -> int:
        """Get count of enabled servers."""
        return sum(1 for s in self._servers if s.enabled)

    def get_active_servers(self, mentioned_names: Set[str]) -> Dict[str, Dict[str, Any]]:
        """Get servers to include in SDK options.

        A server is included if:
        - It is enabled AND
        - Either context-saving is disabled OR the server is @-mentioned
        """
        result: Dict[str, Dict[str, Any]] = {}

        for server in self._servers:
            if not server.enabled:
                continue

            # If context-saving is enabled, only include if @-mentioned
            if server.context_saving and server.name not in mentioned_names:
                continue

            result[server.name] = server.config

        return result

    def get_disallowed_mcp_tools(self, mentioned_names: Set[str]) -> List[str]:
        """Get disabled MCP tools formatted for SDK disallowedTools option."""
        return self._collect_disallowed_tools(
            lambda s: not s.context_saving or s.name in mentioned_names
        )

    def get_all_disallowed_mcp_tools(self) -> List[str]:
        """Get all disabled MCP tools from ALL enabled servers."""
        return sorted(self._collect_disallowed_tools())

    def _collect_disallowed_tools(self, filter_fn: Optional[Callable[[ManagedMcpServer], bool]] = None) -> List[str]:
        """Collect disabled tools from servers."""
        disallowed: Set[str] = set()

        for server in self._servers:
            if not server.enabled:
                continue
            if filter_fn and not filter_fn(server):
                continue
            if not server.disabled_tools:
                continue

            for tool in server.disabled_tools:
                normalized = tool.strip()
                if normalized:
                    disallowed.add(f"mcp__{server.name}__{normalized}")

        return list(disallowed)

    def has_servers(self) -> bool:
        """Check if there are any servers."""
        return len(self._servers) > 0

    def get_context_saving_servers(self) -> List[ManagedMcpServer]:
        """Get servers with context-saving mode enabled."""
        return [s for s in self._servers if s.enabled and s.context_saving]

    def _get_context_saving_names(self) -> Set[str]:
        """Get names of context-saving servers."""
        return {s.name for s in self.get_context_saving_servers()}

    def extract_mentions(self, text: str) -> Set[str]:
        """Extract @mentions from text.

        Only matches against enabled servers with context-saving mode.
        """
        context_saving_names = self._get_context_saving_names()
        if not context_saving_names:
            return set()

        # Pattern: @servername (word boundary after name)
        mentions: Set[str] = set()
        for name in context_saving_names:
            pattern = rf"@{re.escape(name)}\b"
            if re.search(pattern, text):
                mentions.add(name)

        return mentions

    def transform_mentions(self, text: str) -> str:
        """Append " MCP" after each valid @mention.

        Applied to API requests only, not shown in UI.
        """
        context_saving_names = self._get_context_saving_names()
        if not context_saving_names:
            return text

        result = text
        for name in context_saving_names:
            pattern = rf"@{re.escape(name)}\b"
            result = re.sub(pattern, f"@{name} MCP", result)

        return result

    def add_server(self, server: ManagedMcpServer) -> None:
        """Add a server."""
        # Check if already exists
        existing = next((s for s in self._servers if s.name == server.name), None)
        if existing:
            # Update existing
            existing.config = server.config
            existing.enabled = server.enabled
            existing.context_saving = server.context_saving
            existing.disabled_tools = server.disabled_tools
            existing.description = server.description
        else:
            self._servers.append(server)

    def remove_server(self, name: str) -> bool:
        """Remove a server by name."""
        for i, server in enumerate(self._servers):
            if server.name == name:
                self._servers.pop(i)
                return True
        return False

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Enable or disable a server."""
        server = next((s for s in self._servers if s.name == name), None)
        if server:
            server.enabled = enabled
            return True
        return False
