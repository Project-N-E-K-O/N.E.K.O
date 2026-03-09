"""Shared facade for plugin state persistence."""

from __future__ import annotations

from pathlib import Path

from plugin.sdk_v2.public.storage.state import EXTENDED_TYPES, PluginStatePersistence as _ImplPluginStatePersistence
from plugin.sdk_v2.shared.core.types import JsonObject, LoggerLike
from plugin.sdk_v2.shared.models import Err, Result


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
        normalized_backend = (backend or "file").lower()
        if normalized_backend not in {"file", "memory", "off"}:
            normalized_backend = "file"
        self.plugin_id = plugin_id
        self.plugin_dir = Path(plugin_dir)
        self.backend = normalized_backend
        self._impl = _ImplPluginStatePersistence(
            plugin_id=plugin_id,
            plugin_dir=self.plugin_dir,
            logger=logger,
            backend=normalized_backend,
        )

    async def save(self, instance: object) -> Result[bool, Exception]:
        try:
            return await self._impl.save(instance)
        except Exception as error:
            return Err(error)

    async def load(self, instance: object) -> Result[bool, Exception]:
        try:
            return await self._impl.load(instance)
        except Exception as error:
            return Err(error)

    async def clear(self) -> Result[bool, Exception]:
        try:
            return await self._impl.clear()
        except Exception as error:
            return Err(error)

    async def snapshot(self) -> Result[JsonObject, Exception]:
        try:
            return await self._impl.snapshot()
        except Exception as error:
            return Err(error)

    async def save_async(self, instance: object) -> bool:
        return (await self.save(instance)).unwrap_or(False)

    async def load_async(self, instance: object) -> bool:
        return (await self.load(instance)).unwrap_or(False)

    async def clear_async(self) -> bool:
        return (await self.clear()).unwrap_or(False)

    async def snapshot_async(self) -> JsonObject:
        return (await self.snapshot()).unwrap_or({})


__all__ = ["EXTENDED_TYPES", "PluginStatePersistence"]
