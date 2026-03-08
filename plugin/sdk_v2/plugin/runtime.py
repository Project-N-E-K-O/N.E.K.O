"""Plugin runtime surface built on shared SDK v2 primitives."""

from __future__ import annotations

from typing import Mapping

from plugin.sdk_v2.shared import runtime_common as _common_runtime
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
from plugin.sdk_v2.shared.logging import get_plugin_logger
from plugin.sdk_v2.shared.runtime.memory import MemoryClient
from plugin.sdk_v2.shared.runtime.system_info import SystemInfo
from plugin.sdk_v2.shared.storage.database import PluginDatabase, PluginKVStore
from plugin.sdk_v2.shared.storage.state import EXTENDED_TYPES, PluginStatePersistence
from plugin.sdk_v2.shared.storage.store import PluginStore

from .models import Envelope, ErrEnvelope, ErrorDetail, OkEnvelope

for _name in _common_runtime.__all__:
    globals()[_name] = getattr(_common_runtime, _name)

COMMON_RUNTIME_EXPORTS = list(_common_runtime.__all__)
PLUGIN_RUNTIME_EXPORTS = [
    "get_plugin_logger",
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
    "SystemInfo",
    "MemoryClient",
    "PluginStore",
    "PluginDatabase",
    "PluginKVStore",
    "PluginStatePersistence",
    "StatePersistence",
    "EXTENDED_TYPES",
    "PluginContextProtocol",
]


class Plugins(_SharedPlugins):
    """Plugin-facing cross-plugin call helper."""

    async def call_entry(
        self,
        entry_ref: str,
        args: Mapping[str, JsonValue] | None = None,
        *,
        timeout: float = 10.0,
    ):
        return await super().call_entry(entry_ref=entry_ref, params=args, timeout=timeout)

    async def call_event(
        self,
        event_ref: str,
        args: Mapping[str, JsonValue] | None = None,
        *,
        timeout: float = 10.0,
    ):
        return await super().call_event(event_ref=event_ref, params=args, timeout=timeout)


StatePersistence = PluginStatePersistence

__all__ = [*COMMON_RUNTIME_EXPORTS, *PLUGIN_RUNTIME_EXPORTS]

del _name
