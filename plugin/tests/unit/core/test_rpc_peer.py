from __future__ import annotations

import asyncio
import threading
import queue

import pytest

from plugin.core.rpc import (
    RpcClosedError,
    RpcDispatcher,
    RpcPeer,
    RpcRemoteError,
    RpcTransportError,
)


async def test_replies_are_correlated_even_when_reversed() -> None:
    sent: asyncio.Queue = asyncio.Queue()

    async def send(request, timeout):
        await sent.put(request)

    peer = RpcPeer(send)
    first = asyncio.create_task(peer.request("lookup", {"value": 1}, timeout=1))
    second = asyncio.create_task(peer.request("lookup", {"value": 2}, timeout=1))
    requests = [await sent.get(), await sent.get()]
    for request in reversed(requests):
        peer.receive(request["request_id"], result=request["value"])
    assert await first == 1
    assert await second == 2
    assert peer.pending_count == 0


async def test_registers_before_send_and_discards_duplicate_reply() -> None:
    async def send(request, timeout):
        peer.receive(request["request_id"], result="first")
        peer.receive(request["request_id"], result="duplicate")

    peer = RpcPeer(send)
    assert await peer.request("fast", {}, timeout=1) == "first"
    assert peer.pending_count == 0


async def test_remote_error_is_preserved_and_next_request_succeeds() -> None:
    async def send(request, timeout):
        peer.receive(
            request["request_id"],
            error={"code": "PLUGIN_EXECUTION_FAILED", "message": "bad input"},
        )

    peer = RpcPeer(send)
    with pytest.raises(RpcRemoteError, match="bad input") as error:
        await peer.request("lookup", {}, timeout=1)
    assert error.value.code == "PLUGIN_EXECUTION_FAILED"
    assert peer.pending_count == 0


async def test_timeout_cancellation_and_close_release_waiters() -> None:
    sent: asyncio.Queue = asyncio.Queue()
    cancelled = []

    async def send(request, timeout):
        await sent.put(request)

    async def cancel(request):
        cancelled.append(request["request_id"])

    peer = RpcPeer(send, cancel=cancel)
    with pytest.raises(TimeoutError):
        await peer.request("hang", {}, timeout=0.01)
    assert peer.pending_count == 0
    task = asyncio.create_task(peer.request("hang", {}, timeout=1))
    await sent.get()
    await sent.get()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert peer.pending_count == 0
    waiting = asyncio.create_task(peer.request("hang", {}, timeout=1))
    await sent.get()
    peer.close()
    with pytest.raises(RpcClosedError):
        await waiting
    await peer.aclose()
    assert len(cancelled) == 2


async def test_reply_can_arrive_from_another_thread() -> None:
    async def send(request, timeout):
        thread = threading.Thread(
            target=lambda: peer.receive(request["request_id"], result="ok")
        )
        thread.start()
        await asyncio.to_thread(thread.join)

    peer = RpcPeer(send)
    assert await peer.request("lookup", {}, timeout=1) == "ok"


async def test_nested_dispatch_does_not_block_receiving_requests() -> None:
    replies = []
    completed = asyncio.Event()

    async def leaf(request):
        return "leaf-result"

    async def send(request, timeout):
        async def reply(response):
            peer.receive(
                response["request_id"],
                result=response["result"],
                error=response["error"],
            )

        dispatcher.submit(request, reply)

    peer = RpcPeer(send)

    async def parent(request):
        return await peer.request("leaf", {"from_plugin": "b"}, timeout=1)

    dispatcher = RpcDispatcher({"parent": parent, "leaf": leaf})

    async def reply(response):
        replies.append(response)
        completed.set()

    dispatcher.submit(
        {"type": "parent", "from_plugin": "a", "request_id": "root", "timeout": 1},
        reply,
    )
    await asyncio.wait_for(completed.wait(), 1)
    assert replies[0]["result"] == "leaf-result"
    await dispatcher.close()
    await peer.aclose()


async def test_dispatch_failure_does_not_kill_other_requests() -> None:
    async def fail(request):
        raise ValueError("private input")

    async def good(request):
        return "ok"

    dispatcher = RpcDispatcher({"bad": fail, "good": good})
    bad = await dispatcher.dispatch(
        {"type": "bad", "from_plugin": "a", "request_id": "1", "timeout": 1}
    )
    good_reply = await dispatcher.dispatch(
        {"type": "good", "from_plugin": "a", "request_id": "2", "timeout": 1}
    )
    assert bad["error"]["code"] == "INTERNAL_ERROR"
    assert "private input" not in bad["error"]["message"]
    assert good_reply["result"] == "ok"
    await dispatcher.close()


async def test_overload_fails_promptly_and_does_not_wait_for_parent_slot() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def block(request):
        entered.set()
        await release.wait()

    dispatcher = RpcDispatcher({"block": block}, max_in_flight=1)
    first = asyncio.create_task(
        dispatcher.dispatch(
            {"type": "block", "from_plugin": "a", "request_id": "1", "timeout": 1}
        )
    )
    await entered.wait()
    response = await dispatcher.dispatch(
        {"type": "block", "from_plugin": "a", "request_id": "2", "timeout": 1}
    )
    assert response["error"]["code"] == "BUSY"
    release.set()
    await first
    await dispatcher.close()


async def test_close_interrupts_a_blocked_send_and_joins_its_cleanup():
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def send(request, timeout):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    peer = RpcPeer(send)
    request = asyncio.create_task(peer.request("blocked", {}, timeout=None))
    await started.wait()
    await asyncio.wait_for(peer.aclose(), 1)
    with pytest.raises(RpcClosedError):
        await request
    assert cleaned.is_set()


@pytest.mark.parametrize("depth", [None, "bad", -1, True])
async def test_malformed_depth_produces_a_reply(depth):
    async def good(request):
        return "ok"

    dispatcher = RpcDispatcher({"good": good})
    reply = await dispatcher.dispatch(
        {"type": "good", "from_plugin": "a", "request_id": "bad", "_rpc_depth": depth}
    )
    assert reply["error"]["code"] == "INVALID_REQUEST"


async def test_internal_timeout_has_its_own_error_code():
    async def timeout(request):
        raise TimeoutError("backend timed out")

    reply = await RpcDispatcher({"timeout": timeout}).dispatch(
        {"type": "timeout", "from_plugin": "a", "request_id": "1", "timeout": 10}
    )
    assert reply["error"]["code"] == "OPERATION_TIMEOUT"


async def test_admission_covers_blocked_reply_delivery():
    started, release = asyncio.Event(), asyncio.Event()

    async def good(request):
        return "ok"

    async def send(reply):
        started.set()
        await release.wait()

    dispatcher = RpcDispatcher({"good": good}, max_in_flight=1)
    assert dispatcher.submit(
        {"type": "good", "from_plugin": "a", "request_id": "1"}, send
    )
    await started.wait()
    assert not dispatcher.submit(
        {"type": "good", "from_plugin": "a", "request_id": "2"}, send
    )
    release.set()
    await dispatcher.close()


async def test_unexpected_exception_text_is_not_logged(caplog):
    async def fail(request):
        raise ValueError("PRIVATE_DIALOG_SENTINEL")

    await RpcDispatcher({"fail": fail}).dispatch(
        {"type": "fail", "from_plugin": "a", "request_id": "1"}
    )
    assert "ValueError" in caplog.text
    assert "PRIVATE_DIALOG_SENTINEL" not in caplog.text


async def test_close_joins_a_send_on_a_foreign_event_loop():
    started, cleaned = threading.Event(), threading.Event()

    async def send(request, timeout):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    peer = RpcPeer(send)

    async def request():
        with pytest.raises(RpcClosedError):
            await peer.request("foreign", {}, timeout=None)

    thread = threading.Thread(target=lambda: asyncio.run(request()))
    thread.start()
    assert await asyncio.to_thread(started.wait, 1)
    await asyncio.wait_for(peer.aclose(), 2)
    await asyncio.to_thread(thread.join, 1)
    assert not thread.is_alive()
    assert cleaned.is_set()


async def test_send_backpressure_is_a_typed_transport_error(caplog):
    async def send(request, timeout):
        raise queue.Full("PRIVATE_DIALOG_SENTINEL")

    peer = RpcPeer(send)
    with pytest.raises(RpcTransportError) as error:
        await peer.request("lookup", {}, timeout=1)
    assert error.value.code == "TRANSPORT_ERROR"
    assert peer.pending_count == 0
    assert "PRIVATE_DIALOG_SENTINEL" not in caplog.text


async def test_disconnect_is_scoped_to_the_original_connection():
    entered = {"old": asyncio.Event(), "new": asyncio.Event()}
    ended = {"old": asyncio.Event(), "new": asyncio.Event()}

    async def wait(request):
        connection = request["_rpc_connection_id"]
        entered[connection].set()
        try:
            await asyncio.Event().wait()
        finally:
            ended[connection].set()

    async def reply(response):
        pass

    dispatcher = RpcDispatcher({"wait": wait})
    for connection in ("old", "new"):
        dispatcher.submit(
            {
                "type": "wait",
                "from_plugin": "a",
                "_rpc_connection_id": connection,
                "request_id": connection,
                "timeout": 10,
            },
            reply,
        )
    await entered["old"].wait()
    await entered["new"].wait()
    dispatcher.submit(
        {"type": "RPC_DISCONNECT", "from_plugin": "a", "_rpc_connection_id": "old"},
        reply,
    )
    await asyncio.wait_for(ended["old"].wait(), 1)
    assert not ended["new"].is_set()
    await dispatcher.close()
