"""Internal plugin base building blocks.

This module is intentionally explicit: it exposes the shared base primitives the
plugin facade can compose with, without using dynamic namespace injection.
"""

from plugin.sdk_v2.shared.core.base import NEKO_PLUGIN_META_ATTR, NEKO_PLUGIN_TAG, NekoPluginBase, PluginMeta

__all__ = [
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "PluginMeta",
    "NekoPluginBase",
]
