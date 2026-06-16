import pytest

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_handle_agent_event_ignores_voice_bridge_result() -> None:
    from app import main_server

    await main_server._handle_agent_event(
        {
            "event_type": "voice_bridge_result",
            "event_id": "voice-ok",
            "result": {"action": "cancel_response"},
        }
    )


def test_main_server_mounts_card_assist_router() -> None:
    from app import main_server

    paths = {getattr(route, "path", "") for route in main_server.app.routes}

    assert "/api/card-assist/clarify" in paths
    assert "/api/card-assist/generate" in paths
    assert "/api/card-assist/refine" in paths
    assert "/api/card-assist/chat" in paths
