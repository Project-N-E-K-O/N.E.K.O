"""Extension runtime contracts for SDK v2.

This facade is intentionally small but should still be the primary reference
point for extension authors:
- shared runtime primitives come from `runtime_common`
- extension-relevant config/router/transport types are surfaced explicitly here
- `ExtensionRuntime` documents the minimal bundle an extension typically needs

The module favors readability over clever indirection so developers, IDEs, and
agents can understand the extension runtime vocabulary from one screen.
"""

from __future__ import annotations

from dataclasses import dataclass

from plugin.sdk_v2.shared import runtime_common as _common_runtime
from plugin.sdk_v2.shared import logging as _shared_logging
from plugin.sdk_v2.shared import transport as _shared_transport
from plugin.sdk_v2.shared import core as _shared_core

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

get_extension_logger = _shared_logging.get_extension_logger

PluginConfig = _shared_core.config.PluginConfig
PluginConfigError = _shared_core.config.PluginConfigError
ConfigPathError = _shared_core.config.ConfigPathError
ConfigProfileError = _shared_core.config.ConfigProfileError
PluginConfigBaseView = _shared_core.config.PluginConfigBaseView
PluginConfigProfiles = _shared_core.config.PluginConfigProfiles
ConfigValidationError = _shared_core.config.ConfigValidationError

PluginRouter = _shared_core.router.PluginRouter
PluginRouterError = _shared_core.router.PluginRouterError
EntryConflictError = _shared_core.router.EntryConflictError
RouteHandler = _shared_core.router.RouteHandler

MessagePlaneTransport = _shared_transport.message_plane.MessagePlaneTransport

COMMON_RUNTIME_EXPORTS = list(_common_runtime.__all__)
EXTENSION_RUNTIME_EXPORTS = [
    "get_extension_logger",
    "PluginConfig",
    "PluginConfigError",
    "ConfigPathError",
    "ConfigProfileError",
    "PluginConfigBaseView",
    "PluginConfigProfiles",
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
    """Minimal runtime bundle for extension-oriented development.

    The type groups the three capabilities most extension code reaches for:
    configuration access, route registration, and message-plane transport.
    """

    config: PluginConfig
    router: PluginRouter
    transport: MessagePlaneTransport

    async def health(self) -> Result[dict[str, str], SdkError]:
        """Return a small diagnostic snapshot for tooling and smoke checks."""
        return Ok({"status": "healthy", "router": self.router.name(), "transport": self.transport.__class__.__name__})


__all__ = [*COMMON_RUNTIME_EXPORTS, *EXTENSION_RUNTIME_EXPORTS]
