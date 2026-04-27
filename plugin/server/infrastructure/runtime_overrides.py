"""User-level persistence for per-plugin runtime toggles.

Plugin manifests declare a default ``plugin_runtime.enabled`` value. The plugin
manager UI lets users override that default at runtime (currently only the
"Disable / Enable Extension" buttons exposed by ``PluginActions.vue``). Without
persistence those toggles live only in :data:`plugin.core.state.state.plugins`
and are lost on restart.

This module persists the user-toggled subset to ``plugin_runtime_overrides.json``
under the user's app config directory (``ConfigManager.config_dir``). On the
next registry scan the override is layered on top of the manifest's default so
the user's choice survives restarts.

Only entries the user actually toggled are stored; the file is intentionally
small and append-only sparse so that re-installing or upgrading a plugin still
inherits its manifest default unless the user already had an explicit
preference.
"""

from __future__ import annotations

import threading
from typing import Mapping

from plugin.logging_config import get_logger

logger = get_logger("server.infrastructure.runtime_overrides")

OVERRIDES_FILENAME = "plugin_runtime_overrides.json"

_cache_lock = threading.Lock()
_cache: dict[str, bool] | None = None


def _coerce_overrides(raw: object) -> dict[str, bool]:
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, bool] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, bool):
            result[key] = value
    return result


def _load_from_disk() -> dict[str, bool]:
    try:
        from utils.config_manager import get_config_manager

        cm = get_config_manager()
        raw = cm.load_json_config(OVERRIDES_FILENAME, default_value={})
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning(
            "Failed to load plugin runtime overrides from {}: {}",
            OVERRIDES_FILENAME,
            exc,
        )
        return {}
    return _coerce_overrides(raw)


def _save_to_disk(overrides: dict[str, bool]) -> None:
    try:
        from utils.config_manager import get_config_manager

        cm = get_config_manager()
        cm.save_json_config(OVERRIDES_FILENAME, dict(overrides))
    except Exception as exc:
        logger.warning(
            "Failed to persist plugin runtime overrides to {}: {}",
            OVERRIDES_FILENAME,
            exc,
        )


def load_runtime_overrides() -> dict[str, bool]:
    """Return a snapshot of the persisted overrides; loads on first access."""
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = _load_from_disk()
        return dict(_cache)


def get_runtime_override(plugin_id: str) -> bool | None:
    """Return the persisted override for ``plugin_id`` or ``None`` if unset."""
    if not plugin_id:
        return None
    return load_runtime_overrides().get(plugin_id)


def set_runtime_override(plugin_id: str, enabled: bool) -> None:
    """Persist ``enabled`` as the user's override for ``plugin_id``."""
    if not plugin_id:
        return
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = _load_from_disk()
        if _cache.get(plugin_id) == enabled:
            return
        _cache[plugin_id] = enabled
        snapshot = dict(_cache)
    _save_to_disk(snapshot)


def clear_runtime_override(plugin_id: str) -> None:
    """Remove the override for ``plugin_id`` (e.g. when the plugin is deleted)."""
    if not plugin_id:
        return
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = _load_from_disk()
        if plugin_id not in _cache:
            return
        _cache.pop(plugin_id, None)
        snapshot = dict(_cache)
    _save_to_disk(snapshot)


def reset_cache_for_testing() -> None:
    """Reset in-memory cache; intended for tests that swap the underlying store."""
    global _cache
    with _cache_lock:
        _cache = None
