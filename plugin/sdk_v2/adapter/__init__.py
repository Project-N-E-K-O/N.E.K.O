"""SDK v2 adapter surface (contract-only)."""

from .base import AdapterBase, AdapterConfig, AdapterContext, AdapterMode
from .decorators import (
    ADAPTER_EVENT_META,
    ADAPTER_LIFECYCLE_META,
    AdapterEventMeta,
    on_adapter_event,
    on_adapter_shutdown,
    on_adapter_startup,
    on_mcp_resource,
    on_mcp_tool,
    on_nonebot_message,
)
from .gateway_contracts import (
    LoggerLike,
    PluginInvoker,
    PolicyEngine,
    RequestNormalizer,
    ResponseSerializer,
    RouteEngine,
    TransportAdapter,
)
from .gateway_core import AdapterGatewayCore
from .gateway_defaults import (
    CallablePluginInvoker,
    DefaultPolicyEngine,
    DefaultRequestNormalizer,
    DefaultResponseSerializer,
    DefaultRouteEngine,
)
from .gateway_models import (
    ExternalEnvelope,
    GatewayAction,
    GatewayError,
    GatewayErrorException,
    GatewayRequest,
    GatewayResponse,
    RouteDecision,
    RouteMode,
)
from .neko_adapter import NekoAdapterPlugin
from .types import AdapterMessage, AdapterResponse, Protocol, RouteRule, RouteTarget

__all__ = [
    "AdapterBase",
    "AdapterConfig",
    "AdapterContext",
    "AdapterMode",
    "AdapterMessage",
    "AdapterResponse",
    "Protocol",
    "RouteRule",
    "RouteTarget",
    "ADAPTER_EVENT_META",
    "ADAPTER_LIFECYCLE_META",
    "AdapterEventMeta",
    "on_adapter_event",
    "on_adapter_startup",
    "on_adapter_shutdown",
    "on_mcp_tool",
    "on_mcp_resource",
    "on_nonebot_message",
    "ExternalEnvelope",
    "GatewayAction",
    "GatewayRequest",
    "GatewayError",
    "GatewayErrorException",
    "GatewayResponse",
    "RouteDecision",
    "RouteMode",
    "LoggerLike",
    "TransportAdapter",
    "RequestNormalizer",
    "PolicyEngine",
    "RouteEngine",
    "PluginInvoker",
    "ResponseSerializer",
    "AdapterGatewayCore",
    "DefaultRequestNormalizer",
    "DefaultPolicyEngine",
    "DefaultRouteEngine",
    "DefaultResponseSerializer",
    "CallablePluginInvoker",
    "NekoAdapterPlugin",
]
