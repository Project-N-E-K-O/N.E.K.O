"""Internal shared facade templates for SDK v2."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar, cast

from plugin.sdk_v2.shared.core.types import LoggerLike
from plugin.sdk_v2.shared.models import Err, Result

P = ParamSpec("P")
T = TypeVar("T")


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

    def _err(self, operation: str, error: Exception) -> Result[T, Exception]:
        self._log_failure(operation, error)
        return cast(Result[T, Exception], Err(error))

    async def _forward_result(
        self,
        operation: str,
        call: Callable[P, Awaitable[Result[T, Exception]]],
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> Result[T, Exception]:
        try:
            return await call(*args, **kwargs)
        except Exception as error:
            return self._err(operation, error)


__all__ = ["AsyncResultFacadeTemplate"]
