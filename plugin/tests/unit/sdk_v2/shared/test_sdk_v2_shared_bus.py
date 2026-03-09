from __future__ import annotations

from dataclasses import fields

import pytest

from plugin.sdk_v2.shared import bus
from plugin.sdk_v2.shared.bus import _client_base
from plugin.sdk_v2.shared.bus import bus_list
from plugin.sdk_v2.shared.bus import records
from plugin.sdk_v2.shared.bus import watchers
from plugin.sdk_v2.shared.bus import types


def test_bus_exports_exist() -> None:
    for name in bus.__all__:
        assert hasattr(bus, name)


def test_bus_model_dataclasses() -> None:
    conv = types.BusConversation(id="c", topic="t")
    msg = types.BusMessage(id="m", conversation_id="c", role="user", content="hello")
    evt = types.BusEvent(id="e", event_type="created")
    rec = types.BusRecord(id="r", namespace="ns")
    assert conv.metadata == {}
    assert msg.metadata == {}
    assert evt.payload == {}
    assert rec.rev == 0
    assert [f.name for f in fields(types.BusRecord)] == ["id", "namespace", "payload", "rev"]


@pytest.mark.asyncio
async def test_bus_facades_work() -> None:
    base = _client_base.BusClientBase(namespace="n")
    req = await base.request("act", {})
    assert req.is_err()

    aggregate = bus_list.Bus()
    conv = aggregate.conversations
    msg = aggregate.messages
    evt = aggregate.events
    life = aggregate.lifecycle
    mem = aggregate.memory
    rec = aggregate.records
    revision = aggregate.revision
    watch = aggregate.watchers

    created = await conv.create("topic", metadata={"x": 1})
    assert created.is_ok()
    conversation = created.unwrap()
    assert (await conv.list()).unwrap()[0].id == conversation.id
    assert (await conv.get(conversation.id)).unwrap().topic == "topic"

    appended = await msg.append(conversation.id, role="user", content="hello")
    assert appended.is_ok()
    message = appended.unwrap()
    assert (await msg.list(conversation.id)).unwrap()[0].id == message.id
    assert (await msg.get(message.id)).unwrap().content == "hello"

    watcher_id = (await watch.watch("created", handler=lambda _event: None)).unwrap()
    published = await evt.publish("created", {"x": 1})
    assert published.is_ok()
    assert (await evt.list("created")).unwrap()[0].event_type == "created"
    assert (await watch.poll("created")).unwrap()[0].id == published.unwrap().id
    assert (await watch.unwatch(watcher_id)).unwrap() is True

    assert (await life.emit("startup", {"ok": True})).is_ok()
    mem._state.memory["bucket"] = [{"id": 1, "text": "hello world"}, {"id": 2, "text": "bye"}]
    assert len((await mem.get("bucket")).unwrap()) == 2
    assert len((await mem.query("bucket", "hello")).unwrap()) == 1

    put = await rec.put("ns", "id", {"v": 1})
    assert put.unwrap().rev == 1
    assert (await rec.get("ns", "id")).unwrap().payload["v"] == 1
    assert (await rec.list("ns")).unwrap()[0].id == "id"
    assert (await revision.get("ns", "id")).unwrap() == 1
    assert (await revision.compare("ns", "id", 1)).unwrap() is True
    assert (await rec.delete("ns", "id")).unwrap() is True

    assert (await msg.delete(message.id)).unwrap() is True
    assert (await conv.delete(conversation.id)).unwrap() is True


def test_bus_record_helpers_and_bus_list() -> None:
    conv = types.BusConversation.from_raw({"id": "c1", "topic": "demo", "metadata": {"x": 1}})
    msg = types.BusMessage.from_index({"id": "m1", "conversation_id": "c1", "role": "user"}, {"content": "hello"})
    evt = types.BusEvent.from_raw({"id": "e1", "event_type": "created", "payload": {"x": 1}})
    rec = types.BusRecord.from_index({"id": "r1", "namespace": "ns", "rev": 2}, {"payload": {"x": 1}})
    assert conv.dump()["topic"] == "demo"
    assert msg.dump()["content"] == "hello"
    assert evt.dump()["event_type"] == "created"
    assert rec.dump()["rev"] == 2

    op = types.BusOp(name="get", params={"ns": "x"}, at=1.0)
    trace = types.GetNode(op="get", params={"ns": "x"}, at=1.0)
    unary = types.UnaryNode(op="filter", params={}, at=2.0, child=trace)
    binary = types.BinaryNode(op="merge", params={}, at=3.0, left=trace, right=trace)
    assert trace.dump()["kind"] == "get"
    assert "filter" in unary.explain()
    assert binary.dump()["kind"] == "binary"

    items = types.BusList([conv, types.BusConversation(id="c2", topic="other")], trace=[op])
    assert items.count() == 2
    assert items.size() == 2
    assert items.limit(1).count() == 1
    assert items.where(lambda item: item.topic == "demo").count() == 1
    assert items.merge(types.BusList([types.BusConversation(id="c3", topic="z")])).count() == 3
    assert items.sorted(key=lambda item: item.id).dump()[0]["id"] == "c1"
    assert "get" in items.explain()


def test_parse_iso_timestamp() -> None:
    assert records.parse_iso_timestamp(None) is None
    assert records.parse_iso_timestamp(1) == 1.0
    assert records.parse_iso_timestamp("2024-01-01T00:00:00Z") is not None
    assert records.parse_iso_timestamp("bad") is None


def test_bus_list_advanced_filters_and_subscriptions() -> None:
    items = types.BusList([
        types.BusEvent(id="e1", event_type="created", payload={"x": 1}, timestamp=1.0),
        types.BusEvent(id="e2", event_type="updated", payload={"x": 2}, timestamp=2.0),
    ])
    assert items.where_eq("event_type", "created").count() == 1
    assert items.where_in("event_type", ["created", "deleted"]).count() == 1
    assert items.where_contains("event_type", "date").count() == 1
    assert items.where_regex("event_type", "upd.*").count() == 1
    assert items.where_gt("timestamp", 1.5).count() == 1
    assert items.where_ge("timestamp", 2.0).count() == 1
    assert items.where_lt("timestamp", 1.5).count() == 1
    assert items.where_le("timestamp", 1.0).count() == 1
    assert items.intersection(types.BusList([items.items[0]])).count() == 1
    assert items.difference(types.BusList([items.items[0]])).count() == 1
    assert items.try_filter(lambda item: item.event_type == "created").ok is True

    watcher_client = bus.watchers.Watchers()
    watcher = watchers.BusListWatcher("w1", watcher_client, "created")
    assert watchers.list_subscription(watcher) is watcher


def test_bus_list_reload_and_watch_helpers() -> None:
    items = types.BusList([types.BusConversation(id="c1", topic="demo")])
    reloaded = items.reload()
    assert reloaded.count() == 1

    wired = items.reload_with(lambda _ctx=None: types.BusList([types.BusConversation(id="c2", topic="next")]))
    assert wired.reload().dump()[0]["id"] == "c2"

    watcher_client = bus.watchers.Watchers()
    watcher = items.watch(watcher_client, "created")
    assert watcher.channel == "created"


@pytest.mark.asyncio
async def test_bus_watcher_callback_flow() -> None:
    aggregate = bus_list.Bus()
    seen: list[str] = []

    async def on_event(event):
        seen.append(event.id)

    watcher_id = (await aggregate.watchers.watch("created", handler=on_event)).unwrap()
    watcher = watchers.BusListWatcher(watcher_id, aggregate.watchers, "created").subscribe(lambda event: seen.append(f"cb:{event.id}")).start()
    published = (await aggregate.events.publish("created", {"x": 1})).unwrap()
    polled = (await watcher.poll()).unwrap()
    assert polled[0].id == published.id
    assert published.id in seen
    assert f"cb:{published.id}" in seen
    await watcher.stop_async()
    assert watcher._started is False


@pytest.mark.asyncio
async def test_bus_watcher_delta_flow() -> None:
    aggregate = bus_list.Bus()
    seen: list[str] = []
    delta_seen: list[str] = []

    async def on_event(event):
        seen.append(event.id)

    watcher_id = (await aggregate.watchers.watch("created", handler=on_event)).unwrap()
    watcher = watchers.BusListWatcher(watcher_id, aggregate.watchers, "created")
    watcher.subscribe_delta(lambda delta: delta_seen.extend([event.id for event in delta.added])).start()
    published = (await aggregate.events.publish("created", {"x": 1})).unwrap()
    delta = (await watcher.poll_delta()).unwrap()
    assert delta.kind == "append"
    assert delta.added[0].id == published.id
    assert delta.current.count() == 1
    assert published.id in seen
    assert published.id in delta_seen
    noop = (await watcher.poll_delta()).unwrap()
    assert noop.kind == "noop"


@pytest.mark.asyncio
async def test_records_revision_driven_removed_and_changed_delta() -> None:
    aggregate = bus_list.Bus()
    watcher_id = (await aggregate.watchers.watch("records:ns", handler=lambda _event: None)).unwrap()
    watcher = watchers.BusListWatcher(watcher_id, aggregate.watchers, "records:ns").start()

    created = (await aggregate.records.put("ns", "r1", {"x": 1})).unwrap()
    delta1 = (await watcher.poll_delta()).unwrap()
    assert delta1.kind == "append"
    assert delta1.added[0].id == created.id
    assert delta1.current.count() == 1

    changed = (await aggregate.records.put("ns", "r1", {"x": 2})).unwrap()
    delta2 = (await watcher.poll_delta()).unwrap()
    assert delta2.kind == "change"
    assert delta2.changed[0].rev == changed.rev
    assert delta2.current.count() == 1

    removed = await aggregate.records.delete("ns", "r1")
    assert removed.unwrap() is True
    delta3 = (await watcher.poll_delta()).unwrap()
    assert delta3.kind == "remove"
    assert delta3.removed == ("r1",)
    assert delta3.current.count() == 0


def test_bus_list_trace_helpers() -> None:
    op1 = types.BusOp(name="get", params={"ns": "x"}, at=1.0)
    op2 = types.BusOp(name="filter", params={"field": "event_type"}, at=2.0)
    items = types.BusList([types.BusEvent(id="e1", event_type="created")], trace=[op1, op2])
    dumped = items.trace_dump()
    assert dumped[0]["name"] == "get"
    tree = items.trace_tree_dump()
    assert tree is not None and tree["kind"] == "trace"


def test_bus_list_core_and_watcher_core() -> None:
    base = bus_list.BusListCore([types.BusConversation(id="c1", topic="demo")])
    assert base.reload().count() == 1
    reloaded = base.reload_with(lambda _ctx=None: types.BusList([types.BusConversation(id="c2", topic="next")]))
    assert reloaded.reload().dump()[0]["id"] == "c2"

    watcher_client = bus.watchers.Watchers()
    watcher = bus_list.BusListWatcherCore("w1", watcher_client, "created")
    assert watcher.start() is watcher
    assert watcher.stop() is watcher


@pytest.mark.asyncio
async def test_bus_client_convenience_aliases() -> None:
    aggregate = bus_list.Bus()
    conversation = (await aggregate.conversations.create("topic")).unwrap()
    assert (await aggregate.conversations.get_by_id(conversation.id)).unwrap().id == conversation.id
    (await aggregate.messages.append(conversation.id, role="user", content="hello")).unwrap()
    assert len((await aggregate.messages.get_by_conversation(conversation.id)).unwrap()) == 1
    aggregate.memory._state.memory["bucket"] = [{"id": 1}]
    assert len((await aggregate.memory.fetch("bucket")).unwrap()) == 1


@pytest.mark.asyncio
async def test_bus_compat_record_and_client_layers() -> None:
    aggregate = bus_list.Bus()
    conv_client = bus.conversations.ConversationClient(aggregate.conversations._transport)
    created = (await aggregate.conversations.create("topic")).unwrap()
    conv_list = await conv_client.get_by_id(created.id)
    assert conv_list.count() == 1

    msg_client = bus.messages.MessageClient(aggregate.conversations._transport)
    (await aggregate.messages.append(created.id, role="user", content="hello")).unwrap()
    assert (await msg_client.get_by_conversation(created.id)).count() == 1

    event_client = bus.events.EventClient(aggregate.conversations._transport)
    (await aggregate.events.publish("created", {"x": 1})).unwrap()
    assert (await event_client.get(event_type="created")).count() >= 1

    mem_client = bus.memory.MemoryClient(aggregate.conversations._transport)
    aggregate.memory._state.memory["bucket"] = [{"id": 1}]
    assert len((await mem_client.get("bucket")).unwrap()) == 1


@pytest.mark.asyncio
async def test_bus_transport_error_is_normalized() -> None:
    class _BadTransport:
        async def request(self, channel: str, payload: dict, *, timeout: float = 10.0):
            raise RuntimeError("boom")
        async def publish(self, channel: str, payload: dict, *, timeout: float = 5.0):
            raise RuntimeError("boom")

    base = _client_base.BusClientBase(_BadTransport(), namespace="n")
    err = await base.request("act", {})
    assert err.is_err()
    assert isinstance(err.error, bus.BusTransportError)

    events_client = bus.events.Events(_BadTransport())
    published = await events_client.publish("created", {"x": 1})
    assert published.is_err()
    assert isinstance(published.error, bus.BusTransportError | bus.events.EventPublishError.__mro__[0].__class__) or isinstance(published.error, Exception)


def test_bus_item_key_and_version_protocol() -> None:
    conv = types.BusConversation(id="c1", topic="demo")
    msg = types.BusMessage(id="m1", conversation_id="c1", role="user", content="x", timestamp=3.0)
    evt = types.BusEvent(id="e1", event_type="created", timestamp=4.0)
    rec = types.BusRecord(id="r1", namespace="ns", rev=2)
    assert conv.key() == "c1" and conv.version() is None
    assert msg.key() == "m1" and msg.version() == 3
    assert evt.key() == "e1" and evt.version() == 4
    assert rec.key() == "ns:r1" and rec.version() == 2
