"""Plugin-facing base facade for SDK v2."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from plugin.sdk_v2.shared.constants import EVENT_META_ATTR, NEKO_PLUGIN_META_ATTR, NEKO_PLUGIN_TAG
from plugin.sdk_v2.shared.core.base import DEFAULT_PLUGIN_VERSION as _DEFAULT_PLUGIN_VERSION
from plugin.sdk_v2.shared.core.base import NekoPluginBase as _SharedNekoPluginBase
from plugin.sdk_v2.shared.core.base import PluginMeta as _SharedPluginMeta
from plugin.sdk_v2.shared.core.events import EventHandler, EventMeta
from plugin.sdk_v2.shared.models.exceptions import EntryConflictError

DEFAULT_PLUGIN_VERSION = _DEFAULT_PLUGIN_VERSION


class PluginMeta(_SharedPluginMeta):
    """Plugin-facing metadata model."""


class NekoPluginBase(_SharedNekoPluginBase):
    """Plugin-facing base class with convenience helpers."""

    def __init__(self, ctx):
        super().__init__(ctx)
        # Promote plugin-facing helper instead of exposing the shared minimal contract.
        from .runtime import Plugins

        self.plugins = Plugins(self.ctx)
        self._memory_client = None
        self._system_info_client = None
        self._static_ui_config: dict[str, Any] | None = None
        self._dynamic_entries: dict[str, dict[str, Any]] = {}

    @property
    def plugin_id(self) -> str:
        return str(getattr(self.ctx, "plugin_id", "plugin"))

    @property
    def config_dir(self) -> Path:
        config_path = getattr(self.ctx, "config_path", None)
        return Path(config_path).parent if config_path is not None else Path.cwd()

    def data_path(self, *parts: str) -> Path:
        base = self.config_dir / "data"
        return base.joinpath(*parts) if parts else base

    @property
    def metadata(self) -> dict[str, Any]:
        value = self.ctx.metadata
        return dict(value) if isinstance(value, Mapping) else {}

    @property
    def bus(self):
        return self.ctx.bus

    @property
    def memory(self):
        if self._memory_client is None:
            from .runtime import MemoryClient

            self._memory_client = MemoryClient(self.ctx)
        return self._memory_client

    @property
    def system_info(self):
        if self._system_info_client is None:
            from .runtime import SystemInfo

            self._system_info_client = SystemInfo(self.ctx)
        return self._system_info_client

    async def run_update(self, **kwargs: Any) -> object:
        return await self.ctx.run_update(**kwargs)

    async def export_push(self, **kwargs: Any) -> object:
        return await self.ctx.export_push(**kwargs)

    async def finish(self, **kwargs: Any) -> Any:
        return await self.ctx.finish(**kwargs)

    def push_message(self, **kwargs: Any) -> object:
        return self.ctx.push_message(**kwargs)

    def include_router(self, router, *, prefix: str = "") -> None:
        super().include_router(router, prefix=prefix)

    def exclude_router(self, router) -> bool:
        return super().exclude_router(router)

    def get_router(self, name: str):
        for router in self._routers:
            router_name = router.name() if callable(getattr(router, "name", None)) else getattr(router, "name", None)
            if router_name == name:
                return router
        return None

    def list_routers(self) -> list[str]:
        names: list[str] = []
        for router in self._routers:
            router_name = router.name() if callable(getattr(router, "name", None)) else getattr(router, "name", None)
            if isinstance(router_name, str):
                names.append(router_name)
        return names

    def register_static_ui(self, directory: str = "static", *, index_file: str = "index.html", cache_control: str = "public, max-age=3600") -> bool:
        static_dir = self.config_dir / directory
        index_path = static_dir / index_file
        if not static_dir.is_dir() or not index_path.is_file():
            return False
        self._static_ui_config = {
            "enabled": True,
            "directory": str(static_dir),
            "index_file": index_file,
            "cache_control": cache_control,
            "plugin_id": self.plugin_id,
        }
        return True

    def get_static_ui_config(self) -> dict[str, Any] | None:
        return self._static_ui_config

    def register_dynamic_entry(
        self,
        entry_id: str,
        handler,
        name: str = "",
        description: str = "",
        input_schema: dict[str, Any] | None = None,
        kind: str = "action",
        auto_start: bool = False,
    ) -> bool:
        if not callable(handler):
            raise TypeError("handler must be callable")
        existing_entries = self.collect_entries()
        if entry_id in existing_entries or entry_id in self._dynamic_entries:
            raise EntryConflictError(f"duplicate entry id: {entry_id!r}")
        meta = EventMeta(
            event_type="plugin_entry",
            id=entry_id,
            name=name or entry_id,
            description=description,
            input_schema=input_schema,
            kind=kind,
            auto_start=auto_start,
            metadata={"dynamic": True, "enabled": True},
        )
        self._dynamic_entries[entry_id] = {"meta": meta, "handler": handler, "enabled": True}
        return True

    def unregister_dynamic_entry(self, entry_id: str) -> bool:
        return self._dynamic_entries.pop(entry_id, None) is not None

    def enable_entry(self, entry_id: str) -> bool:
        item = self._dynamic_entries.get(entry_id)
        if item is None:
            return False
        item["enabled"] = True
        meta = item.get("meta")
        if meta is not None:
            current = dict(getattr(meta, "metadata", None) or {})
            current["enabled"] = True
            meta.metadata = current
        return True

    def disable_entry(self, entry_id: str) -> bool:
        item = self._dynamic_entries.get(entry_id)
        if item is None:
            return False
        item["enabled"] = False
        meta = item.get("meta")
        if meta is not None:
            current = dict(getattr(meta, "metadata", None) or {})
            current["enabled"] = False
            meta.metadata = current
        return True

    def is_entry_enabled(self, entry_id: str) -> bool | None:
        item = self._dynamic_entries.get(entry_id)
        if item is not None:
            return bool(item.get("enabled", True))
        entries = self.collect_entries()
        if entry_id in entries:
            return True
        return None

    def list_entries(self, include_disabled: bool = False) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        collected_entries = self.collect_entries()
        for entry_id, event_handler in collected_entries.items():
            meta = event_handler.meta
            dynamic_item = self._dynamic_entries.get(entry_id)
            enabled = bool(dynamic_item.get("enabled", True)) if dynamic_item is not None else True
            if enabled is False and not include_disabled:
                continue
            entries.append({
                "id": entry_id,
                "name": getattr(meta, "name", entry_id),
                "description": getattr(meta, "description", ""),
                "event_type": getattr(meta, "event_type", "plugin_entry"),
                "kind": getattr(meta, "kind", "action"),
                "enabled": enabled is not False,
                "dynamic": entry_id in self._dynamic_entries,
                "auto_start": bool(getattr(meta, "auto_start", False)),
                "timeout": getattr(meta, "timeout", None),
                "model_validate": bool(getattr(meta, "model_validate", True)),
                "input_schema": dict(getattr(meta, "input_schema", None) or {}),
                "llm_result_fields": list(getattr(meta, "llm_result_fields", None) or []),
                "llm_result_schema": dict(getattr(meta, "llm_result_schema", None) or {}),
                "metadata": dict(getattr(meta, "metadata", None) or {}),
            })
            seen.add(entry_id)
        if include_disabled:
            for entry_id, item in self._dynamic_entries.items():
                if entry_id in seen:
                    continue
                meta = item.get("meta")
                entries.append({
                    "id": entry_id,
                    "name": getattr(meta, "name", entry_id),
                    "description": getattr(meta, "description", ""),
                    "event_type": getattr(meta, "event_type", "plugin_entry"),
                    "kind": getattr(meta, "kind", "action"),
                    "enabled": bool(item.get("enabled", True)),
                    "dynamic": True,
                    "auto_start": bool(getattr(meta, "auto_start", False)),
                    "timeout": getattr(meta, "timeout", None),
                    "model_validate": bool(getattr(meta, "model_validate", True)),
                    "input_schema": dict(getattr(meta, "input_schema", None) or {}),
                    "llm_result_fields": list(getattr(meta, "llm_result_fields", None) or []),
                    "llm_result_schema": dict(getattr(meta, "llm_result_schema", None) or {}),
                    "metadata": dict(getattr(meta, "metadata", None) or {}),
                })
        return entries

    def collect_entries(self, wrap_with_hooks: bool = True) -> dict[str, EventHandler]:
        entries = super().collect_entries(wrap_with_hooks=wrap_with_hooks)
        for entry_id, item in self._dynamic_entries.items():
            if item.get("enabled", True):
                meta = item.get("meta")
                handler = item.get("handler")
                if meta is not None and callable(handler):
                    entries[entry_id] = EventHandler(meta=meta, handler=handler)
        return entries

    def report_status(self, status: dict[str, Any]) -> None:
        updater = getattr(self.ctx, "update_status", None)
        if callable(updater):
            updater(status)


__all__ = [
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "NekoPluginBase",
    "PluginMeta",
]
