from __future__ import annotations

from typing import Any, Mapping

from plugin.sdk_v2.shared.models import Ok, Result

from ._client_base import BusClientBase


class Memory(BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="memory")

    async def query(self, bucket_id: str, query: str, *, timeout: float = 5.0) -> Result[Any, Exception]:
        items = self._state.memory.get(bucket_id, [])
        matches = [item for item in items if query.lower() in str(item).lower()]
        return Ok(matches)

    async def get(self, bucket_id: str, *, limit: int = 20, timeout: float = 5.0) -> Result[list[Mapping[str, Any]], Exception]:
        return Ok(list(self._state.memory.get(bucket_id, [])[:limit]))


__all__ = ["Memory"]
