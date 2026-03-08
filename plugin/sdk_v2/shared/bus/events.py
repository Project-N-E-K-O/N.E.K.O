"""Event bus contracts."""

from __future__ import annotations

from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Result

from ._client_base import BusClientBase
from .types import BusEvent


class EventPublishError(RuntimeError):
    """Event publish failed due to transport or validation constraints."""


class Events(BusClientBase):
    def __init__(self, *args: object, **kwargs: object):
        raise NotImplementedError("sdk_v2 contract-only facade: shared.bus.events not implemented")

    async def publish(self, event_type: str, payload: JsonObject, *, timeout: float = 5.0) -> Result[BusEvent, Exception]:
        raise NotImplementedError

    async def list(self, event_type: str | None = None, *, limit: int = 100, timeout: float = 10.0) -> Result[list[BusEvent], Exception]:
        raise NotImplementedError


__all__ = ["Events", "EventPublishError"]
