"""SystemActionProvider — lifecycle, toggle, entry, UI and profile actions.

For every registered plugin this provider generates:

* ``start`` / ``stop`` / ``reload`` instant actions (with disabled logic)
* ``plugin_toggle`` — running state toggle
* ``entry_toggle`` — per-entry enabled/disabled toggle (running plugins only)
* Navigation action for plugins with static UI
* Profile dropdown for plugins with multiple config profiles
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from plugin.logging_config import get_logger
from plugin.server.domain.action_models import ActionDescriptor

logger = get_logger("server.application.actions.system_provider")

_SYSTEM_CATEGORY = "系统"


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
    """Check whether a plugin has a usable static UI directory.

    Only returns True when the plugin has *explicitly* registered a
    static UI config (via ``register_static_ui`` or ``static_ui_config``
    in metadata).  The config_path inference is intentionally removed
    to avoid false positives for plugins that happen to have a
    ``static/`` directory but don't serve a UI.
    """
    static_ui_obj = meta.get("static_ui_config")
    if not isinstance(static_ui_obj, Mapping):
        return False
    enabled = _to_bool(static_ui_obj.get("enabled"), default=False)
    if not enabled:
        return False
    directory = static_ui_obj.get("directory")
    if not isinstance(directory, str) or not directory:
        return False
    p = Path(directory)
    return p.is_dir() and (p / "index.html").is_file()


def _get_entries_for_plugin(
    plugin_id: str,
    handlers_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract entry info dicts for *plugin_id* from the event_handlers snapshot."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    for key, handler in handlers_snapshot.items():
        # Keys are "{plugin_id}.{entry_id}" or "{plugin_id}:plugin_entry:{entry_id}"
        if not isinstance(key, str):
            continue

        # Prefer the dot-separated key to avoid duplicates
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

        # Determine enabled state from metadata
        enabled = True
        meta_dict = getattr(meta, "metadata", None)
        if isinstance(meta_dict, Mapping):
            enabled = _to_bool(meta_dict.get("enabled", True), default=True)

        entries.append({
            "id": entry_id,
            "name": entry_name,
            "kind": entry_kind,
            "enabled": enabled,
        })

    return entries


def _get_profiles_for_plugin(plugin_id: str) -> dict[str, Any] | None:
    """Return profile state for *plugin_id*, or None if unavailable / single profile."""
    try:
        from plugin.config.service import get_plugin_profiles_state

        profiles_state = get_plugin_profiles_state(plugin_id)
        if not isinstance(profiles_state, dict):
            return None

        config_profiles = profiles_state.get("config_profiles")
        if not isinstance(config_profiles, dict):
            return None

        files = config_profiles.get("files")
        if not isinstance(files, dict) or len(files) < 2:
            return None

        return {
            "active": config_profiles.get("active"),
            "names": list(files.keys()),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Synchronous core (runs in thread)
# ---------------------------------------------------------------------------

def _collect_system_actions_sync(
    plugin_id_filter: str | None = None,
) -> list[ActionDescriptor]:
    """Collect system-level actions (called from a worker thread)."""
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

        # ── Plugin lifecycle (composite: toggle + reload) ──
        actions.append(ActionDescriptor(
            action_id=f"system:{pid}:toggle",
            type="instant",
            label=plugin_name,
            description="",
            category=_SYSTEM_CATEGORY,
            plugin_id=pid,
            control="plugin_lifecycle",
            current_value=is_running,
        ))

        # ── Entry actions (running plugins only) ──
        if is_running:
            entries = _get_entries_for_plugin(pid, handlers_snapshot)
            for entry in entries:
                entry_id = entry["id"]
                entry_name = entry.get("name", entry_id)
                entry_kind = entry.get("kind", "action")

                # Only long-running service entries get a toggle;
                # everything else (action, hook, timer, …) is a button.
                if entry_kind == "service":
                    actions.append(ActionDescriptor(
                        action_id=f"system:{pid}:entry:{entry_id}",
                        type="instant",
                        label=str(entry_name),
                        description="",
                        category=_SYSTEM_CATEGORY,
                        plugin_id=pid,
                        control="entry_toggle",
                        current_value=entry.get("enabled", True),
                    ))
                else:
                    actions.append(ActionDescriptor(
                        action_id=f"system:{pid}:entry:{entry_id}",
                        type="instant",
                        label=str(entry_name),
                        description="",
                        category=_SYSTEM_CATEGORY,
                        plugin_id=pid,
                        control="button",
                    ))

        # ── Static UI navigation ──
        if _has_static_ui(meta):
            from config import USER_PLUGIN_SERVER_PORT as _ui_port
            actions.append(ActionDescriptor(
                action_id=f"system:{pid}:open_ui",
                type="navigation",
                label=f"打开 {plugin_name} UI",
                description="",
                category=_SYSTEM_CATEGORY,
                plugin_id=pid,
                target=f"http://127.0.0.1:{_ui_port}/plugin/{pid}/ui/",
                open_in="new_tab",
            ))

        # ── Config profile dropdown ──
        profiles = _get_profiles_for_plugin(pid)
        if profiles is not None:
            actions.append(ActionDescriptor(
                action_id=f"system:{pid}:profile",
                type="instant",
                label="配置 Profile",
                description="",
                category=_SYSTEM_CATEGORY,
                plugin_id=pid,
                control="dropdown",
                current_value=profiles.get("active"),
                options=profiles.get("names", []),
            ))

    return actions


# ---------------------------------------------------------------------------
# Public provider
# ---------------------------------------------------------------------------

class SystemActionProvider:
    """Generate system-level ``ActionDescriptor`` items for every registered plugin."""

    async def get_actions(
        self,
        plugin_id: str | None = None,
    ) -> list[ActionDescriptor]:
        return await asyncio.to_thread(_collect_system_actions_sync, plugin_id)
