"""Serialize plugin install and deletion transactions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from functools import wraps
from typing import ParamSpec, TypeVar
from weakref import WeakKeyDictionary

P = ParamSpec("P")
T = TypeVar("T")
_operation_owner: ContextVar[object | None] = ContextVar(
    "plugin_operation_owner",
    default=None,
)


class _ReentrantPluginOperationLock:
    """An asyncio lock that can be reacquired by the current task."""

    def __init__(self) -> None:
        self._locks: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
            WeakKeyDictionary()
        )
        self._owners: WeakKeyDictionary[
            asyncio.AbstractEventLoop, tuple[object, int]
        ] = WeakKeyDictionary()

    async def acquire(self) -> Token[object | None]:
        loop = asyncio.get_running_loop()
        owner_id = _operation_owner.get()
        owner = self._owners.get(loop)
        if owner_id is not None and owner is not None and owner[0] is owner_id:
            self._owners[loop] = (owner_id, owner[1] + 1)
            return _operation_owner.set(owner_id)

        lock = self._locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[loop] = lock
        await lock.acquire()
        owner_id = object()
        self._owners[loop] = (owner_id, 1)
        return _operation_owner.set(owner_id)

    def release(self, context_token: Token[object | None]) -> None:
        loop = asyncio.get_running_loop()
        owner = self._owners.get(loop)
        if owner is None or _operation_owner.get() is not owner[0]:
            raise RuntimeError("plugin operation lock released by a non-owner")
        if owner[1] > 1:
            self._owners[loop] = (owner[0], owner[1] - 1)
        else:
            del self._owners[loop]
            self._locks[loop].release()
        _operation_owner.reset(context_token)

    def hold(self) -> _HeldPluginOperationLock:
        return _HeldPluginOperationLock(self)


class _HeldPluginOperationLock:
    def __init__(self, lock: _ReentrantPluginOperationLock) -> None:
        self._lock = lock
        self._context_token: Token[object | None] | None = None

    async def __aenter__(self) -> None:
        self._context_token = await self._lock.acquire()

    async def __aexit__(self, *_exc_info: object) -> None:
        if self._context_token is None:  # pragma: no cover - invalid context use
            raise RuntimeError("plugin operation lock was not acquired")
        self._lock.release(self._context_token)


_plugin_operation_lock = _ReentrantPluginOperationLock()
plugin_operation_lock = _plugin_operation_lock


def serialized_plugin_operation(
    function: Callable[P, Awaitable[T]],
) -> Callable[P, Awaitable[T]]:
    """Prevent an install and a deletion from mutating the same package concurrently."""

    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        async with _plugin_operation_lock.hold():
            operation = asyncio.create_task(function(*args, **kwargs))
            cancelled = False
            while True:
                try:
                    result = await asyncio.shield(operation)
                except asyncio.CancelledError:
                    cancelled = True
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
