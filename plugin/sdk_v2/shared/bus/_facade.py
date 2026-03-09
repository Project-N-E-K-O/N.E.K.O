"""Shared facade helpers for SDK v2 bus."""

from __future__ import annotations

from typing import Callable, TypeVar

from plugin.sdk_v2.shared.bus._client_base import BusClientBase, BusTransportError
from plugin.sdk_v2.shared.models import Err, Result

T = TypeVar("T")


class BusFacadeMixin(BusClientBase):
    """Common shared-bus facade helpers.

    Keeps shared facades at a medium thickness by centralizing:
    - transport/state/impl wiring
    - field validation
    - transport-error normalization
    - domain-error mapping
    """

    def _setup_impl(self, impl_cls, _transport, *, namespace: str) -> None:
        super().__init__(_transport, namespace=namespace)
        self._impl = impl_cls(self._transport)
        self._state = self._impl._state

    @staticmethod
    def _domain_err(error_cls, message: str):
        return Err(error_cls(message))

    @staticmethod
    def _require_non_empty_str(name: str, value: object, error_cls=ValueError) -> Result[str, Exception]:
        if not isinstance(value, str) or value.strip() == "":
            return Err(error_cls(f"{name} must be non-empty"))
        return value.strip()  # type: ignore[return-value]

    @staticmethod
    def _require_positive_int(name: str, value: object, error_cls=ValueError) -> Result[int, Exception]:
        if not isinstance(value, int) or value <= 0:
            return Err(error_cls(f"{name} must be > 0"))
        return value  # type: ignore[return-value]


    @staticmethod
    def _is_ok(result) -> bool:
        return not isinstance(result, Err)

    @staticmethod
    def _type_error(name: str, expected: str):
        return Err(TypeError(f"{name} must be {expected}"))

    @staticmethod
    def _require_int(name: str, value: object, error_cls=ValueError):
        if not isinstance(value, int):
            return Err(error_cls(f"{name} must be int"))
        return value

    @staticmethod
    def _map_error(error: Exception, mapper: Callable[[Exception], Exception] | None = None) -> Exception:
        if isinstance(error, (ValueError, TypeError, BusTransportError)):
            return error
        if mapper is not None:
            return mapper(error)
        return BusTransportError(str(error))

    def _normalize(self, result: Result[T, Exception], mapper: Callable[[Exception], Exception] | None = None):
        if isinstance(result, Err):
            return Err(self._map_error(result.error, mapper))
        return result

    async def _call(self, operation: str, call, /, *args, error_mapper: Callable[[Exception], Exception] | None = None, **kwargs):
        result = await self._forward_bus_result(operation, call, *args, **kwargs)
        return self._normalize(result, error_mapper)


__all__ = ["BusFacadeMixin"]
