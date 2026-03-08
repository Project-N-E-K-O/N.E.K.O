"""Base plugin runtime for SDK v2 shared core."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from loguru import logger as _loguru_logger

from plugin.logging_config import FORMAT_FILE, LOG_COMPRESSION, LOG_DIR, LOG_MAX_SIZE, LOG_RETENTION
from plugin.sdk_v2.shared.constants import EVENT_META_ATTR, NEKO_PLUGIN_META_ATTR, NEKO_PLUGIN_TAG
from plugin.sdk_v2.shared.logging import LogLevel, LoggerLike, get_plugin_logger, setup_sdk_logging
from .types import EntryHandler, InputSchema, PluginContextProtocol, RouterProtocol


@runtime_checkable
class _EventMetaLike(Protocol):
    id: str


@dataclass(slots=True)
class PluginMeta:
    id: str
    name: str
    version: str = "0.0.0"
    sdk_version: str = "2.0.0a0"
    description: str = ""
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
        self.ctx = ctx
        from plugin.sdk_v2.shared.core.config import PluginConfig
        from plugin.sdk_v2.shared.core.plugins import Plugins

        self.config = PluginConfig(ctx)
        self.plugins = Plugins(ctx)
        self.store = None
        self.db = None
        self._routers: list[RouterProtocol] = []
        self.logger: LoggerLike = getattr(ctx, "logger", None) or self.get_logger()
        self.sdk_logger: LoggerLike = self.logger

    def get_input_schema(self) -> InputSchema:
        schema = getattr(self, "input_schema", None)
        if isinstance(schema, dict):
            return schema
        return {}

    def include_router(self, router: RouterProtocol, *, prefix: str = "") -> None:
        if prefix != "":
            router.set_prefix(prefix)
        self._routers.append(router)

    def exclude_router(self, router: RouterProtocol | str) -> bool:
        if isinstance(router, str):
            for item in self._routers:
                if item.name() == router:
                    self._routers.remove(item)
                    return True
            return False
        if router in self._routers:
            self._routers.remove(router)
            return True
        return False

    def logger_component(self, suffix: str | None = None) -> str:
        plugin_id = str(getattr(self.ctx, "plugin_id", "plugin"))
        from plugin.sdk_v2.shared.logging import build_component_name

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

    def collect_entries(self) -> dict[str, EntryHandler]:
        entries: dict[str, EntryHandler] = {}
        for attr_name in dir(self):
            if attr_name.startswith("_"):
                continue
            value = getattr(self, attr_name)
            if not callable(value):
                continue
            meta = getattr(value, EVENT_META_ATTR, None)
            if isinstance(meta, _EventMetaLike) and meta.id != "":
                entries[str(meta.id)] = value

        for router in self._routers:
            for key, handler in router.iter_handlers().items():
                if callable(handler):
                    entries[str(key)] = handler
        return entries

    def enable_file_logging(
        self,
        *,
        log_dir: str | Path | None = None,
        log_level: str = "INFO",
        max_bytes: int | None = None,
        backup_count: int | None = None,
    ) -> object:
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
        setup_sdk_logging(component=component, level=parsed_level)

        if log_dir is not None or max_bytes is not None or backup_count is not None:
            target_dir = Path(log_dir) if log_dir is not None else LOG_DIR
            target_dir.mkdir(parents=True, exist_ok=True)
            log_file = target_dir / f"{component.replace('.', '_')}.log"

            previous_sink_id = getattr(self, "_file_sink_id", None)
            if isinstance(previous_sink_id, int):
                _loguru_logger.remove(previous_sink_id)

            sink_id = _loguru_logger.add(
                str(log_file),
                format=FORMAT_FILE,
                level=parsed_level.value,
                rotation=max_bytes if max_bytes is not None else LOG_MAX_SIZE,
                retention=backup_count if backup_count is not None else LOG_RETENTION,
                compression=LOG_COMPRESSION,
                encoding="utf-8",
                filter=lambda record, c=component: record["extra"].get("component", "") == c,
            )
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
