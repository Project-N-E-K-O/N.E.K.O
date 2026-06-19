from pathlib import Path


WEBSOCKET_ROUTER_PATH = Path(__file__).resolve().parents[2] / "main_routers" / "websocket_router.py"


def test_goodbye_state_clear_retries_pending_callbacks():
    source = WEBSOCKET_ROUTER_PATH.read_text(encoding="utf-8")

    goodbye_state_block = source.split('if action == "goodbye_state":', 1)[1].split(
        'if action == "start_session":',
        1,
    )[0]

    assert "set_goodbye_silent(active, reason)" in goodbye_state_block
    assert "if not active and goodbye_mgr.pending_agent_callbacks:" in goodbye_state_block
    assert "_fire_task(goodbye_mgr.trigger_agent_callbacks())" in goodbye_state_block
