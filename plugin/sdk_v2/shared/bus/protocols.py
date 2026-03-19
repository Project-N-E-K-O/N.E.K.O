"""Transport protocols for SDK v2 shared bus."""

from __future__ import annotations

from typing import Protocol

from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue
from plugin.sdk_v2.shared.models import Result
from plugin.sdk_v2.shared.models.exceptions import BusErrorLike


class BusTransportProtocol(Protocol):
    async def request(self, channel: str, payload: JsonObject, *, timeout: float = 10.0) -> Result[JsonObject | JsonValue | None, BusErrorLike]:
        ...

    async def publish(self, channel: str, payload: JsonObject, *, timeout: float = 5.0) -> Result[None, BusErrorLike]:
        ...


__all__ = ["BusTransportProtocol"]
