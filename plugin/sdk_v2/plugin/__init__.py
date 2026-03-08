"""Plugin-side SDK v2 surface.

Primary import target for standard plugin development.
"""

from .base import NEKO_PLUGIN_META_ATTR, NEKO_PLUGIN_TAG, NekoPluginBase, PluginMeta
from .decorators import (
    CHECKPOINT_ATTR,
    PERSIST_ATTR,
    EntryKind,
    after_entry,
    around_entry,
    before_entry,
    custom_event,
    hook,
    lifecycle,
    message,
    neko_plugin,
    on_event,
    plugin,
    plugin_entry,
    replace_entry,
    timer_interval,
)
from . import runtime as _runtime

# Re-export runtime symbols explicitly via runtime.__all__ without star import.
for _name in _runtime.__all__:
    globals()[_name] = getattr(_runtime, _name)

del _name

__all__ = [
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "NekoPluginBase",
    "PluginMeta",
    "neko_plugin",
    "plugin_entry",
    "lifecycle",
    "on_event",
    "message",
    "timer_interval",
    "custom_event",
    "plugin",
    "hook",
    "before_entry",
    "after_entry",
    "around_entry",
    "replace_entry",
    "EntryKind",
    "PERSIST_ATTR",
    "CHECKPOINT_ATTR",
    *_runtime.__all__,
]
