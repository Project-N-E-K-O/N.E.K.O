"""Adapter runtime surface for SDK v2.

This runtime surface follows the same layout as the other facades: common
SDK-wide runtime exports first, adapter-specific gateway/runtime contracts
second.
"""

from __future__ import annotations

from plugin.sdk_v2.shared.logging import (
    LogLevel,
    LoggerLike,
    build_component_name,
    configure_sdk_default_logger,
    format_log_text,
    get_adapter_logger,
    get_sdk_logger,
    intercept_standard_logging,
    setup_sdk_logging,
)
from plugin.sdk_v2.shared.models import (
    Err,
    Ok,
    Result,
    ResultError,
    bind_result,
    capture,
    is_err,
    is_ok,
    map_err_result,
    map_result,
    match_result,
    must,
    raise_for_err,
    unwrap,
    unwrap_or,
)
from plugin.sdk_v2.shared.models.errors import ErrorCode
from plugin.sdk_v2.shared.models.responses import fail, is_envelope, ok
from plugin.sdk_v2.shared.models.version import SDK_VERSION
from plugin.sdk_v2.shared.runtime.call_chain import (
    AsyncCallChain,
    CallChain,
    CallChainTooDeepError,
    CircularCallError,
    get_call_chain,
    get_call_depth,
    is_in_call_chain,
)

from .gateway_contracts import (
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

COMMON_RUNTIME_EXPORTS = [
    "SDK_VERSION",
    "LogLevel",
    "build_component_name",
    "LoggerLike",
    "get_sdk_logger",
    "setup_sdk_logging",
    "configure_sdk_default_logger",
    "intercept_standard_logging",
    "format_log_text",
    "ErrorCode",
    "ok",
    "fail",
    "is_envelope",
    "Ok",
    "Err",
    "Result",
    "ResultError",
    "is_ok",
    "is_err",
    "map_result",
    "map_err_result",
    "bind_result",
    "match_result",
    "unwrap",
    "unwrap_or",
    "raise_for_err",
    "must",
    "capture",
    "CallChain",
    "AsyncCallChain",
    "CircularCallError",
    "CallChainTooDeepError",
    "get_call_chain",
    "get_call_depth",
    "is_in_call_chain",
]

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
