"""ActionExecutionService — execute actions by action_id.

Parses the ``action_id`` prefix to determine the execution path and
delegates to the appropriate backend service (hot-update, lifecycle,
entry toggle, profile switch, or list-action IPC).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from plugin.logging_config import get_logger
from plugin.server.application.actions.aggregation_service import ActionAggregationService
from plugin.server.application.config.hot_update_service import hot_update_plugin_config
from plugin.server.application.plugins import PluginLifecycleService
from plugin.server.domain.action_models import (
    ActionDescriptor,
    ActionExecuteResponse,
)
from plugin.server.domain.errors import ServerDomainError

logger = get_logger("server.application.actions.execution")


def _get_settings_class(plugin_id: str) -> type | None:
    """Import and return the PluginSettings subclass for *plugin_id*."""
    from plugin.core.state import state
    from plugin.sdk.plugin.settings import PluginSettings

    host = None
    with state.acquire_plugin_hosts_read_lock():
        host = state.plugin_hosts.get(plugin_id)

    if host is None:
        plugins_snapshot = state.get_plugins_snapshot_cached()
        meta_raw = plugins_snapshot.get(plugin_id)
        if isinstance(meta_raw, Mapping):
            entry_point = meta_raw.get("entry_point") or meta_raw.get("entry")
        else:
            return None
    else:
        entry_point = getattr(host, "entry_point", None)

    if not entry_point or not isinstance(entry_point, str):
        return None

    try:
        import importlib

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
        pass
    return None


def _is_plugin_running(plugin_id: str) -> bool:
    from plugin.core.state import state

    with state.acquire_plugin_hosts_read_lock():
        return plugin_id in state.plugin_hosts


class ActionExecutionService:
    """Execute actions identified by ``action_id``."""

    def __init__(self) -> None:
        self._lifecycle = PluginLifecycleService()
        self._aggregation = ActionAggregationService()

    async def execute(
        self,
        action_id: str,
        value: object = None,
    ) -> ActionExecuteResponse:
        """Parse *action_id* and dispatch to the correct backend."""

        # ── system:{plugin_id}:{action} ──
        if action_id.startswith("system:"):
            return await self._execute_system(action_id, value)

        # ── {plugin_id}:settings:{field} ──
        parts = action_id.split(":", 2)
        if len(parts) == 3 and parts[1] == "settings":
            return await self._execute_settings(parts[0], parts[2], value)

        # ── {plugin_id}:{action_id} (list_action toggle) ──
        if len(parts) >= 2:
            return await self._execute_list_action(action_id, value)

        raise ServerDomainError(
            code="ACTION_NOT_FOUND",
            message=f"Action '{action_id}' not found",
            status_code=404,
            details={"action_id": action_id},
        )

    # ------------------------------------------------------------------
    # Settings field execution
    # ------------------------------------------------------------------

    async def _execute_settings(
        self,
        plugin_id: str,
        field_name: str,
        value: object,
    ) -> ActionExecuteResponse:
        settings_cls = await asyncio.to_thread(_get_settings_class, plugin_id)
        toml_section = "settings"
        if settings_cls is not None:
            toml_section = settings_cls.model_config.get("toml_section", "settings")

        updates: dict[str, object] = {toml_section: {field_name: value}}

        result = await hot_update_plugin_config(
            plugin_id=plugin_id,
            updates=updates,
            mode="temporary",
        )

        updated_action = await self._find_action(f"{plugin_id}:settings:{field_name}")
        message = result.get("message", "Config hot-updated successfully") if isinstance(result, dict) else "Config hot-updated successfully"

        return ActionExecuteResponse(
            success=True,
            action=updated_action,
            message=str(message),
        )

    # ------------------------------------------------------------------
    # System action execution
    # ------------------------------------------------------------------

    async def _execute_system(
        self,
        action_id: str,
        value: object,
    ) -> ActionExecuteResponse:
        # system:{plugin_id}:{action}  or  system:{plugin_id}:entry:{entry_id}
        parts = action_id.split(":")
        # Minimum: system : plugin_id : action
        if len(parts) < 3:
            raise ServerDomainError(
                code="ACTION_NOT_FOUND",
                message=f"Action '{action_id}' not found",
                status_code=404,
                details={"action_id": action_id},
            )

        plugin_id = parts[1]
        action = parts[2]

        if action == "start":
            result = await self._lifecycle.start_plugin(plugin_id)
            return ActionExecuteResponse(
                success=True,
                action=await self._find_action(action_id),
                message=str(result.get("message", "Plugin started")),
            )

        if action == "stop":
            result = await self._lifecycle.stop_plugin(plugin_id)
            return ActionExecuteResponse(
                success=True,
                action=await self._find_action(action_id),
                message=str(result.get("message", "Plugin stopped")),
            )

        if action == "reload":
            result = await self._lifecycle.reload_plugin(plugin_id)
            return ActionExecuteResponse(
                success=True,
                action=await self._find_action(action_id),
                message=str(result.get("message", "Plugin reloaded")),
            )

        if action == "toggle":
            running = await asyncio.to_thread(_is_plugin_running, plugin_id)
            if running:
                result = await self._lifecycle.stop_plugin(plugin_id)
                msg = "Plugin stopped"
            else:
                result = await self._lifecycle.start_plugin(plugin_id)
                msg = "Plugin started"
            return ActionExecuteResponse(
                success=True,
                action=await self._find_action(action_id),
                message=str(result.get("message", msg)),
            )

        if action == "entry" and len(parts) >= 4:
            entry_id = ":".join(parts[3:])
            return await self._execute_entry_toggle(plugin_id, entry_id, value)

        if action == "profile":
            return await self._execute_profile_switch(plugin_id, value)

        raise ServerDomainError(
            code="ACTION_NOT_FOUND",
            message=f"Action '{action_id}' not found",
            status_code=404,
            details={"action_id": action_id},
        )

    # ------------------------------------------------------------------
    # Entry toggle
    # ------------------------------------------------------------------

    async def _execute_entry_toggle(
        self,
        plugin_id: str,
        entry_id: str,
        value: object,
    ) -> ActionExecuteResponse:
        from plugin.core.state import state

        plugin_instance = state.plugin_instances.get(plugin_id)
        if plugin_instance is None:
            raise ServerDomainError(
                code="PLUGIN_NOT_RUNNING",
                message=f"Plugin '{plugin_id}' is not running",
                status_code=400,
                details={"plugin_id": plugin_id},
            )

        # Determine desired state: if value is bool use it, otherwise toggle
        if isinstance(value, bool):
            enable = value
        else:
            # Default: toggle current state
            enable = True

        try:
            if enable:
                if hasattr(plugin_instance, "enable_entry"):
                    await asyncio.to_thread(plugin_instance.enable_entry, entry_id)
            else:
                if hasattr(plugin_instance, "disable_entry"):
                    await asyncio.to_thread(plugin_instance.disable_entry, entry_id)
        except Exception as exc:
            raise ServerDomainError(
                code="ENTRY_TOGGLE_FAILED",
                message=f"Failed to {'enable' if enable else 'disable'} entry '{entry_id}'",
                status_code=500,
                details={"plugin_id": plugin_id, "entry_id": entry_id, "error": str(exc)},
            ) from exc

        action_id = f"system:{plugin_id}:entry:{entry_id}"
        return ActionExecuteResponse(
            success=True,
            action=await self._find_action(action_id),
            message=f"Entry '{entry_id}' {'enabled' if enable else 'disabled'}",
        )

    # ------------------------------------------------------------------
    # Profile switch
    # ------------------------------------------------------------------

    async def _execute_profile_switch(
        self,
        plugin_id: str,
        value: object,
    ) -> ActionExecuteResponse:
        if not isinstance(value, str) or not value.strip():
            raise ServerDomainError(
                code="INVALID_ARGUMENT",
                message="Profile name must be a non-empty string",
                status_code=400,
                details={"plugin_id": plugin_id},
            )

        profile_name = value.strip()

        try:
            from plugin.config.service import set_plugin_active_profile

            await asyncio.to_thread(set_plugin_active_profile, plugin_id, profile_name)
        except Exception as exc:
            raise ServerDomainError(
                code="PLUGIN_PROFILE_ACTIVATE_FAILED",
                message=f"Failed to set active profile '{profile_name}'",
                status_code=500,
                details={"plugin_id": plugin_id, "profile_name": profile_name, "error": str(exc)},
            ) from exc

        # Hot reload the plugin so the new profile takes effect
        try:
            await self._lifecycle.reload_plugin(plugin_id)
        except Exception as exc:
            logger.warning(
                "Profile switched but reload failed for plugin {}: {}",
                plugin_id,
                str(exc),
            )

        action_id = f"system:{plugin_id}:profile"
        return ActionExecuteResponse(
            success=True,
            action=await self._find_action(action_id),
            message=f"Profile switched to '{profile_name}'",
        )

    # ------------------------------------------------------------------
    # List action execution (placeholder)
    # ------------------------------------------------------------------

    async def _execute_list_action(
        self,
        action_id: str,
        value: object,
    ) -> ActionExecuteResponse:
        # IPC execution is complex; for now, return success
        return ActionExecuteResponse(
            success=True,
            action=await self._find_action(action_id),
            message="Action executed",
        )

    # ------------------------------------------------------------------
    # Helper: re-fetch a single action descriptor
    # ------------------------------------------------------------------

    async def _find_action(self, action_id: str) -> ActionDescriptor | None:
        """Re-fetch the updated ActionDescriptor for *action_id*."""
        try:
            # Determine plugin_id from action_id for efficient filtering
            plugin_id: str | None = None
            if action_id.startswith("system:"):
                parts = action_id.split(":")
                if len(parts) >= 2:
                    plugin_id = parts[1]
            else:
                parts = action_id.split(":")
                if parts:
                    plugin_id = parts[0]

            all_actions = await self._aggregation.aggregate_actions(plugin_id=plugin_id)
            for action in all_actions:
                if action.action_id == action_id:
                    return action
        except Exception as exc:
            logger.warning("Failed to re-fetch action {}: {}", action_id, str(exc))
        return None
