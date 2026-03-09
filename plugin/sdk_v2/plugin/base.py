"""Plugin-facing base facade for SDK v2."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from plugin.sdk_v2.shared.constants import EVENT_META_ATTR, NEKO_PLUGIN_META_ATTR, NEKO_PLUGIN_TAG
from plugin.sdk_v2.shared.core.base import NekoPluginBase as _SharedNekoPluginBase
from plugin.sdk_v2.shared.core.base import PluginMeta as _SharedPluginMeta
from plugin.sdk_v2.shared.core.events import EventMeta


class PluginMeta(_SharedPluginMeta):
    """Plugin-facing metadata model."""


class NekoPluginBase(_SharedNekoPluginBase):
    """Plugin-facing base class with convenience helpers."""

    def __init__(self, ctx):
        super().__init__(ctx)
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

    def include_router(self, router, *, prefix: str = "") -> None:
        super().include_router(router, prefix=prefix)
        binder = getattr(router, "_bind", None)
        if callable(binder):
            binder(self)

    def exclude_router(self, router) -> bool:
        target = None
        if isinstance(router, str):
            target = self.get_router(router)
        elif router in self._routers:
            target = router
        removed = super().exclude_router(router)
        if removed and target is not None:
            unbind = getattr(target, "_unbind", None)
            if callable(unbind):
                unbind()
        return removed

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
        if not static_dir.exists() or not index_path.exists():
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

    async def register_dynamic_entry(
        self,
        entry_id: str,
        handler,
        name: str = "",
        description: str = "",
        input_schema: dict[str, Any] | None = None,
        kind: str = "action",
        auto_start: bool = False,
    ) -> bool:
        meta = EventMeta(
            event_type="plugin_entry",
            id=entry_id,
            name=name or entry_id,
            description=description,
            input_schema=input_schema,
            auto_start=auto_start,
            metadata={"kind": kind, "dynamic": True, "enabled": True},
        )
        setattr(handler, EVENT_META_ATTR, meta)
        self._dynamic_entries[entry_id] = {"meta": meta, "handler": handler, "enabled": True}
        return True

    async def unregister_dynamic_entry(self, entry_id: str) -> bool:
        return self._dynamic_entries.pop(entry_id, None) is not None

    async def enable_entry(self, entry_id: str) -> bool:
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

    async def disable_entry(self, entry_id: str) -> bool:
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
        for entry_id, handler in self.collect_entries().items():
            meta = getattr(handler, EVENT_META_ATTR, None)
            enabled = self.is_entry_enabled(entry_id)
            if enabled is False and not include_disabled:
                continue
            entries.append({
                "id": entry_id,
                "name": getattr(meta, "name", entry_id),
                "description": getattr(meta, "description", ""),
                "enabled": enabled is not False,
                "dynamic": entry_id in self._dynamic_entries,
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
                    "enabled": bool(item.get("enabled", True)),
                    "dynamic": True,
                })
        return entries

    def collect_entries(self):
        entries = super().collect_entries()
        for entry_id, item in self._dynamic_entries.items():
            if item.get("enabled", True):
                entries[entry_id] = item["handler"]
        return entries

    def report_status(self, status: dict[str, Any]) -> None:
        updater = getattr(self.ctx, "update_status", None)
        if callable(updater):
            updater(status)


__all__ = [
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "PluginMeta",
    "NekoPluginBase",
]
