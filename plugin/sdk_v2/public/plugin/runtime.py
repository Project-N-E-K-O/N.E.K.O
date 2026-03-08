"""Internal plugin runtime building blocks.

This module explicitly re-exports the shared runtime contracts the plugin facade
can compose with. Keeping the list static makes the internal surface easier to
inspect and maintain.
"""

from plugin.sdk_v2.shared.core.config import ConfigPathError, ConfigValidationError, PluginConfig, PluginConfigError
from plugin.sdk_v2.shared.core.plugins import (
    InvalidEntryRefError,
    InvalidEventRefError,
    PluginCallError,
    PluginDescriptor,
    Plugins,
    parse_entry_ref,
    parse_event_ref,
)
from plugin.sdk_v2.shared.core.router import EntryConflictError, PluginRouter, PluginRouterError, RouteHandler
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

__all__ = [
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
]
