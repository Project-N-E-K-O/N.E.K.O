"""Gateway Core 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class GatewayAction(str, Enum):
    """Core 内统一动作类型。"""

    TOOL_CALL = "tool_call"
    RESOURCE_READ = "resource_read"
    EVENT_PUSH = "event_push"


class RouteMode(str, Enum):
    """路由决策模式。"""

    SELF = "self"
    PLUGIN = "plugin"
    BROADCAST = "broadcast"
    DROP = "drop"


@dataclass(slots=True, frozen=True)
class ExternalEnvelope:
    """外部协议输入包。"""

    protocol: str
    connection_id: str
    request_id: str
    action: str
    payload: dict[str, object]
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class GatewayRequest:
    """Gateway Core 内部统一请求。"""

    request_id: str
    protocol: str
    action: GatewayAction
    source_app: str
    trace_id: str
    params: dict[str, object]
    target_plugin_id: str | None = None
    target_entry_id: str | None = None
    timeout_s: float = 30.0
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class RouteDecision:
    """路由器输出结果。"""

    mode: RouteMode
    plugin_id: str | None = None
    entry_id: str | None = None
    reason: str = ""


@dataclass(slots=True, frozen=True)
class GatewayError:
    """结构化错误对象。"""

    code: str
    message: str
    details: dict[str, object] = field(default_factory=dict)
    retryable: bool = False


@dataclass(slots=True, frozen=True)
class GatewayResponse:
    """Gateway Core 统一响应。"""

    request_id: str
    success: bool
    data: object | None = None
    error: GatewayError | None = None
    latency_ms: float | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class GatewayErrorException(Exception):
    """携带结构化错误的异常。"""

    def __init__(self, error: GatewayError):
        super().__init__(error.message)
        self.error = error
