"""Plugin-side SDK v2 surface.

Primary import target for standard plugin development.
"""

from __future__ import annotations

import sys as _sys
from importlib import import_module as _import_module

from . import base as _base
from . import decorators as _decorators
# 避免 `from . import llm_tool as _llm_tool` 在 importlib.reload 时被外部 `llm_tool` 函数名
# 遮蔽（第一次加载后本模块的 `llm_tool` 属性变成函数，导致下次 `from . import llm_tool` 拿回函数而非子模块）喵
_llm_tool = _sys.modules.get("plugin.sdk.plugin.llm_tool") or _import_module("plugin.sdk.plugin.llm_tool")
from . import activity as _activity
from . import runtime as _runtime
from . import settings as _settings
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
quick_action = _decorators.quick_action

# --- LLM tool ---
llm_tool = _llm_tool.llm_tool
LlmToolMeta = _llm_tool.LlmToolMeta

# --- Activity ---
OsActivitySnapshot = _activity.OsActivitySnapshot
get_os_activity_snapshot = _activity.get_os_activity_snapshot

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
PluginStore = _runtime.PluginStore

# --- Errors ---
SdkError = _runtime.SdkError
TransportError = _runtime.TransportError

# --- Logging ---
get_plugin_logger = _runtime.get_plugin_logger

# --- Settings ---
PluginSettings = _settings.PluginSettings
SettingsField = _settings.SettingsField

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
    "quick_action",
    "ui",
    # LLM tool
    "llm_tool",
    "LlmToolMeta",
    # Activity
    "OsActivitySnapshot",
    "get_os_activity_snapshot",
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
    "PluginStore",
    # Errors
    "SdkError",
    "TransportError",
    # Logging
    "get_plugin_logger",
    # Settings
    "PluginSettings",
    "SettingsField",
]
