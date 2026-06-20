from pathlib import Path

from main_routers import websocket_router


WEBSOCKET_ROUTER_PATH = Path(__file__).resolve().parents[2] / "main_routers" / "websocket_router.py"


def test_home_tutorial_greeting_guard_expires(monkeypatch):
    lanlan_name = "TestCat"
    monkeypatch.setitem(
        websocket_router._home_tutorial_blocking_greeting,
        lanlan_name,
        (True, 100.0),
    )

    monkeypatch.setattr(websocket_router.time, "time", lambda: 120.0)
    assert websocket_router._is_home_tutorial_blocking_greeting(lanlan_name) is True

    monkeypatch.setattr(websocket_router.time, "time", lambda: 200.1)
    assert websocket_router._is_home_tutorial_blocking_greeting(lanlan_name) is False
    assert lanlan_name not in websocket_router._home_tutorial_blocking_greeting


def test_home_tutorial_greeting_guard_false_state_does_not_block(monkeypatch):
    lanlan_name = "TestCat"
    monkeypatch.setitem(
        websocket_router._home_tutorial_blocking_greeting,
        lanlan_name,
        (False, 100.0),
    )

    monkeypatch.setattr(websocket_router.time, "time", lambda: 120.0)
    assert websocket_router._is_home_tutorial_blocking_greeting(lanlan_name) is False


def test_tutorial_release_reason_does_not_block_released_greeting():
    source = WEBSOCKET_ROUTER_PATH.read_text(encoding="utf-8")

    assert "_is_tutorial_release_greeting_reason" not in source
    assert "skipped after tutorial release" not in source
    assert "new-user icebreaker owns this slot" not in source
