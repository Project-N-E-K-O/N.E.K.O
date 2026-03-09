"""Watcher bus facade."""

from __future__ import annotations

from typing import Awaitable, Protocol

from plugin.sdk_v2.public.bus.watchers import Watchers as _ImplWatchers
from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Err, Result

from ._client_base import BusClientBase
from .types import BusEvent


class WatchEventHandler(Protocol):
    def __call__(self, event: BusEvent) -> Awaitable[None]: ...


class Watchers(BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="watchers")
        self._impl = _ImplWatchers(self._transport)
        self._state = self._impl._state

    async def watch(self, channel: str, handler: WatchEventHandler, *, options: JsonObject | None = None, timeout: float = 5.0) -> Result[str, Exception]:
        if not isinstance(channel, str) or channel.strip() == "":
            return Err(ValueError("channel must be non-empty"))
        if not callable(handler):
            return Err(TypeError("handler must be callable"))
        return await self._forward_result("bus.watchers.watch", self._impl.watch, channel, handler, options=options, timeout=timeout)

    async def unwatch(self, watcher_id: str, *, timeout: float = 5.0) -> Result[bool, Exception]:
        if not isinstance(watcher_id, str) or watcher_id.strip() == "":
            return Err(ValueError("watcher_id must be non-empty"))
        return await self._forward_result("bus.watchers.unwatch", self._impl.unwatch, watcher_id, timeout=timeout)

    async def poll(self, channel: str, *, timeout: float = 5.0) -> Result[list[BusEvent], Exception]:
        if not isinstance(channel, str) or channel.strip() == "":
            return Err(ValueError("channel must be non-empty"))
        return await self._forward_result("bus.watchers.poll", self._impl.poll, channel, timeout=timeout)


__all__ = ["Watchers", "WatchEventHandler"]
