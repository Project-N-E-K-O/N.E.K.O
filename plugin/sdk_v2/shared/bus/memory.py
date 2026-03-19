"""Memory bus facade.

This module intentionally exposes two layers:
- `Memory`: the richer bus-style domain facade
- `BusMemoryClient`: a tiny client for code that prefers a focused `get()`
  object over the full bus facade surface

The explicit `BusMemoryClient` name avoids colliding with runtime-level
`MemoryClient`, which targets host memory capabilities rather than bus memory
records.
"""

from __future__ import annotations

import copy
from dataclasses import is_dataclass
from typing import Any, Mapping, cast

from plugin.sdk_v2.shared.models import Err, Ok, Result
from plugin.sdk_v2.shared.models.exceptions import BusErrorLike

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
    """Bus-domain memory facade.

    Use this when you want memory access that stays inside the shared bus
    vocabulary and participates in the bus facade conventions.
    """

    def __init__(self, _transport=None):
        self._setup(_transport, namespace="memory")

    @staticmethod
    def _query_texts(item: object) -> list[str]:
        if isinstance(item, Mapping):
            texts: list[str] = []
            for key in ("text", "content", "description", "value"):
                value = item.get(key)
                if isinstance(value, str):
                    texts.append(value)
            return texts

        texts = []
        for name in ("text", "content", "description"):
            value = getattr(item, name, None)
            if isinstance(value, str):
                texts.append(value)
        if texts:
            return texts

        if is_dataclass(item):
            return [str(item)]

        if isinstance(item, str):
            return [item]

        return []

    async def _do_query(self, bucket_id: str, query: str, *, timeout: float = 5.0) -> Result[Any, BusErrorLike]:
        items = self._state.memory.get(bucket_id, [])
        needle = query.lower()
        matches = [
            copy.deepcopy(item)
            for item in items
            if any(needle in text.lower() for text in self._query_texts(item))
        ]
        return Ok(matches)

    async def _do_get(self, bucket_id: str, *, limit: int = 20, timeout: float = 5.0) -> Result[list[Mapping[str, Any]], BusErrorLike]:
        normalized: list[Mapping[str, Any]] = []
        safe_limit = max(0, limit)
        for item in list(self._state.memory.get(bucket_id, [])[:safe_limit]):
            normalized_item = copy.deepcopy(item if isinstance(item, Mapping) else {"value": item})
            normalized.append(cast(Mapping[str, Any], normalized_item))
        return cast(Result[list[Mapping[str, Any]], BusErrorLike], Ok(normalized))

    async def query(self, bucket_id: str, query: str, *, timeout: float = 5.0) -> Result[Any, BusErrorLike]:
        bucket_ok = self._require_non_empty_str("bucket_id", bucket_id)
        if isinstance(bucket_ok, Err):
            return bucket_ok
        query_ok = self._require_non_empty_str("query", query)
        if isinstance(query_ok, Err):
            return query_ok
        return await self._call("bus.memory.query", self._do_query, bucket_ok.value, query_ok.value, timeout=timeout)

    async def get(self, bucket_id: str, *, limit: int = 20, timeout: float = 5.0) -> Result[list[Mapping[str, Any]], BusErrorLike]:
        bucket_ok = self._require_non_empty_str("bucket_id", bucket_id)
        if isinstance(bucket_ok, Err):
            return bucket_ok
        limit_ok = self._require_positive_int("limit", limit)
        if isinstance(limit_ok, Err):
            return limit_ok
        return await self._call("bus.memory.get", self._do_get, bucket_ok.value, limit=limit_ok.value, timeout=timeout)

class BusMemoryClient:
    """Small client wrapper around `Memory`.

    This type exists for developers and tools that benefit from a narrower
    object with `get()`-style methods, while still keeping the bus-specific
    semantics explicit in the type name.
    """

    def __init__(self, _transport=None):
        self._impl = Memory(_transport)

    async def get(self, bucket_id: str, *, limit: int = 20, timeout: float = 5.0) -> Result[list[Mapping[str, Any]], BusErrorLike]:
        return await self._impl.get(bucket_id, limit=limit, timeout=timeout)


__all__ = ["Memory", "MemoryRecord", "MemoryList", "BusMemoryClient"]
