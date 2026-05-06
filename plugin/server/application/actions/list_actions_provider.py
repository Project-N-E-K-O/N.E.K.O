"""ListActionsProvider — map plugin ``list_actions`` to ActionDescriptors.

Each plugin may register a set of *list_actions* in its metadata (stored
in ``state.plugins[pid]``).  This provider reads them and converts each
entry into the appropriate ``ActionDescriptor`` based on its ``kind``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from plugin.logging_config import get_logger
from plugin.server.domain.action_models import ActionDescriptor

logger = get_logger("server.application.actions.list_actions_provider")


# ---------------------------------------------------------------------------
# Kind → ActionDescriptor mapping
# ---------------------------------------------------------------------------

_NAVIGATION_KINDS = frozenset({"ui", "url", "route"})
_CHAT_INJECT_KIND = "chat_inject"


def _safe_priority(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.debug("Ignoring invalid list_action priority: {}", value)
        return 0


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    return bool(value)


def _map_list_action(
    plugin_id: str,
    plugin_name: str,
    action: dict[str, Any],
) -> ActionDescriptor | None:
    """Convert a single list_action dict to an ``ActionDescriptor``."""
    action_id = action.get("id")
    if not isinstance(action_id, str) or not action_id.strip():
        return None

    kind = str(action.get("kind", "")).strip().lower()
    label = str(action.get("label", action_id))
    description = str(action.get("description", ""))
    full_action_id = f"{plugin_id}:{action_id}"
    quick = _safe_bool(action.get("quick_action", False))
    icon_override = action.get("icon")
    priority_override = action.get("priority")

    if kind == _CHAT_INJECT_KIND:
        target = action.get("target")
        if not isinstance(target, str) or not target.strip():
            inject_text = f"@{plugin_name} /{action_id}"
        else:
            inject_text = target
        return ActionDescriptor(
            action_id=full_action_id,
            type="chat_inject",
            label=label,
            description=description,
            category=plugin_name,
            plugin_id=plugin_id,
            inject_text=inject_text,
            icon=icon_override or "📎",
            keywords=[plugin_id, plugin_name, action_id, label],
            quick_action=quick,
            priority=_safe_priority(priority_override),
        )

    if kind in _NAVIGATION_KINDS:
        raw_target = action.get("target")
        if not isinstance(raw_target, str) or not raw_target.strip():
            logger.debug(
                "Skipping navigation list_action with invalid target plugin_id={} action_id={}",
                plugin_id,
                action_id,
            )
            return None
        target = raw_target.strip()
        open_in = action.get("open_in")
        if open_in not in ("new_tab", "same_tab"):
            open_in = "new_tab"
        return ActionDescriptor(
            action_id=full_action_id,
            type="navigation",
            label=label,
            description=description,
            category=plugin_name,
            plugin_id=plugin_id,
            target=target,
            open_in=open_in,
            icon=icon_override or "↗",
            keywords=[plugin_id, plugin_name, action_id, label],
            quick_action=quick,
            priority=_safe_priority(priority_override),
        )

    if kind:
        logger.debug(
            "Skipping non-routable list_action plugin_id={} action_id={} kind={}",
            plugin_id,
            action_id,
            kind,
        )
    return None


# ---------------------------------------------------------------------------
# Synchronous core (runs in thread)
# ---------------------------------------------------------------------------

def _collect_list_actions_sync(
    plugin_id_filter: str | None = None,
) -> list[ActionDescriptor]:
    """Collect list_actions-derived descriptors (called from a worker thread)."""
    from plugin.core.state import state

    plugins_snapshot = state.get_plugins_snapshot_cached()
    actions: list[ActionDescriptor] = []

    for pid, meta_raw in plugins_snapshot.items():
        if plugin_id_filter is not None and pid != plugin_id_filter:
            continue

        if not isinstance(meta_raw, Mapping):
            continue
        meta: dict[str, Any] = dict(meta_raw)
        plugin_name = str(meta.get("name") or pid)

        # list_actions may be stored under the "list_actions" key in metadata
        raw_list_actions = meta.get("list_actions")
        if not isinstance(raw_list_actions, (list, tuple)):
            continue

        for raw_action in raw_list_actions:
            if not isinstance(raw_action, Mapping):
                continue
            action_dict = dict(raw_action)
            descriptor = _map_list_action(pid, plugin_name, action_dict)
            if descriptor is not None:
                actions.append(descriptor)

    return actions


# ---------------------------------------------------------------------------
# Public provider
# ---------------------------------------------------------------------------

class ListActionsProvider:
    """Generate ``ActionDescriptor`` items from plugin ``list_actions``."""

    async def get_actions(
        self,
        plugin_id: str | None = None,
    ) -> list[ActionDescriptor]:
        return await asyncio.to_thread(_collect_list_actions_sync, plugin_id)
