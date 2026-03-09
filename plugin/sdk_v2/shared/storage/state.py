"""Shared facade for plugin state persistence."""

from __future__ import annotations

from pathlib import Path

from plugin.sdk_v2.public.storage.state import EXTENDED_TYPES, PluginStatePersistence as _ImplPluginStatePersistence
from plugin.sdk_v2.shared.core._facade import AsyncResultFacadeTemplate
from plugin.sdk_v2.shared.core.types import JsonObject, LoggerLike
from plugin.sdk_v2.shared.logging import get_plugin_logger
from plugin.sdk_v2.shared.models import Result


class PluginStatePersistence(AsyncResultFacadeTemplate):
    """Async-first plugin state persistence facade."""

    def __init__(
        self,
        *,
        plugin_id: str,
        plugin_dir: Path,
        logger: LoggerLike | None = None,
        backend: str = "file",
    ):
        normalized_backend = (backend or "file").lower()
        if normalized_backend not in {"file", "memory", "off"}:
            normalized_backend = "file"
        super().__init__(logger=logger or get_plugin_logger(plugin_id, "storage.state"))
        self.plugin_id = plugin_id
        self.plugin_dir = Path(plugin_dir)
        self.backend = normalized_backend
        self._impl = _ImplPluginStatePersistence(
            plugin_id=plugin_id,
            plugin_dir=self.plugin_dir,
            logger=self._logger,
            backend=normalized_backend,
        )

    async def save(self, instance: object) -> Result[bool, Exception]:
        return await self._forward_result("storage.state.save", self._impl.save, instance)

    async def load(self, instance: object) -> Result[bool, Exception]:
        return await self._forward_result("storage.state.load", self._impl.load, instance)

    async def clear(self) -> Result[bool, Exception]:
        return await self._forward_result("storage.state.clear", self._impl.clear)

    async def snapshot(self) -> Result[JsonObject, Exception]:
        return await self._forward_result("storage.state.snapshot", self._impl.snapshot)

    async def collect_attrs(self, instance: object) -> Result[JsonObject, Exception]:
        return await self._forward_result("storage.state.collect_attrs", self._impl.collect_attrs, instance)

    async def restore_attrs(self, instance: object, snapshot: JsonObject) -> Result[int, Exception]:
        return await self._forward_result("storage.state.restore_attrs", self._impl.restore_attrs, instance, snapshot)

    async def has_saved_state(self) -> Result[bool, Exception]:
        return await self._forward_result("storage.state.has_saved_state", self._impl.has_saved_state)

    async def get_state_info(self) -> Result[JsonObject | None, Exception]:
        return await self._forward_result("storage.state.get_state_info", self._impl.get_state_info)


__all__ = ["EXTENDED_TYPES", "PluginStatePersistence"]
