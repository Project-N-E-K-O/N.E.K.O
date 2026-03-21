"""Adapter runtime surface for SDK v2.

This module is the main discovery point for adapter authors who need the
gateway runtime vocabulary in one place. It intentionally re-exports:
- shared runtime primitives (`Result`, errors, logging, call-chain helpers)
- gateway request/response models
- gateway collaborator contracts
- default collaborator implementations

The goal is to keep IDE completion and agent navigation centered on a single
adapter-facing module, instead of forcing readers to assemble concepts from
`shared/*` and `public/adapter/*`.
"""

from __future__ import annotations

from plugin.sdk.shared import runtime_common as _common_runtime
from plugin.sdk.shared import logging as _shared_logging

from . import gateway_contracts as _gateway_contracts
from . import gateway_core as _gateway_core
from . import gateway_defaults as _gateway_defaults
from . import gateway_models as _gateway_models

SDK_VERSION = _common_runtime.SDK_VERSION
LogLevel = _common_runtime.LogLevel
build_component_name = _common_runtime.build_component_name
LoggerLike = _common_runtime.LoggerLike
get_sdk_logger = _common_runtime.get_sdk_logger
setup_sdk_logging = _common_runtime.setup_sdk_logging
configure_sdk_default_logger = _common_runtime.configure_sdk_default_logger
intercept_standard_logging = _common_runtime.intercept_standard_logging
format_log_text = _common_runtime.format_log_text
ErrorCode = _common_runtime.ErrorCode
SdkError = _common_runtime.SdkError
InvalidArgumentError = _common_runtime.InvalidArgumentError
CapabilityUnavailableError = _common_runtime.CapabilityUnavailableError
AuthorizationError = _common_runtime.AuthorizationError
TransportError = _common_runtime.TransportError
Ok = _common_runtime.Ok
Err = _common_runtime.Err
Result = _common_runtime.Result
ResultError = _common_runtime.ResultError
is_ok = _common_runtime.is_ok
is_err = _common_runtime.is_err
map_result = _common_runtime.map_result
map_err_result = _common_runtime.map_err_result
bind_result = _common_runtime.bind_result
match_result = _common_runtime.match_result
unwrap = _common_runtime.unwrap
unwrap_or = _common_runtime.unwrap_or
raise_for_err = _common_runtime.raise_for_err
must = _common_runtime.must
capture = _common_runtime.capture
CallChain = _common_runtime.CallChain
AsyncCallChain = _common_runtime.AsyncCallChain
CircularCallError = _common_runtime.CircularCallError
CallChainTooDeepError = _common_runtime.CallChainTooDeepError
get_call_chain = _common_runtime.get_call_chain
get_call_depth = _common_runtime.get_call_depth
is_in_call_chain = _common_runtime.is_in_call_chain

get_adapter_logger = _shared_logging.get_adapter_logger

ExternalRequest = _gateway_models.ExternalRequest
GatewayAction = _gateway_models.GatewayAction
GatewayRequest = _gateway_models.GatewayRequest
GatewayError = _gateway_models.GatewayError
GatewayErrorException = _gateway_models.GatewayErrorException
GatewayResponse = _gateway_models.GatewayResponse
RouteDecision = _gateway_models.RouteDecision
RouteMode = _gateway_models.RouteMode

TransportAdapter = _gateway_contracts.TransportAdapter
RequestNormalizer = _gateway_contracts.RequestNormalizer
PolicyEngine = _gateway_contracts.PolicyEngine
RouteEngine = _gateway_contracts.RouteEngine
PluginInvoker = _gateway_contracts.PluginInvoker
ResponseSerializer = _gateway_contracts.ResponseSerializer

DefaultRequestNormalizer = _gateway_defaults.DefaultRequestNormalizer
DefaultPolicyEngine = _gateway_defaults.DefaultPolicyEngine
DefaultRouteEngine = _gateway_defaults.DefaultRouteEngine
DefaultResponseSerializer = _gateway_defaults.DefaultResponseSerializer
CallablePluginInvoker = _gateway_defaults.CallablePluginInvoker

COMMON_RUNTIME_EXPORTS = list(_common_runtime.__all__)
ADAPTER_RUNTIME_EXPORTS = [
    "get_adapter_logger",
    "ExternalRequest",
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
AdapterGatewayCore = _gateway_core.AdapterGatewayCore
