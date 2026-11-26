"""Wait for a set of background tasks that remove themselves when done."""

from __future__ import annotations

import asyncio
from collections.abc import Set


async def wait_task_set_empty(
    tasks: Set[asyncio.Future],
    *,
    return_exceptions: bool = False,
) -> None:
    """Wait until ``tasks`` is empty, including tasks added while waiting.

    The product code keeps fire-and-forget tasks in a set and removes each one
    with ``task.add_done_callback(tasks.discard)``. That callback runs on a
    later turn of the event loop, not when the task finishes. Since Python
    3.12, ``asyncio.gather()`` over tasks that are all already done completes
    without yielding to the loop, so a bare
    ``while tasks: await asyncio.gather(*tasks)`` never lets those callbacks
    run and spins forever. Yielding once after each gather lets them run.
    """

    while tasks:
        await asyncio.gather(*tuple(tasks), return_exceptions=return_exceptions)
        await asyncio.sleep(0)
