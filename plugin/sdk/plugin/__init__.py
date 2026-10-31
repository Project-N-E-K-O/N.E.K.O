"""Plugin-side SDK v2 surface.

Primary import target for standard plugin development.
"""

from __future__ import annotations

import sys as _sys
from importlib import import_module as _import_module

from . import base as _base
from . import decorators as _decorators
# Prevent `from . import llm_tool as _llm_tool` from being shadowed by the exported `llm_tool` function during importlib.reload
# (after the first load, this module's `llm_tool` attribute is a function, so the next `from . import llm_tool` returns it instead of the submodule), meow
_llm_tool = _sys.modules.get("plugin.sdk.plugin.llm_tool") or _import_module("plugin.sdk.plugin.llm_tool")
from . import activity as _activity
from . import runtime as _runtime
from . import settings as _settings
from . import ui as ui
from plugin.sdk.shared.core.types import PushMessageResult
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
    "PushMessageResult",
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

from plugin.sdk.shared.core.cards import ChatCard, CardSubmissionError, PluginView

__all__ += ["ChatCard", "CardSubmissionError", "PluginView"]

_MEMORY_TYPES = (
    "MemoryTarget", "MemorySubject", "MemoryHit", "MemorySearchResult",
    "MemoryPersona", "MemoryPersonaSection", "MemoryPersonaEntry", "MemoryError",
)
_CONVERSATION_TYPES = (
    "ConversationMessage", "ReplyOrigin", "ConversationChange", "InputSubmission",
    "InputSubmittedEvent", "ReplyDetail", "ReplyEvent", "PlaybackDetail",
    "InteractionInput", "InteractionDetail",
)
__all__ += ["MemoryClient", *_MEMORY_TYPES, *_CONVERSATION_TYPES]


def __getattr__(name: str):
    if name == "MemoryClient":
        module = _import_module("plugin.sdk.shared.memory.client")
    elif name in _MEMORY_TYPES:
        module = _import_module("plugin.sdk.shared.contracts.memory")
    elif name in _CONVERSATION_TYPES:
        module = _import_module("plugin.sdk.shared.contracts.conversations")
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
