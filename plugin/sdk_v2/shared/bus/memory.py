"""Memory bus facade."""

from __future__ import annotations

from typing import Any, Mapping

from plugin.sdk_v2.public.bus.memory import Memory as _ImplMemory
from plugin.sdk_v2.shared.models import Err, Result

from ._client_base import BusClientBase
from ._facade import BusFacadeMixin
from .types import BusList, BusRecord


class MemoryRecord(BusRecord):
    @classmethod
    def from_raw(cls, raw: object) -> "MemoryRecord":
        base = BusRecord.from_raw(raw)
        return cls(id=base.id, namespace=base.namespace, payload=base.payload, rev=base.rev)


class MemoryList(BusList[MemoryRecord]):
    pass


class Memory(BusFacadeMixin, BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="memory")
        self._impl = _ImplMemory(self._transport)
        self._state = self._impl._state

    async def query(self, bucket_id: str, query: str, *, timeout: float = 5.0) -> Result[Any, Exception]:
        if not isinstance(bucket_id, str) or bucket_id.strip() == "":
            return Err(ValueError("bucket_id must be non-empty"))
        if not isinstance(query, str) or query.strip() == "":
            return Err(ValueError("query must be non-empty"))
        return await self._call("bus.memory.query", self._impl.query, bucket_id, query, timeout=timeout)

    async def get(self, bucket_id: str, *, limit: int = 20, timeout: float = 5.0) -> Result[list[Mapping[str, Any]], Exception]:
        if not isinstance(bucket_id, str) or bucket_id.strip() == "":
            return Err(ValueError("bucket_id must be non-empty"))
        if limit <= 0:
            return Err(ValueError("limit must be > 0"))
        return await self._call("bus.memory.get", self._impl.get, bucket_id, limit=limit, timeout=timeout)

    async def fetch(self, bucket_id: str, *, limit: int = 20, timeout: float = 5.0) -> Result[list[Mapping[str, Any]], Exception]:
        return await self.get(bucket_id, limit=limit, timeout=timeout)


class MemoryClient:
    def __init__(self, _transport=None):
        self._impl = Memory(_transport)

    async def get(self, bucket_id: str, *, limit: int = 20, timeout: float = 5.0):
        return await self._impl.get(bucket_id, limit=limit, timeout=timeout)

    async def get_async(self, bucket_id: str, *, limit: int = 20, timeout: float = 5.0):
        return await self.get(bucket_id, limit=limit, timeout=timeout)

    async def get_sync(self, bucket_id: str, *, limit: int = 20, timeout: float = 5.0):
        return await self.get(bucket_id, limit=limit, timeout=timeout)


__all__ = ["Memory", "MemoryRecord", "MemoryList", "MemoryClient"]
