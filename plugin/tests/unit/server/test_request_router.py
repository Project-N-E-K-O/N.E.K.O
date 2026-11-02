from __future__ import annotations

import asyncio

import pytest

from plugin.core.state import state
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging import request_router as module


@pytest.fixture
async def router(monkeypatch):
    monkeypatch.setattr(state, "_plugin_comm_queue", asyncio.Queue())
    instance = module.PluginRouter()
    try:
        yield instance
    finally:
        await instance.stop()


async def test_validation_unknown_and_domain_failure_use_one_reply_contract(router):
    bad = await router._dispatcher.dispatch({"type": "X", "request_id": "1"})
    assert bad["error"]["code"] == "INVALID_REQUEST"
    unknown = await router._dispatcher.dispatch(
        {"type": "X", "request_id": "2", "from_plugin": "a"}
    )
    assert unknown["error"]["code"] == "UNKNOWN_OPERATION"


async def test_failed_request_does_not_stop_the_queue(router, monkeypatch):
    replies = asyncio.Queue()

    async def fail(ctx, request):
        raise ServerDomainError("PLUGIN_EXECUTION_FAILED", "target rejected input", 502, details={"x": 1})

    async def good(ctx, request):
        return "ok"

    router._handlers.update(FAIL=fail, GOOD=good)
    monkeypatch.setattr(state, "get_downlink_sender", lambda plugin: replies.put)
    await router.start()
    await state.plugin_comm_queue.put(
        {"type": "FAIL", "request_id": "1", "from_plugin": "a"}
    )
    await state.plugin_comm_queue.put(
        {"type": "GOOD", "request_id": "2", "from_plugin": "a"}
    )
    received = {
        reply["request_id"]: reply
        for reply in [
            await asyncio.wait_for(replies.get(), 1),
            await asyncio.wait_for(replies.get(), 1),
        ]
    }
    assert received["1"]["error"]["message"] == "target rejected input"
    assert received["1"]["error"]["details"] == {"x": 1}
    assert received["2"]["result"] == "ok"


async def test_response_delivery_failure_is_logged_without_fallback(
    router, monkeypatch, caplog
):
    async def good(ctx, request):
        return "ok"

    router._handlers["GOOD"] = good
    monkeypatch.setattr(state, "get_downlink_sender", lambda plugin: None)
    await router.start()
    await state.plugin_comm_queue.put(
        {"type": "GOOD", "request_id": "lost", "from_plugin": "a"}
    )
    for _ in range(100):
        if "RPC reply delivery failed" in caplog.text:
            break
        await asyncio.sleep(0.001)
    assert "RPC reply delivery failed" in caplog.text
    assert "lost" in caplog.text


async def test_blocked_overload_reply_does_not_stall_other_requests(
    router, monkeypatch
):
    replies = asyncio.Queue()
    blocked = asyncio.Event()

    async def good(ctx, request):
        return "ok"

    async def send(reply):
        if reply["to_plugin"] == "slow":
            await blocked.wait()
        await replies.put(reply)

    router._handlers["GOOD"] = good
    monkeypatch.setattr(router, "_send_response", send)
    await router.start()
    router._dispatcher._limit = 0
    for index in range(8):
        await state.plugin_comm_queue.put(
            {"type": "GOOD", "request_id": f"s{index}", "from_plugin": "slow"}
        )
    await state.plugin_comm_queue.put(
        {"type": "GOOD", "request_id": "fast", "from_plugin": "fast"}
    )
    reply = await asyncio.wait_for(replies.get(), 0.2)
    assert reply["request_id"] == "fast"
    assert reply["error"]["code"] == "BUSY"


async def test_healthy_downlink_answers_every_request_in_a_queued_burst(router, monkeypatch):
    replies = asyncio.Queue()

    async def good(ctx, request):
        return "ok"

    router._handlers["GOOD"] = good
    monkeypatch.setattr(state, "get_downlink_sender", lambda _: replies.put)
    for index in range(600):
        state.plugin_comm_queue.put_nowait(
            {"type": "GOOD", "request_id": str(index), "from_plugin": "healthy"}
        )
    await router.start()
    async with asyncio.timeout(2):
        received = [await replies.get() for _ in range(600)]
    assert {reply["request_id"] for reply in received} == {str(i) for i in range(600)}
    assert all(
        reply["result"] == "ok" or reply["error"]["code"] == "BUSY"
        for reply in received
    )


@pytest.mark.parametrize("reply_delay", [0, 0.01])
async def test_saturated_dispatcher_delivers_busy_replies_for_the_whole_burst(
    router, monkeypatch, reply_delay
):
    replies = asyncio.Queue()
    release = asyncio.Event()

    async def held(ctx, request):
        await release.wait()
        return "ok"

    async def send(reply):
        await asyncio.sleep(reply_delay)
        await replies.put(reply)

    router._handlers["HELD"] = held
    monkeypatch.setattr(state, "get_downlink_sender", lambda _: send)
    for index in range(600):
        state.plugin_comm_queue.put_nowait(
            {"type": "HELD", "request_id": str(index), "from_plugin": "healthy"}
        )
    await router.start()
    try:
        async with asyncio.timeout(2):
            busy = [await replies.get() for _ in range(344)]
            assert all(reply["error"]["code"] == "BUSY" for reply in busy)
            release.set()
            completed = [await replies.get() for _ in range(256)]
    finally:
        release.set()
    assert all(reply["result"] == "ok" for reply in completed)
    assert {reply["request_id"] for reply in busy + completed} == {str(i) for i in range(600)}


@pytest.mark.parametrize("control", ["RPC_CANCEL", "RPC_DISCONNECT"])
async def test_full_overload_reply_budget_still_allows_control_messages(router, monkeypatch, control):
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    blocked = asyncio.Event()

    async def held(ctx, request):
        entered.set()
        try:
            await blocked.wait()
        finally:
            cancelled.set()

    router._handlers["HELD"] = held

    async def blocked_send(reply):
        await blocked.wait()

    monkeypatch.setattr(state, "get_downlink_sender", lambda _: blocked_send)
    await router.start()
    router._dispatcher._limit = 1
    state.plugin_comm_queue.put_nowait(
        {"type": "HELD", "request_id": "active", "from_plugin": "slow"}
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    for index in range(300):
        state.plugin_comm_queue.put_nowait(
            {"type": "HELD", "request_id": str(index), "from_plugin": "slow"}
        )
    state.plugin_comm_queue.put_nowait(
        {"type": control, "request_id": "active", "from_plugin": "slow"}
    )
    await asyncio.wait_for(cancelled.wait(), timeout=1)
