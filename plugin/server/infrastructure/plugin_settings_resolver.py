"""Resolve a plugin's ``PluginSettings`` subclass from its entry_point.

Centralises the "import plugin class → get .Settings → check it's a
PluginSettings subclass" logic that was previously duplicated across
``execution_service``, ``settings_provider``, and ``hot_update_service``.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any

from plugin.logging_config import get_logger

logger = get_logger("server.infrastructure.plugin_settings_resolver")


def resolve_settings_class(
    plugin_id: str,
    *,
    host: Any = None,
) -> type | None:
    """Return the ``PluginSettings`` subclass for *plugin_id*, or ``None``.

    Resolution order:
    1. If *host* is provided, read ``host.entry_point``.
    2. Otherwise fall back to the plugins snapshot metadata
       (``entry_point`` or ``entry`` key).

    Returns ``None`` when the plugin has no ``Settings`` inner class or
    when the import fails (backward-compat plugins).
    """
    from plugin.core.state import state
    from plugin.sdk.plugin.settings import PluginSettings

    # Determine entry_point
    entry_point: str | None = None
    if host is not None:
        entry_point = getattr(host, "entry_point", None)
    if not entry_point:
        plugins_snapshot = state.get_plugins_snapshot_cached()
        meta_raw = plugins_snapshot.get(plugin_id)
        if isinstance(meta_raw, Mapping):
            entry_point = meta_raw.get("entry_point") or meta_raw.get("entry")

    if not entry_point or not isinstance(entry_point, str):
        return None

    try:
        module_path, class_name = entry_point.split(":", 1)
        mod = importlib.import_module(module_path)
        plugin_cls = getattr(mod, class_name, None)
        if plugin_cls is None:
            return None
        settings_cls = getattr(plugin_cls, "Settings", None)
        if settings_cls is None:
            return None
        if isinstance(settings_cls, type) and issubclass(settings_cls, PluginSettings):
            return settings_cls
    except Exception:
        logger.debug(
            "Failed to resolve PluginSettings for plugin_id={}, entry_point={}",
            plugin_id,
            entry_point,
        )
    return None
