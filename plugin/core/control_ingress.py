"""Confine the host control inbox and cancellation admission to one event loop."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future

from plugin.core.rpc import RpcClosedError, request_deadline


class ControlIngress:
    def __init__(self, queue: asyncio.Queue, control: Callable[[dict], None]):
        self.queue = queue
        self.loop = asyncio.get_running_loop()
        self._control = control
        self._closed = False
        self._handoff_lock = threading.Lock()
        self._handoffs: set[Future] = set()
        self._queued: set[tuple[str, str]] = set()
        self._cancelled: set[tuple[str, str]] = set()

    @staticmethod
    def _key(request: Mapping) -> tuple[str, str]:
        return str(
            request.get("_rpc_connection_id") or request.get("from_plugin", "")
        ), str(request.get("request_id", ""))

    def _admit(
        self, request: dict, is_current: Callable[[], bool] | None = None
    ) -> None:
        if self._closed or (is_current is not None and not is_current()):
            raise RpcClosedError("Plugin control ingress is closed")
        key = self._key(request)
        operation = request.get("type")
        if operation in {"RPC_CANCEL", "RPC_DISCONNECT"}:
            self._cancelled.update(
                k
                for k in self._queued
                if k == key or (operation == "RPC_DISCONNECT" and k[0] == key[0])
            )
            self._control(request)
            return
        self.queue.put_nowait(request)
        self._queued.add(key)

    async def _enqueue_local(
        self,
        request: dict,
        abandoned: threading.Event,
        is_current: Callable[[], bool] | None,
    ) -> None:
        if abandoned.is_set():
            raise asyncio.CancelledError
        self._admit(request, is_current)

    async def enqueue(
        self, request: dict, *, is_current: Callable[[], bool] | None = None
    ) -> None:
        if self._closed or self.loop.is_closed() or not self.loop.is_running():
            raise RpcClosedError("Plugin control owner is unavailable")
        if asyncio.get_running_loop() is self.loop:
            self._admit(request, is_current)
            return
        abandoned = threading.Event()
        coro = self._enqueue_local(dict(request), abandoned, is_current)
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        except BaseException:
            coro.close()
            raise
        with self._handoff_lock:
            if self._closed:
                future.cancel()
            else:
                self._handoffs.add(future)
        try:
            deadline = request_deadline(request, None)
            timeout = None if deadline is None else max(0, deadline - time.monotonic())
            await asyncio.wait_for(asyncio.wrap_future(future), timeout=timeout)
        except asyncio.CancelledError:
            abandoned.set()
            future.cancel()
            if self._closed:
                raise RpcClosedError(
                    "Plugin control ingress closed during handoff"
                ) from None
            raise
        except TimeoutError:
            abandoned.set()
            future.cancel()
            raise
        finally:
            with self._handoff_lock:
                self._handoffs.discard(future)

    def notify_disconnect(self, request: dict) -> bool:
        if self._closed:
            return False

        def admit():
            if not self._closed:
                self._admit(request)

        try:
            self.loop.call_soon_threadsafe(admit)
            return True
        except RuntimeError:
            return False

    def take(self, request: Mapping) -> bool:
        key = self._key(request)
        self._queued.discard(key)
        cancelled = key in self._cancelled
        self._cancelled.discard(key)
        return not self._closed and not cancelled

    def close(self) -> None:
        with self._handoff_lock:
            self._closed = True
            futures = tuple(self._handoffs)
        for future in futures:
            future.cancel()
        self._queued.clear()
        self._cancelled.clear()
