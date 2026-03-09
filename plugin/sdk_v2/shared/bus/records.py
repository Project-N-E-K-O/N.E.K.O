"""Record bus facade and helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from plugin.sdk_v2.public.bus.records import Records as _ImplRecords
from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Err, Result

from ._client_base import BusClientBase
from ._facade import BusFacadeMixin
from .types import BusRecord


class RecordConflictError(RuntimeError):
    """Record revision conflict or invalid write."""


def parse_iso_timestamp(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            dt = datetime.fromisoformat(text[:-1]).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


class Records(BusFacadeMixin, BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="records")
        self._impl = _ImplRecords(self._transport)
        self._state = self._impl._state

    async def list(self, namespace: str, *, limit: int = 100, timeout: float = 10.0) -> Result[list[BusRecord], Exception]:
        if not isinstance(namespace, str) or namespace.strip() == "":
            return Err(ValueError("namespace must be non-empty"))
        if limit <= 0:
            return Err(ValueError("limit must be > 0"))
        return await self._call("bus.records.list", self._impl.list, namespace, limit=limit, timeout=timeout, error_mapper=lambda error: RecordConflictError(str(error)))

    async def get(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[BusRecord, Exception]:
        if not isinstance(namespace, str) or namespace.strip() == "":
            return Err(RecordConflictError("namespace must be non-empty"))
        if not isinstance(record_id, str) or record_id.strip() == "":
            return Err(RecordConflictError("record_id must be non-empty"))
        return await self._call("bus.records.get", self._impl.get, namespace, record_id, timeout=timeout, error_mapper=lambda error: RecordConflictError(str(error)))

    async def put(self, namespace: str, record_id: str, payload: JsonObject, *, timeout: float = 10.0) -> Result[BusRecord, Exception]:
        if not isinstance(namespace, str) or namespace.strip() == "":
            return Err(RecordConflictError("namespace must be non-empty"))
        if not isinstance(record_id, str) or record_id.strip() == "":
            return Err(RecordConflictError("record_id must be non-empty"))
        return await self._call("bus.records.put", self._impl.put, namespace, record_id, dict(payload), timeout=timeout, error_mapper=lambda error: RecordConflictError(str(error)))

    async def delete(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[bool, Exception]:
        if not isinstance(namespace, str) or namespace.strip() == "":
            return Err(RecordConflictError("namespace must be non-empty"))
        if not isinstance(record_id, str) or record_id.strip() == "":
            return Err(RecordConflictError("record_id must be non-empty"))
        return await self._call("bus.records.delete", self._impl.delete, namespace, record_id, timeout=timeout, error_mapper=lambda error: RecordConflictError(str(error)))


__all__ = ["Records", "RecordConflictError", "parse_iso_timestamp"]
