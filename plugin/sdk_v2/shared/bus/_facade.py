"""Shared facade helpers for SDK v2 bus."""

from __future__ import annotations

from typing import Callable

from plugin.sdk_v2.shared.bus._client_base import BusTransportError
from plugin.sdk_v2.shared.core._facade import AsyncResultFacadeTemplate
from plugin.sdk_v2.shared.models import Err, Result


class BusFacadeMixin(AsyncResultFacadeTemplate):
    """Common shared-bus facade helpers.

    Keeps shared facades at a medium thickness by centralizing validation and
    transport-error normalization without leaking public implementation details.
    """

    @staticmethod
    def _require_non_empty_str(name: str, value: object) -> Result[str, Exception]:
        if not isinstance(value, str) or value.strip() == "":
            return Err(ValueError(f"{name} must be non-empty"))
        return value.strip()  # type: ignore[return-value]

    @staticmethod
    def _require_positive_int(name: str, value: object) -> Result[int, Exception]:
        if not isinstance(value, int) or value <= 0:
            return Err(ValueError(f"{name} must be > 0"))
        return value  # type: ignore[return-value]

    @staticmethod
    def _map_error(error: Exception, mapper: Callable[[Exception], Exception] | None = None) -> Exception:
        if isinstance(error, (ValueError, TypeError, BusTransportError)):
            return error
        if mapper is not None:
            return mapper(error)
        return BusTransportError(str(error))

    def _normalize(self, result: Result, mapper: Callable[[Exception], Exception] | None = None):
        if isinstance(result, Err):
            return Err(self._map_error(result.error, mapper))
        return result

    async def _call(self, operation: str, call, /, *args, error_mapper: Callable[[Exception], Exception] | None = None, **kwargs):
        result = await self._forward_result(operation, call, *args, **kwargs)
        return self._normalize(result, error_mapper)


__all__ = ["BusFacadeMixin"]
