"""Shared facade for plugin KV storage."""

from __future__ import annotations

from pathlib import Path

from plugin.sdk_v2.public.storage.store import PluginStore as _ImplPluginStore
from plugin.sdk_v2.shared.core._facade import AsyncResultFacadeTemplate
from plugin.sdk_v2.shared.core.types import JsonValue, LoggerLike
from plugin.sdk_v2.shared.logging import get_plugin_logger
from plugin.sdk_v2.shared.models import Err, Ok, Result


class PluginStore(AsyncResultFacadeTemplate):
    """Async-first KV store facade."""

    def __init__(
        self,
        *,
        plugin_id: str,
        plugin_dir: Path,
        logger: LoggerLike | None = None,
        enabled: bool = True,
        db_name: str = "store.db",
    ):
        super().__init__(logger=logger or get_plugin_logger(plugin_id, "storage.store"))
        self.plugin_id = plugin_id
        self.plugin_dir = Path(plugin_dir)
        self._impl = _ImplPluginStore(
            plugin_id=plugin_id,
            plugin_dir=self.plugin_dir,
            logger=self._logger,
            enabled=enabled,
            db_name=db_name,
        )

    @staticmethod
    def _validate_key(key: str) -> Result[None, Exception]:
        if not isinstance(key, str) or key == "":
            return Err(ValueError("key must be non-empty"))
        return _OK_NONE

    async def get(self, key: str, default: JsonValue | None = None) -> Result[JsonValue | None, Exception]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return key_ok
        return await self._forward_result("storage.store.get", self._impl.get, key, default)

    async def set(self, key: str, value: JsonValue) -> Result[None, Exception]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return key_ok
        return await self._forward_result("storage.store.set", self._impl.set, key, value)

    async def delete(self, key: str) -> Result[bool, Exception]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return key_ok
        return await self._forward_result("storage.store.delete", self._impl.delete, key)

    async def exists(self, key: str) -> Result[bool, Exception]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return key_ok
        return await self._forward_result("storage.store.exists", self._impl.exists, key)

    async def keys(self, prefix: str = "") -> Result[list[str], Exception]:
        return await self._forward_result("storage.store.keys", self._impl.keys, prefix)

    async def clear(self) -> Result[int, Exception]:
        return await self._forward_result("storage.store.clear", self._impl.clear)

    async def count(self) -> Result[int, Exception]:
        return await self._forward_result("storage.store.count", self._impl.count)

    async def dump(self) -> Result[dict[str, JsonValue], Exception]:
        return await self._forward_result("storage.store.dump", self._impl.dump)

    async def close(self) -> Result[None, Exception]:
        return await self._forward_result("storage.store.close", self._impl.close)


_OK_NONE = Ok(None)

__all__ = ["PluginStore"]
