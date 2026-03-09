"""Internal storage templates for SDK v2 public implementations."""

from __future__ import annotations

from collections.abc import Callable
from typing import ParamSpec, TypeVar, cast

from plugin.sdk_v2.shared.core.types import LoggerLike
from plugin.sdk_v2.shared.models import Err, Ok, Result

P = ParamSpec("P")
T = TypeVar("T")


class StorageResultTemplate:
    """Wrap sync implementation details behind async `Result` methods."""

    def __init__(self, *, logger: LoggerLike | None = None):
        self._logger = logger

    def _log_failure(self, operation: str, error: Exception) -> None:
        if self._logger is None:
            return
        try:
            self._logger.exception(f"{operation} failed: {error}")
        except Exception:
            return

    async def _run_sync_result(
        self,
        operation: str,
        call: Callable[P, T],
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> Result[T, Exception]:
        try:
            return Ok(call(*args, **kwargs))
        except Exception as error:
            self._log_failure(operation, error)
            return cast(Result[T, Exception], Err(error))


__all__ = ["StorageResultTemplate"]
