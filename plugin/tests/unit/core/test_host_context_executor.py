"""The plugin process's default executor runs work in the submitter's context."""

from __future__ import annotations

import asyncio
import contextvars
import socket
import threading
from types import SimpleNamespace

from plugin.core import host as host_module
from plugin.core import rpc as rpc_module
from plugin.core.context import _IN_HANDLER
from plugin.core.rpc import request_scope

_VALUE: contextvars.ContextVar[str | None] = contextvars.ContextVar("test_executor_value", default=None)


def _run(awaitable):
    # Same wrapper every plugin-code asyncio.run in the plugin process uses.
    return asyncio.run(host_module._run_with_model_client_cleanup(SimpleNamespace(), awaitable))


def test_run_in_executor_none_sees_handler_and_rpc_scope():
    async def work():
        def read():
            scope = rpc_module._scope.get()
            return _IN_HANDLER.get(), scope.request_id if scope else None, threading.current_thread()

        token = _IN_HANDLER.set("plugin_entry.demo")
        try:
            with request_scope({"request_id": "req-1"}):
                return await asyncio.get_running_loop().run_in_executor(None, read)
        finally:
            _IN_HANDLER.reset(token)

    handler, request_id, thread = _run(work())
    assert (handler, request_id) == ("plugin_entry.demo", "req-1")
    assert thread is not threading.main_thread()


def test_loop_getaddrinfo_resolves_in_the_callers_context(monkeypatch):
    seen = []

    def fake_getaddrinfo(host, port, *args, **kwargs):
        seen.append((host, _VALUE.get(), threading.current_thread()))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    async def work():
        _VALUE.set("resolver-caller")
        return await asyncio.get_running_loop().getaddrinfo("example.invalid", 80)

    assert _run(work())[0][4] == ("127.0.0.1", 80)
    assert len(seen) == 1
    host, value, thread = seen[0]
    assert (host, value) == ("example.invalid", "resolver-caller")
    assert thread is not threading.main_thread()


def test_context_is_the_snapshot_taken_at_submit_time():
    async def work():
        loop = asyncio.get_running_loop()
        release = threading.Event()

        def read_later():
            release.wait(5)
            return _VALUE.get()

        _VALUE.set("at-submit")
        pending = loop.run_in_executor(None, read_later)
        _VALUE.set("after-submit")
        release.set()
        return await pending, _VALUE.get()

    assert _run(work()) == ("at-submit", "after-submit")


def test_concurrent_callers_and_reused_threads_do_not_share_context():
    async def work():
        loop = asyncio.get_running_loop()
        both_running = threading.Barrier(2, timeout=5)

        def read_then_overwrite():
            both_running.wait()
            seen = _VALUE.get()
            _VALUE.set("written-by-worker")
            return seen

        async def caller(value):
            _VALUE.set(value)
            return await loop.run_in_executor(None, read_then_overwrite)

        first = await asyncio.gather(caller("a"), caller("b"))
        # Same pool threads again: what the earlier workers set must not show.
        second = await asyncio.gather(
            loop.run_in_executor(None, read_then_overwrite),
            loop.run_in_executor(None, read_then_overwrite),
        )
        return first, second, _VALUE.get()

    first, second, caller_value = _run(work())
    assert first == ["a", "b"]
    assert second == [None, None]
    assert caller_value is None


def test_pool_thread_reuse_with_a_single_worker():
    pool = host_module._ContextCopyingThreadPoolExecutor(max_workers=1)
    try:
        def overwrite():
            _VALUE.set("leaked")
            return threading.get_ident()

        def read():
            return _VALUE.get(), threading.get_ident()

        token = _VALUE.set("first")
        try:
            first_thread = pool.submit(overwrite).result(5)
        finally:
            _VALUE.reset(token)
        token = _VALUE.set("second")
        try:
            value, second_thread = pool.submit(read).result(5)
        finally:
            _VALUE.reset(token)
    finally:
        pool.shutdown(wait=True)
    assert first_thread == second_thread
    assert value == "second"
    assert _VALUE.get() is None


def test_to_thread_still_carries_context():
    async def work():
        _VALUE.set("to-thread")
        return await asyncio.to_thread(_VALUE.get)

    assert _run(work()) == "to-thread"


def test_executor_is_shut_down_when_the_loop_closes():
    captured = {}

    async def work():
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: None)
        captured["executor"] = loop._default_executor
        captured["threads"] = set(captured["executor"]._threads)

    for _ in range(2):
        _run(work())
        executor = captured["executor"]
        assert isinstance(executor, host_module._ContextCopyingThreadPoolExecutor)
        assert executor._shutdown
        assert captured["threads"]
        assert not any(thread.is_alive() for thread in captured["threads"])


def test_each_loop_gets_its_own_executor():
    executors = []

    async def work():
        executors.append(asyncio.get_running_loop()._default_executor)

    _run(work())
    _run(work())
    assert all(isinstance(e, host_module._ContextCopyingThreadPoolExecutor) for e in executors)
    assert executors[0] is not executors[1]
