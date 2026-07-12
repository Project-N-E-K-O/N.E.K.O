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
from typing import Iterable, Mapping

from plugin.logging_config import get_logger

logger = get_logger("server.infrastructure.runtime_overrides")

OVERRIDES_FILENAME = "plugin_runtime_overrides.json"

_cache_lock = threading.Lock()
RuntimeOverride = bool | dict[str, bool]

_cache: dict[str, RuntimeOverride] | None = None
_cache_write_blocked_by_invalid_content = False


class RuntimeOverridePersistenceError(OSError):
    """Base error for durable runtime-preference storage failures."""


class RuntimeOverrideReadError(RuntimeOverridePersistenceError):
    """The persisted runtime preferences could not be read safely."""


class RuntimeOverrideWriteError(RuntimeOverridePersistenceError):
    """Runtime preferences could not be committed to durable storage."""


def _coerce_overrides_with_status(
    raw: object,
) -> tuple[dict[str, RuntimeOverride], bool]:
    if not isinstance(raw, Mapping):
        raise RuntimeOverrideReadError(
            f"{OVERRIDES_FILENAME} must contain a JSON object"
        )
    result: dict[str, RuntimeOverride] = {}
    invalid_content_found = False
    for key, value in raw.items():
        if not isinstance(key, str):
            invalid_content_found = True
            logger.warning(
                "Ignoring runtime override with invalid plugin id in {}",
                OVERRIDES_FILENAME,
            )
            continue
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
            if unknown_fields:
                invalid_content_found = True
                logger.warning(
                    "Ignoring unknown runtime override fields for plugin {} in {}: {}",
                    key,
                    OVERRIDES_FILENAME,
                    sorted(str(field) for field in unknown_fields),
                )
            if invalid_fields:
                invalid_content_found = True
                logger.warning(
                    "Ignoring runtime override entry with invalid fields for plugin {} in {}: {}",
                    key,
                    OVERRIDES_FILENAME,
                    sorted(invalid_fields),
                )
                continue
            normalized = {
                field: field_value
                for field in ("enabled", "auto_start")
                if isinstance((field_value := value.get(field)), bool)
            }
            if normalized:
                result[key] = normalized
            else:
                invalid_content_found = True
                logger.warning(
                    "Ignoring invalid runtime override entry for plugin {} in {}",
                    key,
                    OVERRIDES_FILENAME,
                )
            continue
        invalid_content_found = True
        logger.warning(
            "Ignoring invalid runtime override entry for plugin {} in {}",
            key,
            OVERRIDES_FILENAME,
        )
    return result, invalid_content_found


def _coerce_overrides(raw: object) -> dict[str, RuntimeOverride]:
    result, _invalid_content_found = _coerce_overrides_with_status(raw)
    return result


def _load_from_disk() -> dict[str, RuntimeOverride]:
    global _cache_write_blocked_by_invalid_content
    try:
        from utils.config_manager import get_config_manager

        cm = get_config_manager()
        raw = cm.load_json_config(OVERRIDES_FILENAME)
    except FileNotFoundError:
        _cache_write_blocked_by_invalid_content = False
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
    overrides, invalid_content_found = _coerce_overrides_with_status(raw)
    _cache_write_blocked_by_invalid_content = invalid_content_found
    return overrides


def _ensure_cache_can_be_written() -> None:
    if _cache_write_blocked_by_invalid_content:
        raise RuntimeOverrideWriteError(
            f"Refusing to overwrite {OVERRIDES_FILENAME} because it contains invalid content"
        )


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
        return {
            plugin_id: dict(override) if isinstance(override, Mapping) else override
            for plugin_id, override in _cache.items()
        }


def _get_runtime_override_entry(plugin_id: str) -> RuntimeOverride | None:
    if not plugin_id:
        return None
    try:
        return load_runtime_overrides().get(plugin_id)
    except RuntimeOverrideReadError as exc:
        # Runtime preferences are an optional layer over the manifest. A damaged
        # preferences file must not make every plugin undiscoverable or prevent a
        # direct/autostart attempt from falling back to manifest defaults. Writes
        # remain strict, so a later toggle cannot silently overwrite that file.
        logger.warning(
            "Ignoring unreadable plugin runtime overrides for plugin {}: {}",
            plugin_id,
            exc,
        )
        return None


def get_runtime_override(plugin_id: str) -> bool | None:
    """Return the persisted override for ``plugin_id`` or ``None`` if unset."""
    override = _get_runtime_override_entry(plugin_id)
    if isinstance(override, bool):
        return override
    if isinstance(override, Mapping):
        enabled = override.get("enabled")
        return enabled if isinstance(enabled, bool) else None
    return None


def get_runtime_auto_start_override(plugin_id: str) -> bool | None:
    """Return the user's auto-start preference, if one was persisted."""
    override = _get_runtime_override_entry(plugin_id)
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
        _ensure_cache_can_be_written()
        _save_to_disk(candidate)
        _cache = candidate


def migrate_runtime_override(
    stale_plugin_ids: Iterable[str],
    target_plugin_id: str,
    enabled: bool,
    *,
    auto_start: bool | None = None,
) -> None:
    """Migrate and update a preference after runtime plugin-ID resolution.

    All stale-ID removals and the target update are committed in one disk write
    while holding ``_cache_lock``. This covers multi-stage ID resolution, such as
    a registry refresh followed by a load-time conflict rename.
    """
    if not target_plugin_id:
        return
    stale_ids = tuple(
        dict.fromkeys(
            plugin_id
            for plugin_id in stale_plugin_ids
            if plugin_id and plugin_id != target_plugin_id
        )
    )
    if not stale_ids:
        set_runtime_override(target_plugin_id, enabled, auto_start=auto_start)
        return

    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = _load_from_disk()

        existing = next(
            (
                _cache[plugin_id]
                for plugin_id in reversed(stale_ids)
                if plugin_id in _cache
            ),
            _cache.get(target_plugin_id),
        )
        if auto_start is None:
            if isinstance(existing, Mapping):
                new_value: RuntimeOverride = dict(existing)
                new_value["enabled"] = enabled
            else:
                new_value = enabled
        else:
            new_value = {"enabled": enabled, "auto_start": auto_start}

        candidate = dict(_cache)
        for plugin_id in stale_ids:
            candidate.pop(plugin_id, None)
        candidate[target_plugin_id] = new_value
        if candidate == _cache:
            return
        _ensure_cache_can_be_written()
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
        _ensure_cache_can_be_written()
        _save_to_disk(candidate)
        _cache = candidate


def reset_cache_for_testing() -> None:
    """Reset in-memory cache; intended for tests that swap the underlying store."""
    global _cache, _cache_write_blocked_by_invalid_content
    with _cache_lock:
        _cache = None
        _cache_write_blocked_by_invalid_content = False
