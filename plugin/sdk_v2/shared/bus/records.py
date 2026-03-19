"""Record bus facade and helpers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Err, Ok, Result
from plugin.sdk_v2.shared.models.exceptions import BusErrorLike, NotFoundError, RecordConflictError

from ._changes import dispatch_bus_change
from ._facade import BusFacadeMixin
from .types import BusRecord


def parse_iso_timestamp(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
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
    except (TypeError, ValueError):
        return None


class Records(BusFacadeMixin):
    def __init__(self, _transport=None):
        self._setup(_transport, namespace="records")

    async def _do_list(self, namespace: str, *, limit: int = 100, timeout: float = 10.0) -> Result[list[BusRecord], BusErrorLike]:
        items = [item for (ns, _), item in self._state.records.items() if ns == namespace]
        return Ok(items[:limit])

    async def _do_get(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[BusRecord, BusErrorLike]:
        item = self._state.records.get((namespace, record_id))
        return Ok(item) if item is not None else Err(NotFoundError(record_id))

    async def _do_put(self, namespace: str, record_id: str, payload: JsonObject, *, timeout: float = 10.0) -> Result[BusRecord, BusErrorLike]:
        existed = (namespace, record_id) in self._state.records
        rev = self._state.revisions.get((namespace, record_id), 0) + 1
        self._state.revisions[(namespace, record_id)] = rev
        item = BusRecord(id=record_id, namespace=namespace, payload=dict(payload), rev=rev)
        self._state.records[(namespace, record_id)] = item
        op = "change" if existed else "put"
        delta = {"namespace": namespace, "record_id": record_id, "rev": rev, "record": item.dump(), "op": op}
        dispatch_bus_change(sub_id=f"records:{namespace}:{record_id}", bus=f"records:{namespace}", op=op, delta=delta)
        try:
            from plugin.sdk_v2.shared.bus.types import BusEvent
            event = BusEvent(id=f"records:{namespace}:{record_id}:{rev}", event_type=f"records:{namespace}", payload=dict(delta))
            for watcher in self._state.watchers.values():
                if watcher.channel in {f"records:{namespace}", "records", "*"}:
                    watcher.queue.append(event)
        except Exception:
            pass
        return Ok(item)

    async def _do_delete(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[bool, BusErrorLike]:
        removed_item = self._state.records.pop((namespace, record_id), None)
        rev = self._state.revisions.pop((namespace, record_id), 0)
        removed = removed_item is not None
        if removed:
            delta = {"namespace": namespace, "record_id": record_id, "rev": rev, "record": removed_item.dump(), "op": "delete"}
            dispatch_bus_change(sub_id=f"records:{namespace}:{record_id}", bus=f"records:{namespace}", op="delete", delta=delta)
            try:
                from plugin.sdk_v2.shared.bus.types import BusEvent
                event = BusEvent(id=f"records:{namespace}:{record_id}:delete", event_type=f"records:{namespace}", payload=dict(delta))
                for watcher in self._state.watchers.values():
                    if watcher.channel in {f"records:{namespace}", "records", "*"}:
                        watcher.queue.append(event)
            except Exception:
                pass
        return Ok(removed)

    async def list(self, namespace: str, *, limit: int = 100, timeout: float = 10.0) -> Result[list[BusRecord], BusErrorLike]:
        namespace_ok = self._require_non_empty_str("namespace", namespace)
        if not self._is_ok(namespace_ok):
            return namespace_ok
        limit_ok = self._require_positive_int("limit", limit)
        if not self._is_ok(limit_ok):
            return limit_ok
        return await self._call("bus.records.list", self._do_list, namespace_ok.value, limit=limit_ok.value, timeout=timeout)

    async def get(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[BusRecord, BusErrorLike]:
        namespace_ok = self._require_non_empty_str("namespace", namespace, RecordConflictError)
        if not self._is_ok(namespace_ok):
            return namespace_ok
        record_ok = self._require_non_empty_str("record_id", record_id, RecordConflictError)
        if not self._is_ok(record_ok):
            return record_ok
        return await self._call(
            "bus.records.get",
            self._do_get,
            namespace_ok.value,
            record_ok.value,
            timeout=timeout,
            error_mapper=lambda error: RecordConflictError(str(error)),
        )

    async def put(self, namespace: str, record_id: str, payload: JsonObject, *, timeout: float = 10.0) -> Result[BusRecord, BusErrorLike]:
        namespace_ok = self._require_non_empty_str("namespace", namespace, RecordConflictError)
        if not self._is_ok(namespace_ok):
            return namespace_ok
        record_ok = self._require_non_empty_str("record_id", record_id, RecordConflictError)
        if not self._is_ok(record_ok):
            return record_ok
        if not isinstance(payload, Mapping) or not all(isinstance(key, str) for key in payload):
            return Err(RecordConflictError("payload must be a mapping with string keys"))
        return await self._call(
            "bus.records.put",
            self._do_put,
            namespace_ok.value,
            record_ok.value,
            dict(payload),
            timeout=timeout,
            error_mapper=lambda error: RecordConflictError(str(error)),
        )

    async def delete(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[bool, BusErrorLike]:
        namespace_ok = self._require_non_empty_str("namespace", namespace, RecordConflictError)
        if not self._is_ok(namespace_ok):
            return namespace_ok
        record_ok = self._require_non_empty_str("record_id", record_id, RecordConflictError)
        if not self._is_ok(record_ok):
            return record_ok
        return await self._call(
            "bus.records.delete",
            self._do_delete,
            namespace_ok.value,
            record_ok.value,
            timeout=timeout,
            error_mapper=lambda error: RecordConflictError(str(error)),
        )


__all__ = ["RecordConflictError", "Records", "parse_iso_timestamp"]
