"""Internal shared facade templates for SDK v2."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar, cast

from plugin.sdk_v2.shared.core.types import LoggerLike
from plugin.sdk_v2.shared.models import Err, Result
from plugin.sdk_v2.shared.models.exceptions import SdkError, TransportError

P = ParamSpec("P")
T = TypeVar("T")
E = TypeVar("E")


class AsyncResultFacadeTemplate:
    """Template for shared-layer facades with unified Result/logging semantics."""

    def __init__(self, *, logger: LoggerLike | None = None):
        self._logger = logger

    def _log_failure(self, operation: str, error: Exception) -> None:
        if self._logger is None:
            return
        try:
            self._logger.exception(f"{operation} failed: {error}")
        except Exception:
            return

    @staticmethod
    def _normalize_error(error: Exception, *, operation: str | None = None) -> SdkError:
        return error if isinstance(error, SdkError) else TransportError(str(error), op_name=operation)

    def _err(self, operation: str, error: Exception) -> Result[T, SdkError]:
        self._log_failure(operation, error)
        return cast(Result[T, SdkError], Err(self._normalize_error(error, operation=operation)))

    async def _forward_result(
        self,
        operation: str,
        call: Callable[P, Awaitable[Result[T, E]]],
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> Result[T, E | TransportError]:
        try:
            result = await call(*args, **kwargs)
            if isinstance(result, Err):
                error = result.error if isinstance(result.error, Exception) else TransportError(str(result.error), op_name=operation)
                return cast(Result[T, E | TransportError], Err(self._normalize_error(error, operation=operation)))
            return cast(Result[T, E | TransportError], result)
        except Exception as error:
            return cast(Result[T, E | TransportError], self._err(operation, error))


__all__ = ["AsyncResultFacadeTemplate"]
