from __future__ import annotations

from dataclasses import fields

import pytest

from plugin.sdk_v2.shared import bus
from plugin.sdk_v2.shared.bus import _client_base
from plugin.sdk_v2.shared.bus import bus_list
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
