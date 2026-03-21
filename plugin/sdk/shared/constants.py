"""Shared constants used across SDK v2 surfaces.

This module is the single source of truth for SDK-wide constant names and
version metadata.
"""

from __future__ import annotations

SDK_VERSION = "0.1.0"
NEKO_PLUGIN_META_ATTR = "__neko_plugin_meta__"
NEKO_PLUGIN_TAG = "__neko_plugin__"
EVENT_META_ATTR = "__neko_event_meta__"
HOOK_META_ATTR = "__neko_hook_meta__"
PERSIST_ATTR = "_neko_persist"

__all__ = [
    "SDK_VERSION",
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "EVENT_META_ATTR",
    "HOOK_META_ATTR",
    "PERSIST_ATTR",
]
