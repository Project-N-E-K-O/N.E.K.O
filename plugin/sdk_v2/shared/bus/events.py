"""Event bus facade."""

from __future__ import annotations

from plugin.sdk_v2.public.bus.events import Events as _ImplEvents
from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Err, Result

from ._client_base import BusClientBase
from .types import BusEvent


class EventPublishError(RuntimeError):
    """Event publish failed due to transport or validation constraints."""


class Events(BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="events")
        self._impl = _ImplEvents(self._transport)
        self._state = self._impl._state

    async def publish(self, event_type: str, payload: JsonObject, *, timeout: float = 5.0) -> Result[BusEvent, Exception]:
        if not isinstance(event_type, str) or event_type.strip() == "":
            return Err(EventPublishError("event_type must be non-empty"))
        result = await self._forward_result("bus.events.publish", self._impl.publish, event_type, dict(payload), timeout=timeout)
        if isinstance(result, Err) and isinstance(result.error, RuntimeError):
            return Err(EventPublishError(str(result.error)))
        return result

    async def list(self, event_type: str | None = None, *, limit: int = 100, timeout: float = 10.0) -> Result[list[BusEvent], Exception]:
        if limit <= 0:
            return Err(ValueError("limit must be > 0"))
        return await self._forward_result("bus.events.list", self._impl.list, event_type, limit=limit, timeout=timeout)


__all__ = ["Events", "EventPublishError"]
