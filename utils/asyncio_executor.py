import asyncio
import concurrent.futures
import contextvars
import functools
import logging
import os


MIN_DEFAULT_EXECUTOR_WORKERS = 16
MAX_DEFAULT_EXECUTOR_WORKERS = 32
DEFAULT_EXECUTOR_THREAD_PREFIX = "neko-asyncio"


async def to_thread_through_cancellation(func, /, *args, **kwargs):
    """``asyncio.to_thread`` that, when cancelled, returns only after the worker finished.

    A worker thread cannot be interrupted: plain ``to_thread`` re-raises the
    cancellation at once while the thread keeps running, so a caller holding
    a lock around the call releases it before the work it guards is done.
    Here the executor future is owned directly (a bare future, out of reach
    of task-cancellation sweeps) and awaited to completion; the cancellation
    is re-raised afterwards. The task's cancellation count is left alone, so
    an enclosing ``asyncio.timeout`` / ``TaskGroup`` still sees it.

    Known limit: if that wait is itself interrupted by a non-cancellation
    ``BaseException`` thrown into the task (e.g. ``GeneratorExit`` at
    shutdown), the original cancellation is still re-raised but the worker
    is NOT guaranteed to have finished.
    """
    loop = asyncio.get_running_loop()
    context = contextvars.copy_context()
    worker = loop.run_in_executor(None, functools.partial(context.run, func, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError as cancellation:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException:  # noqa: BLE001 - the cancellation below is what propagates
                break
        if worker.done() and not worker.cancelled():
            worker.exception()  # retrieved: the result is no longer wanted
        raise cancellation


def resolve_default_executor_max_workers(cpu_count: int | None = None) -> int:
    """Return the process-wide asyncio default executor size.

    Args:
        cpu_count: Optional override used by tests. When omitted, this function
            uses ``os.cpu_count()``.

    Python's default is ``min(32, (os.cpu_count() or 1) + 4)``. Keep that
    behavior for mid/high-core machines, but enforce a floor for low-end devices
    because this app offloads many blocking IO and queue-wait operations through
    ``asyncio.to_thread`` / ``run_in_executor(None, ...)``.
    """
    if cpu_count is None:
        cpu_count = os.cpu_count() or 1
    return min(
        MAX_DEFAULT_EXECUTOR_WORKERS,
        max(MIN_DEFAULT_EXECUTOR_WORKERS, cpu_count + 4),
    )


def configure_default_executor(
    loop: asyncio.AbstractEventLoop,
    logger: logging.Logger | None = None,
) -> int:
    """Install a shared default executor for asyncio offload calls."""
    max_workers = resolve_default_executor_max_workers()
    loop.set_default_executor(
        concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=DEFAULT_EXECUTOR_THREAD_PREFIX,
        )
    )
    if logger is not None:
        logger.info("[asyncio] default executor max_workers=%s", max_workers)
    return max_workers
