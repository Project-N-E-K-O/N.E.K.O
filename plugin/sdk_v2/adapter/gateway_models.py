"""Gateway contract models for SDK v2 adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue


class GatewayAction(str, Enum):
    TOOL_CALL = "tool_call"
    RESOURCE_READ = "resource_read"
    EVENT_PUSH = "event_push"


class RouteMode(str, Enum):
    SELF = "self"
    PLUGIN = "plugin"
    BROADCAST = "broadcast"
    DROP = "drop"


@dataclass(slots=True, frozen=True)
class ExternalRequest:
    protocol: str
    connection_id: str
    request_id: str
    action: str
    payload: JsonObject
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class GatewayRequest:
    request_id: str
    protocol: str
    action: GatewayAction
    source_app: str
    trace_id: str
    params: JsonObject
    target_plugin_id: str | None = None
    target_entry_id: str | None = None
    timeout_s: float = 30.0
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class RouteDecision:
    mode: RouteMode
    plugin_id: str | None = None
    entry_id: str | None = None
    reason: str = ""


@dataclass(slots=True, frozen=True)
class GatewayError:
    code: str
    message: str
    details: JsonObject = field(default_factory=dict)
    retryable: bool = False


@dataclass(slots=True, frozen=True)
class GatewayResponse:
    request_id: str
    success: bool
    data: JsonValue | JsonObject | None = None
    error: GatewayError | None = None
    latency_ms: float | None = None
    metadata: JsonObject = field(default_factory=dict)


class GatewayErrorException(RuntimeError):
    def __init__(self, error: GatewayError):
        super().__init__(error.message)
        self.error = error


__all__ = [
    "ExternalRequest",
    "GatewayAction",
    "GatewayRequest",
    "GatewayError",
    "GatewayErrorException",
    "GatewayResponse",
    "RouteDecision",
    "RouteMode",
]
