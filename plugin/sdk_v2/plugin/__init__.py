"""Plugin-side SDK v2 surface.

Primary import target for standard plugin development.
"""

from __future__ import annotations

from . import base as _base
from . import decorators as _decorators
from . import runtime as _runtime

NEKO_PLUGIN_META_ATTR = _base.NEKO_PLUGIN_META_ATTR
NEKO_PLUGIN_TAG = _base.NEKO_PLUGIN_TAG
PluginMeta = _base.PluginMeta
NekoPluginBase = _base.NekoPluginBase

EntryKind = _decorators.EntryKind
PERSIST_ATTR = _decorators.PERSIST_ATTR
EVENT_META_ATTR = _decorators.EVENT_META_ATTR
HOOK_META_ATTR = _decorators.HOOK_META_ATTR
EventMeta = _runtime.EventMeta
HookDecoratorMeta = _decorators.HookDecoratorMeta
neko_plugin = _decorators.neko_plugin
on_event = _decorators.on_event
plugin_entry = _decorators.plugin_entry
lifecycle = _decorators.lifecycle
message = _decorators.message
timer_interval = _decorators.timer_interval
custom_event = _decorators.custom_event
hook = _decorators.hook
before_entry = _decorators.before_entry
after_entry = _decorators.after_entry
around_entry = _decorators.around_entry
replace_entry = _decorators.replace_entry
plugin = _decorators.plugin

SDK_VERSION = _runtime.SDK_VERSION
LogLevel = _runtime.LogLevel
build_component_name = _runtime.build_component_name
LoggerLike = _runtime.LoggerLike
get_sdk_logger = _runtime.get_sdk_logger
get_plugin_logger = _runtime.get_plugin_logger
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
PluginConfig = _runtime.PluginConfig
PluginConfigError = _runtime.PluginConfigError
ConfigPathError = _runtime.ConfigPathError
ConfigProfileError = _runtime.ConfigProfileError
PluginConfigBaseView = _runtime.PluginConfigBaseView
PluginConfigProfiles = _runtime.PluginConfigProfiles
ConfigValidationError = _runtime.ConfigValidationError
Plugins = _runtime.Plugins
PluginCallError = _runtime.PluginCallError
PluginResultError = _runtime.PluginResultError
PluginDescriptor = _runtime.PluginDescriptor
InvalidEntryRefError = _runtime.InvalidEntryRefError
InvalidEventRefError = _runtime.InvalidEventRefError
parse_entry_ref = _runtime.parse_entry_ref
parse_event_ref = _runtime.parse_event_ref
PluginRouter = _runtime.PluginRouter
PluginRouterError = _runtime.PluginRouterError
EntryConflictError = _runtime.EntryConflictError
RouteHandler = _runtime.RouteHandler
EventHandler = _runtime.EventHandler
HookMeta = _runtime.HookMeta
HookHandler = _runtime.HookHandler
HookTiming = _runtime.HookTiming
HookExecutorMixin = _runtime.HookExecutorMixin
CallChain = _runtime.CallChain
AsyncCallChain = _runtime.AsyncCallChain
CircularCallError = _runtime.CircularCallError
CallChainTooDeepError = _runtime.CallChainTooDeepError
get_call_chain = _runtime.get_call_chain
get_call_depth = _runtime.get_call_depth
is_in_call_chain = _runtime.is_in_call_chain
SystemInfo = _runtime.SystemInfo
MemoryClient = _runtime.MemoryClient
PluginStore = _runtime.PluginStore
PluginDatabase = _runtime.PluginDatabase
PluginKVStore = _runtime.PluginKVStore
PluginStatePersistence = _runtime.PluginStatePersistence
EXTENDED_TYPES = _runtime.EXTENDED_TYPES
PluginContextProtocol = _runtime.PluginContextProtocol
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

BASE_EXPORTS = [
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "PluginMeta",
    "NekoPluginBase",
]
DECORATOR_EXPORTS = [
    "EntryKind",
    "PERSIST_ATTR",
    "EVENT_META_ATTR",
    "HOOK_META_ATTR",
    "EventMeta",
    "HookDecoratorMeta",
    "neko_plugin",
    "on_event",
    "plugin_entry",
    "lifecycle",
    "message",
    "timer_interval",
    "custom_event",
    "hook",
    "before_entry",
    "after_entry",
    "around_entry",
    "replace_entry",
    "plugin",
]
RUNTIME_EXPORTS = [
    "SDK_VERSION",
    "LogLevel",
    "build_component_name",
    "LoggerLike",
    "get_sdk_logger",
    "get_plugin_logger",
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
    "PluginConfig",
    "PluginConfigError",
    "ConfigPathError",
    "ConfigProfileError",
    "PluginConfigBaseView",
    "PluginConfigProfiles",
    "ConfigValidationError",
    "Plugins",
    "PluginCallError",
    "PluginResultError",
    "PluginDescriptor",
    "InvalidEntryRefError",
    "InvalidEventRefError",
    "parse_entry_ref",
    "parse_event_ref",
    "PluginRouter",
    "PluginRouterError",
    "EntryConflictError",
    "RouteHandler",
    "EventHandler",
    "HookMeta",
    "HookHandler",
    "HookTiming",
    "HookExecutorMixin",
    "CallChain",
    "AsyncCallChain",
    "CircularCallError",
    "CallChainTooDeepError",
    "get_call_chain",
    "get_call_depth",
    "is_in_call_chain",
    "SystemInfo",
    "MemoryClient",
    "PluginStore",
    "PluginDatabase",
    "PluginKVStore",
    "PluginStatePersistence",
    "EXTENDED_TYPES",
    "PluginContextProtocol",
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
]

__all__ = [*BASE_EXPORTS, *DECORATOR_EXPORTS, *RUNTIME_EXPORTS]
