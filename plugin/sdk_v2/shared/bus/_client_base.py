"""Base bus client facade."""

from __future__ import annotations

from plugin.sdk_v2.shared.bus._state import ensure_state, ensure_transport
from plugin.sdk_v2.shared.core._facade import AsyncResultFacadeTemplate
from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue
from plugin.sdk_v2.shared.models import Err, Result
from plugin.sdk_v2.shared.models.exceptions import (
    BusError,
    BusErrorLike,
    BusTransportError,
    CapabilityUnavailableError,
    ConflictError,
    InvalidArgumentError,
    NotFoundError,
)

from .protocols import BusTransportProtocol


class BusClientBase(AsyncResultFacadeTemplate):
    """Base shared facade for bus clients."""

    def __init__(self, _transport: BusTransportProtocol | None = None, *, namespace: str):
        super().__init__()
        self.namespace = namespace
        self._transport = ensure_transport(_transport)
        self._state = ensure_state(self._transport)

    def _normalize_transport_error(self, error: Exception, *, operation: str, action: str | None = None) -> BusErrorLike:
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
            return error
        return BusTransportError(str(error), op_name=operation, namespace=self.namespace, channel=operation, action=action)

    def _normalize_result(self, result: Result, *, operation: str, action: str | None = None):
        if isinstance(result, Err):
            error = result.error if isinstance(result.error, Exception) else BusTransportError(str(result.error), op_name=operation, namespace=self.namespace, channel=operation, action=action)
            return Err(self._normalize_transport_error(error, operation=operation, action=action))
        return result

    async def _forward_bus_result(self, operation: str, call, /, *args, action: str | None = None, **kwargs):
        result = await self._forward_result(operation, call, *args, **kwargs)
        return self._normalize_result(result, operation=operation, action=action)

    async def request(self, action: str, payload: JsonObject, *, timeout: float = 10.0) -> Result[JsonObject | JsonValue | None, BusErrorLike]:
        if not isinstance(action, str):
            return Err(InvalidArgumentError("action must be non-empty"))
        normalized_action = action.strip()
        if normalized_action == "":
            return Err(InvalidArgumentError("action must be non-empty"))
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            return Err(InvalidArgumentError("timeout must be > 0"))
        try:
            payload_obj = dict(payload)
        except (TypeError, ValueError):
            return Err(InvalidArgumentError("payload must be an object"))
        return await self._forward_bus_result(
            f"bus.{self.namespace}.{normalized_action}",
            self._transport.request,
            f"bus.{self.namespace}.{normalized_action}",
            payload_obj,
            timeout=timeout,
            action=normalized_action,
        )


__all__ = ["BusClientBase", "BusTransportError"]
