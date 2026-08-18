"""Serialize plugin install and deletion transactions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar
from weakref import WeakKeyDictionary

P = ParamSpec("P")
T = TypeVar("T")


class _ReentrantPluginOperationLock:
    """An asyncio lock that can be reacquired by the current task."""

    def __init__(self) -> None:
        self._locks: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
            WeakKeyDictionary()
        )
        self._owners: WeakKeyDictionary[
            asyncio.AbstractEventLoop, tuple[asyncio.Task[object], int]
        ] = WeakKeyDictionary()

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        if task is None:  # pragma: no cover - async callers always have a task
            raise RuntimeError("plugin operation lock requires an asyncio task")
        owner = self._owners.get(loop)
        if owner is not None and owner[0] is task:
            self._owners[loop] = (task, owner[1] + 1)
            return

        lock = self._locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[loop] = lock
        await lock.acquire()
        self._owners[loop] = (task, 1)

    def release(self) -> None:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        owner = self._owners.get(loop)
        if task is None or owner is None or owner[0] is not task:
            raise RuntimeError("plugin operation lock released by a non-owner")
        if owner[1] > 1:
            self._owners[loop] = (task, owner[1] - 1)
            return
        del self._owners[loop]
        self._locks[loop].release()

    def hold(self) -> _HeldPluginOperationLock:
        return _HeldPluginOperationLock(self)


class _HeldPluginOperationLock:
    def __init__(self, lock: _ReentrantPluginOperationLock) -> None:
        self._lock = lock

    async def __aenter__(self) -> None:
        await self._lock.acquire()

    async def __aexit__(self, *_exc_info: object) -> None:
        self._lock.release()


_plugin_operation_lock = _ReentrantPluginOperationLock()


def serialized_plugin_operation(
    function: Callable[P, Awaitable[T]],
) -> Callable[P, Awaitable[T]]:
    """Prevent an install and a deletion from mutating the same package concurrently."""

    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        async with _plugin_operation_lock.hold():
            return await function(*args, **kwargs)

    return wrapped
