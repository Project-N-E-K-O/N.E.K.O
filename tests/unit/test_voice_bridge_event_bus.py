from __future__ import annotations

import pytest

from main_logic import agent_event_bus

pytestmark = pytest.mark.unit


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
async def test_voice_transcript_observed_attempts_publish_without_agent_liveness(
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

    assert sent is True
    assert published[0]["event_type"] == "voice_transcript_observed"
    assert published[0]["transcript"] == "agent server is gone"


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
