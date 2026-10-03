"""Base plugin runtime for SDK v2 shared core."""

from __future__ import annotations

from dataclasses import dataclass, field
import inspect
from pathlib import Path
from typing import Protocol, runtime_checkable

from plugin.sdk.shared.core.base_runtime import (
    resolve_db_config,
    resolve_effective_config,
    resolve_plugin_data_dir,
    resolve_state_backend,
    resolve_store_enabled,
    setup_plugin_file_logging,
)
from plugin.sdk.shared.constants import EVENT_META_ATTR, NEKO_PLUGIN_META_ATTR, NEKO_PLUGIN_TAG, SDK_VERSION
from plugin.sdk.shared.logging import LogLevel, LoggerLike, get_plugin_logger, setup_sdk_logging
from .context import ensure_sdk_context
from .events import EventHandler, EventMeta
from .types import InputSchema, PluginContextProtocol, RouterProtocol


DEFAULT_PLUGIN_VERSION = "0.0.0"


@runtime_checkable
class _EventMetaLike(Protocol):
    id: str


@dataclass(slots=True)
class PluginMeta:
    id: str
    name: str
    version: str = DEFAULT_PLUGIN_VERSION
    sdk_version: str = SDK_VERSION
    description: str = ""
    short_description: str = ""  # 简短描述（<300字符），用于 agent 两阶段插件筛选
    keywords: list[str] = field(default_factory=list)  # 关键词正则表达式列表
    passive: bool = False  # 被动插件，不参与 agent 主动分派
    sdk_recommended: str | None = None
    sdk_supported: str | None = None
    sdk_untested: str | None = None
    sdk_conflicts: list[str] = field(default_factory=list)


class NekoPluginBase:
    """Async-first plugin base.

    The class keeps a synchronous ergonomic surface and delegates transport
    operations to async APIs under `config/plugins/store/db`.
    """

    __freezable__: list[str] = []
    __persist_mode__: str = "off"

    def __init__(self, ctx: PluginContextProtocol):
        self._host_ctx = ctx
        self.ctx = ensure_sdk_context(ctx)
        from plugin.sdk.shared.core.config import PluginConfig
        from plugin.sdk.shared.core.plugins import Plugins

        self.config = PluginConfig(self.ctx)
        self.plugins = Plugins(self.ctx)
        self._routers: list[RouterProtocol] = []
        self.logger: LoggerLike = self.ctx.logger or self.get_logger()
        self.sdk_logger: LoggerLike = self.logger

        from plugin.sdk.shared.storage.database import PluginDatabase
        from plugin.sdk.shared.storage.state import PluginStatePersistence
        from plugin.sdk.shared.storage.store import PluginStore

        plugin_dir = resolve_plugin_data_dir(self.ctx)
        effective_cfg = resolve_effective_config(self.ctx)
        store_enabled = resolve_store_enabled(effective_cfg)
        db_enabled, db_name = resolve_db_config(effective_cfg)
        state_backend = resolve_state_backend(effective_cfg)

        plugin_id = self.ctx.plugin_id
        self.store = PluginStore(plugin_id=plugin_id, plugin_dir=plugin_dir, logger=self.logger, enabled=store_enabled)
        self.db = PluginDatabase(plugin_id=plugin_id, plugin_dir=plugin_dir, logger=self.logger, enabled=db_enabled, db_name=db_name)
        self.state = PluginStatePersistence(plugin_id=plugin_id, plugin_dir=plugin_dir, logger=self.logger, backend=state_backend)
        self._state_persistence = self.state

    def refresh_runtime_config(self, effective_config: dict[str, object] | None = None) -> None:
        """Refresh SDK runtime helpers after the host effective config changes."""
        cfg = effective_config if isinstance(effective_config, dict) else resolve_effective_config(self.ctx)
        store_enabled = resolve_store_enabled(cfg)
        db_enabled, db_name = resolve_db_config(cfg)
        state_backend = resolve_state_backend(cfg)
        configure_db_name = getattr(self.db, "configure_database_name", None)
        normalize_state_backend = getattr(type(self.state), "normalize_backend", None)
        if callable(normalize_state_backend):
            state_backend = normalize_state_backend(state_backend)

        if callable(configure_db_name):
            configure_db_name(db_name)
        elif hasattr(self.db, "db_name"):
            self.db.db_name = db_name
        self.store.enabled = store_enabled
        self.db.enabled = db_enabled
        self.state.backend = state_backend

    def get_input_schema(self) -> InputSchema:
        schema = getattr(self, "input_schema", None)
        if isinstance(schema, dict):
            return schema
        return {}

    def include_router(self, router: RouterProtocol, *, prefix: str = "") -> None:
        if prefix != "":
            router.set_prefix(prefix)
        binder = getattr(router, "_bind", None)
        if callable(binder):
            binder(self)
        self._routers.append(router)

    def exclude_router(self, router: RouterProtocol | str) -> bool:
        if isinstance(router, str):
            for item in self._routers:
                if item.name() == router:
                    self._routers.remove(item)
                    unbind = getattr(item, "_unbind", None)
                    if callable(unbind):
                        unbind()
                    return True
            return False
        if router in self._routers:
            self._routers.remove(router)
            unbind = getattr(router, "_unbind", None)
            if callable(unbind):
                unbind()
            return True
        return False

    def logger_component(self, suffix: str | None = None) -> str:
        plugin_id = str(getattr(self.ctx, "plugin_id", "plugin"))
        from plugin.sdk.shared.logging import build_component_name

        return build_component_name("plugin", plugin_id, suffix)

    def get_logger(self, suffix: str | None = None) -> LoggerLike:
        plugin_id = str(getattr(self.ctx, "plugin_id", "plugin"))
        return get_plugin_logger(plugin_id, suffix=suffix)

    def setup_logger(
        self,
        *,
        level: str | LogLevel | None = None,
        force: bool = False,
        suffix: str | None = None,
    ) -> LoggerLike:
        parsed_level: LogLevel | None
        if level is None:
            parsed_level = None
        elif isinstance(level, LogLevel):
            parsed_level = level
        else:
            try:
                parsed_level = LogLevel(level.strip().upper())
            except ValueError as error:
                raise ValueError(f"invalid log level: {level!r}") from error

        component = self.logger_component(suffix)
        setup_sdk_logging(component=component, level=parsed_level, force=force)
        logger = self.get_logger(suffix)
        if suffix in (None, ""):
            self.logger = logger
            self.sdk_logger = logger
        return logger

    # ── Store 便捷方法 ──────────────────────────────────────────────────
    #
    # PluginStore 的 ``async def get/set/delete`` 返回 Result[T, E]，
    # 而社区插件习惯用 ``await self.store.get(key)`` 直接拿值。
    # 这三个便捷方法帮插件开发者解包 Result、处理 store=None、
    # 兼容两种返回模式（官方 PluginStore vs 裸值 store）。

    def _unwrap_store_result(self, result: Any, default: Any = None) -> Any:
        """解包 store.get/set/delete 的返回值。

        官方 PluginStore 返回 Result[T, E]（Ok/Err）；
        裸值 store 直接返回值。两种都处理。
        """
        if result is None:
            return default
        # Result[T, E] 包装：用 is_ok() 判断，不依赖 .value 是否存在
        try:
            if hasattr(result, "is_ok") and callable(result.is_ok):
                if result.is_ok():
                    return getattr(result, "value", default)
                return default
        except Exception:
            pass
        return result

    async def store_get(self, key: str, default: Any = None) -> Any:
        """读插件 store，返回值或 default。

        比 ``await self.store.get(key)`` 更易用：
        - 自动解包 Result[T, E] 包装
        - store=None 时 graceful 返回 default（不抛异常）
        - 两种返回模式都支持

        Usage ::

            count = await self.store_get("post_count", default=0)
        """
        store = getattr(self, "store", None)
        if store is None:
            return default
        try:
            result = await store.get(key, default)
        except Exception as exc:
            self.logger.debug("store_get(%s) failed: %s", key, exc)
            return default
        return self._unwrap_store_result(result, default)

    async def store_set(self, key: str, value: Any) -> bool:
        """写插件 store，返回是否成功。

        Usage ::

            ok = await self.store_set("post_count", str(count))
        """
        store = getattr(self, "store", None)
        if store is None:
            return False
        try:
            await store.set(key, value)
            return True
        except Exception as exc:
            self.logger.debug("store_set(%s) failed: %s", key, exc)
            return False

    async def store_delete(self, key: str) -> bool:
        """删插件 store key，返回是否删除成功（key 不存在也算 False）。

        Usage ::

            deleted = await self.store_delete("temp_key")
        """
        store = getattr(self, "store", None)
        if store is None:
            return False
        try:
            result = await store.delete(key)
        except Exception as exc:
            self.logger.debug("store_delete(%s) failed: %s", key, exc)
            return False
        # delete 返回 Result[bool, StoreError]
        unwrapped = self._unwrap_store_result(result, False)
        return bool(unwrapped)

    def collect_entries(self, wrap_with_hooks: bool = True) -> dict[str, EventHandler]:
        del wrap_with_hooks
        entries: dict[str, EventHandler] = {}
        getmembers_static = getattr(inspect, "getmembers_static", inspect.getmembers)
        for attr_name, class_value in getmembers_static(type(self)):
            if attr_name.startswith("_"):
                continue
            target = class_value.__func__ if isinstance(class_value, (staticmethod, classmethod)) else class_value
            if not callable(target):
                continue
            meta = getattr(target, EVENT_META_ATTR, None)
            if isinstance(meta, _EventMetaLike) and meta.id != "":
                value = getattr(self, attr_name, None)
                if callable(value):
                    entries[str(meta.id)] = EventHandler(meta=meta, handler=value)

        for router in self._routers:
            collect_entries = getattr(router, "collect_entries", None)
            if callable(collect_entries):
                router_entries = collect_entries()
                for key, handler in router_entries.items():
                    if isinstance(handler, EventHandler):
                        entries[str(key)] = handler
                    elif callable(handler):
                        meta = getattr(handler, EVENT_META_ATTR, None)
                        if isinstance(meta, _EventMetaLike):
                            entries[str(key)] = EventHandler(meta=meta, handler=handler)
                        else:
                            entries[str(key)] = EventHandler(
                                meta=EventMeta(event_type="plugin_entry", id=str(key), name=str(key)),
                                handler=handler,
                            )
                continue

            for key, handler in router.iter_handlers().items():
                if not callable(handler):
                    continue
                meta = getattr(handler, EVENT_META_ATTR, None)
                if isinstance(meta, _EventMetaLike):
                    entries[str(key)] = EventHandler(meta=meta, handler=handler)
                else:
                    entries[str(key)] = EventHandler(
                        meta=EventMeta(event_type="plugin_entry", id=str(key), name=str(key)),
                        handler=handler,
                    )
        return entries

    def enable_file_logging(
        self,
        *,
        log_dir: str | Path | None = None,
        log_level: str = "INFO",
        max_bytes: int | None = None,
        backup_count: int | None = None,
    ) -> LoggerLike:
        level_str = log_level.strip().upper()
        try:
            parsed_level = LogLevel(level_str)
        except ValueError as error:
            raise ValueError(f"invalid log_level: {log_level!r}") from error

        if max_bytes is not None and max_bytes <= 0:
            raise ValueError("max_bytes must be > 0")
        if backup_count is not None and backup_count <= 0:
            raise ValueError("backup_count must be > 0")

        component = self.logger_component()
        sink_id = setup_plugin_file_logging(
            component=component,
            parsed_level=parsed_level,
            log_dir=log_dir,
            max_bytes=max_bytes,
            backup_count=backup_count,
            previous_sink_id=getattr(self, "_file_sink_id", None),
        )
        if sink_id is not None:
            setattr(self, "_file_sink_id", sink_id)

        logger = self.get_logger()
        self.logger = logger
        self.sdk_logger = logger
        setattr(self, "file_logger", logger)
        return logger


__all__ = [
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "PluginMeta",
    "NekoPluginBase",
]
