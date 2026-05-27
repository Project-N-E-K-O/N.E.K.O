from __future__ import annotations

import asyncio

import pytest

from main_logic import agent_event_bus

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_voice_bridge_notify_keeps_waiter_until_loop_resolves() -> None:
    event_id = "voice-race-regression"
    loop = asyncio.get_running_loop()
    waiter: asyncio.Future = loop.create_future()
    with agent_event_bus._voice_bridge_waiters_lock:
        agent_event_bus._voice_bridge_waiters[event_id] = waiter
        agent_event_bus._voice_bridge_waiters_resolving.discard(event_id)

    try:
        agent_event_bus.notify_voice_bridge_result(event_id, {"action": "noop"})

        with agent_event_bus._voice_bridge_waiters_lock:
            assert agent_event_bus._voice_bridge_waiters.get(event_id) is waiter
            assert event_id in agent_event_bus._voice_bridge_waiters_resolving
        assert not waiter.done()

        await asyncio.sleep(0)

        assert waiter.done()
        assert waiter.result() == {"action": "noop"}
        with agent_event_bus._voice_bridge_waiters_lock:
            assert event_id not in agent_event_bus._voice_bridge_waiters
            assert event_id not in agent_event_bus._voice_bridge_waiters_resolving
    finally:
        with agent_event_bus._voice_bridge_waiters_lock:
            agent_event_bus._voice_bridge_waiters.pop(event_id, None)
            agent_event_bus._voice_bridge_waiters_resolving.discard(event_id)
        if not waiter.done():
            waiter.cancel()


@pytest.mark.asyncio
async def test_voice_transcript_request_returns_agent_result(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _Bridge:
        async def publish_session_event(self, event: dict) -> bool:
            captured.update(event)
            asyncio.get_running_loop().call_soon(
                agent_event_bus.notify_voice_bridge_result,
                str(event["event_id"]),
                {"action": "cancel_response"},
            )
            return True

    monkeypatch.setattr(agent_event_bus, "_main_bridge_ref", _Bridge())

    result = await agent_event_bus.publish_voice_transcript_request_reliably(
        "Yui",
        "hm this is 3x^2",
        timeout_s=0.2,
    )

    assert result == {"action": "cancel_response"}
    assert captured["event_type"] == "voice_transcript_request"
    assert captured["lanlan_name"] == "Yui"
    assert captured["transcript"] == "hm this is 3x^2"
    assert agent_event_bus._voice_bridge_waiters == {}


@pytest.mark.asyncio
async def test_voice_transcript_request_retries_after_send_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[int] = []

    class _Bridge:
        async def publish_session_event(self, event: dict) -> bool:
            attempts.append(int(event["attempt"]))
            if len(attempts) == 1:
                return False
            asyncio.get_running_loop().call_soon(
                agent_event_bus.notify_voice_bridge_result,
                str(event["event_id"]),
                {"action": "prime_context", "context": "screen context"},
            )
            return True

    monkeypatch.setattr(agent_event_bus, "_main_bridge_ref", _Bridge())

    result = await agent_event_bus.publish_voice_transcript_request_reliably(
        "Yui",
        "Yui explain this step",
        timeout_s=0.2,
        retries=1,
    )

    assert attempts == [0, 1]
    assert result == {"action": "prime_context", "context": "screen context"}
    assert agent_event_bus._voice_bridge_waiters == {}
