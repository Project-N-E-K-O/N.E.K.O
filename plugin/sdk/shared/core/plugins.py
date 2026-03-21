"""Cross-plugin call contract for SDK v2 shared core."""

from __future__ import annotations

from typing import Mapping, TypedDict

from plugin.sdk.shared.models import Err, Ok, Result
from plugin.sdk.shared.models.exceptions import (
    InvalidArgumentError,
    InvalidEntryRefError,
    InvalidEventRefError,
    PluginCallError,
)
from .context import ensure_sdk_context
from .types import EntryRef, EventRef, JsonObject, JsonValue, PluginContextProtocol

PluginOpError = InvalidArgumentError | PluginCallError


class PluginDescriptor(TypedDict, total=False):
    plugin_id: str
    name: str
    version: str
    enabled: bool
    metadata: JsonObject


def parse_entry_ref(entry_ref: str) -> EntryRef:
    """Parse `<plugin_id>:<entry_id>` into a typed ref object."""
    parts = entry_ref.split(":")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise InvalidEntryRefError(
            f"Invalid entry ref: {entry_ref!r}",
            entry_ref=entry_ref,
            op_name="plugins.parse_entry_ref",
        )
    return EntryRef(plugin_id=parts[0], entry_id=parts[1])


def parse_event_ref(event_ref: str) -> EventRef:
    """Parse `<plugin_id>:<event_type>:<event_id>` into a typed ref object."""
    parts = event_ref.split(":")
    if len(parts) != 3 or not parts[0] or not parts[1] or not parts[2]:
        raise InvalidEventRefError(
            f"Invalid event ref: {event_ref!r}",
            event_ref=event_ref,
            op_name="plugins.parse_event_ref",
        )
    return EventRef(plugin_id=parts[0], event_type=parts[1], event_id=parts[2])


class Plugins:
    """Async-only plugin call contract."""

    def __init__(self, ctx: PluginContextProtocol):
        self.ctx = ensure_sdk_context(ctx)

    @staticmethod
    def _validate_timeout(timeout: float) -> Result[None, InvalidArgumentError]:
        if timeout <= 0:
            return Err(InvalidArgumentError("timeout must be > 0"))
        return Ok(None)

    async def list(self, *, timeout: float = 5.0) -> Result[list[PluginDescriptor], PluginOpError]:
        """List discoverable plugins."""
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return timeout_ok
        try:
            result = await self.ctx.query_plugins({}, timeout=timeout)
        except AttributeError:
            return Err(PluginCallError("ctx.query_plugins is not available", op_name="plugins.list", timeout=timeout))
        except (RuntimeError, ValueError, TimeoutError, TypeError) as error:
            return Err(PluginCallError(f"failed to query plugins: {error}", op_name="plugins.list", timeout=timeout))
        if not isinstance(result, dict):
            return Err(PluginCallError(f"invalid plugin list response type: {type(result)!r}", op_name="plugins.list", timeout=timeout))
        plugins = result.get("plugins", [])
        if not isinstance(plugins, list):
            return Err(PluginCallError("invalid plugin list response: plugins must be list", op_name="plugins.list", timeout=timeout))
        output: list[PluginDescriptor] = []
        for item in plugins:
            if isinstance(item, dict):
                output.append(item)
        return Ok(output)

    async def call(
        self,
        *,
        plugin_id: str,
        event_type: str,
        event_id: str,
        params: Mapping[str, JsonValue] | None = None,
        timeout: float = 10.0,
    ) -> Result[JsonObject | JsonValue | None, PluginOpError]:
        """Call a plugin event by explicit coordinates."""
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return timeout_ok
        try:
            response = await self.ctx.trigger_plugin_event(
                target_plugin_id=plugin_id,
                event_type=event_type,
                event_id=event_id,
                params=dict(params or {}),
                timeout=timeout,
            )
        except AttributeError:
            return Err(
                PluginCallError(
                    "ctx.trigger_plugin_event is not available",
                    op_name="plugins.call",
                    plugin_id=plugin_id,
                    event_type=event_type,
                    event_id=event_id,
                    timeout=timeout,
                )
            )
        except (RuntimeError, ValueError, TimeoutError, TypeError, KeyError) as error:
            return Err(
                PluginCallError(
                    f"plugin call failed: {error}",
                    op_name="plugins.call",
                    plugin_id=plugin_id,
                    event_type=event_type,
                    event_id=event_id,
                    timeout=timeout,
                )
            )
        return Ok(response)

    async def call_entry(
        self,
        entry_ref: str,
        params: Mapping[str, JsonValue] | None = None,
        *,
        timeout: float = 10.0,
    ) -> Result[JsonObject | JsonValue | None, PluginOpError]:
        """Call `<plugin_id>:<entry_id>`."""
        try:
            parsed = parse_entry_ref(entry_ref)
        except InvalidEntryRefError as error:
            return Err(error)
        return await self.call(
            plugin_id=parsed.plugin_id,
            event_type="plugin_entry",
            event_id=parsed.entry_id,
            params=params,
            timeout=timeout,
        )

    async def call_event(
        self,
        event_ref: str,
        params: Mapping[str, JsonValue] | None = None,
        *,
        timeout: float = 10.0,
    ) -> Result[JsonObject | JsonValue | None, PluginOpError]:
        """Call `<plugin_id>:<event_type>:<event_id>`."""
        try:
            parsed = parse_event_ref(event_ref)
        except InvalidEventRefError as error:
            return Err(error)
        return await self.call(
            plugin_id=parsed.plugin_id,
            event_type=parsed.event_type,
            event_id=parsed.event_id,
            params=params,
            timeout=timeout,
        )

    async def require(self, plugin_id: str, *, timeout: float = 5.0) -> Result[PluginDescriptor, PluginOpError]:
        """Ensure plugin exists and return descriptor."""
        listed = await self.list(timeout=timeout)
        if isinstance(listed, Err):
            return listed
        for descriptor in listed.value:
            if descriptor.get("plugin_id") == plugin_id:
                return Ok(descriptor)
        return Err(PluginCallError(f"required plugin not found: {plugin_id!r}", op_name="plugins.require", plugin_id=plugin_id, timeout=timeout))


__all__ = [
    "PluginDescriptor",
    "PluginCallError",
    "InvalidEntryRefError",
    "InvalidEventRefError",
    "parse_entry_ref",
    "parse_event_ref",
    "Plugins",
]
