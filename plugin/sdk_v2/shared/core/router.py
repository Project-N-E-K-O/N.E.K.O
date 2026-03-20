"""Dynamic router contract for SDK v2 shared core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Mapping, Protocol

from plugin.sdk_v2.shared.models import Err, Ok, Result
from plugin.sdk_v2.shared.models.exceptions import EntryConflictError, PluginRouterError, RouterErrorLike
from .events import EventHandler, EventMeta
from .types import EntryHandler, JsonObject, JsonValue


class RouteHandler(Protocol):
    """Async route handler contract."""

    def __call__(self, payload: Mapping[str, JsonValue]) -> Awaitable[Result[JsonObject | JsonValue | None, RouterErrorLike]]: ...


@dataclass(slots=True)
class _EntryRecord:
    meta: EventMeta
    handler: RouteHandler


class PluginRouter:
    """Async-first router with light plugin-bound convenience accessors."""

    def __init__(self, *, prefix: str = "", tags: list[str] | None = None, name: str | None = None):
        self._prefix = prefix
        self._tags = tags or []
        self._name = name or self.__class__.__name__
        self._entries: dict[str, _EntryRecord] = {}
        self._main_plugin: object | None = None

    @property
    def prefix(self) -> str:
        return self._prefix

    @prefix.setter
    def prefix(self, value: str) -> None:
        self._prefix = value

    @property
    def tags(self) -> list[str]:
        return list(self._tags)

    @property
    def is_bound(self) -> bool:
        return self._main_plugin is not None

    @property
    def entry_ids(self) -> list[str]:
        return list(self._entries.keys())

    @property
    def ctx(self) -> object | None:
        return getattr(self._main_plugin, "ctx", None)

    @property
    def config(self) -> object | None:
        return getattr(self._main_plugin, "config", None)

    @property
    def plugins(self) -> object | None:
        return getattr(self._main_plugin, "plugins", None)

    @property
    def logger(self) -> Any | None:
        return getattr(self._main_plugin, "logger", None)

    @property
    def file_logger(self) -> Any | None:
        return getattr(self._main_plugin, "file_logger", None)

    @property
    def store(self) -> object | None:
        return getattr(self._main_plugin, "store", None)

    @property
    def db(self) -> object | None:
        return getattr(self._main_plugin, "db", None)

    @property
    def plugin_id(self) -> str:
        if self._main_plugin is None:
            return self._name
        plugin_id = getattr(self._main_plugin, "plugin_id", None)
        if plugin_id is not None:
            return str(plugin_id)
        ctx = getattr(self._main_plugin, "ctx", None)
        return str(getattr(ctx, "plugin_id", self._name))

    @property
    def main_plugin(self) -> object:
        if self._main_plugin is None:
            raise PluginRouterError(f"router {self._name!r} is not bound to plugin")
        return self._main_plugin

    def _bind(self, plugin: object) -> None:
        self._main_plugin = plugin

    def _unbind(self) -> None:
        self._main_plugin = None

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

    def get_plugin_attr(self, name: str, default: object | None = None) -> object | None:
        return getattr(self._main_plugin, name, default)

    def has_plugin_attr(self, name: str) -> bool:
        return hasattr(self._main_plugin, name)

    def get_dependency(self, name: str, default: object | None = None) -> object | None:
        value = getattr(self, name, None)
        if value is not None:
            return value
        return getattr(self._main_plugin, name, default)

    def report_status(self, status: dict[str, Any]) -> None:
        plugin = self._main_plugin
        if plugin is not None and hasattr(plugin, "report_status"):
            plugin.report_status(status)

    def collect_entries(self) -> Mapping[str, EventHandler]:
        return {
            entry_id: EventHandler(meta=record.meta, handler=record.handler)
            for entry_id, record in self._entries.items()
        }

    def on_mount(self) -> None:
        return None

    def on_unmount(self) -> None:
        return None

    async def add_entry(
        self,
        entry_id: str,
        handler: RouteHandler,
        *,
        name: str | None = None,
        description: str = "",
        input_schema: Mapping[str, JsonValue] | None = None,
        replace: bool = False,
    ) -> Result[bool, RouterErrorLike]:
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

    async def remove_entry(self, entry_id: str) -> Result[bool, RouterErrorLike]:
        full_entry_id = self._resolve_entry_id(entry_id.strip())
        if full_entry_id in self._entries:
            del self._entries[full_entry_id]
            return Ok(True)
        return Ok(False)

    async def list_entries(self) -> Result[list[EventMeta], RouterErrorLike]:
        return Ok([record.meta for record in self._entries.values()])


__all__ = ["RouteHandler", "PluginRouter", "PluginRouterError", "EntryConflictError"]
