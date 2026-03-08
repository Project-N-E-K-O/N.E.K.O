"""Extension runtime contracts for SDK v2.

This runtime surface stays narrower than `plugin.runtime`, but follows the same
layout: common SDK-wide runtime exports first, extension-specific contracts
second.
"""

from __future__ import annotations

from dataclasses import dataclass

from plugin.sdk_v2.shared.core.config import ConfigPathError, ConfigValidationError, PluginConfig, PluginConfigError
from plugin.sdk_v2.shared.core.router import EntryConflictError, PluginRouter, PluginRouterError, RouteHandler
from plugin.sdk_v2.shared.logging import (
    LogLevel,
    LoggerLike,
    build_component_name,
    configure_sdk_default_logger,
    format_log_text,
    get_extension_logger,
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
from plugin.sdk_v2.shared.transport.message_plane import MessagePlaneTransport

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

EXTENSION_RUNTIME_EXPORTS = [
    "get_extension_logger",
    "PluginConfig",
    "PluginConfigError",
    "ConfigPathError",
    "ConfigValidationError",
    "PluginRouter",
    "PluginRouterError",
    "EntryConflictError",
    "RouteHandler",
    "MessagePlaneTransport",
    "ExtensionRuntime",
]


@dataclass(slots=True)
class ExtensionRuntime:
    config: PluginConfig
    router: PluginRouter
    transport: MessagePlaneTransport

    async def health(self) -> Result[dict[str, str], Exception]:
        raise NotImplementedError("sdk_v2 contract-only facade: extension.runtime not implemented")


__all__ = [*COMMON_RUNTIME_EXPORTS, *EXTENSION_RUNTIME_EXPORTS]
