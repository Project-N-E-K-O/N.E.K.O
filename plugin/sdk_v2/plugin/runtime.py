"""Plugin runtime surface built on shared SDK v2 primitives."""

from __future__ import annotations

from typing import Mapping

from plugin.sdk_v2.shared.constants import EVENT_META_ATTR, HOOK_META_ATTR
from plugin.sdk_v2.shared.core.config import (
    ConfigPathError,
    ConfigValidationError,
    PluginConfig,
    PluginConfigError,
)
from plugin.sdk_v2.shared.core.events import EventHandler, EventMeta
from plugin.sdk_v2.shared.core.hook_executor import HookExecutorMixin
from plugin.sdk_v2.shared.core.hooks import HookHandler, HookMeta, HookTiming
from plugin.sdk_v2.shared.core.plugins import (
    InvalidEntryRefError,
    InvalidEventRefError,
    PluginCallError,
    PluginDescriptor,
    Plugins as _SharedPlugins,
    parse_entry_ref,
    parse_event_ref,
)
from plugin.sdk_v2.shared.core.router import (
    EntryConflictError,
    PluginRouter,
    PluginRouterError,
    RouteHandler,
)
from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue, PluginContextProtocol
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
from plugin.sdk_v2.shared.runtime.memory import MemoryClient
from plugin.sdk_v2.shared.runtime.system_info import SystemInfo
from plugin.sdk_v2.shared.storage.database import PluginDatabase, PluginKVStore
from plugin.sdk_v2.shared.storage.state import EXTENDED_TYPES, PluginStatePersistence
from plugin.sdk_v2.shared.storage.store import PluginStore
from plugin.sdk_v2.public.plugin.runtime_models import Envelope, ErrEnvelope, ErrorDetail, OkEnvelope


class Plugins(_SharedPlugins):
    """Plugin-facing cross-plugin call helper."""

    async def call_entry(
        self,
        entry_ref: str,
        args: Mapping[str, JsonValue] | None = None,
        *,
        timeout: float = 10.0,
    ) -> Result[JsonObject | JsonValue | None, Exception]:
        return await super().call_entry(entry_ref=entry_ref, params=args, timeout=timeout)

    async def call_event(
        self,
        event_ref: str,
        args: Mapping[str, JsonValue] | None = None,
        *,
        timeout: float = 10.0,
    ) -> Result[JsonObject | JsonValue | None, Exception]:
        return await super().call_event(event_ref=event_ref, params=args, timeout=timeout)


StatePersistence = PluginStatePersistence

__all__ = [
    "SDK_VERSION",
    "ErrorCode",
    "ok",
    "fail",
    "is_envelope",
    "Envelope",
    "OkEnvelope",
    "ErrEnvelope",
    "ErrorDetail",
    "PluginConfig",
    "PluginConfigError",
    "ConfigPathError",
    "ConfigValidationError",
    "Plugins",
    "PluginCallError",
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
    "StatePersistence",
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
