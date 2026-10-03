"""Finish physical retirement before propagating caller cancellation."""

import asyncio


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
