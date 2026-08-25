"""Serialize plugin install and deletion transactions across processes."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, Token
import errno
from functools import wraps
import os
from pathlib import Path
import threading
from types import TracebackType
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


class _Waiter:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.future: asyncio.Future[None] = loop.create_future()
        self.state = "waiting"


class _CrossLoopLock:
    """A FIFO async lock whose waiters never occupy an executor thread."""

    def __init__(self) -> None:
        self._state_lock = threading.Lock()
        self._held = False
        self._waiters: deque[_Waiter] = deque()

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        with self._state_lock:
            if not self._held:
                self._held = True
                return
            waiter = _Waiter(loop)
            self._waiters.append(waiter)

        try:
            await waiter.future
        except asyncio.CancelledError:
            wake: _Waiter | None = None
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
            if waiter.state != "granted":  # pragma: no cover - invariant
                raise RuntimeError("plugin operation lock waiter lost its grant")
            waiter.state = "acquired"

    def release(self) -> None:
        with self._state_lock:
            if not self._held:
                raise RuntimeError("plugin operation lock is not held")
            wake = self._handoff_locked()
        self._schedule_wake(wake)

    def _handoff_locked(self) -> _Waiter | None:
        while self._waiters:
            waiter = self._waiters.popleft()
            if waiter.state != "waiting" or waiter.future.cancelled() or waiter.loop.is_closed():
                waiter.state = "cancelled"
                continue
            waiter.state = "granted"
            return waiter
        self._held = False
        return None

    def _schedule_wake(self, waiter: _Waiter | None) -> None:
        if waiter is None:
            return
        try:
            waiter.loop.call_soon_threadsafe(self._finish_wake, waiter)
        except RuntimeError:
            self._abandon_grant(waiter)

    def _finish_wake(self, waiter: _Waiter) -> None:
        wake: _Waiter | None = None
        with self._state_lock:
            if waiter.state != "granted":
                return
            if waiter.future.cancelled() or waiter.loop.is_closed():
                waiter.state = "cancelled"
                wake = self._handoff_locked()
            else:
                waiter.future.set_result(None)
        self._schedule_wake(wake)

    def _abandon_grant(self, waiter: _Waiter) -> None:
        with self._state_lock:
            if waiter.state != "granted":
                return
            waiter.state = "cancelled"
            wake = self._handoff_locked()
        self._schedule_wake(wake)


class _FileLockAcquireCancelled(Exception):
    pass


_PROCESS_LOCK = _CrossLoopLock()
_FILE_LOCK_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="plugin-operation-file-lock",
)
_OPERATION_DEPTH: ContextVar[int] = ContextVar("plugin_operation_depth", default=0)
_OPERATION_OWNER: ContextVar[asyncio.Task[Any] | None] = ContextVar(
    "plugin_operation_owner",
    default=None,
)
_FILE_LOCK_RETRY_INTERVAL_SECONDS = 0.05
_ACTIVE_FILE_LOCK_HANDLE: Any | None = None
_OPEN_FILE_LOCK_HANDLES: set[Any] = set()
_FILE_LOCK_HANDLE_GUARD = threading.Lock()


def _prepare_file_lock_handles_for_fork() -> None:
    _FILE_LOCK_HANDLE_GUARD.acquire()


def _release_file_lock_handles_after_fork() -> None:
    _FILE_LOCK_HANDLE_GUARD.release()


def _drop_inherited_file_lock_handles() -> None:
    """Close a forked child's active and acquisition-phase lock handles."""

    global _ACTIVE_FILE_LOCK_HANDLE

    handles = tuple(_OPEN_FILE_LOCK_HANDLES)
    _OPEN_FILE_LOCK_HANDLES.clear()
    _ACTIVE_FILE_LOCK_HANDLE = None
    try:
        for handle in handles:
            try:
                handle.close()
            except OSError:
                pass
    finally:
        _FILE_LOCK_HANDLE_GUARD.release()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        before=_prepare_file_lock_handles_for_fork,
        after_in_parent=_release_file_lock_handles_after_fork,
        after_in_child=_drop_inherited_file_lock_handles,
    )


def _operation_file_lock_path() -> Path:
    configured = os.environ.get("NEKO_PLUGIN_OPERATION_LOCK_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    from plugin.settings import get_user_plugin_config_root

    return (get_user_plugin_config_root().parent / ".plugin-operation.lock").resolve()


def _is_file_lock_contention(exc: OSError) -> bool:
    return exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(
        exc,
        "winerror",
        None,
    ) in {33, 36}


def _lock_file_once(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:  # pragma: no cover - exercised by Linux CI
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _acquire_file_lock_sync(
    cancel_event: threading.Event | None = None,
    contention_event: Any | None = None,
) -> Any:
    global _ACTIVE_FILE_LOCK_HANDLE

    path = _operation_file_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _FILE_LOCK_HANDLE_GUARD:
        handle = path.open("a+b")
        _OPEN_FILE_LOCK_HANDLES.add(handle)
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise _FileLockAcquireCancelled
            try:
                _lock_file_once(handle)
                with _FILE_LOCK_HANDLE_GUARD:
                    _ACTIVE_FILE_LOCK_HANDLE = handle
                return handle
            except OSError as exc:
                if not _is_file_lock_contention(exc):
                    raise
                if contention_event is not None:
                    contention_event.set()
                if cancel_event is not None:
                    if cancel_event.wait(_FILE_LOCK_RETRY_INTERVAL_SECONDS):
                        raise _FileLockAcquireCancelled from exc
                else:
                    threading.Event().wait(_FILE_LOCK_RETRY_INTERVAL_SECONDS)
    except BaseException:
        with _FILE_LOCK_HANDLE_GUARD:
            handle.close()
            _OPEN_FILE_LOCK_HANDLES.discard(handle)
            if _ACTIVE_FILE_LOCK_HANDLE is handle:
                _ACTIVE_FILE_LOCK_HANDLE = None
        raise


def _release_file_lock_sync(handle: Any) -> None:
    global _ACTIVE_FILE_LOCK_HANDLE

    with _FILE_LOCK_HANDLE_GUARD:
        if _ACTIVE_FILE_LOCK_HANDLE is handle:
            _ACTIVE_FILE_LOCK_HANDLE = None
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
            _OPEN_FILE_LOCK_HANDLES.discard(handle)


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
            if cancellation is None:  # pragma: no cover - invariant
                raise RuntimeError("plugin operation file lock wait was cancelled internally")
            raise cancellation

    if cancellation is not None:
        _release_file_lock_sync(handle)
        raise cancellation
    return handle


class _HeldPluginOperationLock:
    def __init__(self) -> None:
        self._depth_token: Token[int] | None = None
        self._owner_token: Token[asyncio.Task[Any] | None] | None = None
        self._acquired = False
        self._file_lock_handle: Any | None = None

    async def __aenter__(self) -> None:
        current_task = asyncio.current_task()
        if current_task is None:  # pragma: no cover - async context invariant
            raise RuntimeError("plugin operation lock requires an asyncio task")
        depth = _OPERATION_DEPTH.get()
        if depth and _OPERATION_OWNER.get() is current_task:
            self._depth_token = _OPERATION_DEPTH.set(depth + 1)
            return

        await _PROCESS_LOCK.acquire()
        self._acquired = True
        try:
            self._file_lock_handle = await _acquire_file_lock_cancellation_safe()
        except BaseException:
            self._acquired = False
            _PROCESS_LOCK.release()
            raise
        self._owner_token = _OPERATION_OWNER.set(current_task)
        self._depth_token = _OPERATION_DEPTH.set(1)

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if self._depth_token is None:
            raise RuntimeError("plugin operation lock was not acquired")
        _OPERATION_DEPTH.reset(self._depth_token)
        if not self._acquired:
            return
        if self._owner_token is not None:
            _OPERATION_OWNER.reset(self._owner_token)
        if self._file_lock_handle is not None:
            _release_file_lock_sync(self._file_lock_handle)
        self._acquired = False
        _PROCESS_LOCK.release()


class _PluginOperationLock:
    def hold(self) -> _HeldPluginOperationLock:
        return _HeldPluginOperationLock()


plugin_operation_lock = _PluginOperationLock()


def _operation_lock_is_held_by_current_task() -> bool:
    current_task = asyncio.current_task()
    return (
        current_task is not None
        and _OPERATION_DEPTH.get() > 0
        and _OPERATION_OWNER.get() is current_task
    )


def serialized_plugin_operation(
    function: Callable[P, Awaitable[T]],
) -> Callable[P, Awaitable[T]]:
    """Run a cancellation-safe mutation under the process and OS locks."""

    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        if _operation_lock_is_held_by_current_task():
            return await function(*args, **kwargs)

        lock_acquired = asyncio.Event()

        async def run_locked() -> T:
            async with plugin_operation_lock.hold():
                lock_acquired.set()
                return await function(*args, **kwargs)

        operation = asyncio.create_task(run_locked())
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(operation)
            except asyncio.CancelledError:
                cancelled = True
                if not lock_acquired.is_set():
                    operation.cancel()
                if operation.done():
                    break
            except BaseException:
                if cancelled:
                    raise asyncio.CancelledError from None
                raise
            else:
                if cancelled:
                    raise asyncio.CancelledError
                return result
        raise asyncio.CancelledError

    return wrapped
