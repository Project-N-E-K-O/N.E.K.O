"""Shared facade helpers for SDK v2 bus."""

from __future__ import annotations

from typing import Callable, TypeVar, cast

from plugin.sdk_v2.shared.bus._client_base import BusClientBase, BusTransportError
from plugin.sdk_v2.shared.models import Err, Ok, Result
from plugin.sdk_v2.shared.models.exceptions import (
    BusError,
    BusErrorLike,
    CapabilityUnavailableError,
    ConflictError,
    InvalidArgumentError,
    NotFoundError,
)

T = TypeVar("T")


class BusFacadeMixin(BusClientBase):
    """Common shared-bus facade helpers.

    Keeps shared facades at a medium thickness by centralizing:
    - transport/state/impl wiring
    - field validation
    - transport-error normalization
    - domain-error mapping
    """

    def _setup(self, _transport, *, namespace: str) -> None:
        """Initialize transport, state and namespace directly (no public impl)."""
        super().__init__(_transport, namespace=namespace)

    # Keep _setup_impl as an alias during transition; it now ignores the impl_cls.
    def _setup_impl(self, _impl_cls_unused, _transport, *, namespace: str) -> None:
        self._setup(_transport, namespace=namespace)

    @staticmethod
    def _domain_err(error_cls, message: str):
        return Err(error_cls(message))

    @staticmethod
    def _require_non_empty_str(name: str, value: object, error_cls=InvalidArgumentError) -> Result[str, BusErrorLike]:
        if not isinstance(value, str) or value.strip() == "":
            return Err(error_cls(f"{name} must be non-empty"))
        return cast(Result[str, BusErrorLike], Ok(value.strip()))

    @staticmethod
    def _require_positive_int(name: str, value: object, error_cls=InvalidArgumentError) -> Result[int, BusErrorLike]:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return Err(error_cls(f"{name} must be > 0"))
        return cast(Result[int, BusErrorLike], Ok(value))


    @staticmethod
    def _is_ok(result) -> bool:
        return not isinstance(result, Err)

    @staticmethod
    def _type_error(name: str, expected: str) -> Result[object, BusErrorLike]:
        return Err(InvalidArgumentError(f"{name} must be {expected}"))

    @staticmethod
    def _require_int(name: str, value: object, error_cls=InvalidArgumentError) -> Result[int, BusErrorLike]:
        if isinstance(value, bool) or not isinstance(value, int):
            return Err(error_cls(f"{name} must be int"))
        return cast(Result[int, BusErrorLike], Ok(value))

    @staticmethod
    def _map_error(
        error: Exception,
        mapper: Callable[[Exception], BusErrorLike] | None = None,
        *,
        operation: str | None = None,
        namespace: str | None = None,
    ) -> BusErrorLike:
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
        if mapper is not None:
            return mapper(error)
        return BusTransportError(str(error), op_name=operation, namespace=namespace, channel=operation)

    def _normalize(
        self,
        result: Result[T, object],
        mapper: Callable[[Exception], BusErrorLike] | None = None,
        *,
        operation: str,
    ) -> Result[T, BusErrorLike]:
        if isinstance(result, Err):
            error = result.error if isinstance(result.error, Exception) else BusTransportError(str(result.error), op_name=operation, namespace=self.namespace, channel=operation)
            return Err(self._map_error(error, mapper, operation=operation, namespace=self.namespace))
        return result

    async def _call(self, operation: str, call, /, *args, error_mapper: Callable[[Exception], BusErrorLike] | None = None, **kwargs) -> Result[T, BusErrorLike]:
        result = await self._forward_bus_result(operation, call, *args, **kwargs)
        return self._normalize(cast(Result[T, object], result), error_mapper, operation=operation)


__all__ = ["BusFacadeMixin"]
