# Ported from claudian/src/core/types/mcp.ts
# Original author: Claudian contributors
# License: MIT

"""
MCP (Model Context Protocol) type definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


@dataclass
class McpStdioServerConfig:
    """Stdio server configuration (local command-line programs)."""
    type: str = "stdio"
    command: str = ""
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"type": self.type, "command": self.command}
        if self.args:
            out["args"] = self.args
        if self.env:
            out["env"] = self.env
        return out


@dataclass
class McpSSEServerConfig:
    """Server-Sent Events remote server configuration."""
    type: str = "sse"
    url: str = ""
    headers: Optional[Dict[str, str]] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"type": self.type, "url": self.url}
        if self.headers:
            out["headers"] = self.headers
        return out


@dataclass
class McpHttpServerConfig:
    """HTTP remote server configuration."""
    type: str = "http"
    url: str = ""
    headers: Optional[Dict[str, str]] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"type": self.type, "url": self.url}
        if self.headers:
            out["headers"] = self.headers
        return out


# Union type for all MCP server configurations
McpServerConfig = Union[McpStdioServerConfig, McpSSEServerConfig, McpHttpServerConfig]

# Server type identifier
McpServerType = str  # "stdio" | "sse" | "http"


@dataclass
class ManagedMcpServer:
    """Managed MCP server configuration with UI/runtime metadata."""
    name: str = ""
    config: Optional[McpServerConfig] = None
    enabled: bool = True
    context_saving: bool = True
    disabled_tools: Optional[List[str]] = None
    description: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "name": self.name,
            "enabled": self.enabled,
            "contextSaving": self.context_saving,
        }
        if self.config:
            out["config"] = self.config.to_dict() if hasattr(self.config, 'to_dict') else self.config
        if self.disabled_tools:
            out["disabledTools"] = self.disabled_tools
        if self.description:
            out["description"] = self.description
        return out


@dataclass
class McpConfigFile:
    """MCP configuration file format."""
    mcp_servers: Dict[str, McpServerConfig] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mcpServers": {
                name: config.to_dict() if hasattr(config, 'to_dict') else config
                for name, config in self.mcp_servers.items()
            }
        }


@dataclass
class ManagedMcpConfigFile(McpConfigFile):
    """Extended config file with app-owned server metadata."""
    _neko: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        out = super().to_dict()
        if self._neko:
            out["_neko"] = self._neko
        return out


@dataclass
class ParsedMcpConfig:
    """Result of parsing clipboard config."""
    servers: List[Dict[str, Any]] = field(default_factory=list)
    needs_name: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "servers": self.servers,
            "needsName": self.needs_name,
        }


def get_mcp_server_type(config: McpServerConfig) -> str:
    """Get the server type from config."""
    if isinstance(config, McpSSEServerConfig):
        return "sse"
    if isinstance(config, McpHttpServerConfig):
        return "http"
    if isinstance(config, McpStdioServerConfig):
        return "stdio"
    # Fallback: check dict-like access
    if hasattr(config, 'type'):
        t = config.type
        if t in ("sse", "http", "stdio"):
            return t
    return "stdio"


def is_valid_mcp_server_config(obj: Any) -> bool:
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


# Default MCP server settings
DEFAULT_MCP_SERVER = {
    "enabled": True,
    "contextSaving": True,
}
