"""
NEKO Plugin SDK - Adapter Module

Adapter 是一种特殊的插件类型，用于：
1. 作为网关转发外部协议请求到 NEKO 插件
2. 作为路由器直接处理外部请求
3. 作为桥接器在不同协议间转换

支持的协议：
- MCP (Model Context Protocol)
- NoneBot
- OpenClaw
- 自定义协议
"""

from plugin.sdk.adapter.base import (
    AdapterBase,
    AdapterConfig,
    AdapterContext,
    AdapterMode,
)
from plugin.sdk.adapter.types import (
    AdapterMessage,
    AdapterResponse,
    Protocol,
)
from plugin.sdk.adapter.decorators import (
    on_adapter_event,
    on_adapter_startup,
    on_adapter_shutdown,
)
from plugin.sdk.adapter.gateway_models import (
    ExternalEnvelope,
    GatewayAction,
    GatewayError,
    GatewayErrorException,
    GatewayRequest,
    GatewayResponse,
    RouteDecision,
    RouteMode,
)
from plugin.sdk.adapter.gateway_contracts import (
    LoggerLike,
    PluginInvoker,
    PolicyEngine,
    RequestNormalizer,
    ResponseSerializer,
    RouteEngine,
    TransportAdapter,
)
from plugin.sdk.adapter.gateway_core import AdapterGatewayCore
from plugin.sdk.adapter.gateway_defaults import (
    CallablePluginInvoker,
    DefaultPolicyEngine,
    DefaultRequestNormalizer,
    DefaultResponseSerializer,
    DefaultRouteEngine,
)
from plugin.sdk.adapter.neko_adapter import NekoAdapterPlugin

__all__ = [
    # 基类
    "AdapterBase",
    "AdapterConfig",
    "AdapterContext",
    "AdapterMode",
    # 类型
    "AdapterMessage",
    "AdapterResponse",
    "Protocol",
    # 装饰器
    "on_adapter_event",
    "on_adapter_startup",
    "on_adapter_shutdown",
    # Gateway Core 模型
    "ExternalEnvelope",
    "GatewayAction",
    "GatewayRequest",
    "GatewayError",
    "GatewayErrorException",
    "GatewayResponse",
    "RouteDecision",
    "RouteMode",
    # Gateway Core 契约
    "LoggerLike",
    "TransportAdapter",
    "RequestNormalizer",
    "PolicyEngine",
    "RouteEngine",
    "PluginInvoker",
    "ResponseSerializer",
    # Gateway Core
    "AdapterGatewayCore",
    # Gateway Core 默认组件
    "DefaultRequestNormalizer",
    "DefaultPolicyEngine",
    "DefaultRouteEngine",
    "DefaultResponseSerializer",
    "CallablePluginInvoker",
    # NEKO Adapter 基类
    "NekoAdapterPlugin",
]
