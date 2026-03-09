from __future__ import annotations

from plugin.sdk_v2.shared.bus.types import BusRecord
from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Err, Ok, Result

from ._client_base import BusClientBase


class Records(BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="records")

    async def list(self, namespace: str, *, limit: int = 100, timeout: float = 10.0) -> Result[list[BusRecord], Exception]:
        items = [item for (ns, _), item in self._state.records.items() if ns == namespace]
        return Ok(items[:limit])

    async def get(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[BusRecord, Exception]:
        item = self._state.records.get((namespace, record_id))
        return Ok(item) if item is not None else Err(RuntimeError(record_id))

    async def put(self, namespace: str, record_id: str, payload: JsonObject, *, timeout: float = 10.0) -> Result[BusRecord, Exception]:
        rev = self._state.revisions.get((namespace, record_id), 0) + 1
        self._state.revisions[(namespace, record_id)] = rev
        item = BusRecord(id=record_id, namespace=namespace, payload=dict(payload), rev=rev)
        self._state.records[(namespace, record_id)] = item
        return Ok(item)

    async def delete(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[bool, Exception]:
        removed = self._state.records.pop((namespace, record_id), None) is not None
        self._state.revisions.pop((namespace, record_id), None)
        return Ok(removed)


__all__ = ["Records"]
