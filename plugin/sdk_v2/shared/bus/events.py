"""Event bus facade."""

from __future__ import annotations

import logging
import time

from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Err, Ok, Result
from plugin.sdk_v2.shared.models.exceptions import (
    BusError,
    BusErrorLike,
    BusTransportError,
    CapabilityUnavailableError,
    ConflictError,
    EventPublishError,
    InvalidArgumentError,
    NotFoundError,
)

from ._facade import BusFacadeMixin
from .types import BusEvent, BusList

logger = logging.getLogger(__name__)


class EventRecord(BusEvent):
    pass


class EventList(BusList[EventRecord]):
    pass


class Events(BusFacadeMixin):
    MAX_EVENTS = 1000

    def __init__(self, _transport=None):
        self._setup(_transport, namespace="events")

    async def _do_publish(self, event_type: str, payload: JsonObject, *, timeout: float = 5.0) -> Result[BusEvent, BusErrorLike]:
        sent = await self._transport.publish(f"bus.events.{event_type}", payload, timeout=timeout)
        if isinstance(sent, Err):
            error = sent.error
            if isinstance(
                error,
                (
                    BusError,
                    NotFoundError,
                    ConflictError,
                    InvalidArgumentError,
                    CapabilityUnavailableError,
                    BusTransportError,
                ),
            ):
                return Err(error)
            return Err(
                EventPublishError(
                    f"event publish failed topic=bus.events.{event_type} timeout={timeout} error={error}"
                )
            )
        item = BusEvent(id=self._state.next_id("event"), event_type=event_type, payload=dict(payload), timestamp=time.time())
        self._state.events.append(item)
        if len(self._state.events) > self.MAX_EVENTS:
            del self._state.events[:-self.MAX_EVENTS]
        for watcher in list(self._state.watchers.values()):
            if watcher.channel == event_type or watcher.channel == "*":
                watcher.queue.append(item)
                try:
                    result = watcher.handler(item)
                    if hasattr(result, "__await__"):
                        await result
                except Exception as error:
                    logger.exception(
                        "event watcher handler failed handler=%r event_type=%s item=%r error=%s",
                        watcher.handler,
                        event_type,
                        item,
                        error,
                    )
                    continue
        return Ok(item)

    async def _do_list(self, event_type: str | None = None, *, limit: int = 100, timeout: float = 10.0) -> Result[list[BusEvent], BusErrorLike]:
        if limit < 0:
            return Err(InvalidArgumentError("limit must be >= 0"))
        items = self._state.events
        if event_type is not None:
            items = [item for item in items if item.event_type == event_type]
        return Ok(list(items[:limit]))

    async def publish(self, event_type: str, payload: JsonObject, *, timeout: float = 5.0) -> Result[BusEvent, BusErrorLike]:
        event_ok = self._require_non_empty_str("event_type", event_type, EventPublishError)
        if isinstance(event_ok, Err):
            return event_ok
        return await self._call("bus.events.publish", self._do_publish, event_ok.value, dict(payload), timeout=timeout, error_mapper=lambda e: EventPublishError(str(e)))

    async def list(self, event_type: str | None = None, *, limit: int = 100, timeout: float = 10.0) -> Result[list[BusEvent], BusErrorLike]:
        limit_ok = self._require_positive_int("limit", limit)
        if isinstance(limit_ok, Err):
            return limit_ok
        return await self._call("bus.events.list", self._do_list, event_type, limit=limit_ok.value, timeout=timeout)


class EventClient:
    def __init__(self, _transport=None):
        self._impl = Events(_transport)

    async def get(self, *, event_type: str | None = None, max_count: int = 100, timeout: float = 5.0) -> EventList:
        listed = await self._impl.list(event_type, limit=max_count, timeout=timeout)
        if listed.is_err():
            error = listed.error
            if isinstance(error, Exception):
                raise error
            raise RuntimeError(str(error))
        return EventList([EventRecord(**item.dump()) for item in listed.value])


__all__ = ["EventClient", "EventList", "EventPublishError", "EventRecord", "Events"]
