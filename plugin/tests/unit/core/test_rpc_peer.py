from __future__ import annotations

import asyncio
import threading
import time
import queue

import pytest

from plugin.core.rpc import (
    DEFAULT_HANDLER_TIMEOUT_SECONDS,
    MAX_HANDLER_TIMEOUT_SECONDS,
    RpcClosedError,
    RpcDispatcher,
    RpcDeadlineExceededError,
    RpcPeer,
    RpcRemoteError,
    RpcTransportError,
    owns_deadline,
    request_scope,
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


@pytest.mark.parametrize("outcome", ["return", "error", "cancel"])
@pytest.mark.parametrize("execution", ["task", "thread"])
async def test_background_request_uses_its_own_budget_after_entry_scope_ends(outcome, execution):
    sent = []
    release = asyncio.Event()

    async def send(request, timeout):
        sent.append((request, timeout))
        peer.receive(request["request_id"], result="background completed")

    peer = RpcPeer(send)

    async def background():
        await release.wait()
        if execution == "thread":
            return await asyncio.to_thread(lambda: asyncio.run(peer.request("lookup", {}, timeout=1)))
        return await peer.request("lookup", {}, timeout=1)

    try:
        with request_scope({
            "request_id": "finished-entry",
            "_request_deadline_monotonic": time.monotonic() + 0.01,
            "_rpc_depth": 4,
        }):
            task = asyncio.create_task(background())
            if outcome == "error":
                raise ValueError("Entry failed")
            if outcome == "cancel":
                raise asyncio.CancelledError
    except (ValueError, asyncio.CancelledError):
        if outcome == "return":
            raise
    await asyncio.sleep(0.02)
    release.set()
    try:
        assert await task == "background completed"
        (request, budget), = sent
        assert request["_rpc_parent_request_id"] is None
        assert request["_rpc_trace_id"] == request["request_id"]
        assert request["_rpc_depth"] == 0
        assert 0.5 < budget <= 1
    finally:
        await peer.aclose()


@pytest.mark.parametrize("outcome", ["return", "error", "cancel"])
async def test_dispatcher_background_request_outlives_the_handler_scope(outcome):
    sent = []
    entered = asyncio.Event()
    release = asyncio.Event()
    background = None

    async def send(request, timeout):
        sent.append(request)
        peer.receive(request["request_id"], result="background completed")

    peer = RpcPeer(send)

    async def work():
        await release.wait()
        return await peer.request("lookup", {}, timeout=1)

    async def handler(ctx, request):
        nonlocal background
        background = asyncio.create_task(work())
        entered.set()
        if outcome == "error":
            raise RpcRemoteError({"code": "PROBE_FAILED", "message": "expected"})
        if outcome == "cancel":
            await asyncio.Event().wait()
        return "scheduled"

    dispatcher = RpcDispatcher({"launch": handler})
    task = asyncio.create_task(dispatcher.dispatch({
        "type": "launch", "from_plugin": "probe", "request_id": "handler",
        "timeout": 0.05, "_rpc_depth": 4,
    }))
    try:
        await entered.wait()
        if outcome == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            reply = await task
            if outcome == "error":
                assert reply["error"]["code"] == "PROBE_FAILED"
            else:
                assert reply["result"] == "scheduled"
        await asyncio.sleep(0.06)
        release.set()
        assert await background == "background completed"
        (request,) = sent
        assert request["_rpc_parent_request_id"] is None
        assert request["_rpc_trace_id"] == request["request_id"]
        assert request["_rpc_depth"] == 0
    finally:
        release.set()
        if background is not None:
            await asyncio.gather(background, return_exceptions=True)
        await dispatcher.close()
        await peer.aclose()


async def test_gather_children_keep_the_active_parent_deadline_and_trace():
    sent = []

    async def send(request, timeout):
        sent.append((request, timeout))

    peer = RpcPeer(send)
    deadline = time.monotonic() + 0.03
    try:
        with request_scope({
            "request_id": "active-entry", "_rpc_trace_id": "trace",
            "_rpc_depth": 2, "_request_deadline_monotonic": deadline,
        }):
            results = await asyncio.gather(
                peer.request("lookup", {}, timeout=1),
                peer.request("lookup", {}, timeout=1),
                return_exceptions=True,
            )
        assert all(isinstance(result, RpcDeadlineExceededError) for result in results)
        assert len(sent) == 2
        for request, budget in sent:
            assert request["_request_deadline_monotonic"] == deadline
            assert request["_rpc_parent_request_id"] == "active-entry"
            assert request["_rpc_trace_id"] == "trace"
            assert request["_rpc_depth"] == 3
            assert 0 < budget <= 0.03
    finally:
        await peer.aclose()


async def test_request_already_started_keeps_its_budget_when_parent_scope_ends():
    sent = asyncio.Event()

    async def send(request, timeout):
        sent.set()

    peer = RpcPeer(send)
    try:
        with request_scope({
            "request_id": "entry", "_request_deadline_monotonic": time.monotonic() + 0.03,
        }):
            request = asyncio.create_task(peer.request("lookup", {}, timeout=1))
            await sent.wait()
        with pytest.raises(RpcDeadlineExceededError):
            await request
    finally:
        await peer.aclose()


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

    async def leaf(ctx, request):
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

    async def parent(ctx, request):
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
    async def fail(ctx, request):
        raise ValueError("private input")

    async def good(ctx, request):
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

    async def block(ctx, request):
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
    async def good(ctx, request):
        return "ok"

    dispatcher = RpcDispatcher({"good": good})
    reply = await dispatcher.dispatch(
        {"type": "good", "from_plugin": "a", "request_id": "bad", "_rpc_depth": depth}
    )
    assert reply["error"]["code"] == "INVALID_REQUEST"


async def test_internal_timeout_has_its_own_error_code():
    async def timeout(ctx, request):
        raise TimeoutError("backend timed out")

    reply = await RpcDispatcher({"timeout": timeout}).dispatch(
        {"type": "timeout", "from_plugin": "a", "request_id": "1", "timeout": 10}
    )
    assert reply["error"]["code"] == "OPERATION_TIMEOUT"


async def test_dispatcher_cuts_off_a_handler_at_the_deadline():
    async def slow(ctx, request):
        await asyncio.sleep(1)

    reply = await RpcDispatcher({"slow": slow}).dispatch(
        {"type": "slow", "from_plugin": "a", "request_id": "1", "timeout": 0.05}
    )
    assert reply["error"]["code"] == "DEADLINE_EXCEEDED"


async def test_deadline_owning_handler_replies_after_its_deadline():
    @owns_deadline
    async def degraded(ctx, request):
        await asyncio.sleep(max(0.0, ctx.deadline - time.monotonic()) + 0.02)
        return "degraded"

    reply = await RpcDispatcher({"degraded": degraded}).dispatch(
        {"type": "degraded", "from_plugin": "a", "request_id": "1", "timeout": 0.05}
    )
    assert reply["error"] is None
    assert reply["result"] == "degraded"


async def test_request_expired_in_queue_is_a_deadline_error():
    async def good(ctx, request):
        return "ok"

    reply = await RpcDispatcher({"good": good}).dispatch(
        {
            "type": "good",
            "from_plugin": "a",
            "request_id": "1",
            "_request_deadline_monotonic": time.monotonic() - 1,
        }
    )
    assert reply["error"]["code"] == "DEADLINE_EXCEEDED"


async def test_admission_covers_blocked_reply_delivery():
    started, release = asyncio.Event(), asyncio.Event()

    async def good(ctx, request):
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
    async def fail(ctx, request):
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

    async def wait(ctx, request):
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


@pytest.mark.parametrize(
    "identity",
    [{}, {"from_plugin": " ", "request_id": "r1"}, {"from_plugin": "p1", "request_id": " "}],
)
async def test_missing_identity_is_rejected_before_the_handler_runs(identity):
    called = False

    async def good(ctx, request):
        nonlocal called
        called = True
        return "ok"

    reply = await RpcDispatcher({"good": good}).dispatch(dict(identity, type="good"))
    assert reply["error"]["code"] == "INVALID_REQUEST"
    assert called is False


async def test_handler_receives_normalized_context():
    seen = []

    async def good(ctx, request):
        seen.append(ctx)
        return "ok"

    reply = await RpcDispatcher({"good": good}).dispatch(
        {"type": "good", "from_plugin": " p1 ", "request_id": " r1 ", "timeout": 1.5}
    )
    assert reply["error"] is None
    (ctx,) = seen
    assert (ctx.from_plugin, ctx.request_id, ctx.depth) == ("p1", "r1", 0)
    assert 0 < ctx.remaining() <= 1.5


@pytest.mark.parametrize(
    ("timeout", "budget"),
    [
        (None, DEFAULT_HANDLER_TIMEOUT_SECONDS),
        (0, DEFAULT_HANDLER_TIMEOUT_SECONDS),
        (-1, DEFAULT_HANDLER_TIMEOUT_SECONDS),
        (True, DEFAULT_HANDLER_TIMEOUT_SECONDS),
        ("5", DEFAULT_HANDLER_TIMEOUT_SECONDS),
        (float("inf"), DEFAULT_HANDLER_TIMEOUT_SECONDS),
        (1000, MAX_HANDLER_TIMEOUT_SECONDS),
    ],
)
async def test_unusable_timeout_falls_back_instead_of_rejecting(timeout, budget):
    seen = []

    async def good(ctx, request):
        seen.append(ctx.remaining())
        return "ok"

    reply = await RpcDispatcher({"good": good}).dispatch(
        {"type": "good", "from_plugin": "p1", "request_id": "r1", "timeout": timeout}
    )
    assert reply["error"] is None
    (remaining,) = seen
    assert budget - 1 < remaining <= budget
