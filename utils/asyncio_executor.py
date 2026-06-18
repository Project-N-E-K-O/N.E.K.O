import asyncio
import concurrent.futures
import logging
import os


MIN_DEFAULT_EXECUTOR_WORKERS = 16
MAX_DEFAULT_EXECUTOR_WORKERS = 32
DEFAULT_EXECUTOR_THREAD_PREFIX = "neko-asyncio"


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
