from __future__ import annotations

import time

from plugin.sdk_v2.shared.bus.types import BusEvent
from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Err, Ok, Result

from ._client_base import BusClientBase


class Events(BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="events")

    async def publish(self, event_type: str, payload: JsonObject, *, timeout: float = 5.0) -> Result[BusEvent, Exception]:
        sent = await self._transport.publish(f"bus.events.{event_type}", payload, timeout=timeout)
        if isinstance(sent, Err):
            return Err(RuntimeError(str(sent.error)))
        item = BusEvent(id=self._state.next_id("event"), event_type=event_type, payload=dict(payload), timestamp=time.time())
        self._state.events.append(item)
        for watcher in self._state.watchers.values():
            if watcher.channel == event_type or watcher.channel == "*":
                watcher.queue.append(item)
        return Ok(item)

    async def list(self, event_type: str | None = None, *, limit: int = 100, timeout: float = 10.0) -> Result[list[BusEvent], Exception]:
        items = self._state.events
        if event_type is not None:
            items = [item for item in items if item.event_type == event_type]
        return Ok(list(items[:limit]))


__all__ = ["Events"]
