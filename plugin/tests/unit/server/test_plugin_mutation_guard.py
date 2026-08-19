from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import os
from pathlib import Path
import threading
from typing import Any

import pytest

from plugin.server.application.plugins.mutation_guard import plugin_mutation_guard


@pytest.mark.skipif(os.name != "nt", reason="Windows msvcrt retry semantics")
def test_windows_file_lock_retries_beyond_msvcrt_blocking_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Contention must remain queued after the finite LK_LOCK retry window."""

    from plugin.server.application.plugins import mutation_guard
    import msvcrt

    lock_attempts = 0

    def controlled_locking(_fd: int, mode: int, _size: int) -> None:
        nonlocal lock_attempts
        if mode == msvcrt.LK_UNLCK:
            return
        lock_attempts += 1
        if lock_attempts <= 12:
            raise OSError(13, "controlled lock contention")

    class _ImmediateRetryEvent:
        def is_set(self) -> bool:
            return False

        def wait(self, _timeout: float | None = None) -> bool:
            return False

    monkeypatch.setenv(
        "NEKO_PLUGIN_MUTATION_LOCK_PATH",
        str(tmp_path / "plugin-mutation.lock"),
    )
    monkeypatch.setattr(msvcrt, "locking", controlled_locking)

    handle = mutation_guard._acquire_file_lock_sync(  # type: ignore[call-arg]
        _ImmediateRetryEvent()
    )
    try:
        assert lock_attempts == 13
    finally:
        mutation_guard._release_file_lock_sync(handle)


def _hold_mutation_file_lock(
    lock_path: str,
    ready: Any,
    release: Any,
) -> None:
    os.environ["NEKO_PLUGIN_MUTATION_LOCK_PATH"] = lock_path
    from plugin.server.application.plugins import mutation_guard

    handle = mutation_guard._acquire_file_lock_sync()
    ready.set()
    try:
        release.wait()
    finally:
        mutation_guard._release_file_lock_sync(handle)


class _CountingExecutor(ThreadPoolExecutor):
    def __init__(self) -> None:
        super().__init__(max_workers=1)
        self._count_lock = threading.Lock()
        self.submission_count = 0

    def submit(self, fn: Any, /, *args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        with self._count_lock:
            self.submission_count += 1
        return super().submit(fn, *args, **kwargs)

    def reset_count(self) -> None:
        with self._count_lock:
            self.submission_count = 0


@pytest.mark.asyncio
async def test_waiting_for_mutation_guard_does_not_consume_default_executor() -> None:
    loop = asyncio.get_running_loop()
    previous_executor = getattr(loop, "_default_executor", None)
    executor = _CountingExecutor()
    loop.set_default_executor(executor)
    waiter_attempted = asyncio.Event()
    waiter_entered = asyncio.Event()
    waiter_task: asyncio.Task[None] | None = None

    async def waiter() -> None:
        waiter_attempted.set()
        async with plugin_mutation_guard():
            waiter_entered.set()

    try:
        async with plugin_mutation_guard():
            executor.reset_count()
            waiter_task = asyncio.create_task(waiter())
            await waiter_attempted.wait()
            scheduling_barrier = asyncio.Event()
            loop.call_soon(scheduling_barrier.set)
            await scheduling_barrier.wait()
            assert executor.submission_count == 0
            await asyncio.to_thread(lambda: None)
            assert executor.submission_count == 1
            assert not waiter_entered.is_set()
        await waiter_task
        assert waiter_entered.is_set()
    finally:
        if waiter_task is not None:
            await asyncio.gather(waiter_task, return_exceptions=True)
        loop._default_executor = previous_executor  # type: ignore[attr-defined]
        executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.asyncio
async def test_canceled_mutation_waiter_does_not_keep_or_steal_lock() -> None:
    waiter_attempted = asyncio.Event()

    async def waiter() -> None:
        waiter_attempted.set()
        async with plugin_mutation_guard():
            raise AssertionError("canceled waiter must not enter the guard")

    async with plugin_mutation_guard():
        waiter_task = asyncio.create_task(waiter())
        await waiter_attempted.wait()
        waiter_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiter_task

    async with plugin_mutation_guard():
        pass


@pytest.mark.asyncio
async def test_cross_process_file_lock_wait_can_be_cancelled_before_unlock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "plugin-mutation.lock"
    monkeypatch.setenv("NEKO_PLUGIN_MUTATION_LOCK_PATH", str(lock_path))
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_mutation_file_lock,
        args=(str(lock_path), ready, release),
    )
    process.start()
    waiter_started = asyncio.Event()

    async def waiter() -> None:
        waiter_started.set()
        async with plugin_mutation_guard():
            raise AssertionError("canceled cross-process waiter must not enter")

    waiter_task: asyncio.Task[None] | None = None
    try:
        assert await asyncio.to_thread(ready.wait, 10)
        waiter_task = asyncio.create_task(waiter())
        await waiter_started.wait()
        scheduling_barrier = asyncio.Event()
        asyncio.get_running_loop().call_soon(scheduling_barrier.set)
        await scheduling_barrier.wait()
        assert not waiter_task.done()

        waiter_task.cancel()
        waiter_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter_task

        release.set()
        async with plugin_mutation_guard():
            pass
    finally:
        release.set()
        if waiter_task is not None and not waiter_task.done():
            waiter_task.cancel()
            await asyncio.gather(waiter_task, return_exceptions=True)
        await asyncio.to_thread(process.join, 10)
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 10)
        assert process.exitcode == 0
