"""Plugin-side SDK v2 surface.

Primary import target for standard plugin development.
"""

from __future__ import annotations

from . import base as _base
from . import decorators as _decorators
from . import runtime as _runtime
from . import ui as ui
from plugin.sdk.shared.i18n import PluginI18n, tr

# --- Base ---
NEKO_PLUGIN_META_ATTR = _base.NEKO_PLUGIN_META_ATTR
NEKO_PLUGIN_TAG = _base.NEKO_PLUGIN_TAG
PluginMeta = _base.PluginMeta
NekoPluginBase = _base.NekoPluginBase

# --- Decorators ---
EntryKind = _decorators.EntryKind
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

# --- Result ---
Ok = _runtime.Ok
Err = _runtime.Err
Result = _runtime.Result
unwrap = _runtime.unwrap
unwrap_or = _runtime.unwrap_or

# --- Config & Runtime ---
PluginConfig = _runtime.PluginConfig
Plugins = _runtime.Plugins
PluginRouter = _runtime.PluginRouter
SystemInfo = _runtime.SystemInfo
MemoryClient = _runtime.MemoryClient
PluginStore = _runtime.PluginStore

# --- Errors ---
SdkError = _runtime.SdkError
TransportError = _runtime.TransportError

# --- Logging ---
get_plugin_logger = _runtime.get_plugin_logger

__all__ = [
    # Base
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "PluginMeta",
    "NekoPluginBase",
    # Decorators
    "EntryKind",
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
    "ui",
    "PluginI18n",
    "tr",
    # Result
    "Ok",
    "Err",
    "Result",
    "unwrap",
    "unwrap_or",
    # Config & Runtime
    "PluginConfig",
    "Plugins",
    "PluginRouter",
    "SystemInfo",
    "MemoryClient",
    "PluginStore",
    # Errors
    "SdkError",
    "TransportError",
    # Logging
    "get_plugin_logger",
]
