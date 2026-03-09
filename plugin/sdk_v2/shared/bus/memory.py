"""Memory bus facade."""

from __future__ import annotations

from typing import Any, Mapping

from plugin.sdk_v2.public.bus.memory import Memory as _ImplMemory
from plugin.sdk_v2.shared.models import Err, Result

from ._facade import BusFacadeMixin
from .types import BusList, BusRecord


class MemoryRecord(BusRecord):
    @classmethod
    def from_raw(cls, raw: object) -> "MemoryRecord":
        base = BusRecord.from_raw(raw)
        return cls(id=base.id, namespace=base.namespace, payload=base.payload, rev=base.rev)


class MemoryList(BusList[MemoryRecord]):
    pass


class Memory(BusFacadeMixin):
    def __init__(self, _transport=None):
        self._setup_impl(_ImplMemory, _transport, namespace="memory")

    async def query(self, bucket_id: str, query: str, *, timeout: float = 5.0) -> Result[Any, Exception]:
        bucket_ok = self._require_non_empty_str("bucket_id", bucket_id)
        if isinstance(bucket_ok, Err):
            return bucket_ok
        query_ok = self._require_non_empty_str("query", query)
        if isinstance(query_ok, Err):
            return query_ok
        return await self._call("bus.memory.query", self._impl.query, bucket_ok, query_ok, timeout=timeout)

    async def get(self, bucket_id: str, *, limit: int = 20, timeout: float = 5.0) -> Result[list[Mapping[str, Any]], Exception]:
        bucket_ok = self._require_non_empty_str("bucket_id", bucket_id)
        if isinstance(bucket_ok, Err):
            return bucket_ok
        limit_ok = self._require_positive_int("limit", limit)
        if isinstance(limit_ok, Err):
            return limit_ok
        return await self._call("bus.memory.get", self._impl.get, bucket_ok, limit=limit_ok, timeout=timeout)

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
