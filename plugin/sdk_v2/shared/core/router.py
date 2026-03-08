"""Dynamic router contract for SDK v2 shared core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Mapping, Protocol

from plugin.sdk_v2.shared.models import Err, Ok, Result
from .events import EventMeta
from .types import EntryHandler, JsonObject, JsonValue


class RouteHandler(Protocol):
    """Async route handler contract."""

    def __call__(self, payload: Mapping[str, JsonValue]) -> Awaitable[Result[JsonObject | JsonValue | None, Exception]]: ...


class PluginRouterError(RuntimeError):
    """Router API contract violation / duplicate entry conflict."""


class EntryConflictError(PluginRouterError):
    """Raised when a duplicate entry is added without replace flag."""


@dataclass(slots=True)
class _EntryRecord:
    meta: EventMeta
    handler: RouteHandler


class PluginRouter:
    """Contract-only async router."""

    def __init__(self, *, prefix: str = "", tags: list[str] | None = None, name: str | None = None):
        self._prefix = prefix
        self._tags = tags or []
        self._name = name or self.__class__.__name__
        self._entries: dict[str, _EntryRecord] = {}

    def _resolve_entry_id(self, entry_id: str) -> str:
        candidate = entry_id.strip()
        if candidate.startswith(self._prefix):
            return candidate
        return f"{self._prefix}{candidate}"

    def name(self) -> str:
        return self._name

    def set_prefix(self, prefix: str) -> None:
        self._prefix = prefix

    def iter_handlers(self) -> Mapping[str, EntryHandler]:
        return {entry_id: record.handler for entry_id, record in self._entries.items()}

    async def add_entry(
        self,
        entry_id: str,
        handler: RouteHandler,
        *,
        name: str | None = None,
        description: str = "",
        input_schema: Mapping[str, JsonValue] | None = None,
        replace: bool = False,
    ) -> Result[bool, Exception]:
        """Add dynamic entry into router namespace."""
        trimmed = entry_id.strip()
        if trimmed == "":
            return Err(PluginRouterError("entry_id must be non-empty"))
        full_entry_id = self._resolve_entry_id(trimmed)
        if full_entry_id in self._entries and not replace:
            return Err(EntryConflictError(f"duplicate entry id: {full_entry_id!r}"))
        meta = EventMeta(
            event_type="plugin_entry",
            id=full_entry_id,
            name=name or full_entry_id,
            description=description,
            input_schema=dict(input_schema) if input_schema is not None else None,
        )
        self._entries[full_entry_id] = _EntryRecord(meta=meta, handler=handler)
        return Ok(True)

    async def remove_entry(self, entry_id: str) -> Result[bool, Exception]:
        """Remove dynamic entry by id."""
        full_entry_id = self._resolve_entry_id(entry_id.strip())
        if full_entry_id in self._entries:
            del self._entries[full_entry_id]
            return Ok(True)
        return Ok(False)

    async def list_entries(self) -> Result[list[EventMeta], Exception]:
        """List dynamic entries currently mounted."""
        return Ok([record.meta for record in self._entries.values()])


__all__ = ["RouteHandler", "PluginRouter", "PluginRouterError", "EntryConflictError"]
