"""Run final disk operations under the host's storage fence."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def discard_temporary_file(path: Path | None) -> None:
    """Best-effort removal of an unpublished temp file inside a ``finally`` block.

    On Windows another process (antivirus, indexer, sync client) may still hold
    the file open, so ``unlink`` raises. Raising from ``finally`` would replace
    the real outcome (for example a "session exists" conflict) with that
    ``OSError``; a leftover dot-prefixed ``.tmp`` file is harmless instead.
    """

    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Numeric v2 could not remove temporary file %s", path, exc_info=True)


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
