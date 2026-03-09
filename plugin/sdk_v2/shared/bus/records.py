"""Record bus facade."""

from __future__ import annotations

from plugin.sdk_v2.public.bus.records import Records as _ImplRecords
from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Err, Result

from ._client_base import BusClientBase
from .types import BusRecord


class RecordConflictError(RuntimeError):
    """Record revision conflict or invalid write."""


class Records(BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="records")
        self._impl = _ImplRecords(self._transport)
        self._state = self._impl._state

    async def list(self, namespace: str, *, limit: int = 100, timeout: float = 10.0) -> Result[list[BusRecord], Exception]:
        if not isinstance(namespace, str) or namespace.strip() == "":
            return Err(ValueError("namespace must be non-empty"))
        if limit <= 0:
            return Err(ValueError("limit must be > 0"))
        return await self._forward_result("bus.records.list", self._impl.list, namespace, limit=limit, timeout=timeout)

    async def get(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[BusRecord, Exception]:
        if not isinstance(namespace, str) or namespace.strip() == "":
            return Err(RecordConflictError("namespace must be non-empty"))
        if not isinstance(record_id, str) or record_id.strip() == "":
            return Err(RecordConflictError("record_id must be non-empty"))
        result = await self._forward_result("bus.records.get", self._impl.get, namespace, record_id, timeout=timeout)
        if isinstance(result, Err) and isinstance(result.error, RuntimeError):
            return Err(RecordConflictError(str(result.error)))
        return result

    async def put(self, namespace: str, record_id: str, payload: JsonObject, *, timeout: float = 10.0) -> Result[BusRecord, Exception]:
        if not isinstance(namespace, str) or namespace.strip() == "":
            return Err(RecordConflictError("namespace must be non-empty"))
        if not isinstance(record_id, str) or record_id.strip() == "":
            return Err(RecordConflictError("record_id must be non-empty"))
        return await self._forward_result("bus.records.put", self._impl.put, namespace, record_id, dict(payload), timeout=timeout)

    async def delete(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[bool, Exception]:
        if not isinstance(namespace, str) or namespace.strip() == "":
            return Err(RecordConflictError("namespace must be non-empty"))
        if not isinstance(record_id, str) or record_id.strip() == "":
            return Err(RecordConflictError("record_id must be non-empty"))
        return await self._forward_result("bus.records.delete", self._impl.delete, namespace, record_id, timeout=timeout)


__all__ = ["Records", "RecordConflictError"]
