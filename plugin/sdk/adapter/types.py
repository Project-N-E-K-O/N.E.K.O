"""Adapter contract types for SDK v2."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from plugin.sdk.shared.core.types import JsonObject, JsonValue


class Protocol(str, Enum):
    MCP = "mcp"
    NONEBOT = "nonebot"
    OPENCLAW = "openclaw"
    HTTP = "http"
    WEBSOCKET = "websocket"
    CUSTOM = "custom"


class RouteTarget(str, Enum):
    SELF = "self"
    PLUGIN = "plugin"
    BROADCAST = "broadcast"
    DROP = "drop"


@dataclass(slots=True)
class AdapterMessage:
    id: str
    protocol: Protocol
    action: str
    payload: JsonObject
    source: str = ""
    target: str = "*"
    timestamp: float = 0.0
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class AdapterResponse:
    request_id: str
    success: bool = True
    data: JsonValue | JsonObject | None = None
    error: str | None = None
    error_code: str | None = None
    protocol: Protocol = Protocol.CUSTOM


@dataclass(slots=True)
class RouteRule:
    protocol: str = "*"
    action: str = "*"
    pattern: str | None = None
    target: RouteTarget = RouteTarget.SELF
    plugin_id: str | None = None
    entry_id: str | None = None
    priority: int = 0


__all__ = [
    "Protocol",
    "RouteTarget",
    "AdapterMessage",
    "AdapterResponse",
    "RouteRule",
]
