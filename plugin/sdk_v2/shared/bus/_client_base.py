"""Base bus client facade."""

from __future__ import annotations

from plugin.sdk_v2.public.bus._client_base import BusClientBase as _ImplBusClientBase
from plugin.sdk_v2.shared.core._facade import AsyncResultFacadeTemplate
from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue
from plugin.sdk_v2.shared.models import Err, Result

from .protocols import BusTransportProtocol


class BusTransportError(RuntimeError):
    """Transport/runtime failure while talking to bus infrastructure."""


class BusClientBase(AsyncResultFacadeTemplate):
    """Base shared facade for bus clients."""

    def __init__(self, _transport: BusTransportProtocol | None = None, *, namespace: str):
        super().__init__()
        self.namespace = namespace
        self._impl = _ImplBusClientBase(_transport, namespace=namespace)
        self._transport = self._impl._transport
        self._state = self._impl._state

    def _normalize_transport_error(self, error: Exception) -> Exception:
        if isinstance(error, (ValueError, TypeError, BusTransportError)):
            return error
        return BusTransportError(str(error))

    def _normalize_result(self, result: Result):
        if isinstance(result, Err):
            return Err(self._normalize_transport_error(result.error))
        return result

    async def _forward_bus_result(self, operation: str, call, /, *args, **kwargs):
        result = await self._forward_result(operation, call, *args, **kwargs)
        return self._normalize_result(result)

    async def request(self, action: str, payload: JsonObject, *, timeout: float = 10.0) -> Result[JsonObject | JsonValue | None, Exception]:
        if not isinstance(action, str) or action.strip() == "":
            return Err(ValueError("action must be non-empty"))
        if timeout <= 0:
            return Err(ValueError("timeout must be > 0"))
        return await self._forward_bus_result(f"bus.{self.namespace}.{action}", self._impl.request, action, dict(payload), timeout=timeout)


__all__ = ["BusClientBase", "BusTransportError"]
