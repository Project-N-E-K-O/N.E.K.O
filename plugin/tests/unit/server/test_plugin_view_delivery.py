import asyncio

import pytest
from starlette.websockets import WebSocketState

from main_logic.plugin_cards import _active_views, _targets, _view_targets, deliver_plugin_card


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
    _view_targets.clear()
    _active_views.clear()
    yield
    _targets.clear()
    _view_targets.clear()
    _active_views.clear()


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


def test_chat_churn_cannot_evict_a_live_untargeted_view():
    alice, bob = Manager(), Manager()

    async def accept_chat(*args, **kwargs):
        return True

    bob.render_chat_blocks = accept_chat
    managers = {"Alice": alice, "Bob": bob}

    async def run():
        assert await deliver_plugin_card(event("create", title="Long job", html="Ready"), managers, "Alice")
        for index in range(1024):
            assert await deliver_plugin_card({"plugin_id": "chat-producer", "card": {
                "card_id": str(index), "operation": "create", "html": "Message", "summary": "Message",
            }}, managers, "Bob")
        assert len(_targets) == 512
        assert len(_view_targets) == len(_active_views) == 1
        assert await deliver_plugin_card(event("update", html="Still working"), managers, "Bob")
        assert await deliver_plugin_card(event("close"), managers, "Bob")
        assert not _view_targets and not _active_views
        assert not await deliver_plugin_card(event("update", html="Late result"), managers, "Bob")

    asyncio.run(run())
    assert len(alice.websocket.frames) == 3
    assert not bob.websocket.frames
    assert all(frame["view"]["targetLanlan"] == "Alice" for frame in alice.websocket.frames)


def test_view_replacement_reclaims_routes_and_stale_handles_do_not_regrow_them():
    alice, bob = Manager(), Manager()
    managers = {"Alice": alice, "Bob": bob}

    async def run():
        other = event("create", title="Other role", html="Ready")
        other["card"]["card_id"] = "bob-view"
        assert await deliver_plugin_card(other, managers, "Bob")
        for index in range(1024):
            created = event("create", title="Replacement", html="Ready")
            created["card"]["card_id"] = str(index)
            assert await deliver_plugin_card(created, managers, "Alice")
        assert len(_view_targets) == len(_active_views) == 2
        assert not _targets, "Agent routing must not consume chat cache entries"
        for operation in ("update", "close"):
            stale = event(operation, html="Old completion")
            stale["card"]["card_id"] = "0"
            assert not await deliver_plugin_card(stale, managers, "Bob")
            # Recovered handles with an explicit target may still send, but the
            # frontend ignores stale IDs and routing must not retain them again.
            stale["lanlan_name"] = "Alice"
            assert await deliver_plugin_card(stale, managers, "Bob")
        assert len(_view_targets) == len(_active_views) == 2
        latest = event("close")
        latest["card"]["card_id"] = "1023"
        assert await deliver_plugin_card(latest, managers, "Bob")
        assert _view_targets == {("demo", "bob-view"): "Bob"}
        assert _active_views == {("demo", "Bob"): "bob-view"}

    asyncio.run(run())


def test_failed_replacement_and_close_preserve_the_previous_route_for_retry():
    alice = Manager()
    managers = {"Alice": alice}

    async def run():
        assert await deliver_plugin_card(event("create", title="Job", html="Ready"), managers, "Alice")
        send = alice.websocket.send_json

        async def fail_send(frame):
            raise RuntimeError("socket unavailable")

        alice.websocket.send_json = fail_send
        replacement = event("create", title="Replacement", html="New")
        replacement["card"]["card_id"] = "replacement"
        assert not await deliver_plugin_card(replacement, managers, "Alice")
        assert not await deliver_plugin_card(event("close"), managers, None)
        assert _view_targets == {("demo", "one"): "Alice"}
        assert _active_views == {("demo", "Alice"): "one"}
        alice.websocket.send_json = send
        assert await deliver_plugin_card(event("update", html="Retry"), managers, None)
        assert await deliver_plugin_card(event("close"), managers, None)
        assert not _view_targets and not _active_views

    asyncio.run(run())
