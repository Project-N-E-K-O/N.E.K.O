"""Plugin runtime surface built on shared SDK v2 primitives."""

from __future__ import annotations

from typing import Mapping

import plugin.sdk.shared.constants as _shared_constants
import plugin.sdk.shared.core.config as _config
import plugin.sdk.shared.core.events as _events
import plugin.sdk.shared.core.hook_executor as _hook_executor
import plugin.sdk.shared.core.hooks as _hooks
import plugin.sdk.shared.core.plugins as _plugins
import plugin.sdk.shared.core.router as _router
import plugin.sdk.shared.core.types as _types
import plugin.sdk.shared.logging as _shared_logging
import plugin.sdk.shared.models as _models
import plugin.sdk.shared.models.exceptions as _exceptions
import plugin.sdk.shared.runtime.memory as _memory
import plugin.sdk.shared.runtime.system_info as _system_info
import plugin.sdk.shared.storage.database as _database
import plugin.sdk.shared.storage.state as _state
import plugin.sdk.shared.storage.store as _store
from plugin.sdk.shared import runtime_common as _common_runtime

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

get_plugin_logger = _shared_logging.get_plugin_logger
PluginConfig = _config.PluginConfig
PluginConfigError = _config.PluginConfigError
ConfigPathError = _config.ConfigPathError
ConfigProfileError = _config.ConfigProfileError
PluginConfigBaseView = _config.PluginConfigBaseView
PluginConfigProfiles = _config.PluginConfigProfiles
ConfigValidationError = _config.ConfigValidationError
PluginCallError = _plugins.PluginCallError
PluginDescriptor = _plugins.PluginDescriptor
InvalidEntryRefError = _plugins.InvalidEntryRefError
InvalidEventRefError = _plugins.InvalidEventRefError
parse_entry_ref = _plugins.parse_entry_ref
parse_event_ref = _plugins.parse_event_ref
PluginRouter = _router.PluginRouter
PluginRouterError = _router.PluginRouterError
EntryConflictError = _router.EntryConflictError
RouteHandler = _router.RouteHandler
EventMeta = _events.EventMeta
EventHandler = _events.EventHandler
EVENT_META_ATTR = _shared_constants.EVENT_META_ATTR
HookMeta = _hooks.HookMeta
HookHandler = _hooks.HookHandler
HookTiming = _hooks.HookTiming
HOOK_META_ATTR = _shared_constants.HOOK_META_ATTR
HookExecutorMixin = _hook_executor.HookExecutorMixin
SystemInfo = _system_info.SystemInfo
MemoryClient = _memory.MemoryClient
PluginStore = _store.PluginStore
PluginDatabase = _database.PluginDatabase
PluginKVStore = _database.PluginKVStore
PluginStatePersistence = _state.PluginStatePersistence
EXTENDED_TYPES = _state.EXTENDED_TYPES
PluginContextProtocol = _types.PluginContextProtocol
PluginResultError = _exceptions.InvalidArgumentError | _exceptions.PluginCallError

COMMON_RUNTIME_EXPORTS = list(_common_runtime.__all__)
PLUGIN_RUNTIME_EXPORTS = [
    "get_plugin_logger",
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
    "EventMeta",
    "EventHandler",
    "EVENT_META_ATTR",
    "HookMeta",
    "HookHandler",
    "HookTiming",
    "HOOK_META_ATTR",
    "HookExecutorMixin",
    "SystemInfo",
    "MemoryClient",
    "PluginStore",
    "PluginDatabase",
    "PluginKVStore",
    "PluginStatePersistence",
    "EXTENDED_TYPES",
    "PluginContextProtocol",
]


class Plugins(_plugins.Plugins):
    """Plugin-facing cross-plugin call helper."""

    async def list(
        self,
        *,
        timeout: float = 5.0,
        enabled: bool | None = None,
    ) -> _models.Result[list[_plugins.PluginDescriptor], PluginResultError]:
        listed = await super().list(timeout=timeout)
        if isinstance(listed, _models.Err):
            return listed
        if enabled is None:
            return listed
        filtered: list[_plugins.PluginDescriptor] = []
        for item in listed.value:
            if bool(item.get("enabled", True)) is enabled:
                filtered.append(item)
        return _models.Ok(filtered)

    async def list_ids(
        self,
        *,
        timeout: float = 5.0,
        enabled: bool | None = None,
    ) -> _models.Result[list[str], PluginResultError]:
        listed = await self.list(timeout=timeout, enabled=enabled)
        if isinstance(listed, _models.Err):
            return listed
        ids = [str(item.get("plugin_id", "")) for item in listed.value if isinstance(item.get("plugin_id"), str) and item.get("plugin_id")]
        return _models.Ok(ids)

    async def get(
        self,
        plugin_id: str,
        *,
        timeout: float = 5.0,
    ) -> _models.Result[_plugins.PluginDescriptor | None, PluginResultError]:
        listed = await self.list(timeout=timeout)
        if isinstance(listed, _models.Err):
            return listed
        for item in listed.value:
            if item.get("plugin_id") == plugin_id:
                return _models.Ok(item)
        return _models.Ok(None)

    async def exists(
        self,
        plugin_id: str,
        *,
        timeout: float = 5.0,
    ) -> _models.Result[bool, PluginResultError]:
        got = await self.get(plugin_id, timeout=timeout)
        if isinstance(got, _models.Err):
            return got
        return _models.Ok(got.value is not None)

    async def call_entry(
        self,
        entry_ref: str,
        params: Mapping[str, _types.JsonValue] | None = None,
        *,
        timeout: float = 10.0,
    ) -> _models.Result[_types.JsonObject | _types.JsonValue | None, PluginResultError]:
        return await super().call_entry(entry_ref=entry_ref, params=params, timeout=timeout)

    async def call_event(
        self,
        event_ref: str,
        params: Mapping[str, _types.JsonValue] | None = None,
        *,
        timeout: float = 10.0,
    ) -> _models.Result[_types.JsonObject | _types.JsonValue | None, PluginResultError]:
        return await super().call_event(event_ref=event_ref, params=params, timeout=timeout)

    async def require_enabled(self, plugin_id: str, *, timeout: float = 5.0) -> _models.Result[_plugins.PluginDescriptor, PluginResultError]:
        required = await super().require(plugin_id, timeout=timeout)
        if isinstance(required, _models.Err):
            return required
        if not bool(required.value.get("enabled", True)):
            return _models.Err(
                _exceptions.PluginCallError(
                    f"required plugin is disabled: {plugin_id!r}",
                    op_name="plugins.require_enabled",
                    plugin_id=plugin_id,
                    timeout=timeout,
                )
            )
        return required

    async def call_entry_json(
        self,
        entry_ref: str,
        args: Mapping[str, _types.JsonValue] | None = None,
        *,
        timeout: float = 10.0,
    ) -> _models.Result[_types.JsonObject | None, PluginResultError]:
        result = await self.call_entry(entry_ref=entry_ref, params=args, timeout=timeout)
        if isinstance(result, _models.Err):
            return result
        value = result.value
        if value is None or isinstance(value, dict):
            return _models.Ok(value)
        return _models.Err(
            _exceptions.PluginCallError(
                f"entry {entry_ref!r} returned non-object payload: {type(value)!r}",
                op_name="plugins.call_entry_json",
                entry_ref=entry_ref,
                timeout=timeout,
            )
        )

    async def call_event_json(
        self,
        event_ref: str,
        args: Mapping[str, _types.JsonValue] | None = None,
        *,
        timeout: float = 10.0,
    ) -> _models.Result[_types.JsonObject | None, PluginResultError]:
        result = await self.call_event(event_ref=event_ref, params=args, timeout=timeout)
        if isinstance(result, _models.Err):
            return result
        value = result.value
        if value is None or isinstance(value, dict):
            return _models.Ok(value)
        return _models.Err(
            _exceptions.PluginCallError(
                f"event {event_ref!r} returned non-object payload: {type(value)!r}",
                op_name="plugins.call_event_json",
                event_ref=event_ref,
                timeout=timeout,
            )
        )


__all__ = [*COMMON_RUNTIME_EXPORTS, *PLUGIN_RUNTIME_EXPORTS]
