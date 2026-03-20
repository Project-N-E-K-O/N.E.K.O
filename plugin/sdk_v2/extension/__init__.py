"""SDK v2 extension surface."""

from __future__ import annotations

from . import base as _base
from . import decorators as _decorators
from . import runtime as _runtime

ExtensionMeta = _base.ExtensionMeta
NekoExtensionBase = _base.NekoExtensionBase

EXTENSION_ENTRY_META = _decorators.EXTENSION_ENTRY_META
EXTENSION_HOOK_META = _decorators.EXTENSION_HOOK_META
ExtensionEntryMeta = _decorators.ExtensionEntryMeta
ExtensionHookMeta = _decorators.ExtensionHookMeta
extension_entry = _decorators.extension_entry
extension_hook = _decorators.extension_hook
extension = _decorators.extension

SDK_VERSION = _runtime.SDK_VERSION
LogLevel = _runtime.LogLevel
build_component_name = _runtime.build_component_name
LoggerLike = _runtime.LoggerLike
get_sdk_logger = _runtime.get_sdk_logger
get_extension_logger = _runtime.get_extension_logger
setup_sdk_logging = _runtime.setup_sdk_logging
configure_sdk_default_logger = _runtime.configure_sdk_default_logger
intercept_standard_logging = _runtime.intercept_standard_logging
format_log_text = _runtime.format_log_text
ErrorCode = _runtime.ErrorCode
SdkError = _runtime.SdkError
InvalidArgumentError = _runtime.InvalidArgumentError
CapabilityUnavailableError = _runtime.CapabilityUnavailableError
AuthorizationError = _runtime.AuthorizationError
TransportError = _runtime.TransportError
Ok = _runtime.Ok
Err = _runtime.Err
Result = _runtime.Result
ResultError = _runtime.ResultError
is_ok = _runtime.is_ok
is_err = _runtime.is_err
map_result = _runtime.map_result
map_err_result = _runtime.map_err_result
bind_result = _runtime.bind_result
match_result = _runtime.match_result
unwrap = _runtime.unwrap
unwrap_or = _runtime.unwrap_or
raise_for_err = _runtime.raise_for_err
must = _runtime.must
capture = _runtime.capture
CallChain = _runtime.CallChain
AsyncCallChain = _runtime.AsyncCallChain
CircularCallError = _runtime.CircularCallError
CallChainTooDeepError = _runtime.CallChainTooDeepError
get_call_chain = _runtime.get_call_chain
get_call_depth = _runtime.get_call_depth
is_in_call_chain = _runtime.is_in_call_chain
PluginConfig = _runtime.PluginConfig
PluginConfigError = _runtime.PluginConfigError
ConfigPathError = _runtime.ConfigPathError
ConfigProfileError = _runtime.ConfigProfileError
PluginConfigBaseView = _runtime.PluginConfigBaseView
PluginConfigProfiles = _runtime.PluginConfigProfiles
ConfigValidationError = _runtime.ConfigValidationError
PluginRouter = _runtime.PluginRouter
PluginRouterError = _runtime.PluginRouterError
EntryConflictError = _runtime.EntryConflictError
RouteHandler = _runtime.RouteHandler
MessagePlaneTransport = _runtime.MessagePlaneTransport
ExtensionRuntime = _runtime.ExtensionRuntime

__all__ = [
    "ExtensionMeta",
    "NekoExtensionBase",
    "EXTENSION_ENTRY_META",
    "EXTENSION_HOOK_META",
    "ExtensionEntryMeta",
    "ExtensionHookMeta",
    "extension_entry",
    "extension_hook",
    "extension",
    "SDK_VERSION",
    "LogLevel",
    "build_component_name",
    "LoggerLike",
    "get_sdk_logger",
    "get_extension_logger",
    "setup_sdk_logging",
    "configure_sdk_default_logger",
    "intercept_standard_logging",
    "format_log_text",
    "ErrorCode",
    "SdkError",
    "InvalidArgumentError",
    "CapabilityUnavailableError",
    "AuthorizationError",
    "TransportError",
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
