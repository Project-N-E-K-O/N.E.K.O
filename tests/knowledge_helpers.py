"""Shared synchronization helpers for isolated knowledge service tests.

These helpers observe the real service tasks and lock queue. They deliberately
own no storage roots or global fixtures, so callers retain teardown ownership.
"""

import asyncio


async def _stop_automatic_indexer(service):
    """Let each fixture explicitly schedule the index round it is observing."""
    indexer = next(task for task in service._tasks if task.get_name() == "knowledge-indexer")
    indexer.cancel()
    await asyncio.gather(indexer, return_exceptions=True)


class ObservedLock(asyncio.Lock):
    """Report when real lock acquisitions have joined the blocked FIFO."""

    def __init__(self):
        super().__init__()
        self.waiting = asyncio.Queue()

    async def acquire(self):
        if self.locked():
            self.waiting.put_nowait(None)
        return await super().acquire()
