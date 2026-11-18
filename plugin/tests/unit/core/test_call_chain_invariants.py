"""P0: pin how the parent request id travels today (docs/design/plugin-call-chain.md 5.1).

These tests record the current behavior, including the paths where the id does
NOT arrive. P1 treats a missing id as "the plugin acting on its own"
(``unattributed``), so a change in any of these outcomes changes attribution.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading

import pytest

from plugin.core.rpc import RpcPeer, current_request_id, request_scope

pytestmark = pytest.mark.plugin_unit

PARENT = "host-req-1"


class _CapturePeer:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.peer = RpcPeer(self._send)

    async def _send(self, request, _timeout) -> None:
        self.sent.append(dict(request))
        self.peer.receive(request["request_id"], result="ok")


def _scope():
    # The plugin process enters this for every TRIGGER / TRIGGER_CUSTOM; the
    # host-generated req_id becomes the scope's request id.
    return request_scope({"req_id": PARENT})


async def test_scope_adopts_the_host_delivery_id_and_nested_requests_report_it():
    capture = _CapturePeer()
    with _scope():
        assert current_request_id() == PARENT
        await capture.peer.request("PLUGIN_TO_PLUGIN", {}, timeout=1.0)
    assert capture.sent[0]["_rpc_parent_request_id"] == PARENT
    assert current_request_id() is None


async def test_to_thread_carries_the_parent_id():
    with _scope():
        assert await asyncio.to_thread(current_request_id) == PARENT


async def test_sync_asyncio_run_in_a_worker_thread_carries_the_parent_id():
    # The sync SDK calls (``_send_request_and_wait``) run asyncio.run in a
    # worker thread; asyncio.run copies the calling thread's context.
    capture = _CapturePeer()

    def sync_call():
        return asyncio.run(capture.peer.request("PLUGIN_TO_PLUGIN", {}, timeout=1.0))

    with _scope():
        assert await asyncio.to_thread(sync_call) == "ok"
    assert capture.sent[0]["_rpc_parent_request_id"] == PARENT


async def test_run_in_executor_does_not_carry_the_parent_id():
    with _scope():
        loop = asyncio.get_running_loop()
        assert await loop.run_in_executor(None, current_request_id) is None


async def test_bare_thread_does_not_carry_the_parent_id():
    seen: list[object] = []
    with _scope():
        thread = threading.Thread(target=lambda: seen.append(current_request_id()))
        thread.start()
        thread.join()
    assert seen == [None]


async def test_copied_context_in_a_bare_thread_carries_the_parent_id():
    # The workaround documented for plugin authors.
    seen: list[object] = []
    with _scope():
        context = contextvars.copy_context()
        thread = threading.Thread(target=lambda: seen.append(context.run(current_request_id)))
        thread.start()
        thread.join()
    assert seen == [PARENT]


async def test_background_task_loses_the_parent_id_when_the_invocation_ends():
    started = asyncio.Event()
    release = asyncio.Event()
    seen: list[object] = []

    async def background():
        seen.append(current_request_id())
        started.set()
        await release.wait()
        seen.append(current_request_id())

    with _scope():
        task = asyncio.create_task(background())
        await started.wait()
    release.set()
    await task
    assert seen == [PARENT, None]


def _other_loop():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    return loop, thread


async def test_contextvar_crosses_run_coroutine_threadsafe_on_311():
    # The host's _run_on_owner_loop hops loops this way. Python 3.11 carries
    # the caller's context through the scheduled handle; the design does not
    # rely on it (the host passes chain data explicitly across the hop).
    loop, thread = _other_loop()

    async def read():
        return current_request_id()

    try:
        with _scope():
            future = asyncio.run_coroutine_threadsafe(read(), loop)
            assert await asyncio.wrap_future(future) == PARENT
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()
