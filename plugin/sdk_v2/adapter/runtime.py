"""Adapter runtime surface for SDK v2.

This facade groups adapter runtime contracts, gateway models, and the common
Result/error/version primitives adapter authors need at runtime.
"""

from __future__ import annotations

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
from plugin.sdk_v2.shared.logging import (
    LogLevel,
    LoggerLike,
    configure_sdk_default_logger,
    format_log_text,
    get_adapter_logger,
    get_sdk_logger,
    intercept_standard_logging,
    setup_sdk_logging,
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

__all__ = [
    "SDK_VERSION",
    "LogLevel",
    "LoggerLike",
    "get_sdk_logger",
    "get_adapter_logger",
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
]
