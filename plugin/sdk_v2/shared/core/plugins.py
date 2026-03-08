"""Cross-plugin call contract for SDK v2 shared core."""

from __future__ import annotations

from typing import Mapping, TypedDict

from plugin.sdk_v2.shared.models import Err, Ok, Result
from .types import EntryRef, EventRef, JsonObject, JsonValue, PluginContextProtocol


class PluginDescriptor(TypedDict, total=False):
    plugin_id: str
    name: str
    version: str
    enabled: bool
    metadata: JsonObject


class PluginCallError(RuntimeError):
    """Cross-plugin call contract violation / timeout / transport error."""


class InvalidEntryRefError(PluginCallError):
    """Invalid `<plugin_id>:<entry_id>` reference."""


class InvalidEventRefError(PluginCallError):
    """Invalid `<plugin_id>:<event_type>:<event_id>` reference."""


def parse_entry_ref(entry_ref: str) -> EntryRef:
    """Parse `<plugin_id>:<entry_id>` into a typed ref object."""
    parts = entry_ref.split(":")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise InvalidEntryRefError(f"Invalid entry ref: {entry_ref!r}")
    return EntryRef(plugin_id=parts[0], entry_id=parts[1])


def parse_event_ref(event_ref: str) -> EventRef:
    """Parse `<plugin_id>:<event_type>:<event_id>` into a typed ref object."""
    parts = event_ref.split(":")
    if len(parts) != 3 or not parts[0] or not parts[1] or not parts[2]:
        raise InvalidEventRefError(f"Invalid event ref: {event_ref!r}")
    return EventRef(plugin_id=parts[0], event_type=parts[1], event_id=parts[2])


class Plugins:
    """Async-only plugin call contract."""

    def __init__(self, _ctx: PluginContextProtocol):
        self._ctx = _ctx

    @staticmethod
    def _validate_timeout(timeout: float) -> Result[None, Exception]:
        if timeout <= 0:
            return Err(ValueError("timeout must be > 0"))
        return Ok(None)

    async def list(self, *, timeout: float = 5.0) -> Result[list[PluginDescriptor], Exception]:
        """List discoverable plugins."""
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return timeout_ok
        query_plugins_async = getattr(self._ctx, "query_plugins_async", None)
        if query_plugins_async is None:
            return Err(PluginCallError("ctx.query_plugins_async is not available"))
        try:
            result = await query_plugins_async({}, timeout=timeout)
        except (RuntimeError, ValueError, TimeoutError, TypeError) as error:
            return Err(PluginCallError(f"failed to query plugins: {error}"))
        if not isinstance(result, dict):
            return Err(PluginCallError(f"invalid plugin list response type: {type(result)!r}"))
        plugins = result.get("plugins", [])
        if not isinstance(plugins, list):
            return Err(PluginCallError("invalid plugin list response: plugins must be list"))
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
    ) -> Result[JsonObject | JsonValue | None, Exception]:
        """Call a plugin event by explicit coordinates."""
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return timeout_ok
        trigger_async = getattr(self._ctx, "trigger_plugin_event_async", None)
        if trigger_async is None:
            return Err(PluginCallError("ctx.trigger_plugin_event_async is not available"))
        try:
            response = await trigger_async(
                target_plugin_id=plugin_id,
                event_type=event_type,
                event_id=event_id,
                params=dict(params or {}),
                timeout=timeout,
            )
        except (RuntimeError, ValueError, TimeoutError, TypeError, KeyError) as error:
            return Err(PluginCallError(f"plugin call failed: {error}"))
        return Ok(response)

    async def call_entry(
        self,
        entry_ref: str,
        params: Mapping[str, JsonValue] | None = None,
        *,
        timeout: float = 10.0,
    ) -> Result[JsonObject | JsonValue | None, Exception]:
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
    ) -> Result[JsonObject | JsonValue | None, Exception]:
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

    async def require(self, plugin_id: str, *, timeout: float = 5.0) -> Result[PluginDescriptor, Exception]:
        """Ensure plugin exists and return descriptor."""
        listed = await self.list(timeout=timeout)
        if isinstance(listed, Err):
            return listed
        for descriptor in listed.value:
            if descriptor.get("plugin_id") == plugin_id:
                return Ok(descriptor)
        return Err(PluginCallError(f"required plugin not found: {plugin_id!r}"))


__all__ = [
    "PluginDescriptor",
    "PluginCallError",
    "InvalidEntryRefError",
    "InvalidEventRefError",
    "parse_entry_ref",
    "parse_event_ref",
    "Plugins",
]
