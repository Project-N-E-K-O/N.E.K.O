"""Database contracts for SDK v2 shared storage."""

from __future__ import annotations

from typing import Protocol

from plugin.sdk_v2.shared.core.types import JsonValue
from plugin.sdk_v2.shared.models import Result


class AsyncSessionProtocol(Protocol):
    async def execute(self, statement: object, parameters: object | None = None) -> object: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    async def close(self) -> None: ...


class PluginDatabase:
    """Async-only database contract."""

    def __init__(self, *args: object, **kwargs: object):
        raise NotImplementedError("sdk_v2 contract-only facade: shared.storage.database not implemented")

    async def create_all(self) -> Result[None, Exception]:
        raise NotImplementedError

    async def drop_all(self) -> Result[None, Exception]:
        raise NotImplementedError

    async def session(self) -> Result[AsyncSessionProtocol, Exception]:
        raise NotImplementedError


class PluginKVStore:
    """DB-backed KV storage contract."""

    def __init__(self, *args: object, **kwargs: object):
        raise NotImplementedError("sdk_v2 contract-only facade: shared.storage.database not implemented")

    async def get(self, key: str, default: JsonValue | None = None) -> Result[JsonValue | None, Exception]:
        raise NotImplementedError

    async def set(self, key: str, value: JsonValue) -> Result[None, Exception]:
        raise NotImplementedError

    async def delete(self, key: str) -> Result[bool, Exception]:
        raise NotImplementedError


__all__ = ["AsyncSessionProtocol", "PluginDatabase", "PluginKVStore"]
