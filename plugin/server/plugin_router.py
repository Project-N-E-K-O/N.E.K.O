"""Compatibility module for plugin request router.

Router implementation moved to `plugin.server.messaging.request_router`.
"""
from __future__ import annotations

from plugin.server.messaging.request_router import PluginRouter, plugin_router

__all__ = ["PluginRouter", "plugin_router"]

