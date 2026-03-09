from __future__ import annotations

from typing import Awaitable, Protocol

from plugin.sdk_v2.shared.bus.types import BusEvent
from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Ok, Result

from ._client_base import BusClientBase
from ._state import _Watcher


class WatchEventHandler(Protocol):
    def __call__(self, event: BusEvent) -> Awaitable[None]: ...


class Watchers(BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="watchers")

    async def watch(self, channel: str, handler: WatchEventHandler, *, options: JsonObject | None = None, timeout: float = 5.0) -> Result[str, Exception]:
        watcher_id = self._state.next_id("watcher")
        self._state.watchers[watcher_id] = _Watcher(id=watcher_id, channel=channel, handler=handler)
        return Ok(watcher_id)

    async def unwatch(self, watcher_id: str, *, timeout: float = 5.0) -> Result[bool, Exception]:
        return Ok(self._state.watchers.pop(watcher_id, None) is not None)

    async def poll(self, channel: str, *, timeout: float = 5.0) -> Result[list[BusEvent], Exception]:
        items: list[BusEvent] = []
        for watcher in self._state.watchers.values():
            if watcher.channel == channel or watcher.channel == "*":
                items.extend(watcher.queue)
                watcher.queue.clear()
        return Ok(items)


__all__ = ["Watchers", "WatchEventHandler"]
