"""Finish physical retirement before propagating caller cancellation."""

import asyncio
from contextlib import suppress


async def await_retirement(awaitable):
    task = asyncio.ensure_future(awaitable)
    cancellation = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if asyncio.current_task().cancelling():
                cancellation = exc
        except Exception:
            break
    try:
        result = task.result()
    except BaseException:
        if cancellation is not None:
            raise cancellation
        raise
    if cancellation is not None:
        raise cancellation
    return result


async def await_completion_with_cancel(coro):
    """Finish an async transaction despite cancellation and report that cancellation."""
    operation = asyncio.create_task(coro)
    try:
        return await asyncio.shield(operation), False
    except asyncio.CancelledError:
        while not operation.done():
            with suppress(asyncio.CancelledError):
                await asyncio.wait({operation})
        return operation.result(), True
