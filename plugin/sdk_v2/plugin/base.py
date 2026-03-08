"""Plugin flavor base contracts.

This module exposes the plugin-author base API as a focused facade over the
shared implementation layer instead of mirroring the whole module namespace.
"""

from __future__ import annotations

from plugin.sdk_v2.shared.constants import NEKO_PLUGIN_META_ATTR, NEKO_PLUGIN_TAG
from plugin.sdk_v2.shared.core.base import NekoPluginBase, PluginMeta

__all__ = [
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "PluginMeta",
    "NekoPluginBase",
]
