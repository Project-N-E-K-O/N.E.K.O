"""Adapter runtime surface for SDK v2."""

from __future__ import annotations

from plugin.sdk_v2.shared import runtime_common as _common_runtime
from plugin.sdk_v2.shared.logging import get_adapter_logger  # noqa: F401

from .gateway_contracts import (  # noqa: F401
    PluginInvoker,
    PolicyEngine,
    RequestNormalizer,
    ResponseSerializer,
    RouteEngine,
    TransportAdapter,
)
from .gateway_core import AdapterGatewayCore  # noqa: F401
from .gateway_defaults import (  # noqa: F401
    CallablePluginInvoker,
    DefaultPolicyEngine,
    DefaultRequestNormalizer,
    DefaultResponseSerializer,
    DefaultRouteEngine,
)
from .gateway_models import (  # noqa: F401
    ExternalEnvelope,
    GatewayAction,
    GatewayError,
    GatewayErrorException,
    GatewayRequest,
    GatewayResponse,
    RouteDecision,
    RouteMode,
)

for _name in _common_runtime.__all__:
    globals()[_name] = getattr(_common_runtime, _name)

COMMON_RUNTIME_EXPORTS = list(_common_runtime.__all__)
ADAPTER_RUNTIME_EXPORTS = [
    "get_adapter_logger",
    "ExternalEnvelope",
    "GatewayAction",
    "GatewayRequest",
    "GatewayError",
    "GatewayErrorException",
    "GatewayResponse",
    "RouteDecision",
    "RouteMode",
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
]

__all__ = [*COMMON_RUNTIME_EXPORTS, *ADAPTER_RUNTIME_EXPORTS]

del _name
