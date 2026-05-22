"""Galgame plugin package entry point.

The plugin runtime resolves ``plugin.toml``'s
``entry = "plugin.plugins.galgame_plugin:GalgamePlugin"`` against this module,
so re-exporting ``GalgamePlugin`` (and its ``GalgameBridgePlugin`` alias) from
``plugin_core`` keeps the public import surface unchanged after the PR2 split.

Before the split, ``__init__.py`` was a 7,500-line monolith whose top-level
imports (``time``, ``json_copy``, ``build_summarize_context``,
``MemoryReaderManager``, ...) became attributes of the package object.
Several tests and external callers reach into that surface
(``monkeypatch.setattr(galgame_plugin_module, "build_summarize_context", ...)``,
``from plugin.plugins.galgame_plugin import GalgamePluginConfigService``),
so we star-import from ``plugin_core`` here to keep the original public
attribute surface intact. The explicit private re-exports below cover the
two underscore-prefixed helpers tests monkeypatch — star-import skips them.
``__all__`` stays narrow (only the three classes external code is meant to
depend on) so ``from plugin.plugins.galgame_plugin import *`` still yields a
curated surface.
"""
from __future__ import annotations

from .plugin_config_service import GalgamePluginConfigService
from .plugin_core import *  # noqa: F401, F403 — preserve original package surface
from .plugin_core import (  # explicit: star-import skips underscore names
    _after_advance_screen_refresh_needed,
    _open_url_in_browser,
)

__all__ = ["GalgameBridgePlugin", "GalgamePlugin", "GalgamePluginConfigService"]
