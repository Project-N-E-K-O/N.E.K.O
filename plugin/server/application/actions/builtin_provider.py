"""BuiltinActionsProvider — always-available commands for the command palette.

These actions are available regardless of whether a plugin exposes entries
or settings.  For every registered plugin, this provider generates
start / stop / reload buttons so the command palette is never empty.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from plugin.logging_config import get_logger
from plugin.server.domain.action_models import ActionDescriptor

logger = get_logger("server.application.actions.builtin_provider")


def _collect_builtin_actions_sync(
    plugin_id_filter: str | None = None,
) -> list[ActionDescriptor]:
    """Collect built-in actions (called from a worker thread)."""
    from plugin.core.state import state

    actions: list[ActionDescriptor] = []

    # ── Plugin lifecycle: start/stop for every registered plugin ──
    # This gives users a way to manage plugins from the command palette
    # without the old full lifecycle control panel.
    plugins_snapshot = state.get_plugins_snapshot_cached()
    hosts_snapshot: dict[str, Any] = {}
    with state.acquire_plugin_hosts_read_lock():
        hosts_snapshot = dict(state.plugin_hosts)

    for pid, meta_raw in plugins_snapshot.items():
        if plugin_id_filter is not None and pid != plugin_id_filter:
            continue

        if not isinstance(meta_raw, Mapping):
            continue
        meta: dict[str, Any] = dict(meta_raw)
        plugin_name = str(meta.get("name") or pid)
        is_running = pid in hosts_snapshot

        if is_running:
            actions.append(ActionDescriptor(
                action_id=f"system:{pid}:stop",
                type="instant",
                label=f"停止 {plugin_name}",
                description="",
                category="插件管理",
                plugin_id=pid,
                control="button",
                icon="⏹",
                keywords=[pid, plugin_name, "stop", "停止", "关闭"],
                priority=-10,
            ))
            actions.append(ActionDescriptor(
                action_id=f"system:{pid}:reload",
                type="instant",
                label=f"重载 {plugin_name}",
                description="",
                category="插件管理",
                plugin_id=pid,
                control="button",
                icon="🔄",
                keywords=[pid, plugin_name, "reload", "重载", "刷新"],
                priority=-10,
            ))
        else:
            actions.append(ActionDescriptor(
                action_id=f"system:{pid}:start",
                type="instant",
                label=f"启动 {plugin_name}",
                description="",
                category="插件管理",
                plugin_id=pid,
                control="button",
                icon="▶",
                keywords=[pid, plugin_name, "start", "启动", "开启"],
                priority=-10,
            ))

    return actions


class BuiltinActionsProvider:
    """Generate always-available built-in ``ActionDescriptor`` items."""

    async def get_actions(
        self,
        plugin_id: str | None = None,
    ) -> list[ActionDescriptor]:
        return await asyncio.to_thread(_collect_builtin_actions_sync, plugin_id)
