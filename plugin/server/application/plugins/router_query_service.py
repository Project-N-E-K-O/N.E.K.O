from __future__ import annotations

import asyncio
from collections.abc import Mapping

from plugin.core.state import state
from plugin.core.status import status_manager
from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError

logger = get_logger("server.application.plugins.router_query")


def _normalize_mapping(raw: Mapping[object, object], *, context: str) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ServerDomainError(
                code="INVALID_DATA_SHAPE",
                message=f"{context} contains non-string key",
                status_code=500,
                details={"key_type": type(key).__name__},
            )
        normalized[key] = value
    return normalized


def _extract_status_value(raw_status: object) -> str:
    if not isinstance(raw_status, Mapping):
        return "unknown"
    nested_status = raw_status.get("status")
    if isinstance(nested_status, Mapping):
        status_value = nested_status.get("status")
        if isinstance(status_value, str) and status_value:
            return status_value
    return "unknown"


def _build_status_index(raw_statuses: object) -> dict[str, str]:
    if not isinstance(raw_statuses, Mapping):
        return {}

    status_index: dict[str, str] = {}
    for plugin_id_obj, status_obj in raw_statuses.items():
        if not isinstance(plugin_id_obj, str):
            continue
        status_index[plugin_id_obj] = _extract_status_value(status_obj)
    return status_index


def _parse_event_key(key: str) -> tuple[str | None, str | None, str | None]:
    if ":" in key:
        parts = key.split(":", 2)
        if len(parts) != 3:
            return None, None, None
        return parts[0], parts[1], parts[2]

    if "." in key:
        parts = key.split(".", 1)
        if len(parts) != 2:
            return None, None, None
        return parts[0], "plugin_entry", parts[1]

    return None, None, None


def _coerce_plugin_id_filter(value: object) -> set[str] | None:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return {stripped}
        return None
    if isinstance(value, (list, tuple, set)):
        plugin_ids: set[str] = set()
        for item in value:
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    plugin_ids.add(stripped)
        return plugin_ids if plugin_ids else None
    return None


def _coerce_status_filter(value: object) -> set[str] | None:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return {stripped}
        return None
    if isinstance(value, (list, tuple, set)):
        statuses: set[str] = set()
        for item in value:
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    statuses.add(stripped)
        return statuses if statuses else None
    return None


def _coerce_custom_event_filter(value: object) -> tuple[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    event_type_obj = value.get("event_type")
    event_id_obj = value.get("event_id")
    if not isinstance(event_type_obj, str) or not event_type_obj:
        return None
    if not isinstance(event_id_obj, str) or not event_id_obj:
        return None
    return event_type_obj, event_id_obj


def _build_events_index(handlers_snapshot: Mapping[str, object]) -> dict[str, dict[str, object]]:
    events_by_plugin: dict[str, dict[str, object]] = {}
    for key, handler_obj in handlers_snapshot.items():
        plugin_id, event_type, event_id = _parse_event_key(key)
        if not plugin_id or not event_type or not event_id:
            continue

        meta = getattr(handler_obj, "meta", None)
        if meta is not None:
            event_type_meta = getattr(meta, "event_type", None)
            event_id_meta = getattr(meta, "id", None)
            if isinstance(event_type_meta, str) and event_type_meta:
                event_type = event_type_meta
            if isinstance(event_id_meta, str) and event_id_meta:
                event_id = event_id_meta

        bucket = events_by_plugin.setdefault(plugin_id, {"plugin_entry": [], "custom": []})
        plugin_entries_obj = bucket.get("plugin_entry")
        custom_events_obj = bucket.get("custom")
        if not isinstance(plugin_entries_obj, list) or not isinstance(custom_events_obj, list):
            continue

        if event_type == "plugin_entry":
            if event_id not in plugin_entries_obj:
                plugin_entries_obj.append(event_id)
            continue
        if event_type in {"lifecycle", "message", "timer"}:
            continue

        custom_item = {"event_type": event_type, "event_id": event_id}
        if custom_item not in custom_events_obj:
            custom_events_obj.append(custom_item)
    return events_by_plugin


def _query_plugins_sync(filters: Mapping[str, object] | None) -> list[dict[str, object]]:
    normalized_filters = _normalize_mapping(filters, context="plugin_query.filters") if filters is not None else {}

    plugin_id_filter = _coerce_plugin_id_filter(normalized_filters.get("plugin_ids"))
    name_contains_obj = normalized_filters.get("name_contains")
    name_contains = name_contains_obj.lower() if isinstance(name_contains_obj, str) and name_contains_obj else None
    status_filter = _coerce_status_filter(normalized_filters.get("status_in"))
    has_entry_obj = normalized_filters.get("has_entry")
    has_entry = has_entry_obj if isinstance(has_entry_obj, str) and has_entry_obj else None
    custom_filter = _coerce_custom_event_filter(normalized_filters.get("has_custom_event"))
    include_events = bool(normalized_filters.get("include_events", False))

    plugins_snapshot = state.get_plugins_snapshot_cached(timeout=1.0)
    handlers_snapshot = state.get_event_handlers_snapshot_cached(timeout=1.0)
    status_snapshot = status_manager.get_plugin_status()

    status_by_plugin = _build_status_index(status_snapshot)
    events_by_plugin: dict[str, dict[str, object]] = {}
    if include_events or has_entry is not None or custom_filter is not None:
        normalized_handlers: dict[str, object] = {}
        for key_obj, value in handlers_snapshot.items():
            if isinstance(key_obj, str):
                normalized_handlers[key_obj] = value
        events_by_plugin = _build_events_index(normalized_handlers)

    results: list[dict[str, object]] = []
    for plugin_id_obj, meta_obj in plugins_snapshot.items():
        if not isinstance(plugin_id_obj, str):
            continue
        if not isinstance(meta_obj, Mapping):
            continue

        plugin_id = plugin_id_obj
        plugin_meta = _normalize_mapping(meta_obj, context=f"plugins[{plugin_id}]")

        if plugin_id_filter is not None and plugin_id not in plugin_id_filter:
            continue

        if name_contains is not None:
            name_text = str(plugin_meta.get("name", "")).lower()
            description_text = str(plugin_meta.get("description", "")).lower()
            if name_contains not in plugin_id.lower() and name_contains not in name_text and name_contains not in description_text:
                continue

        status_value = status_by_plugin.get(plugin_id, "unknown")
        if status_filter is not None and status_value not in status_filter:
            continue

        plugin_events = events_by_plugin.get(plugin_id)
        entries: list[str] = []
        custom_events: list[dict[str, str]] = []
        if isinstance(plugin_events, Mapping):
            entries_obj = plugin_events.get("plugin_entry")
            custom_obj = plugin_events.get("custom")
            if isinstance(entries_obj, list):
                entries = [item for item in entries_obj if isinstance(item, str)]
            if isinstance(custom_obj, list):
                custom_events = [
                    {"event_type": item.get("event_type"), "event_id": item.get("event_id")}
                    for item in custom_obj
                    if isinstance(item, Mapping)
                    and isinstance(item.get("event_type"), str)
                    and isinstance(item.get("event_id"), str)
                ]

        if has_entry is not None and has_entry not in entries:
            continue
        if custom_filter is not None:
            matched_custom = any(
                item["event_type"] == custom_filter[0] and item["event_id"] == custom_filter[1]
                for item in custom_events
            )
            if not matched_custom:
                continue

        item: dict[str, object] = {
            "plugin_id": plugin_id,
            "name": plugin_meta.get("name"),
            "description": plugin_meta.get("description"),
            "version": plugin_meta.get("version"),
            "sdk_version": plugin_meta.get("sdk_version"),
            "status": status_value,
        }
        if include_events:
            item["events"] = {
                "plugin_entry": entries,
                "custom": custom_events,
            }
        results.append(item)

    return results


class PluginRouterQueryService:
    async def query_plugins(self, *, filters: Mapping[str, object] | None) -> list[dict[str, object]]:
        try:
            return await asyncio.to_thread(_query_plugins_sync, filters)
        except ServerDomainError:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            logger.error(
                "query_plugins failed: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_QUERY_FAILED",
                message="Failed to query plugins",
                status_code=500,
                details={"error_type": type(exc).__name__},
            ) from exc
