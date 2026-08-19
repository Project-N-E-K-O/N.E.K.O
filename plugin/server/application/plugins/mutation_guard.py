from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import errno
import threading
from contextvars import ContextVar, Token
import os
from pathlib import Path
from types import TracebackType
from typing import Any


class _MutationWaiter:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.future: asyncio.Future[None] = loop.create_future()
        self.state = "waiting"


class _AsyncMutationLock:
    """Process-wide async mutex whose waiters never occupy an executor thread."""

    def __init__(self) -> None:
        self._state_lock = threading.Lock()
        self._held = False
        self._waiters: deque[_MutationWaiter] = deque()

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        with self._state_lock:
            if not self._held:
                self._held = True
                return
            waiter = _MutationWaiter(loop)
            self._waiters.append(waiter)

        try:
            await waiter.future
        except asyncio.CancelledError:
            wake: _MutationWaiter | None = None
            with self._state_lock:
                if waiter.state == "waiting":
                    waiter.state = "cancelled"
                    try:
                        self._waiters.remove(waiter)
                    except ValueError:
                        pass
                elif waiter.state == "granted":
                    waiter.state = "cancelled"
                    wake = self._handoff_locked()
            self._schedule_wake(wake)
            raise

        with self._state_lock:
            if waiter.state != "granted":  # pragma: no cover - defensive invariant
                raise RuntimeError("plugin mutation lock waiter lost its grant")
            waiter.state = "acquired"

    def release(self) -> None:
        with self._state_lock:
            if not self._held:
                raise RuntimeError("plugin mutation lock is not held")
            wake = self._handoff_locked()
        self._schedule_wake(wake)

    def _handoff_locked(self) -> _MutationWaiter | None:
        while self._waiters:
            waiter = self._waiters.popleft()
            if (
                waiter.state != "waiting"
                or waiter.future.cancelled()
                or waiter.loop.is_closed()
            ):
                waiter.state = "cancelled"
                continue
            waiter.state = "granted"
            return waiter
        self._held = False
        return None

    def _schedule_wake(self, waiter: _MutationWaiter | None) -> None:
        if waiter is None:
            return
        try:
            waiter.loop.call_soon_threadsafe(self._finish_wake, waiter)
        except RuntimeError:
            self._abandon_grant(waiter)

    def _finish_wake(self, waiter: _MutationWaiter) -> None:
        wake: _MutationWaiter | None = None
        with self._state_lock:
            if waiter.state != "granted":
                return
            if waiter.future.cancelled() or waiter.loop.is_closed():
                waiter.state = "cancelled"
                wake = self._handoff_locked()
            else:
                waiter.future.set_result(None)
        self._schedule_wake(wake)

    def _abandon_grant(self, waiter: _MutationWaiter) -> None:
        with self._state_lock:
            if waiter.state != "granted":
                return
            waiter.state = "cancelled"
            wake = self._handoff_locked()
        self._schedule_wake(wake)


_MUTATION_LOCK = _AsyncMutationLock()
_FILE_LOCK_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="plugin-mutation-file-lock",
)
_MUTATION_DEPTH: ContextVar[int] = ContextVar(
    "plugin_mutation_depth",
    default=0,
)
_MUTATION_OWNER: ContextVar[asyncio.Task[Any] | None] = ContextVar(
    "plugin_mutation_owner",
    default=None,
)
_FILE_LOCK_RETRY_INTERVAL_SECONDS = 0.05


class _FileLockAcquireCancelled(Exception):
    pass


def _mutation_file_lock_path() -> Path:
    configured = os.environ.get("NEKO_PLUGIN_MUTATION_LOCK_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    from plugin.server.application.plugins.inventory_store import resolve_inventory_path

    return (resolve_inventory_path().parent / ".plugin-mutation.lock").resolve()


def _is_file_lock_contention(exc: OSError) -> bool:
    return exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(
        exc,
        "winerror",
        None,
    ) in {33, 36}


def _acquire_file_lock_sync(cancel_event: threading.Event | None = None) -> Any:
    path = _mutation_file_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise _FileLockAcquireCancelled
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:  # pragma: no cover - exercised by Linux CI
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if not _is_file_lock_contention(exc):
                    raise
                if cancel_event is not None:
                    if cancel_event.wait(_FILE_LOCK_RETRY_INTERVAL_SECONDS):
                        raise _FileLockAcquireCancelled from exc
                else:
                    threading.Event().wait(_FILE_LOCK_RETRY_INTERVAL_SECONDS)
        return handle
    except BaseException:
        handle.close()
        raise


def _release_file_lock_sync(handle: Any) -> None:
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover - exercised by Linux CI
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


async def _acquire_file_lock_cancellation_safe() -> Any:
    loop = asyncio.get_running_loop()
    cancel_event = threading.Event()
    operation = asyncio.ensure_future(
        loop.run_in_executor(_FILE_LOCK_EXECUTOR, _acquire_file_lock_sync, cancel_event)
    )
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            handle = await asyncio.shield(operation)
            break
        except asyncio.CancelledError as exc:
            if cancellation is None:
                cancellation = exc
            cancel_event.set()
        except _FileLockAcquireCancelled:
            if cancellation is None:  # pragma: no cover - internal invariant
                raise RuntimeError("plugin mutation file lock wait was canceled internally")
            raise cancellation

    if cancellation is not None:
        _release_file_lock_sync(handle)
        raise cancellation
    return handle


class _PluginMutationGuard:
    def __init__(self) -> None:
        self._token: Token[int] | None = None
        self._owner_token: Token[asyncio.Task[Any] | None] | None = None
        self._acquired = False
        self._file_lock_handle: Any | None = None

    async def __aenter__(self) -> None:
        current_task = asyncio.current_task()
        if current_task is None:  # pragma: no cover - async context always has a task
            raise RuntimeError("plugin mutation guard requires an asyncio task")
        depth = _MUTATION_DEPTH.get()
        if depth and _MUTATION_OWNER.get() is current_task:
            self._token = _MUTATION_DEPTH.set(depth + 1)
            return

        await _MUTATION_LOCK.acquire()
        self._acquired = True
        try:
            self._file_lock_handle = await _acquire_file_lock_cancellation_safe()
        except BaseException:
            self._acquired = False
            _MUTATION_LOCK.release()
            raise
        self._owner_token = _MUTATION_OWNER.set(current_task)
        self._token = _MUTATION_DEPTH.set(1)

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        assert self._token is not None
        _MUTATION_DEPTH.reset(self._token)
        if self._acquired:
            assert self._owner_token is not None
            _MUTATION_OWNER.reset(self._owner_token)
            try:
                assert self._file_lock_handle is not None
                _release_file_lock_sync(self._file_lock_handle)
            finally:
                _MUTATION_LOCK.release()
        return False


def plugin_mutation_guard() -> _PluginMutationGuard:
    """Serialize plugin filesystem and metadata mutations across processes."""

    return _PluginMutationGuard()


def plugin_mutation_guard_is_held_by_current_task() -> bool:
    current_task = asyncio.current_task()
    return (
        current_task is not None
        and _MUTATION_DEPTH.get() > 0
        and _MUTATION_OWNER.get() is current_task
    )
