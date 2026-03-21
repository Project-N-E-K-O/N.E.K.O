"""Internal storage templates for SDK v2 public implementations."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import ParamSpec, TypeVar, cast

from plugin.sdk.shared.core.types import LoggerLike
from plugin.sdk.shared.models import Err, Ok, Result
from plugin.sdk.shared.models.exceptions import TransportError

P = ParamSpec("P")
T = TypeVar("T")


class StorageResultTemplate:
    """Wrap local implementation details behind async `Result` methods."""

    def __init__(self, *, logger: LoggerLike | None = None):
        self._logger = logger

    def _log_failure(self, operation: str, error: Exception) -> None:
        if self._logger is None:
            return
        try:
            self._logger.exception(f"{operation} failed: {error}")
        except Exception:
            return

    async def _run_local_result(
        self,
        operation: str,
        call: Callable[P, T],
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> Result[T, TransportError]:
        """Run a synchronous local callable in `asyncio.to_thread`.

        The provided `call` must be a synchronous function. Coroutine functions
        and coroutine objects are not awaited by `asyncio.to_thread` and are
        rejected so `_run_local_result` behaves deterministically.
        """
        try:
            if asyncio.iscoroutinefunction(call):
                raise TypeError("_run_local_result requires a synchronous callable; coroutine functions are not supported by asyncio.to_thread")
            result = await asyncio.to_thread(call, *args, **kwargs)
            if inspect.isawaitable(result):
                raise TypeError("_run_local_result requires a synchronous callable; coroutine results are not supported by asyncio.to_thread")
            return Ok(result)
        except Exception as error:
            self._log_failure(operation, error)
            normalized = error if isinstance(error, TransportError) else TransportError(str(error), op_name=operation)
            return cast(Result[T, TransportError], Err(normalized))


__all__ = ["StorageResultTemplate"]
