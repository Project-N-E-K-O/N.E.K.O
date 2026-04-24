"""SystemActionProvider — entry actions and UI navigation for running plugins.

For every registered *running* plugin this provider generates:

* Button actions for non-service entries (action, hook, timer, chat_command, …)
* Navigation action for plugins with static UI

Lifecycle management (start/stop/reload/toggle) and service toggles are
intentionally excluded — those belong in the plugin management dashboard,
not in the chat command palette.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from plugin.logging_config import get_logger
from plugin.server.domain.action_models import ActionDescriptor

logger = get_logger("server.application.actions.system_provider")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _has_static_ui(meta: dict[str, Any]) -> bool:
    """Check whether a plugin has a usable static UI directory."""
    static_ui_obj = meta.get("static_ui_config")
    if not isinstance(static_ui_obj, Mapping):
        return False
    enabled = _to_bool(static_ui_obj.get("enabled"), default=False)
    if not enabled:
        return False
    directory = static_ui_obj.get("directory")
    if not isinstance(directory, str) or not directory:
        return False
    index_file = static_ui_obj.get("index_file", "index.html")
    if not isinstance(index_file, str) or not index_file:
        index_file = "index.html"
    p = Path(directory)
    return p.is_dir() and (p / index_file).is_file()


def _get_entries_for_plugin(
    plugin_id: str,
    handlers_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract entry info dicts for *plugin_id* from the event_handlers snapshot."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    for key, handler in handlers_snapshot.items():
        if not isinstance(key, str):
            continue

        if key.startswith(f"{plugin_id}."):
            entry_id = key[len(plugin_id) + 1:]
        elif key.startswith(f"{plugin_id}:plugin_entry:"):
            entry_id = key[len(f"{plugin_id}:plugin_entry:"):]
        else:
            continue

        if entry_id in seen:
            continue
        seen.add(entry_id)

        meta = getattr(handler, "meta", None)
        entry_name = getattr(meta, "name", entry_id) if meta else entry_id
        entry_kind = getattr(meta, "kind", "action") if meta else "action"
        entry_description = getattr(meta, "description", "") if meta else ""
        entry_input_schema = getattr(meta, "input_schema", None) if meta else None
        is_quick = bool(getattr(meta, "quick_action", False)) if meta else False
        qa_config = getattr(meta, "quick_action_config", None) if meta else None

        entries.append({
            "id": entry_id,
            "name": entry_name,
            "kind": entry_kind,
            "description": entry_description,
            "input_schema": entry_input_schema,
            "quick_action": is_quick,
            "quick_action_icon": getattr(qa_config, "icon", None) if qa_config else None,
            "quick_action_priority": getattr(qa_config, "priority", 0) if qa_config else 0,
        })

    return entries


# ---------------------------------------------------------------------------
# Synchronous core (runs in thread)
# ---------------------------------------------------------------------------

def _collect_system_actions_sync(
    plugin_id_filter: str | None = None,
) -> list[ActionDescriptor]:
    """Collect user-facing actions from running plugins (called from a worker thread)."""
    from plugin.core.state import state

    plugins_snapshot = state.get_plugins_snapshot_cached()
    hosts_snapshot: dict[str, Any] = {}
    with state.acquire_plugin_hosts_read_lock():
        hosts_snapshot = dict(state.plugin_hosts)
    handlers_snapshot = state.get_event_handlers_snapshot_cached()

    actions: list[ActionDescriptor] = []

    for pid, meta_raw in plugins_snapshot.items():
        if plugin_id_filter is not None and pid != plugin_id_filter:
            continue

        if not isinstance(meta_raw, Mapping):
            continue
        meta: dict[str, Any] = dict(meta_raw)
        plugin_name = str(meta.get("name") or pid)
        is_running = pid in hosts_snapshot

        # ── Entry actions (running plugins only) ──
        # Only non-service entries are exposed as user-facing commands.
        # Services are background processes managed via the admin dashboard.
        if is_running:
            entries = _get_entries_for_plugin(pid, handlers_snapshot)
            for entry in entries:
                entry_kind = entry.get("kind", "action")

                # Skip service entries — they are not user-facing commands
                if entry_kind == "service":
                    continue

                entry_id = entry["id"]
                entry_name = entry.get("name", entry_id)
                entry_desc = entry.get("description", "")

                # chat_command entries appear as slash commands (chat_inject)
                if entry_kind == "chat_command":
                    actions.append(ActionDescriptor(
                        action_id=f"system:{pid}:entry:{entry_id}",
                        type="chat_inject",
                        label=str(entry_name),
                        description=str(entry_desc),
                        category=plugin_name,
                        plugin_id=pid,
                        inject_text=f"@{plugin_name} /{entry_id}",
                        icon="📎",
                        keywords=[pid, plugin_name, str(entry_name), entry_id],
                        quick_action=entry.get("quick_action", False),
                    ))
                    continue

                is_quick = entry.get("quick_action", False)
                qa_icon = entry.get("quick_action_icon")
                qa_priority = entry.get("quick_action_priority", 0)

                raw_schema = entry.get("input_schema")
                schema: dict[str, object] | None = None
                if isinstance(raw_schema, dict):
                    props = raw_schema.get("properties")
                    if isinstance(props, dict) and len(props) > 0:
                        schema = raw_schema

                actions.append(ActionDescriptor(
                    action_id=f"system:{pid}:entry:{entry_id}",
                    type="instant",
                    label=str(entry_name),
                    description=str(entry_desc),
                    category=plugin_name,
                    plugin_id=pid,
                    control="button",
                    input_schema=schema,
                    icon=str(qa_icon) if qa_icon else "⚡",
                    keywords=[pid, plugin_name, str(entry_name)],
                    quick_action=bool(is_quick),
                    priority=int(qa_priority) if is_quick else 0,
                ))

        # ── Static UI navigation ──
        if _has_static_ui(meta):
            from config import USER_PLUGIN_SERVER_PORT as _ui_port
            actions.append(ActionDescriptor(
                action_id=f"system:{pid}:open_ui",
                type="navigation",
                label=f"打开 {plugin_name} UI",
                description="",
                category=plugin_name,
                plugin_id=pid,
                target=f"http://127.0.0.1:{_ui_port}/plugin/{pid}/ui/",
                open_in="new_tab",
                icon="↗",
                keywords=[pid, plugin_name, "ui", "界面"],
            ))

    return actions


# ---------------------------------------------------------------------------
# Public provider
# ---------------------------------------------------------------------------

class SystemActionProvider:
    """Generate user-facing ``ActionDescriptor`` items for running plugins."""

    async def get_actions(
        self,
        plugin_id: str | None = None,
    ) -> list[ActionDescriptor]:
        return await asyncio.to_thread(_collect_system_actions_sync, plugin_id)
