from __future__ import annotations

import asyncio

import pytest

from plugin.core.state import state
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging import request_router as module


@pytest.fixture
async def router(monkeypatch):
    monkeypatch.setenv("NEKO_PLUGIN_ZMQ_IPC_ENABLED", "false")
    monkeypatch.setattr(state, "_plugin_comm_queue", asyncio.Queue())
    instance = module.PluginRouter()
    try:
        yield instance
    finally:
        await instance.stop()


async def test_validation_unknown_and_domain_failure_use_one_reply_contract(router):
    bad = await router._handle_zmq_request({"type": "X", "request_id": "1"})
    assert bad["error"]["code"] == "INVALID_REQUEST"
    unknown = await router._handle_zmq_request(
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
