"""Serialize plugin install and deletion transactions across processes."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, Token
import time
import errno
from functools import wraps
import os
from pathlib import Path
import threading
from types import TracebackType
from typing import Any, ParamSpec, TypeVar

from plugin.utils.asyncio_utils import await_cancellation_safe

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

    async def acquire(self, deadline: float | None = None) -> None:
        loop = asyncio.get_running_loop()
        with self._state_lock:
            if not self._held:
                self._held = True
                return
            waiter = _Waiter(loop)
            self._waiters.append(waiter)

        def _abandon_waiter() -> None:
            wake: _Waiter | None = None
            with self._state_lock:
                if waiter.state == "waiting":
                    waiter.state = "cancelled"
                    try:
                        self._waiters.remove(waiter)
                    except ValueError:
                        pass
                elif waiter.state == "granted":
                    # Our turn arrived but we no longer want it; pass ownership to the next waiter,
                    # or the lock remains stranded with nobody to release it.
                    waiter.state = "cancelled"
                    wake = self._handoff_locked()
            self._schedule_wake(wake)

        # Same-process contention occurs here before the file lock; two HTTP requests to one server
        # block at this step. A file-lock-only deadline covers only the rarer half.
        #
        # Calculate the deadline once at the caller and share it across both locks. Separate budgets are spent twice
        # under both local and cross-process contention: wait a full budget here, then restart it at the file lock.
        # A nominal 20-second request may mutate state after the frontend has given up at 30 seconds (codex).
        if deadline is None:
            deadline = _wait_deadline()
        try:
            if deadline is None:
                await waiter.future
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                await asyncio.wait_for(asyncio.shield(waiter.future), remaining)
        except asyncio.TimeoutError:
            _abandon_waiter()
            raise PluginOperationBusy(
                "another plugin operation is holding the lock"
            ) from None
        except asyncio.CancelledError:
            _abandon_waiter()
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


class PluginOperationBusy(Exception):
    """The cross-process plugin lock was held past the caller's deadline."""


# How long the caller will wait for the lock. None means unbounded, preserving existing behavior; unattended background
# callers such as autostart reconciliation and install transactions should not fail merely for waiting too long.
#
# HTTP routes set deadlines because a person is waiting and the frontend gives up at 30 seconds. Worse, the operation
# still completes afterward under asyncio.shield, making users see failure while the plugin actually starts/stops.
# Prefer telling them immediately that another plugin operation is in progress.
_OPERATION_WAIT_BUDGET: ContextVar[float | None] = ContextVar(
    "plugin_operation_wait_budget", default=None
)


def _wait_deadline() -> float | None:
    """Deadline for a lock wait starting now, or None when unbounded.

    Store budget seconds rather than an absolute deadline, starting afresh for each acquisition. An absolute deadline
    was incorrect: reload-all refreshes the registry before acquiring its first lock, potentially consuming
    that refresh's entire budget and expiring the lock deadline before waiting, producing 409
    without any contention (codex). The budget expresses how long the user will wait for the lock,
    not the total duration allowed for the request.
    """
    budget = _OPERATION_WAIT_BUDGET.get()
    return None if budget is None else time.monotonic() + budget


class bounded_operation_wait:
    """Give lock acquisition inside this block a deadline.

    A class, not ``@contextmanager``, and that is load-bearing:
    ``_GeneratorContextManager.__exit__`` assigns ``exc.__traceback__`` before
    throwing back into the generator, and ``ServerDomainError`` refuses
    attribute assignment — so wrapping an endpoint that raises one in a
    generator-based manager turns a clean 409 into
    ``TypeError: super(type, obj)``. A plain ``__exit__`` never touches the
    exception, so anything raised inside propagates untouched.
    """

    __slots__ = ("_seconds", "_token")

    def __init__(self, seconds: float) -> None:
        self._seconds = max(0.0, float(seconds))
        self._token: Token[float | None] | None = None

    def __enter__(self) -> None:
        self._token = _OPERATION_WAIT_BUDGET.set(self._seconds)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if self._token is not None:
            _OPERATION_WAIT_BUDGET.reset(self._token)
            self._token = None
        return False


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

    from plugin.server.application.install_source.manager import _shared_state_lock_path

    return _shared_state_lock_path().with_name(".plugin-operation.lock")


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
    deadline: float | None = None,
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
        # Accept deadlines explicitly, falling back to the context variable only when omitted.
        #
        # Context variables alone are insufficient: this function runs through loop.run_in_executor,
        # which does not propagate contextvars, unlike asyncio.to_thread.
        # In practice the same ContextVar is visible in to_thread but None in run_in_executor,
        # so setting only context cannot deliver this deadline here.
        if deadline is None:
            deadline = _wait_deadline()
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
                # Check expiration after contention: the budget means willingness to wait, and zero seconds is valid.
                # Checking before trying would return 409 to a caller with exhausted budget even when nobody
                # holds the lock. _CrossLoopLock likewise takes the uncontended fast path
                # first, keeping both layers symmetric.
                if deadline is not None and time.monotonic() >= deadline:
                    raise PluginOperationBusy(
                        "another plugin operation is holding the lock"
                    ) from exc
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


async def _acquire_file_lock_cancellation_safe(deadline: float | None = None) -> Any:
    loop = asyncio.get_running_loop()
    cancel_event = threading.Event()
    # Read here while still in the caller's context; the executor thread cannot see it.
    if deadline is None:
        deadline = _wait_deadline()
    operation = asyncio.ensure_future(
        loop.run_in_executor(
            _FILE_LOCK_EXECUTOR, _acquire_file_lock_sync, cancel_event, deadline
        )
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
                raise RuntimeError(
                    "plugin operation file lock wait was cancelled internally"
                )
            raise cancellation

    if cancellation is not None:
        _release_file_lock_sync(handle)
        raise cancellation
    return handle


def _reload_install_source_manager_sync() -> None:
    """Refresh the shared lock snapshot after cross-process serialization."""

    from plugin.server.application.install_source import get_install_source_manager

    manager = get_install_source_manager()
    if manager is not None:
        manager.load()


class _HeldPluginOperationLock:
    def __init__(self) -> None:
        self._depth_token: Token[int] | None = None
        self._owner_token: Token[asyncio.Task[Any] | None] | None = None
        self._acquired = False
        self._file_lock_handle: Any | None = None
        self._owner_loop: asyncio.AbstractEventLoop | None = None

    async def __aenter__(self) -> _HeldPluginOperationLock:
        current_task = asyncio.current_task()
        if current_task is None:  # pragma: no cover - async context invariant
            raise RuntimeError("plugin operation lock requires an asyncio task")
        self._owner_loop = current_task.get_loop()
        depth = _OPERATION_DEPTH.get()
        if depth and _OPERATION_OWNER.get() is current_task:
            self._depth_token = _OPERATION_DEPTH.set(depth + 1)
            return self

        # Calculate one deadline per logical acquisition, shared by both locks (see _CrossLoopLock.acquire).
        deadline = _wait_deadline()
        await _PROCESS_LOCK.acquire(deadline)
        self._acquired = True
        try:
            self._file_lock_handle = await _acquire_file_lock_cancellation_safe(
                deadline
            )
            await asyncio.to_thread(_reload_install_source_manager_sync)
        except BaseException:
            if self._file_lock_handle is not None:
                _release_file_lock_sync(self._file_lock_handle)
                self._file_lock_handle = None
            self._acquired = False
            _PROCESS_LOCK.release()
            raise
        self._owner_token = _OPERATION_OWNER.set(current_task)
        self._depth_token = _OPERATION_DEPTH.set(1)
        return self

    def require_active(self) -> None:
        """Validate an explicitly borrowed scope before starting a batch member."""
        if (
            self._depth_token is None
            or asyncio.get_running_loop() is not self._owner_loop
        ):
            raise RuntimeError(
                "Plugin startup requires an active operation scope on this loop"
            )

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if self._depth_token is None:
            raise RuntimeError("plugin operation lock was not acquired")
        _OPERATION_DEPTH.reset(self._depth_token)
        self._depth_token = None
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
        return await await_cancellation_safe(
            operation, cancel_if=lambda: not lock_acquired.is_set()
        )

    return wrapped
