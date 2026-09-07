import asyncio

import pytest
from starlette.websockets import WebSocketState

from main_logic.plugin_cards import _targets, deliver_plugin_card


class Socket:
    client_state = WebSocketState.CONNECTED

    def __init__(self):
        self.frames = []

    async def send_json(self, frame):
        self.frames.append(frame)


class Manager:
    def __init__(self):
        self.websocket = Socket()

    async def render_chat_blocks(self, *args, **kwargs):
        pytest.fail("AgentHUD content must not enter chat history")


@pytest.fixture(autouse=True)
def clear_targets():
    _targets.clear()
    yield
    _targets.clear()


def event(operation, **fields):
    return {"plugin_id": "demo", "card": {"type": "html_card", "presentation": "agent",
            "card_id": "one", "operation": operation, **fields}}


def test_view_create_update_close_are_display_only_and_keep_first_character():
    alice, bob = Manager(), Manager()
    managers = {"Alice": alice, "Bob": bob}

    async def run():
        assert await deliver_plugin_card(event("create", title="Job", html="Ready"), managers, "Alice")
        update = event("update", title="Working", actions={})
        update["lanlan_name"] = "Bob"
        assert await deliver_plugin_card(update, managers, "Bob")
        assert await deliver_plugin_card(event("close"), managers, "Bob")
        assert not await deliver_plugin_card(event("update", html="Late"), {"Bob": bob}, "Bob")

    asyncio.run(run())
    assert not bob.websocket.frames
    frames = alice.websocket.frames
    assert [frame["type"] for frame in frames] == ["plugin_view"] * 3
    assert frames[0]["view"]["summary"] == "Job"
    assert frames[1]["view"] == {"type": "html_card", "presentation": "agent", "operation": "update",
                                 "cardId": "one", "pluginId": "demo", "targetLanlan": "Alice",
                                 "title": "Working", "actions": {}}
    assert frames[2]["view"] == {"type": "html_card", "presentation": "agent", "operation": "close",
                                 "cardId": "one", "pluginId": "demo", "targetLanlan": "Alice"}


@pytest.mark.parametrize("operation", ["update", "close"])
def test_unknown_view_operations_do_not_choose_a_new_default(operation):
    bob = Manager()
    assert not asyncio.run(deliver_plugin_card(event(operation), {"Bob": bob}, "Bob"))
    assert not bob.websocket.frames


def test_view_routing_is_scoped_by_plugin_and_does_not_fallback_offline():
    alice, bob = Manager(), Manager()
    first = event("create", title="Job", html="Ready", summary="")
    first["lanlan_name"] = "Alice"
    assert not asyncio.run(deliver_plugin_card(first, {"Bob": bob}, "Bob"))
    assert asyncio.run(deliver_plugin_card(first, {"Alice": alice, "Bob": bob}, "Bob"))
    second = event("create", title="Other job", html="Other")
    second["plugin_id"] = "other"
    assert asyncio.run(deliver_plugin_card(second, {"Alice": alice, "Bob": bob}, "Bob"))
    assert alice.websocket.frames[0]["view"]["summary"] == ""
    assert bob.websocket.frames[0]["view"]["pluginId"] == "other"


@pytest.mark.parametrize("fields", [
    {"presentation": "unknown"}, {"title": 123}, {"html": []},
    {"actions": {"go": {}}}, {"operation": "show"},
    {"presentation": "chat", "operation": "close"},
])
def test_invalid_view_payloads_do_not_reach_frontend(fields):
    alice = Manager()
    payload = event("create", title="Job", html="Ready")
    payload["card"].update(fields)
    assert not asyncio.run(deliver_plugin_card(payload, {"Alice": alice}, "Alice"))
    assert not alice.websocket.frames


def test_view_delivery_is_best_effort_without_a_connected_socket():
    alice = Manager()
    alice.websocket.client_state = WebSocketState.DISCONNECTED
    assert not asyncio.run(deliver_plugin_card(event("create", title="Job", html="Ready"), {"Alice": alice}, "Alice"))
    assert not alice.websocket.frames

    async def fail_send(frame):
        raise RuntimeError("socket closed during send")

    alice.websocket.client_state = WebSocketState.CONNECTED
    alice.websocket.send_json = fail_send
    assert not asyncio.run(deliver_plugin_card(event("close"), {"Alice": alice}, "Alice"))
