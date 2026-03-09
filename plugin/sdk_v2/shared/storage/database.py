"""Shared facade for plugin database storage."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from plugin.sdk_v2.public.storage.database import (
    AsyncSessionProtocol,
    PluginDatabase as _ImplPluginDatabase,
    PluginKVStore as _ImplPluginKVStore,
)
from plugin.sdk_v2.shared.core.types import JsonValue, LoggerLike
from plugin.sdk_v2.shared.models import Result


class PluginKVStore:
    """DB-backed KV storage facade."""

    def __init__(self, *, database: "PluginDatabase"):
        self._db = database
        self._impl = _ImplPluginKVStore(database=database._impl)

    async def get(self, key: str, default: JsonValue | None = None) -> Result[JsonValue | None, Exception]:
        return await self._impl.get(key, default)

    async def set(self, key: str, value: JsonValue) -> Result[None, Exception]:
        return await self._impl.set(key, value)

    async def delete(self, key: str) -> Result[bool, Exception]:
        return await self._impl.delete(key)

    async def get_async(self, key: str, default: JsonValue | None = None) -> JsonValue | None:
        return await self._impl.get_async(key, default)

    async def set_async(self, key: str, value: JsonValue) -> None:
        await self._impl.set_async(key, value)

    async def delete_async(self, key: str) -> bool:
        return await self._impl.delete_async(key)


class PluginDatabase:
    """Async-first plugin database facade."""

    def __init__(
        self,
        *,
        plugin_id: str,
        plugin_dir: Path,
        logger: LoggerLike | None = None,
        enabled: bool = True,
        db_name: str | None = None,
    ):
        self._impl = _ImplPluginDatabase(
            plugin_id=plugin_id,
            plugin_dir=plugin_dir,
            logger=logger,
            enabled=enabled,
            db_name=db_name,
        )
        self._kv: PluginKVStore | None = None

    async def create_all(self) -> Result[None, Exception]:
        return await self._impl.create_all()

    async def drop_all(self) -> Result[None, Exception]:
        return await self._impl.drop_all()

    async def session(self) -> Result[AsyncSessionProtocol, Exception]:
        return await self._impl.session()

    @property
    def kv(self) -> PluginKVStore:
        if self._kv is None:
            self._kv = PluginKVStore(database=self)
        return self._kv


__all__ = ["AsyncSessionProtocol", "PluginDatabase", "PluginKVStore"]
