from main_routers import websocket_router


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
