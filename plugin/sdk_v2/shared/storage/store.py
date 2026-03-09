"""Shared facade for plugin KV storage."""

from __future__ import annotations

from pathlib import Path

from plugin.sdk_v2.public.storage.store import PluginStore as _ImplPluginStore
from plugin.sdk_v2.shared.core.types import JsonValue, LoggerLike
from plugin.sdk_v2.shared.models import Result


class PluginStore:
    """Async-first KV store facade.

    The concrete SQLite-backed implementation lives in `public.storage.store`.
    This layer keeps the shared API explicit and stable.
    """

    def __init__(
        self,
        *,
        plugin_id: str,
        plugin_dir: Path,
        logger: LoggerLike | None = None,
        enabled: bool = True,
        db_name: str = "store.db",
    ):
        self._impl = _ImplPluginStore(
            plugin_id=plugin_id,
            plugin_dir=plugin_dir,
            logger=logger,
            enabled=enabled,
            db_name=db_name,
        )

    async def get(self, key: str, default: JsonValue | None = None) -> Result[JsonValue | None, Exception]:
        return await self._impl.get(key, default)

    async def set(self, key: str, value: JsonValue) -> Result[None, Exception]:
        return await self._impl.set(key, value)

    async def delete(self, key: str) -> Result[bool, Exception]:
        return await self._impl.delete(key)

    async def exists(self, key: str) -> Result[bool, Exception]:
        return await self._impl.exists(key)

    async def keys(self, prefix: str = "") -> Result[list[str], Exception]:
        return await self._impl.keys(prefix)

    async def clear(self) -> Result[int, Exception]:
        return await self._impl.clear()

    async def get_async(self, key: str, default: JsonValue | None = None) -> JsonValue | None:
        return await self._impl.get_async(key, default)

    async def set_async(self, key: str, value: JsonValue) -> None:
        await self._impl.set_async(key, value)

    async def delete_async(self, key: str) -> bool:
        return await self._impl.delete_async(key)

    async def exists_async(self, key: str) -> bool:
        return await self._impl.exists_async(key)

    async def keys_async(self, prefix: str = "") -> list[str]:
        return await self._impl.keys_async(prefix)

    async def clear_async(self) -> int:
        return await self._impl.clear_async()


__all__ = ["PluginStore"]
