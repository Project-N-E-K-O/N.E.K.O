from __future__ import annotations

from plugin.sdk_v2.shared.bus.types import BusRecord
from plugin.sdk_v2.shared.core.types import JsonObject
from ._changes import dispatch_bus_change
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

    async def delete(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[bool, Exception]:
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


__all__ = ["Records"]
