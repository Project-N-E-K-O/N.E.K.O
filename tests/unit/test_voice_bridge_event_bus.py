from __future__ import annotations

import asyncio

import pytest

from main_logic import agent_event_bus

pytestmark = pytest.mark.unit


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
