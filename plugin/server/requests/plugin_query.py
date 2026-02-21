from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from plugin.core.state import state


logger = logger.bind(component="router")


async def handle_plugin_query(request: Dict[str, Any], send_response) -> None:
    from plugin.core.status import status_manager

    from_plugin = request.get("from_plugin")
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    filters = request.get("filters") or {}
    plugin_ids = filters.get("plugin_ids")
    name_contains = filters.get("name_contains")
    status_in = filters.get("status_in")
    has_entry = filters.get("has_entry")
    has_custom_event = filters.get("has_custom_event")
    include_events = bool(filters.get("include_events", False))

    try:
        # 使用缓存快照避免锁竞争
        plugins_snapshot = state.get_plugins_snapshot_cached(timeout=1.0)
        event_handlers_snapshot = state.get_event_handlers_snapshot_cached(timeout=1.0)

        statuses_snapshot = status_manager.get_plugin_status()
        status_by_pid: Dict[str, str] = {}
        if isinstance(statuses_snapshot, dict):
            for pid, s in statuses_snapshot.items():
                try:
                    status_by_pid[pid] = str((s.get("status") or {}).get("status") or "unknown")
                except Exception:
                    status_by_pid[pid] = "unknown"

        events_by_pid: Dict[str, Dict[str, Any]] = {}
        if include_events or has_entry or has_custom_event:
            for key, eh in event_handlers_snapshot.items():
                meta = getattr(eh, "meta", None)
                event_type = getattr(meta, "event_type", None) if meta else None
                event_id = getattr(meta, "id", None) if meta else None

                pid = None
                if isinstance(key, str):
                    if ":" in key:
                        parts = key.split(":", 2)
                        if len(parts) == 3:
                            pid = parts[0]
                            if event_type is None:
                                event_type = parts[1]
                            if event_id is None:
                                event_id = parts[2]
                    elif "." in key:
                        parts = key.split(".", 1)
                        if len(parts) == 2:
                            pid = parts[0]
                            if event_type is None:
                                event_type = "plugin_entry"
                            if event_id is None:
                                event_id = parts[1]

                if not pid or not event_type or not event_id:
                    continue

                bucket = events_by_pid.setdefault(pid, {"plugin_entry": [], "custom": []})
                if event_type == "plugin_entry":
                    bucket["plugin_entry"].append(event_id)
                elif event_type in ("lifecycle", "message", "timer"):
                    continue
                else:
                    bucket["custom"].append({"event_type": event_type, "event_id": event_id})

        results: list[dict[str, Any]] = []
        for pid, meta in plugins_snapshot.items():
            if plugin_ids:
                if isinstance(plugin_ids, str):
                    if pid != plugin_ids:
                        continue
                elif isinstance(plugin_ids, (list, tuple, set)):
                    if pid not in plugin_ids:
                        continue

            if name_contains:
                s = str(name_contains).lower()
                name = str(meta.get("name", "")).lower()
                desc = str(meta.get("description", "")).lower()
                if s not in pid.lower() and s not in name and s not in desc:
                    continue

            status_value = status_by_pid.get(pid, "unknown")
            if status_in:
                allowed = set([str(x) for x in status_in]) if isinstance(status_in, (list, tuple, set)) else {str(status_in)}
                if status_value not in allowed:
                    continue

            if has_entry:
                entry_id = str(has_entry)
                entries = (events_by_pid.get(pid) or {}).get("plugin_entry") or []
                if entry_id not in entries:
                    continue

            if has_custom_event:
                et = str((has_custom_event or {}).get("event_type") or "")
                eid = str((has_custom_event or {}).get("event_id") or "")
                customs = (events_by_pid.get(pid) or {}).get("custom") or []
                if not any((c.get("event_type") == et and c.get("event_id") == eid) for c in customs if isinstance(c, dict)):
                    continue

            item = {
                "plugin_id": pid,
                "name": meta.get("name"),
                "description": meta.get("description"),
                "version": meta.get("version"),
                "sdk_version": meta.get("sdk_version"),
                "status": status_value,
            }
            if include_events:
                item["events"] = events_by_pid.get(pid, {"plugin_entry": [], "custom": []})
            results.append(item)

        send_response(from_plugin, request_id, {"plugins": results}, None, timeout=timeout)
    except Exception as e:
        logger.exception(f"[PluginRouter] Error handling plugin query: {e}")
        send_response(from_plugin, request_id, None, str(e), timeout=timeout)
