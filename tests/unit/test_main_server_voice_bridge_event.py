from typing import Any

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_handle_agent_event_drops_invalid_voice_bridge_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main_server

    calls: list[tuple[str, dict[str, Any]]] = []

    def _notify_voice_bridge_result(event_id: str, result: dict[str, Any]) -> None:
        calls.append((event_id, result))

    monkeypatch.setattr(
        main_server,
        "notify_voice_bridge_result",
        _notify_voice_bridge_result,
    )

    await main_server._handle_agent_event(
        {
            "event_type": "voice_bridge_result",
            "event_id": "voice-bad",
            "result": ["bad"],
        }
    )

    assert calls == []


@pytest.mark.asyncio
async def test_handle_agent_event_notifies_valid_voice_bridge_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main_server

    calls: list[tuple[str, dict[str, Any]]] = []

    def _notify_voice_bridge_result(event_id: str, result: dict[str, Any]) -> None:
        calls.append((event_id, result))

    monkeypatch.setattr(
        main_server,
        "notify_voice_bridge_result",
        _notify_voice_bridge_result,
    )

    await main_server._handle_agent_event(
        {
            "event_type": "voice_bridge_result",
            "event_id": "voice-ok",
            "result": {"action": "noop"},
        }
    )

    assert calls == [("voice-ok", {"action": "noop"})]


def test_main_server_mounts_card_assist_router() -> None:
    from app import main_server

    paths = {getattr(route, "path", "") for route in main_server.app.routes}

    assert "/api/card-assist/clarify" in paths
    assert "/api/card-assist/generate" in paths
    assert "/api/card-assist/refine" in paths
    assert "/api/card-assist/chat" in paths
