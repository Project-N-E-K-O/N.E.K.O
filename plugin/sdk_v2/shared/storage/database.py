"""Shared facade for plugin database storage."""

from __future__ import annotations

from pathlib import Path

from plugin.sdk_v2.public.storage.database import (
    AsyncSessionProtocol,
    PluginDatabase as _ImplPluginDatabase,
    PluginKVStore as _ImplPluginKVStore,
)
from plugin.sdk_v2.shared.core.types import JsonValue, LoggerLike
from plugin.sdk_v2.shared.models import Err, Ok, Result


class PluginKVStore:
    """DB-backed KV storage facade."""

    def __init__(self, *, database: "PluginDatabase"):
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
        try:
            return await self._impl.get(key, default)
        except Exception as error:
            return Err(error)

    async def set(self, key: str, value: JsonValue) -> Result[None, Exception]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return key_ok
        try:
            return await self._impl.set(key, value)
        except Exception as error:
            return Err(error)

    async def delete(self, key: str) -> Result[bool, Exception]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return key_ok
        try:
            return await self._impl.delete(key)
        except Exception as error:
            return Err(error)

    async def exists(self, key: str) -> Result[bool, Exception]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return key_ok
        try:
            return await self._impl.exists(key)
        except Exception as error:
            return Err(error)

    async def keys(self, prefix: str = "") -> Result[list[str], Exception]:
        try:
            return await self._impl.keys(prefix)
        except Exception as error:
            return Err(error)

    async def clear(self) -> Result[int, Exception]:
        try:
            return await self._impl.clear()
        except Exception as error:
            return Err(error)

    async def count(self) -> Result[int, Exception]:
        try:
            return await self._impl.count()
        except Exception as error:
            return Err(error)

    async def get_async(self, key: str, default: JsonValue | None = None) -> JsonValue | None:
        return (await self.get(key, default)).unwrap_or(default)

    async def set_async(self, key: str, value: JsonValue) -> None:
        (await self.set(key, value)).raise_for_err()

    async def delete_async(self, key: str) -> bool:
        return (await self.delete(key)).unwrap_or(False)

    async def exists_async(self, key: str) -> bool:
        return (await self.exists(key)).unwrap_or(False)

    async def keys_async(self, prefix: str = "") -> list[str]:
        return (await self.keys(prefix)).unwrap_or([])

    async def clear_async(self) -> int:
        return (await self.clear()).unwrap_or(0)

    async def count_async(self) -> int:
        return (await self.count()).unwrap_or(0)


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
        self.plugin_id = plugin_id
        self.plugin_dir = Path(plugin_dir)
        self._impl = _ImplPluginDatabase(
            plugin_id=plugin_id,
            plugin_dir=self.plugin_dir,
            logger=logger,
            enabled=enabled,
            db_name=db_name,
        )
        self._kv: PluginKVStore | None = None

    async def create_all(self) -> Result[None, Exception]:
        try:
            return await self._impl.create_all()
        except Exception as error:
            return Err(error)

    async def drop_all(self) -> Result[None, Exception]:
        try:
            return await self._impl.drop_all()
        except Exception as error:
            return Err(error)

    async def session(self) -> Result[AsyncSessionProtocol, Exception]:
        try:
            return await self._impl.session()
        except Exception as error:
            return Err(error)

    async def close(self) -> Result[None, Exception]:
        try:
            return await self._impl.close()
        except Exception as error:
            return Err(error)

    async def create_all_async(self) -> None:
        (await self.create_all()).raise_for_err()

    async def drop_all_async(self) -> None:
        (await self.drop_all()).raise_for_err()

    async def close_async(self) -> None:
        (await self.close()).raise_for_err()

    @property
    def kv(self) -> PluginKVStore:
        if self._kv is None:
            self._kv = PluginKVStore(database=self)
        return self._kv


_OK_NONE = Ok(None)

__all__ = ["AsyncSessionProtocol", "PluginDatabase", "PluginKVStore"]
