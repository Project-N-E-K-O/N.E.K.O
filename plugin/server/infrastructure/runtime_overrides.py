"""User-level persistence for per-plugin runtime toggles.

Plugin manifests declare a default ``plugin_runtime.enabled`` value. The plugin
manager UI lets users override that default at runtime via plugin start/stop and
extension enable/disable actions. Without persistence those toggles live only in
:data:`plugin.core.state.state.plugins` and are lost on restart.

This module persists the user-toggled subset to ``plugin_runtime_overrides.json``
under the user's app config directory (``ConfigManager.config_dir``). On the
next registry scan the override is layered on top of the manifest's default so
the user's choice survives restarts.

Only entries the user actually toggled are stored; the file is intentionally
small and sparse so that re-installing or upgrading a plugin still inherits
its manifest defaults unless the user already had an explicit preference.
Legacy boolean entries remain valid and represent an ``enabled`` override only.
"""

from __future__ import annotations

import threading
from typing import Mapping

from plugin.logging_config import get_logger

logger = get_logger("server.infrastructure.runtime_overrides")

OVERRIDES_FILENAME = "plugin_runtime_overrides.json"

_cache_lock = threading.Lock()
RuntimeOverride = bool | dict[str, bool]

_cache: dict[str, RuntimeOverride] | None = None


class RuntimeOverridePersistenceError(OSError):
    """Base error for durable runtime-preference storage failures."""


class RuntimeOverrideReadError(RuntimeOverridePersistenceError):
    """The persisted runtime preferences could not be read safely."""


class RuntimeOverrideWriteError(RuntimeOverridePersistenceError):
    """Runtime preferences could not be committed to durable storage."""


def _coerce_overrides(raw: object) -> dict[str, RuntimeOverride]:
    if not isinstance(raw, Mapping):
        raise RuntimeOverrideReadError(
            f"{OVERRIDES_FILENAME} must contain a JSON object"
        )
    result: dict[str, RuntimeOverride] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise RuntimeOverrideReadError(
                f"{OVERRIDES_FILENAME} contains an invalid plugin id"
            )
        if isinstance(value, bool):
            result[key] = value
            continue
        if isinstance(value, Mapping):
            unknown_fields = set(value) - {"enabled", "auto_start"}
            invalid_fields = {
                field
                for field in ("enabled", "auto_start")
                if field in value and not isinstance(value[field], bool)
            }
            if unknown_fields or invalid_fields or not value:
                raise RuntimeOverrideReadError(
                    f"{OVERRIDES_FILENAME} contains invalid fields for plugin {key!r}"
                )
            normalized = {
                field: field_value
                for field in ("enabled", "auto_start")
                if isinstance((field_value := value.get(field)), bool)
            }
            result[key] = normalized
            continue
        raise RuntimeOverrideReadError(
            f"{OVERRIDES_FILENAME} contains an invalid entry for plugin {key!r}"
        )
    return result


def _load_from_disk() -> dict[str, RuntimeOverride]:
    try:
        from utils.config_manager import get_config_manager

        cm = get_config_manager()
        raw = cm.load_json_config(OVERRIDES_FILENAME)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.error(
            "Failed to load plugin runtime overrides from {}: {}",
            OVERRIDES_FILENAME,
            exc,
        )
        raise RuntimeOverrideReadError(
            f"Failed to load plugin runtime overrides from {OVERRIDES_FILENAME}"
        ) from exc
    return _coerce_overrides(raw)


def _save_to_disk(overrides: dict[str, RuntimeOverride]) -> None:
    try:
        from utils.config_manager import get_config_manager

        cm = get_config_manager()
        cm.save_json_config(OVERRIDES_FILENAME, dict(overrides))
    except Exception as exc:
        logger.error(
            "Failed to persist plugin runtime overrides to {}: {}",
            OVERRIDES_FILENAME,
            exc,
        )
        raise RuntimeOverrideWriteError(
            f"Failed to persist plugin runtime overrides to {OVERRIDES_FILENAME}"
        ) from exc


def load_runtime_overrides() -> dict[str, RuntimeOverride]:
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
    override = load_runtime_overrides().get(plugin_id)
    if isinstance(override, bool):
        return override
    if isinstance(override, Mapping):
        enabled = override.get("enabled")
        return enabled if isinstance(enabled, bool) else None
    return None


def get_runtime_auto_start_override(plugin_id: str) -> bool | None:
    """Return the user's auto-start preference, if one was persisted."""
    if not plugin_id:
        return None
    override = load_runtime_overrides().get(plugin_id)
    if not isinstance(override, Mapping):
        return None
    auto_start = override.get("auto_start")
    return auto_start if isinstance(auto_start, bool) else None


def set_runtime_override(
    plugin_id: str,
    enabled: bool,
    *,
    auto_start: bool | None = None,
) -> None:
    """Persist user runtime preferences for ``plugin_id``.

    ``enabled`` is always updated. When ``auto_start`` is omitted, an existing
    auto-start preference is preserved; when provided, both fields are stored
    together.

    The disk write happens while ``_cache_lock`` is still held so that two
    concurrent toggles cannot race and overwrite each other with stale
    snapshots (each writer would see only its own mutation).
    """
    if not plugin_id:
        return
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = _load_from_disk()
        new_value: RuntimeOverride
        if auto_start is None:
            existing = _cache.get(plugin_id)
            if isinstance(existing, Mapping):
                new_value = dict(existing)
                new_value["enabled"] = enabled
            else:
                new_value = enabled
        else:
            new_value = {"enabled": enabled, "auto_start": auto_start}
        if _cache.get(plugin_id) == new_value:
            return
        candidate = dict(_cache)
        candidate[plugin_id] = new_value
        _save_to_disk(candidate)
        _cache = candidate


def clear_runtime_override(plugin_id: str) -> None:
    """Remove the override for ``plugin_id`` (e.g. when the plugin is deleted).

    Holds ``_cache_lock`` across the disk write for the same race-avoidance
    reason as :func:`set_runtime_override`.
    """
    if not plugin_id:
        return
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = _load_from_disk()
        if plugin_id not in _cache:
            return
        candidate = dict(_cache)
        candidate.pop(plugin_id, None)
        _save_to_disk(candidate)
        _cache = candidate


def reset_cache_for_testing() -> None:
    """Reset in-memory cache; intended for tests that swap the underlying store."""
    global _cache
    with _cache_lock:
        _cache = None
