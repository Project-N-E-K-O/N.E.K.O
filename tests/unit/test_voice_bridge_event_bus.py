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
async def test_voice_transcript_observed_broadcasts_without_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Bridge:
        ready = True
        pub = object()

        async def publish_session_event(self, event: dict) -> bool:
            captured.update(event)
            return True

    monkeypatch.setattr(agent_event_bus, "_main_bridge_ref", _Bridge())
    agent_event_bus._mark_agent_bridge_seen()

    sent = await agent_event_bus.publish_voice_transcript_observed_best_effort(
        "Yui",
        "hm this is 3x^2",
        metadata={"session_id": "s1"},
    )

    assert sent is True
    assert captured["event_type"] == "voice_transcript_observed"
    assert captured["lanlan_name"] == "Yui"
    assert captured["transcript"] == "hm this is 3x^2"
    assert captured["metadata"] == {"session_id": "s1"}
    assert isinstance(captured["event_id"], str)


@pytest.mark.asyncio
async def test_voice_transcript_observed_skips_publish_without_agent_liveness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[dict] = []

    class _Bridge:
        ready = True
        pub = object()

        async def publish_session_event(self, event: dict) -> bool:
            published.append(event)
            return True

    monkeypatch.setattr(agent_event_bus, "_main_bridge_ref", _Bridge())

    sent = await agent_event_bus.publish_voice_transcript_observed_best_effort(
        "Yui",
        "agent server is gone",
    )

    assert sent is False
    assert published == []


@pytest.mark.asyncio
async def test_legacy_voice_request_wrapper_does_not_wait_for_plugin_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Bridge:
        ready = True
        pub = object()

        async def publish_session_event(self, event: dict) -> bool:
            captured.update(event)
            return True

    monkeypatch.setattr(agent_event_bus, "_main_bridge_ref", _Bridge())
    agent_event_bus._mark_agent_bridge_seen()

    result = await agent_event_bus.publish_voice_transcript_request_reliably(
        "Yui",
        "Yui explain this step",
        timeout_s=0.001,
        retries=3,
    )

    assert result is None
    assert captured["event_type"] == "voice_transcript_observed"


def test_notify_voice_bridge_result_is_ignored() -> None:
    agent_event_bus.notify_voice_bridge_result("late-event", {"action": "cancel_response"})


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
