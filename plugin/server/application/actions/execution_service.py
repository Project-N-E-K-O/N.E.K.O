"""ActionExecutionService — execute actions by action_id.

Parses the ``action_id`` prefix to determine the execution path and
delegates to the appropriate handler module.

Execution paths:
* ``system:{plugin_id}:{action}`` → ``_SystemActionHandler``
* ``{plugin_id}:settings:{field}`` → ``_SettingsActionHandler``
* ``{plugin_id}:{action_id}``      → ``_ListActionHandler``
"""

from __future__ import annotations

import asyncio
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
from plugin.server.infrastructure.plugin_settings_resolver import resolve_settings_class

logger = get_logger("server.application.actions.execution")


# ======================================================================
# Shared helper
# ======================================================================

def _is_plugin_running(plugin_id: str) -> bool:
    from plugin.core.state import state

    with state.acquire_plugin_hosts_read_lock():
        return plugin_id in state.plugin_hosts


async def _find_action(
    aggregation: ActionAggregationService,
    action_id: str,
) -> ActionDescriptor | None:
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

        all_actions = await aggregation.aggregate_actions(plugin_id=plugin_id)
        for action in all_actions:
            if action.action_id == action_id:
                return action
    except Exception as exc:
        logger.warning("Failed to re-fetch action {}: {}", action_id, str(exc))
    return None


# ======================================================================
# Settings handler
# ======================================================================

class _SettingsActionHandler:
    """Handle ``{plugin_id}:settings:{field}`` actions via config hot-update."""

    def __init__(self, aggregation: ActionAggregationService) -> None:
        self._aggregation = aggregation

    async def execute(
        self,
        plugin_id: str,
        field_name: str,
        value: object,
    ) -> ActionExecuteResponse:
        settings_cls = await asyncio.to_thread(
            resolve_settings_class, plugin_id,
        )
        toml_section = "settings"
        if settings_cls is not None:
            toml_section = settings_cls.model_config.get("toml_section", "settings")

        updates: dict[str, object] = {toml_section: {field_name: value}}

        result = await hot_update_plugin_config(
            plugin_id=plugin_id,
            updates=updates,
            mode="temporary",
        )

        updated_action = await _find_action(
            self._aggregation, f"{plugin_id}:settings:{field_name}",
        )
        message = (
            result.get("message", "Config hot-updated successfully")
            if isinstance(result, dict)
            else "Config hot-updated successfully"
        )

        return ActionExecuteResponse(
            success=True,
            action=updated_action,
            message=str(message),
        )


# ======================================================================
# System handler
# ======================================================================

class _SystemActionHandler:
    """Handle ``system:{plugin_id}:{action}`` actions."""

    def __init__(
        self,
        lifecycle: PluginLifecycleService,
        aggregation: ActionAggregationService,
    ) -> None:
        self._lifecycle = lifecycle
        self._aggregation = aggregation

    async def execute(
        self,
        action_id: str,
        value: object,
    ) -> ActionExecuteResponse:
        parts = action_id.split(":")
        if len(parts) < 3:
            raise ServerDomainError(
                code="ACTION_NOT_FOUND",
                message=f"Action '{action_id}' not found",
                status_code=404,
                details={"action_id": action_id},
            )

        plugin_id = parts[1]
        action = parts[2]

        handler = self._DISPATCH.get(action)
        if handler is not None:
            return await handler(self, plugin_id, action_id, value)

        # entry:{entry_id}
        if action == "entry" and len(parts) >= 4:
            entry_id = ":".join(parts[3:])
            return await self._entry_toggle(plugin_id, entry_id, action_id, value)

        raise ServerDomainError(
            code="ACTION_NOT_FOUND",
            message=f"Action '{action_id}' not found",
            status_code=404,
            details={"action_id": action_id},
        )

    # -- Lifecycle actions --

    async def _start(
        self, plugin_id: str, action_id: str, value: object,
    ) -> ActionExecuteResponse:
        result = await self._lifecycle.start_plugin(plugin_id)
        return ActionExecuteResponse(
            success=True,
            action=await _find_action(self._aggregation, action_id),
            message=str(result.get("message", "Plugin started")),
        )

    async def _stop(
        self, plugin_id: str, action_id: str, value: object,
    ) -> ActionExecuteResponse:
        result = await self._lifecycle.stop_plugin(plugin_id)
        return ActionExecuteResponse(
            success=True,
            action=await _find_action(self._aggregation, action_id),
            message=str(result.get("message", "Plugin stopped")),
        )

    async def _reload(
        self, plugin_id: str, action_id: str, value: object,
    ) -> ActionExecuteResponse:
        result = await self._lifecycle.reload_plugin(plugin_id)
        return ActionExecuteResponse(
            success=True,
            action=await _find_action(self._aggregation, action_id),
            message=str(result.get("message", "Plugin reloaded")),
        )

    async def _toggle(
        self, plugin_id: str, action_id: str, value: object,
    ) -> ActionExecuteResponse:
        running = await asyncio.to_thread(_is_plugin_running, plugin_id)
        if running:
            result = await self._lifecycle.stop_plugin(plugin_id)
            msg = "Plugin stopped"
        else:
            result = await self._lifecycle.start_plugin(plugin_id)
            msg = "Plugin started"
        return ActionExecuteResponse(
            success=True,
            action=await _find_action(self._aggregation, action_id),
            message=str(result.get("message", msg)),
        )

    async def _profile(
        self, plugin_id: str, action_id: str, value: object,
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
                details={
                    "plugin_id": plugin_id,
                    "profile_name": profile_name,
                    "error": str(exc),
                },
            ) from exc

        try:
            await self._lifecycle.reload_plugin(plugin_id)
        except Exception as exc:
            logger.warning(
                "Profile switched but reload failed for plugin {}: {}",
                plugin_id,
                str(exc),
            )

        return ActionExecuteResponse(
            success=True,
            action=await _find_action(self._aggregation, action_id),
            message=f"Profile switched to '{profile_name}'",
        )

    # -- Entry toggle --

    async def _entry_toggle(
        self,
        plugin_id: str,
        entry_id: str,
        action_id: str,
        value: object,
    ) -> ActionExecuteResponse:
        # Button-type entries (action, hook, timer, …) send value=null.
        # They don't have enable/disable semantics — just acknowledge.
        if not isinstance(value, bool):
            return ActionExecuteResponse(
                success=True,
                action=await _find_action(self._aggregation, action_id),
                message=f"Entry '{entry_id}' triggered",
            )

        # Toggle-type entries (service) send value=true/false.
        running = await asyncio.to_thread(_is_plugin_running, plugin_id)
        if not running:
            raise ServerDomainError(
                code="PLUGIN_NOT_RUNNING",
                message=f"Plugin '{plugin_id}' is not running",
                status_code=400,
                details={"plugin_id": plugin_id},
            )

        enable = value

        try:
            from plugin.core.state import state

            host = None
            with state.acquire_plugin_hosts_read_lock():
                host = state.plugin_hosts.get(plugin_id)

            if host is not None:
                method_name = "enable_entry" if enable else "disable_entry"
                method = getattr(host, method_name, None)
                if method is not None:
                    await asyncio.to_thread(method, entry_id)
        except Exception as exc:
            raise ServerDomainError(
                code="ENTRY_TOGGLE_FAILED",
                message=f"Failed to {'enable' if enable else 'disable'} entry '{entry_id}'",
                status_code=500,
                details={
                    "plugin_id": plugin_id,
                    "entry_id": entry_id,
                    "error": str(exc),
                },
            ) from exc

        return ActionExecuteResponse(
            success=True,
            action=await _find_action(self._aggregation, action_id),
            message=f"Entry '{entry_id}' {'enabled' if enable else 'disabled'}",
        )

    # Dispatch table — avoids long if/elif chains
    _DISPATCH: dict[str, Any] = {
        "start": _start,
        "stop": _stop,
        "reload": _reload,
        "toggle": _toggle,
        "profile": _profile,
    }


# ======================================================================
# List action handler
# ======================================================================

class _ListActionHandler:
    """Handle ``{plugin_id}:{action_id}`` list actions."""

    async def execute(
        self,
        action_id: str,
        value: object,
    ) -> ActionExecuteResponse:
        # List action IPC execution is not yet implemented.
        raise ServerDomainError(
            code="ACTION_NOT_IMPLEMENTED",
            message=f"List action '{action_id}' execution is not yet implemented",
            status_code=501,
            details={"action_id": action_id},
        )


# ======================================================================
# Public facade
# ======================================================================

class ActionExecutionService:
    """Execute actions identified by ``action_id``.

    Delegates to specialised handler classes based on the action_id prefix.
    """

    def __init__(self) -> None:
        self._lifecycle = PluginLifecycleService()
        self._aggregation = ActionAggregationService()
        self._settings_handler = _SettingsActionHandler(self._aggregation)
        self._system_handler = _SystemActionHandler(self._lifecycle, self._aggregation)
        self._list_action_handler = _ListActionHandler()

    async def execute(
        self,
        action_id: str,
        value: object = None,
    ) -> ActionExecuteResponse:
        """Parse *action_id* and dispatch to the correct handler."""

        # system:{plugin_id}:{action}
        if action_id.startswith("system:"):
            return await self._system_handler.execute(action_id, value)

        # {plugin_id}:settings:{field}
        parts = action_id.split(":", 2)
        if len(parts) == 3 and parts[1] == "settings":
            return await self._settings_handler.execute(parts[0], parts[2], value)

        # {plugin_id}:{action_id} (list_action)
        if len(parts) >= 2:
            return await self._list_action_handler.execute(action_id, value)

        raise ServerDomainError(
            code="ACTION_NOT_FOUND",
            message=f"Action '{action_id}' not found",
            status_code=404,
            details={"action_id": action_id},
        )
