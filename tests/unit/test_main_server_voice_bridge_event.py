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


@pytest.mark.asyncio
async def test_agent_status_update_syncs_master_state_into_session_flags(monkeypatch) -> None:
    from app import main_server

    class DummyManager:
        websocket = None

        def __init__(self):
            self.seen_flags = None

        def update_agent_flags(self, flags):
            self.seen_flags = dict(flags)

    mgr = DummyManager()
    monkeypatch.setattr(main_server.character_runtime, "_get_session_manager", lambda lanlan_name=None: mgr)

    await main_server._handle_agent_event(
        {
            "event_type": "agent_status_update",
            "lanlan_name": "lanlan-test",
            "snapshot": {
                "analyzer_enabled": True,
                "flags": {"openclaw_enabled": True, "openclaw_ready": True},
            },
        }
    )

    assert mgr.seen_flags == {
        "agent_enabled": True,
        "openclaw_enabled": True,
        "openclaw_ready": True,
    }


def test_main_server_mounts_card_assist_router() -> None:
    from app import main_server

    paths = {getattr(route, "path", "") for route in main_server.app.routes}

    assert "/api/card-assist/clarify" in paths
    assert "/api/card-assist/generate" in paths
    assert "/api/card-assist/refine" in paths
    assert "/api/card-assist/chat" in paths
