"""Shared facade for plugin database storage."""

from __future__ import annotations

from pathlib import Path

from plugin.sdk_v2.public.storage.database import (
    AsyncSessionProtocol,
    PluginDatabase as _ImplPluginDatabase,
    PluginKVStore as _ImplPluginKVStore,
)
from plugin.sdk_v2.shared.core._facade import AsyncResultFacadeTemplate
from plugin.sdk_v2.shared.core.types import JsonValue, LoggerLike
from plugin.sdk_v2.shared.logging import get_plugin_logger
from plugin.sdk_v2.shared.models import Err, Ok, Result


class PluginKVStore(AsyncResultFacadeTemplate):
    """DB-backed KV storage facade."""

    def __init__(self, *, database: "PluginDatabase"):
        super().__init__(logger=database._logger)
        self._db = database
        self._impl = _ImplPluginKVStore(database=database._impl)

    @staticmethod
    def _validate_key(key: str) -> Result[None, Exception]:
        if not isinstance(key, str) or key == "":
            return Err(ValueError("key must be non-empty"))
        return _OK_NONE

    async def get(self, key: str, default: JsonValue | None = None) -> Result[JsonValue | None, Exception]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return key_ok
        return await self._forward_result("storage.database.kv.get", self._impl.get, key, default)

    async def set(self, key: str, value: JsonValue) -> Result[None, Exception]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return key_ok
        return await self._forward_result("storage.database.kv.set", self._impl.set, key, value)

    async def delete(self, key: str) -> Result[bool, Exception]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return key_ok
        return await self._forward_result("storage.database.kv.delete", self._impl.delete, key)

    async def exists(self, key: str) -> Result[bool, Exception]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return key_ok
        return await self._forward_result("storage.database.kv.exists", self._impl.exists, key)

    async def keys(self, prefix: str = "") -> Result[list[str], Exception]:
        return await self._forward_result("storage.database.kv.keys", self._impl.keys, prefix)

    async def clear(self) -> Result[int, Exception]:
        return await self._forward_result("storage.database.kv.clear", self._impl.clear)

    async def count(self) -> Result[int, Exception]:
        return await self._forward_result("storage.database.kv.count", self._impl.count)


class PluginDatabase(AsyncResultFacadeTemplate):
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
        super().__init__(logger=logger or get_plugin_logger(plugin_id, "storage.database"))
        self.plugin_id = plugin_id
        self.plugin_dir = Path(plugin_dir)
        self._impl = _ImplPluginDatabase(
            plugin_id=plugin_id,
            plugin_dir=self.plugin_dir,
            logger=self._logger,
            enabled=enabled,
            db_name=db_name,
        )
        self._kv: PluginKVStore | None = None

    async def create_all(self) -> Result[None, Exception]:
        return await self._forward_result("storage.database.create_all", self._impl.create_all)

    async def drop_all(self) -> Result[None, Exception]:
        return await self._forward_result("storage.database.drop_all", self._impl.drop_all)

    async def session(self) -> Result[AsyncSessionProtocol, Exception]:
        return await self._forward_result("storage.database.session", self._impl.session)

    async def close(self) -> Result[None, Exception]:
        return await self._forward_result("storage.database.close", self._impl.close)

    @property
    def kv(self) -> PluginKVStore:
        if self._kv is None:
            self._kv = PluginKVStore(database=self)
        return self._kv


_OK_NONE = Ok(None)

__all__ = ["AsyncSessionProtocol", "PluginDatabase", "PluginKVStore"]
