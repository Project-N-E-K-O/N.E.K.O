from __future__ import annotations

from plugin.sdk_v2.shared.bus.protocols import BusTransportProtocol
from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue
from plugin.sdk_v2.shared.models import Result

from ._state import ensure_state, ensure_transport


class BusClientBase:
    def __init__(self, _transport: BusTransportProtocol | None = None, *, namespace: str):
        self._transport = ensure_transport(_transport)
        self._state = ensure_state(self._transport)
        self.namespace = namespace

    async def request(self, action: str, payload: JsonObject, *, timeout: float = 10.0) -> Result[JsonObject | JsonValue | None, Exception]:
        return await self._transport.request(f"bus.{self.namespace}.{action}", payload, timeout=timeout)


__all__ = ["BusClientBase"]
