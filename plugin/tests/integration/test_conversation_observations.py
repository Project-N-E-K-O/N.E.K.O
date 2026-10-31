from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import ormsgpack
import pytest
import zmq

from plugin.core import host as host_module
from plugin.core.host import PluginHost
from plugin.core.state import state
from plugin.message_plane.runner import MessagePlaneEndpoints, PythonMessagePlaneRunner
from plugin.server.application.conversations import ConversationService
from plugin.server.messaging.bus_subscriptions import BusSubscriptionManager
from plugin.server.messaging.request_router import PluginRouter

pytestmark = pytest.mark.plugin_integration


def endpoints():
    sockets = [socket.socket() for _ in range(3)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        ports = [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()
    return MessagePlaneEndpoints(*(f"tcp://127.0.0.1:{port}" for port in ports)), ports


@pytest.fixture
async def observation_runtime(monkeypatch, tmp_path):
    from plugin.server import lifecycle as lifecycle_module

    monkeypatch.setattr(
        host_module, "_refresh_child_storage_layout_env", lambda _: None
    )
    monkeypatch.setattr(state, "_plugin_comm_queue", asyncio.Queue())
    monkeypatch.setattr(
        state,
        "_bus_subscriptions",
        {
            name: {}
            for name in (
                "messages",
                "events",
                "lifecycle",
                "runs",
                "export",
                "conversations",
            )
        },
    )
    hosts = {}
    monkeypatch.setattr(
        state, "get_plugin_hosts_snapshot_cached", lambda timeout=1: hosts
    )
    addresses, ports = endpoints()
    monkeypatch.setenv("NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT", addresses.rpc)
    manager = BusSubscriptionManager()
    router = PluginRouter()
    service = None
    host = None
    token = "integration-test-token"

    runner = PythonMessagePlaneRunner(
        endpoints=addresses,
        auth_token=token,
        on_record_accepted=manager.accept_conversation_record,
    )
    context = zmq.Context()
    push = context.socket(zmq.PUSH)
    push.setsockopt(zmq.LINGER, 0)
    try:
        await manager.start()
        await router.start()
        runner.start()
        assert await asyncio.to_thread(runner.health_check, timeout_s=3)
        service = ConversationService(
            runner.get_conversation_message, runner.update_conversation_message
        )
        manager.set_conversation_observers(
            on_record=service.accept_message, on_fact=service.accept_fact
        )
        monkeypatch.setattr(
            lifecycle_module, "get_conversation_service", lambda: service
        )
        push.connect(addresses.ingest)
        directory = tmp_path / "conversation_probe"
        directory.mkdir()
        fixture = (
            Path(__file__).parents[1] / "fixtures" / "conversation_observation_probe.py"
        )
        (directory / "__init__.py").write_text(
            fixture.read_text(encoding="utf-8"), encoding="utf-8"
        )
        config = directory / "plugin.toml"
        entry = "plugins.conversation_probe:ObservationProbe"
        config.write_text(
            f"[plugin]\nid='conversation_probe'\nname='conversation_probe'\nentry='{entry}'\n"
        )
        host = PluginHost("conversation_probe", entry, config, source_only=True)
        hosts["conversation_probe"] = host
        await host.start(startup_timeout=10, startup_failure="abort")
        yield host, manager, runner, service, push, token
    finally:
        if host is not None and host.process is not None and host.process.is_alive():
            await host.shutdown(timeout=2)
        await manager.stop()
        await router.stop()
        if service is not None:
            service.close()
        push.close()
        context.term()
        await asyncio.to_thread(runner.stop)
        for port in ports:
            sock = socket.socket()
            try:
                sock.bind(("127.0.0.1", port))
            finally:
                sock.close()


async def wait_events(host, predicate):
    async with asyncio.timeout(5):
        while True:
            events = await host.trigger("snapshot", {}, timeout=2)
            if predicate(events):
                return events
            await asyncio.sleep(0.02)


def ingest(push, token, mid, text, *, reply_id=None, input_id=None):
    payload = {
        "id": mid,
        "timestamp": 1.0,
        "content": text,
        "reply_id": reply_id,
        "input_id": input_id,
        "metadata": {
            "turn_type": "assistant_message" if reply_id else "user_message",
            "lanlan_name": "YUI",
            "is_voice": False,
        },
    }
    push.send(
        ormsgpack.packb(
            {
                "v": 1,
                "kind": "delta_batch",
                "_auth": token,
                "items": [
                    {"store": "conversations", "topic": "all", "payload": payload}
                ],
            }
        )
    )


async def test_real_plugin_watch_and_reply_detail(observation_runtime):
    host, manager, runner, service, push, token = observation_runtime
    events = await wait_events(
        host, lambda items: any(x["kind"] == "change" for x in items)
    )
    initial = next(x["value"] for x in events if x["kind"] == "change")
    assert initial["reason"] == "initial" and not initial["current"]

    manager.accept_raw_conversation_fact(
        "input.submitted",
        {
            "input_id": "i",
            "character": "YUI",
            "text": "question",
            "input_source": "text",
            "observed_at": 1.0,
        },
    )
    manager.accept_raw_conversation_fact(
        "reply.started",
        {
            "reply_id": "r",
            "input_id": "i",
            "turn_id": "turn-1",
            "character": "YUI",
            "text": "draft",
            "observed_at": 2.0,
        },
    )
    await wait_events(
        host,
        lambda items: (
            any(x["kind"] == "reply.started" for x in items)
            and any(x["kind"] == "input.submitted" for x in items)
        ),
    )
    assert (await host.trigger("reply", {"reply_id": "r"}, timeout=2))[
        "text"
    ] == "draft"

    manager.accept_raw_conversation_fact(
        "playback.started",
        {
            "turn_id": "turn-1",
            "character": "YUI",
            "presentation_id": "p",
            "presenter_id": "window",
        },
    )
    events = await wait_events(
        host, lambda items: any(x["kind"] == "playback.started" for x in items)
    )
    playback = next(x["value"] for x in events if x["kind"] == "playback.started")
    assert playback["reply_id"] == "r" and playback["presentation_id"] == "p"
    assert (await host.trigger("reply", {"reply_id": "r"}, timeout=2))["playbacks"][0][
        "state"
    ] == "playing"

    ingest(push, token, "m1", "first")
    ingest(push, token, "m2", "second", reply_id="r", input_id="i")
    await wait_events(
        host, lambda items: len([x for x in items if x["kind"] == "record"]) == 2
    )
    assert (await host.trigger("reply", {"reply_id": "r"}, timeout=2))[
        "text"
    ] == "second"
    manager.accept_raw_conversation_fact(
        "reply.updated", {"reply_id": "r", "text": "corrected"}
    )
    await wait_events(
        host,
        lambda items: (
            any(
                x["kind"] == "message.updated" and x["value"]["text"] == "corrected"
                for x in items
            )
            and any(
                x["kind"] == "change"
                and any(m["text"] == "corrected" for m in x["value"]["current"])
                for x in items
            )
        ),
    )
    assert (await host.trigger("interaction", {"input_id": "i"}, timeout=2))["replies"][
        0
    ]["text"] == "corrected"
    assert (await host.trigger("stop_watch", {}, timeout=2)) is True
    count = len(await host.trigger("snapshot", {}, timeout=2))
    ingest(push, token, "m3", "after stop")
    await asyncio.sleep(0.2)
    assert len(await host.trigger("snapshot", {}, timeout=2)) == count
    await host.trigger("restart_watch", {}, timeout=2)
    events = await wait_events(
        host,
        lambda items: (
            len(
                [
                    x
                    for x in items
                    if x["kind"] == "change" and x["value"]["reason"] == "initial"
                ]
            )
            == 2
        ),
    )
    latest = [
        x for x in events if x["kind"] == "change" and x["value"]["reason"] == "initial"
    ][-1]
    assert [m["id"] for m in latest["value"]["current"]] == ["m2", "m3"]
    original_id = (await host.trigger("watch_status", {}, timeout=2))["sub_id"]
    await manager.invalidate_conversations("plane reset")
    async with asyncio.timeout(3):
        while (await host.trigger("watch_status", {}, timeout=2))["active"]:
            await asyncio.sleep(0.02)
    await host.trigger("restart_watch", {}, timeout=2)
    new_status = await host.trigger("watch_status", {}, timeout=2)
    assert new_status["active"] and new_status["sub_id"] != original_id
    ingest(push, token, "m4", "after reset")
    events = await wait_events(
        host,
        lambda items: any(
            x["kind"] == "record" and x["value"]["id"] == "m4" for x in items
        ),
    )
    record_count = sum(item["kind"] == "record" for item in events)
    push.send(
        ormsgpack.packb(
            {
                "v": 1,
                "kind": "snapshot",
                "_auth": token,
                "store": "conversations",
                "topic": "all",
                "mode": "replace",
                "items": [],
            }
        )
    )
    events = await wait_events(
        host,
        lambda items: any(
            item["kind"] == "change"
            and item["value"]["reason"] == "update"
            and not item["value"]["current"]
            for item in items
        ),
    )
    assert sum(item["kind"] == "record" for item in events) == record_count


async def test_shutdown_removes_only_owned_conversation_subscriptions(
    observation_runtime,
):
    host, manager, runner, service, push, token = observation_runtime
    await wait_events(
        host, lambda items: any(item["kind"] == "change" for item in items)
    )
    own_id = (await host.trigger("watch_status", {}, timeout=2))["sub_id"]
    state.add_bus_subscription(
        "conversations", "another-plugin", {"from_plugin": "another"}
    )
    state.add_bus_subscription(
        "messages", "legacy-sub", {"from_plugin": "conversation_probe"}
    )
    assert own_id in state.get_bus_subscriptions("conversations")
    await host.shutdown(timeout=2)
    assert own_id not in state.get_bus_subscriptions("conversations")
    assert "another-plugin" in state.get_bus_subscriptions("conversations")
    assert "legacy-sub" in state.get_bus_subscriptions("messages")
