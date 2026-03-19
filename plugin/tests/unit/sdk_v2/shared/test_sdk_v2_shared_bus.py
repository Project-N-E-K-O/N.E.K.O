from __future__ import annotations

from dataclasses import dataclass
from dataclasses import fields

import pytest

from plugin.sdk_v2.shared import bus
from plugin.sdk_v2.shared.bus.memory import Memory as PublicMemory
from plugin.sdk_v2.shared.bus.watchers import Watchers as PublicWatchers
from plugin.sdk_v2.shared.bus.conversations import Conversations as PublicConversations
from plugin.sdk_v2.shared.bus.messages import Messages as PublicMessages
from plugin.sdk_v2.shared.bus.records import Records as PublicRecords
from plugin.sdk_v2.shared.bus import bus_list
from plugin.sdk_v2.shared.bus import _changes as public_changes
from plugin.sdk_v2.shared.bus import lifecycle as public_lifecycle
from plugin.sdk_v2.shared.bus import _state as public_bus_state
from plugin.sdk_v2.shared.bus import events as public_events
from plugin.sdk_v2.shared.bus import _client_base
from plugin.sdk_v2.shared.bus import records
from plugin.sdk_v2.shared.bus import rev as shared_rev
from plugin.sdk_v2.shared.bus import watchers
from plugin.sdk_v2.shared.models import Err, Ok
from plugin.sdk_v2.shared.bus import types
from plugin.sdk_v2.shared.models.exceptions import CapabilityUnavailableError, ConversationNotFoundError, InvalidArgumentError, NotFoundError, RecordConflictError, TransportError


def test_bus_exports_exist() -> None:
    for name in bus.__all__:
        assert hasattr(bus, name)


def test_bus_change_listener_register_dispatch_and_unsubscribe() -> None:
    public_changes._BUS_CHANGE_LISTENERS.clear()
    seen: list[tuple[str, str, dict[str, object]]] = []

    def _listener(bus_name: str, op: str, payload: dict[str, object]) -> None:
        seen.append((bus_name, op, payload))
        payload["mutated"] = True

    def _raising_listener(_bus_name: str, _op: str, _payload: dict[str, object]) -> None:
        raise RuntimeError("ignore me")

    unsubscribe = public_changes.register_bus_change_listener(" records ", _listener)
    unsubscribe_raising = public_changes.register_bus_change_listener("records", _raising_listener)
    public_changes.register_bus_change_listener("   ", _listener)
    public_changes.register_bus_change_listener("records", object())  # type: ignore[arg-type]

    original_delta = {"id": "r1"}
    public_changes.dispatch_bus_change(sub_id="sub-1", bus="records", op="put", delta=original_delta)
    public_changes.dispatch_bus_change(sub_id="   ", bus="records", op="skip", delta={"id": "r2"})
    public_changes.dispatch_bus_change(sub_id="sub-2", bus="   ", op="skip", delta={"id": "r3"})

    assert seen == [("records", "put", {"id": "r1", "mutated": True})]
    assert original_delta == {"id": "r1"}

    unsubscribe()
    unsubscribe()
    unsubscribe_raising()
    public_changes.dispatch_bus_change(sub_id="sub-3", bus="records", op="delete", delta={"id": "r1"})
    assert seen == [("records", "put", {"id": "r1", "mutated": True})]
    assert public_changes._BUS_CHANGE_LISTENERS == {}


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
    assert (await base.request("act", object())).is_err()
    assert (await base.request("act", {}, timeout="10")).is_err()

    class _Transport:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict, float]] = []

        async def request(self, channel: str, payload: dict, *, timeout: float = 10.0):
            self.calls.append((channel, payload, timeout))
            return Ok({"ok": True})

    transport = _Transport()
    base = _client_base.BusClientBase(transport, namespace="n")
    sent = await base.request("  act  ", {})
    assert sent.is_ok()
    assert transport.calls == [("bus.n.act", {}, 10.0)]

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

    assert (await life.emit("startup", {"success": True})).is_ok()
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


@pytest.mark.asyncio
async def test_shared_bus_client_and_facade_validation_edges() -> None:
    base = _client_base.BusClientBase(namespace="n")
    assert (await base.request(object(), {})).is_err()  # type: ignore[arg-type]
    assert (await base.request("", {})).is_err()
    assert (await base.request("   ", {})).is_err()

    conv = bus.conversations.Conversations()
    assert (await conv.list(limit=0)).is_err()
    assert (await conv.get("")).is_err()
    assert (await conv.create("")).is_err()
    assert (await conv.delete("")).is_err()

    events_client = bus.events.Events()
    assert (await events_client.publish("", {})).is_err()
    assert (await events_client.list(limit=0)).is_err()


@pytest.mark.asyncio
async def test_public_watchers_watch_rejects_empty_channel() -> None:
    watcher_client = PublicWatchers()
    watched = await watcher_client.watch("   ", handler=lambda _event: None)
    assert watched.is_err()
    assert isinstance(watched.error, InvalidArgumentError)


@pytest.mark.asyncio
async def test_public_memory_get_normalizes_scalar_items() -> None:
    aggregate = bus_list.Bus()
    aggregate.memory._state.memory["bucket"] = [{"id": 1}, "hello", 2]
    items = (await aggregate.memory.get("bucket", limit=2)).unwrap()
    assert items == [{"id": 1}, {"value": "hello"}]


@pytest.mark.asyncio
async def test_public_memory_get_clamps_negative_limit() -> None:
    memory = PublicMemory()
    memory._state.memory["bucket"] = [{"id": 1}, {"id": 2}]
    result = await memory.get("bucket", limit=-1)
    assert result.is_err()


@pytest.mark.asyncio
async def test_public_memory_query_prefers_expected_text_fields() -> None:
    memory = PublicMemory()
    memory._state.memory["bucket"] = [
        {"id": 1, "nested": {"text": "hello"}},
        {"id": 2, "text": "hello world"},
        type("_WithText", (), {"text": "HELLO attr"})(),
    ]

    matches = (await memory.query("bucket", "hello")).unwrap()
    assert matches[0] == {"id": 2, "text": "hello world"}
    assert getattr(matches[1], "text", None) == "HELLO attr"
    assert matches[1] is not memory._state.memory["bucket"][2]


def test_public_memory_query_texts_ignore_bytes_and_keep_strings() -> None:
    assert PublicMemory._query_texts("hello") == ["hello"]
    assert PublicMemory._query_texts(b"hello") == []


def test_public_memory_query_texts_supports_dataclass_fallback() -> None:
    @dataclass
    class _Data:
        value: int

    assert PublicMemory._query_texts(_Data(2)) == ["test_public_memory_query_texts_supports_dataclass_fallback.<locals>._Data(value=2)"]


def test_local_message_cache_tail_handles_non_positive_counts() -> None:
    cache = bus.messages.LocalMessageCache()
    cache._items = [bus.messages.MessageRecord(id="m1", conversation_id="c", role="user", content="x")]

    assert cache.tail(0) == []
    assert cache.tail(-1) == []
    assert [item.id for item in cache.tail(1)] == ["m1"]


@pytest.mark.asyncio
async def test_public_memory_returns_snapshots() -> None:
    memory = PublicMemory()
    original = {"id": 1, "text": "hello", "meta": {"count": 1}}
    memory._state.memory["bucket"] = [original]

    queried = (await memory.query("bucket", "hello")).unwrap()
    fetched = (await memory.get("bucket", limit=1)).unwrap()
    queried[0]["meta"]["count"] = 99
    fetched[0]["meta"]["count"] = 42

    assert memory._state.memory["bucket"][0]["meta"]["count"] == 1


def test_memory_record_from_raw_wraps_bus_record() -> None:
    record = bus.memory.MemoryRecord.from_raw({"id": "m1", "namespace": "bucket", "payload": {"x": 1}, "rev": 2})
    assert record.id == "m1"
    assert record.namespace == "bucket"
    assert record.payload == {"x": 1}
    assert record.rev == 2


@pytest.mark.asyncio
async def test_public_watchers_watch_accepts_reserved_args() -> None:
    watcher_client = PublicWatchers()
    watched = await watcher_client.watch(
        "created",
        handler=lambda _event: None,
        options={"debounce_ms": 10},
        timeout=0.1,
    )
    assert watched.is_ok()


@pytest.mark.asyncio
async def test_public_conversations_and_messages_reject_bad_metadata() -> None:
    conversations = PublicConversations()
    invalid_conversation = await conversations.create("topic", metadata=object())  # type: ignore[arg-type]
    assert invalid_conversation.is_err()
    assert isinstance(invalid_conversation.error, InvalidArgumentError)

    created = (await conversations.create("topic")).unwrap()
    messages = PublicMessages()
    messages._state = conversations._state  # type: ignore[attr-defined]
    appended = await messages.append(created.id, role="user", content="hello", metadata={"x": 1})
    assert appended.is_ok()
    assert appended.unwrap().metadata == {"x": 1}
    invalid_message = await messages.append(created.id, role="user", content="hello", metadata=object())  # type: ignore[arg-type]
    assert invalid_message.is_err()
    assert isinstance(invalid_message.error, InvalidArgumentError)


@pytest.mark.asyncio
async def test_public_bus_state_and_event_watcher_edge_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = public_bus_state.ensure_transport(None)
    requested = await transport.request("bus.events.created", {})
    assert requested.is_err()
    assert isinstance(requested.error, CapabilityUnavailableError)
    assert (await transport.publish("bus.events.created", {})).is_ok()

    class _TransportNoAttrs:
        __slots__ = ()

    stateless_transport = _TransportNoAttrs()
    state = public_bus_state.ensure_state(stateless_transport)
    assert state.next_id("custom") == "custom:1"
    assert public_bus_state.ensure_state(stateless_transport) is not state

    client = public_events.Events()
    logged: list[tuple[object, ...]] = []
    monkeypatch.setattr(public_events.logger, "exception", lambda *args, **kwargs: logged.append(args))
    client._state.watchers["watcher:broken"] = public_bus_state._Watcher(  # type: ignore[attr-defined]
        id="watcher:broken",
        channel="created",
        handler=lambda _event: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    published = await client.publish("created", {"x": 1})
    assert published.is_ok()
    assert client._state.watchers["watcher:broken"].queue[0].event_type == "created"
    assert len(logged) == 1
    assert logged[0][0] == "event watcher handler failed handler=%r event_type=%s item=%r error=%s"
    assert logged[0][2] == "created"
    assert logged[0][3].event_type == "created"
    assert str(logged[0][4]) == "boom"

    class _BadTransport:
        async def publish(self, channel: str, payload: dict, *, timeout: float = 5.0):
            return Err(RuntimeError("boom"))

    failed = await public_events.Events(_BadTransport()).publish("created", {"x": 1})
    assert failed.is_err()
    assert isinstance(failed.error, bus.events.EventPublishError)
    assert "bus.events.created" in str(failed.error)
    assert "timeout=5.0" in str(failed.error)
    assert "boom" in str(failed.error)

    negative_limit = await client.list(limit=-1)
    assert negative_limit.is_err()
    assert isinstance(negative_limit.error, InvalidArgumentError)
    assert "limit must be" in str(negative_limit.error)


@pytest.mark.asyncio
async def test_public_events_publish_isolated_from_watcher_dict_reentry() -> None:
    client = public_events.Events()
    seen: list[str] = []

    async def _mutating_handler(event):
        seen.append(event.id)
        client._state.watchers.pop("watcher:mutating", None)
        client._state.watchers["watcher:new"] = public_bus_state._Watcher(
            id="watcher:new",
            channel="created",
            handler=lambda _event: None,
        )

    client._state.watchers["watcher:mutating"] = public_bus_state._Watcher(
        id="watcher:mutating",
        channel="created",
        handler=_mutating_handler,
    )

    published = await client.publish("created", {"x": 1})

    assert published.is_ok()
    assert seen == [published.unwrap().id]
    assert "watcher:new" in client._state.watchers


@pytest.mark.asyncio
async def test_public_events_publish_prunes_old_items_by_max_events() -> None:
    client = public_events.Events()
    client.MAX_EVENTS = 2

    first = (await client.publish("created", {"x": 1})).unwrap()
    second = (await client.publish("created", {"x": 2})).unwrap()
    third = (await client.publish("created", {"x": 3})).unwrap()

    listed = await client.list()

    assert listed.is_ok()
    assert [item.id for item in listed.unwrap()] == [second.id, third.id]
    assert first.id not in {item.id for item in listed.unwrap()}


@pytest.mark.asyncio
async def test_public_watchers_poll_missing_and_mismatched_watcher() -> None:
    watcher_client = PublicWatchers()
    watcher_id = (await watcher_client.watch("created", handler=lambda _event: None)).unwrap()

    missing = await watcher_client.poll("created", watcher_id="missing")
    mismatched = await watcher_client.poll("other", watcher_id=watcher_id)

    assert missing.unwrap() == []
    assert mismatched.unwrap() == []


@pytest.mark.asyncio
async def test_public_records_ignore_watcher_queue_failures() -> None:
    records_client = PublicRecords()

    class _BrokenWatcher:
        channel = "records:ns"
        queue = None

    records_client._state.watchers["broken"] = _BrokenWatcher()  # type: ignore[attr-defined]

    created = await records_client.put("ns", "id", {"x": 1})
    deleted = await records_client.delete("ns", "id")

    assert created.is_ok()
    assert deleted.unwrap() is True


@pytest.mark.asyncio
async def test_lifecycle_client_rejects_negative_max_count() -> None:
    client = bus.lifecycle.LifecycleClient()

    with pytest.raises(ValueError, match="max_count must be >= 0"):
        await client.get(max_count=-1)


@pytest.mark.asyncio
async def test_shared_lifecycle_emit_and_client_filtering() -> None:
    aggregate = bus_list.Bus()

    invalid = await aggregate.lifecycle.emit("   ")
    assert invalid.is_err()

    assert (await aggregate.lifecycle.emit("startup", {"ok": True})).is_ok()
    assert (await aggregate.events.publish("created", {"x": 1})).is_ok()
    aggregate.lifecycle._state.events.append(types.BusEvent(id="e2", event_type="lifecycle:shutdown", payload={"ok": False}, timestamp=2.0))

    listed = await bus.lifecycle.LifecycleClient(aggregate.lifecycle._transport).get(max_count=1)
    assert listed.count() == 1
    assert next(iter(listed)).event_type.startswith("lifecycle:")


@pytest.mark.asyncio
async def test_message_client_get_message_plane_all_uses_public_api() -> None:
    aggregate = bus_list.Bus()
    conv = (await aggregate.conversations.create("topic")).unwrap()
    appended = (await aggregate.messages.append(conv.id, role="user", content="hello")).unwrap()
    client = bus.messages.MessageClient(aggregate.messages._transport)

    listed = await client.get_message_plane_all(max_count=10)

    assert listed.count() == 1
    first = next(iter(listed))
    assert first.id == appended.id


@pytest.mark.asyncio
async def test_message_facade_list_all_and_cursor_paging() -> None:
    aggregate = bus_list.Bus()
    conv = (await aggregate.conversations.create("topic")).unwrap()
    first = (await aggregate.messages.append(conv.id, role="user", content="one")).unwrap()
    second = (await aggregate.messages.append(conv.id, role="assistant", content="two")).unwrap()
    third = (await aggregate.messages.append(conv.id, role="user", content="three")).unwrap()

    listed_all = await aggregate.messages.list_all(limit=2)
    paged = await aggregate.messages.list(conv.id, limit=2, cursor=first.id)
    invalid = await aggregate.messages.list_all(limit=0)

    assert [item.id for item in listed_all.unwrap()] == [first.id, second.id]
    assert [item.id for item in paged.unwrap()] == [second.id, third.id]
    assert invalid.is_err()
    assert isinstance(invalid.error, bus.messages.MessageValidationError)


@pytest.mark.asyncio
async def test_message_facade_preserves_impl_error_types_and_metadata_validation() -> None:
    aggregate = bus_list.Bus()
    missing_message = await aggregate.messages.get("missing")
    missing_conversation = await aggregate.messages.append("missing", role="user", content="hello")
    invalid_metadata = await aggregate.messages.append("missing", role="user", content="hello", metadata=object())  # type: ignore[arg-type]

    assert missing_message.is_err()
    assert isinstance(missing_message.error, NotFoundError)
    assert missing_conversation.is_err()
    assert isinstance(missing_conversation.error, ConversationNotFoundError)
    assert invalid_metadata.is_err()
    assert isinstance(invalid_metadata.error, ConversationNotFoundError)

    conv = (await aggregate.conversations.create("topic")).unwrap()
    bad_metadata = await aggregate.messages.append(conv.id, role="user", content="hello", metadata=object())  # type: ignore[arg-type]
    assert bad_metadata.is_err()
    assert isinstance(bad_metadata.error, InvalidArgumentError)


@pytest.mark.asyncio
async def test_event_client_get_propagates_list_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = bus.events.EventClient()

    async def _fail(*args, **kwargs):
        return Err(bus.BusTransportError("boom", op_name="bus.events.list", namespace="events", channel="bus.events.list"))

    monkeypatch.setattr(client._impl, "list", _fail)

    with pytest.raises(bus.BusTransportError, match="boom"):
        await client.get(event_type="created")

    async def _fail_string(*args, **kwargs):
        return Err("boom")

    monkeypatch.setattr(client._impl, "list", _fail_string)

    with pytest.raises(RuntimeError, match="boom"):
        await client.get(event_type="created")


def test_shared_revision_module_exports_only_revision() -> None:
    assert shared_rev.__all__ == ["Revision"]


def test_bus_facade_int_validators_reject_bools() -> None:
    assert bus.messages.Messages()._require_positive_int("limit", True).is_err()
    assert bus.messages.Messages()._require_int("count", False).is_err()


def test_bus_record_helpers_and_bus_list() -> None:
    conv = types.BusConversation.from_raw({"id": "c1", "topic": "demo", "metadata": {"x": 1}})
    msg = types.BusMessage.from_index({"id": "m1", "conversation_id": "c1", "role": "user"}, {"content": "hello"})
    evt = types.BusEvent.from_raw({"id": "e1", "event_type": "created", "payload": {"x": 1}})
    rec = types.BusRecord.from_index({"id": "r1", "namespace": "ns", "rev": 2}, {"payload": {"x": 1}})
    assert conv.dump()["topic"] == "demo"
    assert msg.dump()["content"] == "hello"
    assert evt.dump()["event_type"] == "created"
    assert rec.dump()["rev"] == 2

    # BusOp, GetNode, UnaryNode, BinaryNode, trace, explain removed in refactor

    items = types.BusList(items=[conv, types.BusConversation(id="c2", topic="other")])
    assert items.count() == 2
    assert len(items) == 2
    assert items.limit(1).count() == 1
    assert items.where(lambda item: item.topic == "demo").count() == 1
    assert items.merge(types.BusList(items=[types.BusConversation(id="c3", topic="z")])).count() == 3
    assert items.sorted(key=lambda item: item.id).dump()[0]["id"] == "c1"


def test_bus_from_raw_tolerates_malformed_nested_payloads() -> None:
    conv = types.BusConversation.from_raw({"id": "c1", "topic": "demo", "metadata": "bad"})
    msg = types.BusMessage.from_raw(
        {"id": "m1", "conversation_id": "c1", "role": "user", "metadata": 1, "timestamp": "bad"}
    )
    evt = types.BusEvent.from_raw({"id": "e1", "event_type": "created", "payload": 1, "timestamp": "bad"})
    rec = types.BusRecord.from_raw({"id": "r1", "namespace": "ns", "payload": 1, "rev": "bad"})

    assert conv.metadata == {}
    assert msg.metadata == {}
    assert msg.timestamp is None
    assert evt.payload == {}
    assert evt.timestamp is None
    assert rec.payload == {}
    assert rec.rev == 0


def test_parse_iso_timestamp() -> None:
    assert records.parse_iso_timestamp(None) is None
    assert records.parse_iso_timestamp(1) == 1.0
    assert records.parse_iso_timestamp("2024-01-01T00:00:00Z") is not None
    assert records.parse_iso_timestamp("bad") is None


def test_parse_iso_timestamp_additional_edges() -> None:
    assert records.parse_iso_timestamp(object()) is None
    assert records.parse_iso_timestamp("   ") is None
    assert records.parse_iso_timestamp("2024-01-01T00:00:00") == records.parse_iso_timestamp("2024-01-01T00:00:00+00:00")


def test_bus_list_basic_filters_and_subscriptions() -> None:
    """Simplified: where_eq/where_in/where_regex/intersection/difference/try_filter removed."""
    items = types.BusList(items=[
        types.BusEvent(id="e1", event_type="created", payload={"x": 1}, timestamp=1.0),
        types.BusEvent(id="e2", event_type="updated", payload={"x": 2}, timestamp=2.0),
    ])
    assert items.filter(lambda item: item.event_type == "created").count() == 1
    assert items.where(lambda item: item.event_type == "updated").count() == 1
    assert items.sorted(key=lambda item: item.timestamp, reverse=True).dump()[0]["id"] == "e2"
    assert items.limit(1).count() == 1
    assert items.merge(types.BusList(items=[types.BusEvent(id="e3", event_type="deleted")])).count() == 3

    watcher_client = bus.watchers.Watchers()
    watcher = watchers.BusListWatcher("w1", watcher_client, "created")
    assert watchers.list_subscription(watcher) is watcher


def test_bus_list_watcher_core_helper_paths() -> None:
    class _WithId:
        id = "evt-1"

    class _BrokenVersion:
        def version(self) -> int:
            raise RuntimeError("boom")

    class _WithRev:
        rev = 2

    assert watchers.BusListWatcherCore._item_key(_WithId()) == "evt-1"
    assert watchers.BusListWatcherCore._item_version(_BrokenVersion()) is None
    assert watchers.BusListWatcherCore._item_version(_WithRev()) == 2


# test_bus_list_reload_and_watch_helpers removed: reload, reload_with, watch removed from BusList


# test_bus_list_reload_preserves_transform_chain removed: reload_with, where_eq, reload, trace removed
# _AwaitableReload helper class removed


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
    watcher.stop()
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
async def test_shared_records_facade_validation_paths() -> None:
    client = records.Records()

    assert (await client.list("", limit=1)).is_err()
    assert (await client.list("ns", limit=0)).is_err()

    bad_get_namespace = await client.get("", "id")
    assert bad_get_namespace.is_err()
    assert isinstance(bad_get_namespace.error, RecordConflictError)

    bad_get_record = await client.get("ns", "")
    assert bad_get_record.is_err()
    assert isinstance(bad_get_record.error, RecordConflictError)

    bad_put_namespace = await client.put("", "id", {})
    assert bad_put_namespace.is_err()
    assert isinstance(bad_put_namespace.error, RecordConflictError)

    bad_put_record = await client.put("ns", "", {})
    assert bad_put_record.is_err()
    assert isinstance(bad_put_record.error, RecordConflictError)

    bad_delete_namespace = await client.delete("", "id")
    assert bad_delete_namespace.is_err()
    assert isinstance(bad_delete_namespace.error, RecordConflictError)

    bad_delete_record = await client.delete("ns", "")
    assert bad_delete_record.is_err()
    assert isinstance(bad_delete_record.error, RecordConflictError)


@pytest.mark.asyncio
async def test_bus_list_watcher_core_error_and_callback_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    class _State:
        def __init__(self, watchers_value: object) -> None:
            self.watchers = watchers_value

    class _WatcherClient:
        def __init__(self, result: object, watchers_value: object = None) -> None:
            self._result = result
            self._state = _State(watchers_value)

        def _is_ok(self, result: object) -> bool:
            return bool(getattr(result, "is_ok", lambda: False)())

        async def poll(self, channel: str, *, watcher_id: str | None = None, timeout: float = 5.0):
            return self._result

    watcher_auto = watchers.BusListWatcherCore(
        "w-auto",
        _WatcherClient(Ok([]), watchers_value=[]),
        "created",
        auto_register=True,
    ).start()
    assert watcher_auto._registered is False

    transport_error = TransportError("boom")
    error_result = Err(transport_error)
    failing = watchers.BusListWatcherCore("w-err", _WatcherClient(error_result, watchers_value={}), "created", auto_register=True).start()
    assert await failing.poll() is error_result
    assert await failing.poll_delta() is error_result

    debug_messages: list[str] = []
    exception_messages: list[str] = []
    monkeypatch.setattr(watchers.logger, "debug", lambda message, *args, **kwargs: debug_messages.append(message))
    monkeypatch.setattr(watchers.logger, "exception", lambda message, *args, **kwargs: exception_messages.append(message))

    seen_callbacks: list[str] = []

    async def _async_callback(event: types.BusEvent) -> None:
        seen_callbacks.append(f"event:{event.id}")

    def _bad_callback(event: types.BusEvent) -> None:
        raise RuntimeError(f"bad:{event.id}")

    event_result = Ok([types.BusEvent(id="evt-1", event_type="created", timestamp=1.0)])
    started = watchers.BusListWatcherCore("w-ok", _WatcherClient(event_result, watchers_value={}), "created").subscribe(_async_callback).subscribe(_bad_callback).start()
    polled = await started.poll()
    assert polled.is_ok()
    assert seen_callbacks == ["event:evt-1"]
    assert debug_messages == ["watcher callback failed callback=%r event=%r error=%s"]

    changed_result = Ok([types.BusEvent(id="evt-1", event_type="created", timestamp=2.0)])
    changed = watchers.BusListWatcherCore(
        "w-change",
        _WatcherClient(changed_result, watchers_value={}),
        "created",
        current_items=[types.BusEvent(id="evt-1", event_type="created", timestamp=1.0)],
        seen={"evt-1": 1.0},
    )
    changed_delta = (await changed.poll_delta()).unwrap()
    assert changed_delta.kind == "change"
    assert [item.id for item in changed_delta.changed] == ["evt-1"]

    delta_seen: list[str] = []

    async def _async_delta(delta: watchers.BusListDelta) -> None:
        delta_seen.append(delta.kind)

    def _bad_delta(delta: watchers.BusListDelta) -> None:
        raise RuntimeError(delta.kind)

    delta_result = Ok([types.BusEvent(id="evt-2", event_type="created", timestamp=3.0)])
    delta_watcher = watchers.BusListWatcherCore("w-delta", _WatcherClient(delta_result, watchers_value={}), "created").subscribe_delta(_async_delta).subscribe_delta(_bad_delta).start()
    delta = (await delta_watcher.poll_delta()).unwrap()
    assert delta.kind == "append"
    assert delta_seen == ["append"]
    assert exception_messages == ["watcher delta callback failed callback=%r delta=%r"]


@pytest.mark.asyncio
async def test_bus_watchers_poll_is_isolated_per_watcher() -> None:
    aggregate = bus_list.Bus()
    watcher_id_1 = (await aggregate.watchers.watch("created", handler=lambda _event: None)).unwrap()
    watcher_id_2 = (await aggregate.watchers.watch("created", handler=lambda _event: None)).unwrap()
    watcher_1 = watchers.BusListWatcher(watcher_id_1, aggregate.watchers, "created").start()
    watcher_2 = watchers.BusListWatcher(watcher_id_2, aggregate.watchers, "created").start()

    published = (await aggregate.events.publish("created", {"x": 1})).unwrap()

    polled_1 = (await watcher_1.poll()).unwrap()
    polled_2 = (await watcher_2.poll()).unwrap()

    assert [event.id for event in polled_1] == [published.id]
    assert [event.id for event in polled_2] == [published.id]


@pytest.mark.asyncio
async def test_records_change_for_unseen_key_is_classified_as_add() -> None:
    aggregate = bus_list.Bus()
    (await aggregate.records.put("ns", "r1", {"x": 1})).unwrap()
    watcher_id = (await aggregate.watchers.watch("records:ns", handler=lambda _event: None)).unwrap()
    watcher = watchers.BusListWatcher(watcher_id, aggregate.watchers, "records:ns").start()

    changed = (await aggregate.records.put("ns", "r1", {"x": 2})).unwrap()
    delta = (await watcher.poll_delta()).unwrap()

    assert delta.kind == "append"
    assert [item.rev for item in delta.added] == [changed.rev]
    assert delta.changed == ()


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
    assert delta3.removed == ("ns:r1",)
    assert delta3.current.count() == 0


# test_bus_list_trace_helpers removed: BusOp, trace, trace_dump, trace_tree_dump removed from BusList


def test_bus_list_core_and_watcher_core() -> None:
    base = bus_list.BusListCore([types.BusConversation(id="c1", topic="demo")])
    assert base.count() == 1

    watcher_client = bus.watchers.Watchers()
    watcher = bus_list.BusListWatcherCore("w1", watcher_client, "created")
    assert watcher.start() is watcher
    assert watcher.stop() is watcher


@pytest.mark.asyncio
async def test_bus_client_primary_methods() -> None:
    aggregate = bus_list.Bus()
    conversation = (await aggregate.conversations.create("topic")).unwrap()
    assert (await aggregate.conversations.get(conversation.id)).unwrap().id == conversation.id
    (await aggregate.messages.append(conversation.id, role="user", content="hello")).unwrap()
    assert len((await aggregate.messages.get_by_conversation(conversation.id)).unwrap()) == 1
    aggregate.memory._state.memory["bucket"] = [{"id": 1}]
    assert len((await aggregate.memory.get("bucket")).unwrap()) == 1


@pytest.mark.asyncio
async def test_shared_memory_and_message_validation_paths() -> None:
    aggregate = bus_list.Bus()

    assert (await aggregate.memory.query("", "hello")).is_err()
    assert (await aggregate.memory.query("bucket", "")).is_err()
    assert (await aggregate.memory.get("", limit=1)).is_err()
    assert (await aggregate.memory.get("bucket", limit=0)).is_err()

    assert (await aggregate.messages.list("", limit=1)).is_err()
    assert (await aggregate.messages.list("c1", limit=0)).is_err()
    assert (await aggregate.messages.get("")).is_err()
    assert (await aggregate.messages.append("", role="user", content="x")).is_err()
    assert (await aggregate.messages.append("c1", role=" ", content="x")).is_err()
    assert (await aggregate.messages.delete("")).is_err()

    conv = (await aggregate.conversations.create("topic")).unwrap()
    appended = await aggregate.messages.append(conv.id, role="user", content="hello")
    assert appended.is_ok()
    by_conv = await aggregate.messages.get_by_conversation(conv.id, limit=1)
    assert by_conv.is_ok()
    assert by_conv.unwrap()[0].content == "hello"


@pytest.mark.asyncio
async def test_bus_compat_record_and_client_layers() -> None:
    aggregate = bus_list.Bus()
    conv_client = bus.conversations.ConversationClient(aggregate.conversations._transport)
    created = (await aggregate.conversations.create("topic")).unwrap()
    conv_list = await conv_client.get(conversation_id=created.id)
    assert conv_list.count() == 1

    msg_client = bus.messages.MessageClient(aggregate.conversations._transport)
    (await aggregate.messages.append(created.id, role="user", content="hello")).unwrap()
    assert (await msg_client.get_by_conversation(created.id)).count() == 1

    event_client = bus.events.EventClient(aggregate.conversations._transport)
    (await aggregate.events.publish("created", {"x": 1})).unwrap()
    assert (await event_client.get(event_type="created")).count() >= 1

    mem_client = bus.memory.BusMemoryClient(aggregate.conversations._transport)
    aggregate.memory._state.memory["bucket"] = [{"id": 1}]
    assert len((await mem_client.get("bucket")).unwrap()) == 1


@pytest.mark.asyncio
async def test_conversation_client_empty_id_and_record_put_invalid_payload() -> None:
    aggregate = bus_list.Bus()
    conv_client = bus.conversations.ConversationClient(aggregate.conversations._transport)

    with pytest.raises(ConversationNotFoundError):
        await conv_client.get(conversation_id="")

    invalid_put = await aggregate.records.put("ns", "id", object())  # type: ignore[arg-type]
    assert invalid_put.is_err()
    assert isinstance(invalid_put.error, RecordConflictError)

    invalid_create = await aggregate.conversations.create("topic", metadata=object())  # type: ignore[arg-type]
    assert invalid_create.is_err()
    assert isinstance(invalid_create.error, InvalidArgumentError)


@pytest.mark.asyncio
async def test_conversations_list_honors_cursor_and_timestamp_bool_is_ignored() -> None:
    aggregate = bus_list.Bus()
    first = (await aggregate.conversations.create("topic-1")).unwrap()
    second = (await aggregate.conversations.create("topic-2")).unwrap()
    paged = await aggregate.conversations.list(limit=1, cursor=first.id)
    assert paged.is_ok()
    assert [item.id for item in paged.unwrap()] == [second.id]

    assert records.parse_iso_timestamp(True) is None
    assert records.parse_iso_timestamp(False) is None


@pytest.mark.asyncio
async def test_watchers_facade_validation_and_start_subscription() -> None:
    client = watchers.Watchers()

    invalid_channel = await client.watch("", handler=lambda _event: None)
    assert invalid_channel.is_err()
    assert isinstance(invalid_channel.error, InvalidArgumentError)

    invalid_watch = await client.watch("created", handler=object())  # type: ignore[arg-type]
    assert invalid_watch.is_err()
    assert isinstance(invalid_watch.error, InvalidArgumentError)

    invalid_unwatch = await client.unwatch("")
    assert invalid_unwatch.is_err()
    assert isinstance(invalid_unwatch.error, InvalidArgumentError)

    invalid_poll = await client.poll("")
    assert invalid_poll.is_err()
    assert isinstance(invalid_poll.error, InvalidArgumentError)

    watcher = watchers.BusListWatcher("w-start", client, "created")
    assert watchers.list_subscription(watcher, on="start")._started is True


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
    assert "bus.n.act" in str(err.error) or err.error.context.get("op_name") == "bus.n.act"

    events_client = bus.events.Events(_BadTransport())
    published = await events_client.publish("created", {"x": 1})
    assert published.is_err()
    assert isinstance(published.error, (bus.BusTransportError, bus.events.EventPublishError))

    class _CapUnavailableTransport:
        async def request(self, channel: str, payload: dict, *, timeout: float = 10.0):
            return Err(
                CapabilityUnavailableError(
                    "missing request capability",
                    op_name="bus.request",
                    capability="transport.request",
                    namespace="n",
                    channel=channel,
                    action="act",
                    timeout=timeout,
                )
            )

        async def publish(self, channel: str, payload: dict, *, timeout: float = 5.0):
            return Err(
                CapabilityUnavailableError(
                    "missing publish capability",
                    op_name="bus.publish",
                    capability="transport.publish",
                    namespace="events",
                    channel=channel,
                    timeout=timeout,
                )
            )

    cap_base = _client_base.BusClientBase(_CapUnavailableTransport(), namespace="n")
    cap_err = await cap_base.request("act", {})
    assert cap_err.is_err()
    assert isinstance(cap_err.error, CapabilityUnavailableError)
    assert cap_err.error.context["op_name"] == "bus.request"
    assert cap_err.error.namespace == "n"
    assert cap_err.error.channel == "bus.n.act"
    assert cap_err.error.action == "act"

    cap_events = bus.events.Events(_CapUnavailableTransport())
    cap_published = await cap_events.publish("created", {"x": 1})
    assert cap_published.is_err()
    assert isinstance(cap_published.error, CapabilityUnavailableError)


@pytest.mark.asyncio
async def test_message_client_runtimeerror_branches() -> None:
    client = bus.messages.MessageClient()

    async def _string_err(*args, **kwargs):
        return Err("boom")

    client._impl.list = _string_err  # type: ignore[method-assign]
    client._impl.list_all = _string_err  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="boom"):
        await client.get(conversation_id="c1")
    with pytest.raises(RuntimeError, match="boom"):
        await client.get_message_plane_all()


@pytest.mark.asyncio
async def test_message_client_exception_branches_and_local_cache_on_delta() -> None:
    client = bus.messages.MessageClient()

    async def _exception_err(*args, **kwargs):
        return Err(InvalidArgumentError("bad"))

    client._impl.list = _exception_err  # type: ignore[method-assign]
    client._impl.list_all = _exception_err  # type: ignore[method-assign]

    with pytest.raises(InvalidArgumentError, match="bad"):
        await client.get(conversation_id="c1")
    with pytest.raises(InvalidArgumentError, match="bad"):
        await client.get_message_plane_all()


@pytest.mark.asyncio
async def test_conversation_client_runtimeerror_branch() -> None:
    client = bus.conversations.ConversationClient()

    async def _string_err(*args, **kwargs):
        return Err("boom")

    client._impl.list = _string_err  # type: ignore[method-assign]
    client._impl.get = _string_err  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="boom"):
        await client.get()

    with pytest.raises(RuntimeError, match="boom"):
        await client.get(conversation_id="c1")


@pytest.mark.asyncio
async def test_conversation_client_list_success_branch() -> None:
    aggregate = bus_list.Bus()
    created = (await aggregate.conversations.create("topic")).unwrap()
    client = bus.conversations.ConversationClient(aggregate.conversations._transport)

    listed = await client.get()

    assert listed.count() == 1
    assert next(iter(listed)).id == created.id

    cache = bus.messages.LocalMessageCache()
    delta = type(
        "_Delta",
        (),
        {
            "current": type(
                "_Current",
                (),
                {
                    "items": [bus.messages.MessageRecord(id="m1", conversation_id="c1", role="user", content="hello")],
                },
            )(),
        },
    )()
    cache.on_delta(delta)
    assert [item.id for item in cache.tail(1)] == ["m1"]


def test_public_bus_rejects_extra_constructor_args() -> None:
    with pytest.raises(TypeError):
        bus_list.Bus(None, "extra")
    with pytest.raises(TypeError):
        bus_list.Bus(None, debug=True)


def test_public_bus_aggregate_uses_shared_transport_and_state() -> None:
    aggregate = bus_list.Bus()

    assert aggregate.messages._transport is aggregate.conversations._transport
    assert aggregate.events._transport is aggregate.conversations._transport

    state = aggregate.conversations._state
    state.revisions[("ns", "r1")] = 2

    rev = aggregate.revision
    assert rev._state is state


@pytest.mark.asyncio
async def test_shared_revision_validates_inputs() -> None:
    revision = shared_rev.Revision()

    assert (await revision.get("", "id")).is_err()
    assert (await revision.get("ns", "")).is_err()
    assert (await revision.compare("", "id", 1)).is_err()
    assert (await revision.compare("ns", "", 1)).is_err()
    assert (await revision.compare("ns", "id", True)).is_err()


@pytest.mark.asyncio
async def test_public_lifecycle_emit_normalizes_errors() -> None:
    class _BadTransport:
        async def request(self, channel: str, payload: dict, *, timeout: float = 10.0):
            raise RuntimeError("boom")

        async def publish(self, channel: str, payload: dict, *, timeout: float = 5.0):
            raise RuntimeError("boom")

    life = public_lifecycle.Lifecycle(_BadTransport())
    assert (await life.emit("", {})).is_err()
    assert (await life.emit("startup", {}, timeout=0)).is_err()
    assert (await life.emit("startup", {}, timeout="5")).is_err()
    assert (await life.emit("startup", object())).is_err()
    emitted = await life.emit("startup", {"ok": True})
    assert emitted.is_err()
    assert isinstance(emitted.error, TransportError)


@pytest.mark.asyncio
async def test_public_lifecycle_emit_preserves_known_and_wraps_unknown_errors() -> None:
    class _TransportReturningKnownErr:
        async def request(self, channel: str, payload: dict, *, timeout: float = 10.0):
            return Ok({})

        async def publish(self, channel: str, payload: dict, *, timeout: float = 5.0):
            return Err(InvalidArgumentError("known"))

    class _TransportReturningUnknownErr:
        async def request(self, channel: str, payload: dict, *, timeout: float = 10.0):
            return Ok({})

        async def publish(self, channel: str, payload: dict, *, timeout: float = 5.0):
            return Err("boom")

    class _TransportReturningRuntimeErr:
        async def request(self, channel: str, payload: dict, *, timeout: float = 10.0):
            return Ok({})

        async def publish(self, channel: str, payload: dict, *, timeout: float = 5.0):
            return Err(RuntimeError("runtime"))

    class _TransportRaisingKnownErr:
        async def request(self, channel: str, payload: dict, *, timeout: float = 10.0):
            return Ok({})

        async def publish(self, channel: str, payload: dict, *, timeout: float = 5.0):
            raise InvalidArgumentError("raised")

    known = await public_lifecycle.Lifecycle(_TransportReturningKnownErr()).emit("startup", {"ok": True})
    wrapped = await public_lifecycle.Lifecycle(_TransportReturningUnknownErr()).emit("startup", {"ok": True})
    wrapped_runtime = await public_lifecycle.Lifecycle(_TransportReturningRuntimeErr()).emit("startup", {"ok": True})
    raised = await public_lifecycle.Lifecycle(_TransportRaisingKnownErr()).emit("startup", {"ok": True})

    assert known.is_err()
    assert isinstance(known.error, InvalidArgumentError)
    assert wrapped.is_err()
    assert isinstance(wrapped.error, TransportError)
    assert wrapped.error.channel == "bus.lifecycle.startup"
    assert wrapped_runtime.is_err()
    assert isinstance(wrapped_runtime.error, TransportError)
    assert wrapped_runtime.error.channel == "bus.lifecycle.startup"
    assert raised.is_err()
    assert isinstance(raised.error, InvalidArgumentError)


@pytest.mark.asyncio
async def test_public_lifecycle_emit_trims_stage() -> None:
    class _Transport:
        def __init__(self) -> None:
            self.published: list[tuple[str, dict, float]] = []

        async def request(self, channel: str, payload: dict, *, timeout: float = 10.0):
            return Ok({})

        async def publish(self, channel: str, payload: dict, *, timeout: float = 5.0):
            self.published.append((channel, payload, timeout))
            return Ok(None)

    transport = _Transport()
    life = public_lifecycle.Lifecycle(transport)
    sent = await life.emit("  startup  ", {"ok": True})
    assert sent.is_ok()
    assert transport.published == [
        ("bus.lifecycle.startup", {"stage": "startup", "payload": {"ok": True}}, 5.0)
    ]


def test_bus_item_key_and_version_protocol() -> None:
    conv = types.BusConversation(id="c1", topic="demo")
    msg = types.BusMessage(id="m1", conversation_id="c1", role="user", content="x", timestamp=3.25)
    evt = types.BusEvent(id="e1", event_type="created", timestamp=4.75)
    rec = types.BusRecord(id="r1", namespace="ns", rev=2)
    assert conv.key() == "c1" and conv.version() is None
    assert msg.key() == "m1" and msg.version() == 3.25
    assert evt.key() == "e1" and evt.version() == 4.75
    assert rec.key() == "ns:r1" and rec.version() == 2


# test_bus_query_and_plan_objects removed: BusOp, BusList.query, BusQueryPlan removed
# test_bus_query_watch_wrapper removed: BusList.query, watch removed


def test_shared_bus_types_internal_model_and_trace_branches() -> None:
    from collections.abc import Mapping

    class _BadMapping(Mapping):
        def __getitem__(self, key):
            raise KeyError(key)

        def __iter__(self):
            raise ValueError("boom")

        def __len__(self) -> int:
            return 0

    assert types._as_object_dict(_BadMapping()) == {}
    assert types._as_int(None, default=9) == 9

    assert types.BusConversation.from_raw("bad").dump() == {"id": "", "topic": "", "metadata": {}}
    assert types.BusConversation.from_index({"id": "c1", "topic": "topic"}, {"metadata": {"x": 1}}).dump() == {
        "id": "c1",
        "topic": "topic",
        "metadata": {"x": 1},
    }

    assert types.BusMessage.from_raw("bad").dump() == {
        "id": "",
        "conversation_id": "",
        "role": "",
        "content": None,
        "timestamp": None,
        "metadata": {},
    }
    assert types.BusEvent.from_raw("bad").dump() == {"id": "", "event_type": "", "payload": {}, "timestamp": None}
    assert types.BusEvent.from_index({"id": "e1", "type": "created"}).dump() == {
        "id": "e1",
        "event_type": "created",
        "payload": {},
        "timestamp": None,
    }
    assert types.BusRecord.from_raw("bad").dump() == {"id": "", "namespace": "", "payload": {}, "rev": 0}

    # Removed: GetNode, UnaryNode, BinaryNode, BusOp, fast_mode, explain, trace_tree_dump,
    # sort (mutable), where_gt, _stable_key, intersect, subtract, try_filter, BusQueryPlan

    values = types.BusList(items=[2, 1])
    assert values.dump_records() == [{"value": "2"}, {"value": "1"}]

    # BusList.sorted (immutable) still works
    sorted_values = values.sorted()
    assert sorted_values.items == [1, 2]
    assert values.items == [2, 1]  # original unchanged


# test_shared_bus_types_replay_and_watch_edge_branches removed: _replay_from_factory, query, watch removed
