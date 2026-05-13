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

# Known lifecycle keywords that authorize the ``system:`` prefix to mean
# "system-handler lifecycle action" rather than "plugin literally named
# system". Module-level so dispatch (``ActionExecutionService.execute``) and
# action_id → plugin_id reverse lookup (``_plugin_id_from_action_id``,
# ``_find_action``) stay in sync.
_SYSTEM_LIFECYCLE_KEYS: frozenset[str] = frozenset({
    "start", "stop", "reload", "toggle", "profile", "entry",
})


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
        plugin_id = _plugin_id_from_action_id(action_id)
        all_actions = await aggregation.aggregate_actions(plugin_id=plugin_id)
        for action in all_actions:
            if action.action_id == action_id:
                return action
    except Exception as exc:
        logger.warning("Failed to re-fetch action {}: {}", action_id, str(exc))
    return None


def _plugin_id_from_action_id(action_id: str) -> str | None:
    """Map an action_id to its owning plugin_id.

    Mirrors the structural dispatch in :class:`ActionExecutionService.execute`
    so a plugin literally named ``system`` doesn't get its settings / list
    actions misclassified as lifecycle calls. The rule (precedence matters):

    1. ``{plugin_id}:settings:{field}`` → ``parts[0]``. Checked first so a
       field name that happens to collide with a lifecycle keyword (e.g.
       ``system:settings:start``) still resolves to plugin ``parts[0]``.
    2. ``system:{plugin_id}:{lifecycle | entry...}`` → ``parts[1]``, only
       when ``parts[2]`` is a known lifecycle keyword.
    3. Otherwise → ``parts[0]`` (covers list actions, including plugin
       "system" cases like ``system:foo``).
    """
    parts = action_id.split(":")
    if not parts:
        return None
    if len(parts) >= 3 and parts[1] == "settings":
        return parts[0]
    if (
        action_id.startswith("system:")
        and len(parts) >= 3
        and parts[2] in _SYSTEM_LIFECYCLE_KEYS
    ):
        return parts[1]
    return parts[0]


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
        if settings_cls is None:
            raise ServerDomainError(
                code="SETTINGS_NOT_FOUND",
                message=f"Plugin '{plugin_id}' has no PluginSettings class",
                status_code=404,
                details={"plugin_id": plugin_id},
            )

        # Validate the field is actually a hot field
        from plugin.sdk.plugin.settings import get_hot_fields
        hot_fields = get_hot_fields(settings_cls)
        if field_name not in hot_fields:
            raise ServerDomainError(
                code="FIELD_NOT_HOT",
                message=f"Field '{field_name}' is not a hot-updatable setting",
                status_code=403,
                details={"plugin_id": plugin_id, "field": field_name},
            )

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
    ) -> None:
        self._lifecycle = lifecycle

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
            # Lifecycle actions (start/stop/reload/toggle/profile) must have
            # exactly three segments — reject malformed ids like
            # `system:demo:stop:unexpected` so crafted tails cannot smuggle
            # extra data into a privileged op.
            if len(parts) != 3:
                raise ServerDomainError(
                    code="ACTION_NOT_FOUND",
                    message=f"Action '{action_id}' not found",
                    status_code=404,
                    details={"action_id": action_id},
                )
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
    # These return action=None because the frontend does a full
    # fetchChatActions() refresh after every execute anyway.

    async def _start(
        self, plugin_id: str, action_id: str, value: object,
    ) -> ActionExecuteResponse:
        result = await self._lifecycle.start_plugin(plugin_id)
        return ActionExecuteResponse(
            success=True,
            message=str(result.get("message", "Plugin started")),
        )

    async def _stop(
        self, plugin_id: str, action_id: str, value: object,
    ) -> ActionExecuteResponse:
        result = await self._lifecycle.stop_plugin(plugin_id)
        return ActionExecuteResponse(
            success=True,
            message=str(result.get("message", "Plugin stopped")),
        )

    async def _reload(
        self, plugin_id: str, action_id: str, value: object,
    ) -> ActionExecuteResponse:
        result = await self._lifecycle.reload_plugin(plugin_id)
        return ActionExecuteResponse(
            success=True,
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
        except ServerDomainError:
            raise
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

        reload_error: str | None = None
        try:
            await self._lifecycle.reload_plugin(plugin_id)
        except Exception as exc:
            reload_error = str(exc) or type(exc).__name__
            logger.warning(
                "Profile switched but reload failed for plugin {}: {}",
                plugin_id,
                reload_error,
            )

        # The profile config DID change, so success=True is still correct.
        # The message must surface the failed reload, though — otherwise the
        # palette tells the user "Profile switched" while the running plugin
        # is still on the old profile until the next manual reload.
        if reload_error is None:
            message = f"Profile switched to '{profile_name}'"
        else:
            message = (
                f"Profile switched to '{profile_name}', but reload failed "
                f"({reload_error}). A manual reload is required for the "
                f"running plugin to pick up the new profile."
            )
        return ActionExecuteResponse(success=True, message=message)

    # -- Entry toggle --

    async def _entry_toggle(
        self,
        plugin_id: str,
        entry_id: str,
        action_id: str,
        value: object,
    ) -> ActionExecuteResponse:
        from plugin.core.state import state

        # Resolve the host — required for both trigger and toggle paths.
        host = None
        with state.acquire_plugin_hosts_read_lock():
            host = state.plugin_hosts.get(plugin_id)

        if host is None:
            raise ServerDomainError(
                code="PLUGIN_NOT_RUNNING",
                message=f"Plugin '{plugin_id}' is not running",
                status_code=400,
                details={"plugin_id": plugin_id},
            )

        # ── Button-type entries (value != bool): trigger execution via IPC ──
        if not isinstance(value, bool):
            # value may be a dict of parameters from the frontend form,
            # or null for entries without input_schema.
            args: dict = value if isinstance(value, dict) else {}
            try:
                trigger = getattr(host, "trigger", None)
                if trigger is not None:
                    result = await trigger(entry_id, args)
                    msg = str(result) if result is not None else f"Entry '{entry_id}' executed"
                else:
                    raise ServerDomainError(
                        code="ENTRY_TRIGGER_UNSUPPORTED",
                        message=f"Plugin host for '{plugin_id}' does not support trigger",
                        status_code=501,
                        details={"plugin_id": plugin_id, "entry_id": entry_id},
                    )
            except ServerDomainError:
                # Preserve intentional domain errors (status/code) instead of
                # collapsing them into a generic 500 ENTRY_TRIGGER_FAILED.
                raise
            except Exception as exc:
                raise ServerDomainError(
                    code="ENTRY_TRIGGER_FAILED",
                    message=f"Failed to trigger entry '{entry_id}': {exc}",
                    status_code=500,
                    details={
                        "plugin_id": plugin_id,
                        "entry_id": entry_id,
                        "error": str(exc),
                    },
                ) from exc

            return ActionExecuteResponse(
                success=True,
                message=msg,
            )

        # ── Toggle-type entries (value == bool): enable/disable service ──
        enable = value

        try:
            method_name = "enable_entry" if enable else "disable_entry"
            method = getattr(host, method_name, None)
            if method is not None:
                await asyncio.to_thread(method, entry_id)
            else:
                raise ServerDomainError(
                    code="ENTRY_TOGGLE_UNSUPPORTED",
                    message=f"Plugin host for '{plugin_id}' does not support {method_name}",
                    status_code=501,
                    details={"plugin_id": plugin_id, "entry_id": entry_id},
                )
        except ServerDomainError:
            # Preserve intentional domain errors (status/code) instead of
            # collapsing them into a generic 500 ENTRY_TOGGLE_FAILED.
            raise
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
        self._system_handler = _SystemActionHandler(self._lifecycle)
        self._list_action_handler = _ListActionHandler()

    # Mirror the module-level constant so existing callers / tests that
    # reach for ``ActionExecutionService._SYSTEM_LIFECYCLE_KEYS`` keep
    # working. The authoritative source is the module-level one above.
    _SYSTEM_LIFECYCLE_KEYS = _SYSTEM_LIFECYCLE_KEYS

    async def execute(
        self,
        action_id: str,
        value: object = None,
    ) -> ActionExecuteResponse:
        """Parse *action_id* and dispatch to the correct handler."""

        parts = action_id.split(":")

        # {plugin_id}:settings:{field} — checked first so that a plugin
        # literally named "system" still routes its settings (e.g.
        # ``system:settings:enabled``) to the settings handler instead of
        # being swallowed by the lifecycle prefix below.
        if len(parts) >= 3 and parts[1] == "settings":
            field_name = ":".join(parts[2:])
            return await self._settings_handler.execute(parts[0], field_name, value)

        # system:{plugin_id}:{lifecycle | entry[:...]} — only treat the
        # ``system:`` prefix as a lifecycle namespace when ``parts[2]`` is a
        # known lifecycle keyword. Otherwise (e.g. plugin "system" exposing a
        # list action ``foo`` as ``system:foo``) fall through to the
        # list-action handler so that namespace doesn't become a privileged
        # tombstone for any plugin unfortunate enough to pick the same name.
        if (
            action_id.startswith("system:")
            and len(parts) >= 3
            and parts[2] in _SYSTEM_LIFECYCLE_KEYS
        ):
            return await self._system_handler.execute(action_id, value)

        # {plugin_id}:{action_id} (list_action) — covers plugin "system"
        # list actions like ``system:foo`` (len(parts) == 2).
        if len(parts) >= 2:
            return await self._list_action_handler.execute(action_id, value)

        raise ServerDomainError(
            code="ACTION_NOT_FOUND",
            message=f"Action '{action_id}' not found",
            status_code=404,
            details={"action_id": action_id},
        )
