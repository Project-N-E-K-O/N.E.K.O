"""Record bus facade and helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from plugin.sdk_v2.public.bus.records import Records as _ImplRecords
from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Result

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


class Records(BusFacadeMixin):
    def __init__(self, _transport=None):
        self._setup_impl(_ImplRecords, _transport, namespace="records")

    async def list(self, namespace: str, *, limit: int = 100, timeout: float = 10.0) -> Result[list[BusRecord], Exception]:
        namespace_ok = self._require_non_empty_str("namespace", namespace)
        if not self._is_ok(namespace_ok):
            return namespace_ok
        limit_ok = self._require_positive_int("limit", limit)
        if not self._is_ok(limit_ok):
            return limit_ok
        return await self._call("bus.records.list", self._impl.list, namespace_ok, limit=limit_ok, timeout=timeout)

    async def get(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[BusRecord, Exception]:
        namespace_ok = self._require_non_empty_str("namespace", namespace, RecordConflictError)
        if not self._is_ok(namespace_ok):
            return namespace_ok
        record_ok = self._require_non_empty_str("record_id", record_id, RecordConflictError)
        if not self._is_ok(record_ok):
            return record_ok
        return await self._call(
            "bus.records.get",
            self._impl.get,
            namespace_ok,
            record_ok,
            timeout=timeout,
            error_mapper=lambda error: RecordConflictError(str(error)),
        )

    async def put(self, namespace: str, record_id: str, payload: JsonObject, *, timeout: float = 10.0) -> Result[BusRecord, Exception]:
        namespace_ok = self._require_non_empty_str("namespace", namespace, RecordConflictError)
        if not self._is_ok(namespace_ok):
            return namespace_ok
        record_ok = self._require_non_empty_str("record_id", record_id, RecordConflictError)
        if not self._is_ok(record_ok):
            return record_ok
        return await self._call(
            "bus.records.put",
            self._impl.put,
            namespace_ok,
            record_ok,
            dict(payload),
            timeout=timeout,
            error_mapper=lambda error: RecordConflictError(str(error)),
        )

    async def delete(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[bool, Exception]:
        namespace_ok = self._require_non_empty_str("namespace", namespace, RecordConflictError)
        if not self._is_ok(namespace_ok):
            return namespace_ok
        record_ok = self._require_non_empty_str("record_id", record_id, RecordConflictError)
        if not self._is_ok(record_ok):
            return record_ok
        return await self._call(
            "bus.records.delete",
            self._impl.delete,
            namespace_ok,
            record_ok,
            timeout=timeout,
            error_mapper=lambda error: RecordConflictError(str(error)),
        )


__all__ = ["Records", "RecordConflictError", "parse_iso_timestamp"]
