"""P0: pin how the parent request id travels today (docs/design/plugin-call-chain.md 5.1).

Recorded as they are, including the paths where the id does NOT arrive: P1
treats a missing id as the plugin acting on its own (``unattributed``), so a
change in any outcome here changes attribution.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading

import pytest

from plugin.core.rpc import RpcPeer, current_request_id, request_scope

pytestmark = pytest.mark.plugin_unit

PARENT = "host-req-1"


def _in_thread(fn):
    out = []
    thread = threading.Thread(target=lambda: out.append(fn()))
    thread.start()
    thread.join()
    return out[0]


async def _sync_asyncio_run_in_worker():
    # Sync SDK calls run asyncio.run in a worker thread, which copies context.
    sent = []

    async def send(request, _timeout):
        sent.append(request)
        peer.receive(request["request_id"], result="ok")

    peer = RpcPeer(send)
    await asyncio.to_thread(lambda: asyncio.run(peer.request("PLUGIN_TO_PLUGIN", {}, timeout=1.0)))
    return sent[0]["_rpc_parent_request_id"]


async def _other_loop_via_run_coroutine_threadsafe():
    # The host's _run_on_owner_loop hop. 3.11 carries context through the
    # scheduled handle; the design still passes chain data explicitly.
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    async def read():
        return current_request_id()

    try:
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(read(), loop))
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(2)
        loop.close()


PATHS = {
    "same task": (lambda: _value(current_request_id()), PARENT),
    "asyncio.to_thread": (lambda: asyncio.to_thread(current_request_id), PARENT),
    "sync asyncio.run in a worker": (_sync_asyncio_run_in_worker, PARENT),
    "run_coroutine_threadsafe": (_other_loop_via_run_coroutine_threadsafe, PARENT),
    "run_in_executor": (lambda: asyncio.get_running_loop().run_in_executor(None, current_request_id), None),
    "bare thread": (lambda: asyncio.to_thread(_in_thread, current_request_id), None),
    "bare thread with copy_context": (
        lambda: asyncio.to_thread(_in_thread, lambda c=contextvars.copy_context(): c.run(current_request_id)),
        PARENT,
    ),
}


async def _value(value):
    return value


@pytest.mark.parametrize("path", PATHS)
async def test_parent_id_propagation(path):
    start, expected = PATHS[path]
    # The plugin process enters this scope for every TRIGGER / TRIGGER_CUSTOM;
    # the host-generated req_id becomes the scope's request id.
    with request_scope({"req_id": PARENT}):
        assert await start() == expected


async def test_background_task_loses_the_parent_id_when_the_invocation_ends():
    seen, release = [], asyncio.Event()

    async def background():
        seen.append(current_request_id())
        await release.wait()
        seen.append(current_request_id())

    with request_scope({"req_id": PARENT}):
        task = asyncio.create_task(background())
        await asyncio.sleep(0)
    release.set()
    await task
    assert seen == [PARENT, None]
