"""Galgame plugin package entry point.

The plugin runtime resolves ``plugin.toml``'s
``entry = "plugin.plugins.galgame_plugin:GalgamePlugin"`` against this module,
so re-exporting ``GalgamePlugin`` (and its ``GalgameBridgePlugin`` alias) from
``plugin_core`` keeps the public import surface unchanged after the PR2 split.
"""
from __future__ import annotations

from .plugin_core import GalgameBridgePlugin, GalgamePlugin

__all__ = ["GalgameBridgePlugin", "GalgamePlugin"]
