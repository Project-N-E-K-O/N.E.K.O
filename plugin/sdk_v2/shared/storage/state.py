"""Shared facade for plugin state persistence."""

from __future__ import annotations

from pathlib import Path

from plugin.sdk_v2.public.storage.state import EXTENDED_TYPES, PluginStatePersistence as _ImplPluginStatePersistence
from plugin.sdk_v2.shared.core.types import JsonObject, LoggerLike
from plugin.sdk_v2.shared.models import Result


class PluginStatePersistence:
    """Async-first plugin state persistence facade."""

    def __init__(
        self,
        *,
        plugin_id: str,
        plugin_dir: Path,
        logger: LoggerLike | None = None,
        backend: str = "file",
    ):
        self._impl = _ImplPluginStatePersistence(
            plugin_id=plugin_id,
            plugin_dir=plugin_dir,
            logger=logger,
            backend=backend,
        )

    async def save(self, instance: object) -> Result[bool, Exception]:
        return await self._impl.save(instance)

    async def load(self, instance: object) -> Result[bool, Exception]:
        return await self._impl.load(instance)

    async def clear(self) -> Result[bool, Exception]:
        return await self._impl.clear()

    async def snapshot(self) -> Result[JsonObject, Exception]:
        return await self._impl.snapshot()

    async def save_async(self, instance: object) -> bool:
        return await self._impl.save_async(instance)

    async def load_async(self, instance: object) -> bool:
        return await self._impl.load_async(instance)

    async def clear_async(self) -> bool:
        return await self._impl.clear_async()

    async def snapshot_async(self) -> JsonObject:
        return await self._impl.snapshot_async()


__all__ = ["EXTENDED_TYPES", "PluginStatePersistence"]
