"""Base bus client contract."""

from __future__ import annotations

from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue
from plugin.sdk_v2.shared.models import Result

from .protocols import BusTransportProtocol


class BusClientBase:
    """Base class for bus clients."""

    def __init__(self, _transport: BusTransportProtocol, *, namespace: str):
        raise NotImplementedError("sdk_v2 contract-only facade: shared.bus._client_base not implemented")

    async def request(self, action: str, payload: JsonObject, *, timeout: float = 10.0) -> Result[JsonObject | JsonValue | None, Exception]:
        raise NotImplementedError


__all__ = ["BusClientBase"]
