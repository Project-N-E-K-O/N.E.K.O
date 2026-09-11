"""Run final disk operations under the host's storage fence."""

from __future__ import annotations

import asyncio


async def run_storage_mutation(transaction, operation, *args, **kwargs):
    # Acquire and release on the same worker (Windows mutexes are thread-bound).
    # No model or HTTP request runs inside this scope.
    def mutate():
        with transaction():
            return operation(*args, **kwargs)

    task = asyncio.create_task(asyncio.to_thread(mutate))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # A disconnected caller must not release story/character locks while its
        # worker is still writing or rolling back files.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise
