"""Admission, tracking, and cross-process fencing for knowledge writers."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

# The barrier is shared with root migration, so it lives in the storage layer;
# re-exported here for the knowledge writers and routers that already use it.
from utils.storage.knowledge_contract import knowledge_root_barrier


_T = TypeVar("_T")
_STATE_LOCK = threading.Lock()
_ADMISSION_OPEN = True
_WRITERS: set[asyncio.Task[object]] = set()


class KnowledgeMutationAdmissionClosed(RuntimeError):
    """Raised when a writer races with the shutdown admission barrier."""


def _run_under_root_barrier(
    knowledge_root: str | Path,
    action: Callable[..., _T],
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> _T:
    with knowledge_root_barrier(knowledge_root):
        return action(*args, **kwargs)


def _writer_finished(task: asyncio.Task[object]) -> None:
    with _STATE_LOCK:
        _WRITERS.discard(task)
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


async def run_knowledge_writer(
    knowledge_root: str | Path,
    action: Callable[..., _T],
    /,
    *args: object,
    **kwargs: object,
) -> _T:
    """Admit one off-loop writer and retain it until its real thread returns.

    Cancelling the request or indexer coordinator only stops awaiting the
    worker.  The task remains strongly referenced and continues occupying a
    shutdown slot until ``asyncio.to_thread`` has actually finished.
    """

    loop = asyncio.get_running_loop()
    with _STATE_LOCK:
        if not _ADMISSION_OPEN:
            raise KnowledgeMutationAdmissionClosed("knowledge_mutation_stopping")
        task = loop.create_task(
            asyncio.to_thread(
                _run_under_root_barrier,
                knowledge_root,
                action,
                tuple(args),
                dict(kwargs),
            ),
            name=f"knowledge-writer:{getattr(action, '__name__', 'mutation')}",
        )
        _WRITERS.add(task)
        task.add_done_callback(_writer_finished)
    return await asyncio.shield(task)


def open_knowledge_writer_admission() -> None:
    """Open writer admission for a newly started main-server runtime."""

    global _ADMISSION_OPEN
    with _STATE_LOCK:
        _ADMISSION_OPEN = True


def request_knowledge_writer_stop() -> tuple[asyncio.Task[object], ...]:
    """Close admission atomically and snapshot all real in-flight writers."""

    global _ADMISSION_OPEN
    with _STATE_LOCK:
        _ADMISSION_OPEN = False
        return tuple(_WRITERS)


async def finish_knowledge_writer_stop(*, deadline_monotonic: float) -> bool:
    """Wait until every admitted writer really returns or the deadline passes."""

    while True:
        with _STATE_LOCK:
            pending = {task for task in _WRITERS if not task.done()}
        if not pending:
            return True
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            return False
        done, _pending = await asyncio.wait(pending, timeout=remaining)
        for task in done:
            try:
                task.result()
            except (asyncio.CancelledError, Exception):
                pass
        if not done:
            return False


def knowledge_writer_state() -> tuple[bool, int]:
    """Return bounded process-local state for diagnostics and regression tests."""

    with _STATE_LOCK:
        return _ADMISSION_OPEN, sum(not task.done() for task in _WRITERS)
