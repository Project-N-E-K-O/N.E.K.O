"""Event bus facade."""

from __future__ import annotations

from plugin.sdk_v2.public.bus.events import Events as _ImplEvents
from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Err, Result

from ._facade import BusFacadeMixin
from .types import BusEvent, BusList


class EventPublishError(RuntimeError):
    """Event publish failed due to transport or validation constraints."""


class EventRecord(BusEvent):
    pass


class EventList(BusList[EventRecord]):
    pass


class Events(BusFacadeMixin):
    def __init__(self, _transport=None):
        self._setup_impl(_ImplEvents, _transport, namespace="events")

    async def publish(self, event_type: str, payload: JsonObject, *, timeout: float = 5.0) -> Result[BusEvent, Exception]:
        event_ok = self._require_non_empty_str("event_type", event_type, EventPublishError)
        if isinstance(event_ok, Err):
            return event_ok
        return await self._call("bus.events.publish", self._impl.publish, event_ok, dict(payload), timeout=timeout, error_mapper=lambda e: EventPublishError(str(e)))

    async def list(self, event_type: str | None = None, *, limit: int = 100, timeout: float = 10.0) -> Result[list[BusEvent], Exception]:
        limit_ok = self._require_positive_int("limit", limit)
        if isinstance(limit_ok, Err):
            return limit_ok
        return await self._call("bus.events.list", self._impl.list, event_type, limit=limit_ok, timeout=timeout)


class EventClient:
    def __init__(self, _transport=None):
        self._impl = Events(_transport)

    async def get(self, *, event_type: str | None = None, max_count: int = 100, timeout: float = 5.0) -> EventList:
        listed = await self._impl.list(event_type, limit=max_count, timeout=timeout)
        return EventList([EventRecord(**item.dump()) for item in listed.unwrap_or([])])

    async def get_async(self, **kwargs) -> EventList:
        return await self.get(**kwargs)


__all__ = ["Events", "EventPublishError", "EventRecord", "EventList", "EventClient"]
