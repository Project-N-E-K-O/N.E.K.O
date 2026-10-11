"""Wait for operations whose resources must outlive caller cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


async def await_cancellation_safe(
    operation: asyncio.Task[T],
    *,
    cancel_if: Callable[[], bool] | None = None,
) -> T:
    """Drain an operation before propagating cancellation, including repeated cancels.

    ``cancel_if`` permits cancelling an operation that has not begun its mutation
    yet, such as one still waiting for an operation lock. Once it has begun, its
    own cleanup must finish before the caller can release its resources.
    """
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            result = await asyncio.shield(operation)
        except asyncio.CancelledError as exc:
            if operation.cancelled():
                raise cancellation or exc
            if cancellation is None:
                cancellation = exc
            if cancel_if is not None and cancel_if():
                operation.cancel()
        except BaseException:
            if cancellation is not None:
                raise cancellation from None
            raise
        else:
            if cancellation is not None:
                raise cancellation
            return result
