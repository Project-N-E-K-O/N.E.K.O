from __future__ import annotations

import asyncio

import pytest

from main_logic import agent_event_bus

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_agent_bridge_liveness() -> None:
    agent_event_bus._clear_agent_bridge_seen()
    yield
    agent_event_bus._clear_agent_bridge_seen()


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


def test_voice_bridge_notify_cancels_waiter_when_loop_is_closed() -> None:
    event_id = "voice-closed-loop"

    class _ClosedLoop:
        def call_soon_threadsafe(self, _callback):
            raise RuntimeError("event loop is closed")

    class _Waiter:
        def __init__(self):
            self.cancelled = False

        def done(self):
            return self.cancelled

        def cancel(self):
            self.cancelled = True

        def get_loop(self):
            return _ClosedLoop()

    waiter = _Waiter()
    with agent_event_bus._voice_bridge_waiters_lock:
        agent_event_bus._voice_bridge_waiters[event_id] = waiter
        agent_event_bus._voice_bridge_waiters_resolving.discard(event_id)

    try:
        agent_event_bus.notify_voice_bridge_result(event_id, {"action": "noop"})

        assert waiter.cancelled is True
        with agent_event_bus._voice_bridge_waiters_lock:
            assert event_id not in agent_event_bus._voice_bridge_waiters
            assert event_id not in agent_event_bus._voice_bridge_waiters_resolving
    finally:
        with agent_event_bus._voice_bridge_waiters_lock:
            agent_event_bus._voice_bridge_waiters.pop(event_id, None)
            agent_event_bus._voice_bridge_waiters_resolving.discard(event_id)


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
    agent_event_bus._mark_agent_bridge_seen()

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
async def test_voice_transcript_request_skips_publish_without_agent_liveness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[dict] = []

    class _Bridge:
        async def publish_session_event(self, event: dict) -> bool:
            published.append(event)
            return True

    monkeypatch.setattr(agent_event_bus, "_main_bridge_ref", _Bridge())

    result = await agent_event_bus.publish_voice_transcript_request_reliably(
        "Yui",
        "agent server is gone",
        timeout_s=0.2,
    )

    assert result is None
    assert published == []
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
    agent_event_bus._mark_agent_bridge_seen()

    result = await agent_event_bus.publish_voice_transcript_request_reliably(
        "Yui",
        "Yui explain this step",
        timeout_s=0.2,
        retries=1,
    )

    assert attempts == [0, 1]
    assert result == {"action": "prime_context", "context": "screen context"}
    assert agent_event_bus._voice_bridge_waiters == {}


@pytest.mark.asyncio
async def test_agent_bridge_stop_cancels_heartbeat_task() -> None:
    async def _noop(_event: dict) -> None:
        return None

    bridge = agent_event_bus.AgentServerEventBridge(on_session_event=_noop)
    heartbeat_finally_ran = False

    async def _heartbeat() -> None:
        nonlocal heartbeat_finally_ran
        try:
            await asyncio.sleep(30)
        finally:
            heartbeat_finally_ran = True

    heartbeat_task = asyncio.create_task(_heartbeat())
    bridge.ready = True
    bridge._heartbeat_task = heartbeat_task
    await asyncio.sleep(0)

    await bridge.stop()

    assert bridge._stop.is_set()
    assert bridge.ready is False
    assert bridge._heartbeat_task is None
    assert heartbeat_task.cancelled()
    assert heartbeat_finally_ran is True
