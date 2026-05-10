"""Dynamic router contract for SDK v2 shared core."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Mapping, Protocol

from plugin.sdk.shared.constants import EVENT_META_ATTR
from plugin.sdk.shared.models import Err, Ok, Result
from plugin.sdk.shared.models.exceptions import EntryConflictError, PluginRouterError, RouterErrorLike
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
        # 存放 @plugin_entry 装饰过的方法（原始 id + 原始 meta + 绑定的 bound 方法喵）。
        # 不在 __init__ 里把它们写进 self._entries，因为 Base.include_router 随后可能调
        # set_prefix() 改 prefix —— 提前写入会让后续 prefix 没机会生效。
        # 改为在 collect_entries() 时按当前 prefix 懒解析。
        self._decorated_entries: list[tuple[str, EventMeta, RouteHandler]] = []
        self._main_plugin: object | None = None
        self._collect_decorated_entries()

    def _collect_decorated_entries(self) -> None:
        """扫描子类上带有 EVENT_META_ATTR 的方法，暂存到 `_decorated_entries`。

        设计理由：
        - 子类通常用 `@plugin_entry(id=...)` 声明 entry；`PluginRouter.collect_entries`
          原先只返回 `self._entries`，装饰器信息完全被忽略了喵。
        - 这里只做一次静态扫描，保留 `add_entry` 的动态注册能力；prefix 解析和
          id 规范化延迟到 `collect_entries()`。
        """
        for attr_name, class_value in inspect.getmembers_static(type(self)):
            if attr_name.startswith("_"):
                continue
            target = class_value.__func__ if isinstance(class_value, (staticmethod, classmethod)) else class_value
            if not callable(target):
                continue
            meta = getattr(target, EVENT_META_ATTR, None)
            if meta is None or getattr(meta, "id", "") == "":
                continue
            bound = getattr(self, attr_name, None)
            if not callable(bound):
                continue
            self._decorated_entries.append((str(meta.id), meta, bound))

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
        # 动态 add_entry 注册的 id + 装饰器在当前 prefix 下解析得到的 id
        return list(self._resolved_entries().keys())

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
        return {entry_id: record.handler for entry_id, record in self._resolved_entries().items()}

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
            for entry_id, record in self._resolved_entries().items()
        }

    def _resolved_entries(self) -> dict[str, _EntryRecord]:
        """合并动态 (add_entry) 条目与 @plugin_entry 装饰的条目。

        装饰器条目在此时才按当前 prefix 解析 id、并规范化 meta.id 与字典 key；
        重复 id（装饰器内部自冲突 或 装饰器 vs add_entry 冲突）会抛 EntryConflictError，
        保持与 `add_entry` 的冲突语义一致喵。
        """
        resolved: dict[str, _EntryRecord] = dict(self._entries)
        for raw_id, meta, handler in self._decorated_entries:
            entry_id = self._resolve_entry_id(raw_id)
            if entry_id in resolved:
                raise EntryConflictError(f"duplicate entry id: {entry_id!r}")
            # 规范化：确保 meta.id 与 dict key 一致（两者都带 prefix）
            normalized = EventMeta(
                event_type=meta.event_type,
                id=entry_id,
                name=meta.name if meta.name else entry_id,
                description=meta.description,
                input_schema=dict(meta.input_schema) if meta.input_schema is not None else None,
                kind=meta.kind,
                auto_start=meta.auto_start,
                persist=meta.persist,
                params=meta.params,
                model_validate=meta.model_validate,
                timeout=meta.timeout,
                llm_result_fields=list(meta.llm_result_fields) if meta.llm_result_fields else None,
                llm_result_schema=dict(meta.llm_result_schema) if meta.llm_result_schema else None,
                llm_result_model=meta.llm_result_model,
                quick_action=meta.quick_action,
                quick_action_config=meta.quick_action_config,
                extra=dict(meta.extra),
                metadata=dict(meta.metadata),
            )
            resolved[entry_id] = _EntryRecord(meta=normalized, handler=handler)
        return resolved

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
        return Ok([record.meta for record in self._resolved_entries().values()])


__all__ = ["RouteHandler", "PluginRouter", "PluginRouterError", "EntryConflictError"]
