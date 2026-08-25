from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import os
from pathlib import Path
import threading
from typing import Any

import pytest

from plugin.server.application.plugins.operation_lock import (
    plugin_operation_lock,
    serialized_plugin_operation,
)


@pytest.fixture(autouse=True)
def _isolate_operation_file_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Never let unit tests contend with a running user's Core process."""

    monkeypatch.setenv(
        "NEKO_PLUGIN_OPERATION_LOCK_PATH",
        str(tmp_path / "operation.lock"),
    )


def _hold_operation_file_lock(lock_path: str, ready: Any, release: Any) -> None:
    os.environ["NEKO_PLUGIN_OPERATION_LOCK_PATH"] = lock_path
    from plugin.server.application.plugins import operation_lock

    handle = operation_lock._acquire_file_lock_sync()
    ready.set()
    try:
        release.wait()
    finally:
        operation_lock._release_file_lock_sync(handle)


def _keep_forked_child_alive(ready: Any, release: Any) -> None:
    ready.set()
    release.wait()


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


class _TrackedHandle:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_fork_child_cleanup_closes_active_and_acquisition_phase_handles() -> None:
    from plugin.server.application.plugins import operation_lock

    active = _TrackedHandle()
    pending = _TrackedHandle()
    operation_lock._ACTIVE_FILE_LOCK_HANDLE = active
    operation_lock._OPEN_FILE_LOCK_HANDLES.update({active, pending})

    operation_lock._prepare_file_lock_handles_for_fork()
    operation_lock._drop_inherited_file_lock_handles()

    assert active.closed is True
    assert pending.closed is True
    assert operation_lock._ACTIVE_FILE_LOCK_HANDLE is None
    assert operation_lock._OPEN_FILE_LOCK_HANDLES == set()


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_plugin_operation_lock_serializes_tasks_and_allows_reentry() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    observed: list[str] = []

    @serialized_plugin_operation
    async def nested() -> None:
        observed.append("nested")

    @serialized_plugin_operation
    async def first() -> None:
        observed.append("first")
        await nested()
        entered.set()
        await release.wait()

    @serialized_plugin_operation
    async def second() -> None:
        observed.append("second")

    first_task = asyncio.create_task(first())
    await entered.wait()
    second_task = asyncio.create_task(second())
    await asyncio.sleep(0)
    assert observed == ["first", "nested"]

    release.set()
    await asyncio.gather(first_task, second_task)
    assert observed == ["first", "nested", "second"]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_cancelled_queued_operation_never_runs_after_lock_release() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    mutation_ran = False

    @serialized_plugin_operation
    async def holder() -> None:
        entered.set()
        await release.wait()

    @serialized_plugin_operation
    async def queued_mutation() -> None:
        nonlocal mutation_ran
        mutation_ran = True

    holder_task = asyncio.create_task(holder())
    await entered.wait()
    queued_task = asyncio.create_task(queued_mutation())
    scheduled = asyncio.Event()
    asyncio.get_running_loop().call_soon(scheduled.set)
    await scheduled.wait()

    queued_task.cancel()
    queued_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued_task
    assert mutation_ran is False

    release.set()
    await holder_task
    async with plugin_operation_lock.hold():
        pass
    assert mutation_ran is False


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_plugin_operation_lock_waits_for_thread_work_after_cancellation() -> None:
    thread_started = threading.Event()
    release_thread = threading.Event()
    observed: list[str] = []

    def _blocking_work() -> None:
        thread_started.set()
        release_thread.wait(timeout=2)

    @serialized_plugin_operation
    async def blocked() -> None:
        await asyncio.to_thread(_blocking_work)

    @serialized_plugin_operation
    async def second() -> None:
        observed.append("second")

    first_task = asyncio.create_task(blocked())
    await asyncio.to_thread(thread_started.wait)
    first_task.cancel()
    await asyncio.sleep(0)
    second_task = asyncio.create_task(second())
    await asyncio.sleep(0)
    assert observed == []

    release_thread.set()
    with pytest.raises(asyncio.CancelledError):
        await first_task
    await second_task
    assert observed == ["second"]


@pytest.mark.skipif(os.name != "nt", reason="Windows msvcrt retry semantics")
def test_windows_file_lock_retries_without_a_fixed_attempt_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from plugin.server.application.plugins import operation_lock
    import msvcrt

    attempts = 0

    def controlled_locking(_fd: int, mode: int, _size: int) -> None:
        nonlocal attempts
        if mode == msvcrt.LK_UNLCK:
            return
        attempts += 1
        if attempts <= 20:
            raise OSError(13, "controlled contention")

    class _ImmediateRetryEvent:
        def is_set(self) -> bool:
            return False

        def wait(self, _timeout: float | None = None) -> bool:
            return False

    monkeypatch.setenv(
        "NEKO_PLUGIN_OPERATION_LOCK_PATH",
        str(tmp_path / "operation.lock"),
    )
    monkeypatch.setattr(msvcrt, "locking", controlled_locking)

    handle = operation_lock._acquire_file_lock_sync(_ImmediateRetryEvent())
    try:
        assert attempts == 21
    finally:
        operation_lock._release_file_lock_sync(handle)


@pytest.mark.skipif(os.name != "posix", reason="fork semantics are POSIX-only")
def test_forked_child_does_not_keep_parent_file_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from plugin.server.application.plugins import operation_lock

    lock_path = tmp_path / "fork-operation.lock"
    monkeypatch.setenv("NEKO_PLUGIN_OPERATION_LOCK_PATH", str(lock_path))
    parent_handle = operation_lock._acquire_file_lock_sync()
    context = multiprocessing.get_context("fork")
    child_ready = context.Event()
    release_child = context.Event()
    child = context.Process(
        target=_keep_forked_child_alive,
        args=(child_ready, release_child),
    )
    child.start()

    contender_finished = threading.Event()
    contender_handles: list[Any] = []
    contender_errors: list[BaseException] = []

    def acquire_after_parent_release() -> None:
        try:
            contender_handles.append(operation_lock._acquire_file_lock_sync())
        except BaseException as exc:  # pragma: no cover - diagnostic path
            contender_errors.append(exc)
        finally:
            contender_finished.set()

    contender: threading.Thread | None = None
    acquired_while_child_alive = False
    try:
        assert child_ready.wait(5)
        operation_lock._release_file_lock_sync(parent_handle)
        parent_handle = None
        contender = threading.Thread(target=acquire_after_parent_release)
        contender.start()
        acquired_while_child_alive = contender_finished.wait(5)
    finally:
        release_child.set()
        child.join(10)
        if contender is not None:
            contender.join(10)
        if parent_handle is not None:
            operation_lock._release_file_lock_sync(parent_handle)
        for handle in contender_handles:
            operation_lock._release_file_lock_sync(handle)
        if child.is_alive():
            child.terminate()
            child.join(10)

    assert acquired_while_child_alive
    assert not contender_errors
    assert child.exitcode == 0


@pytest.mark.asyncio
async def test_waiter_does_not_consume_the_default_executor() -> None:
    loop = asyncio.get_running_loop()
    previous_executor = getattr(loop, "_default_executor", None)
    executor = _CountingExecutor()
    loop.set_default_executor(executor)
    waiter_started = asyncio.Event()
    waiter_entered = asyncio.Event()
    waiter_task: asyncio.Task[None] | None = None

    async def waiter() -> None:
        waiter_started.set()
        async with plugin_operation_lock.hold():
            waiter_entered.set()

    try:
        async with plugin_operation_lock.hold():
            executor.reset_count()
            waiter_task = asyncio.create_task(waiter())
            await waiter_started.wait()
            barrier = asyncio.Event()
            loop.call_soon(barrier.set)
            await barrier.wait()
            assert executor.submission_count == 0
            await asyncio.to_thread(lambda: None)
            assert executor.submission_count == 1
            assert not waiter_entered.is_set()
        await waiter_task
    finally:
        if waiter_task is not None:
            await asyncio.gather(waiter_task, return_exceptions=True)
        loop._default_executor = previous_executor  # type: ignore[attr-defined]
        executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.asyncio
async def test_cross_process_file_lock_wait_can_be_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "operation.lock"
    monkeypatch.setenv("NEKO_PLUGIN_OPERATION_LOCK_PATH", str(lock_path))
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_operation_file_lock,
        args=(str(lock_path), ready, release),
    )
    process.start()
    waiter_task: asyncio.Task[None] | None = None

    async def waiter() -> None:
        async with plugin_operation_lock.hold():
            raise AssertionError("cancelled waiter must not enter")

    try:
        assert await asyncio.to_thread(ready.wait, 10)
        waiter_task = asyncio.create_task(waiter())
        barrier = asyncio.Event()
        asyncio.get_running_loop().call_soon(barrier.set)
        await barrier.wait()
        assert not waiter_task.done()

        waiter_task.cancel()
        waiter_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter_task

        release.set()
        async with plugin_operation_lock.hold():
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
