from __future__ import annotations

from dataclasses import fields

import pytest

from plugin.sdk_v2.shared import bus
from plugin.sdk_v2.shared.bus import _client_base
from plugin.sdk_v2.shared.bus import bus_list
from plugin.sdk_v2.shared.bus import conversations
from plugin.sdk_v2.shared.bus import events
from plugin.sdk_v2.shared.bus import lifecycle
from plugin.sdk_v2.shared.bus import memory
from plugin.sdk_v2.shared.bus import messages
from plugin.sdk_v2.shared.bus import records
from plugin.sdk_v2.shared.bus import rev
from plugin.sdk_v2.shared.bus import types
from plugin.sdk_v2.shared.bus import watchers


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


def test_bus_init_raises() -> None:
    with pytest.raises(NotImplementedError):
        _client_base.BusClientBase(_transport=object(), namespace="n")
    with pytest.raises(NotImplementedError):
        bus_list.Bus()
    with pytest.raises(NotImplementedError):
        conversations.Conversations()
    with pytest.raises(NotImplementedError):
        messages.Messages()
    with pytest.raises(NotImplementedError):
        events.Events()
    with pytest.raises(NotImplementedError):
        lifecycle.Lifecycle()
    with pytest.raises(NotImplementedError):
        memory.Memory()
    with pytest.raises(NotImplementedError):
        records.Records()
    with pytest.raises(NotImplementedError):
        rev.Revision()
    with pytest.raises(NotImplementedError):
        watchers.Watchers()


@pytest.mark.asyncio
async def test_bus_contract_methods_raise() -> None:
    base = object.__new__(_client_base.BusClientBase)
    conv = object.__new__(conversations.Conversations)
    msg = object.__new__(messages.Messages)
    evt = object.__new__(events.Events)
    life = object.__new__(lifecycle.Lifecycle)
    mem = object.__new__(memory.Memory)
    rec = object.__new__(records.Records)
    revision = object.__new__(rev.Revision)
    watch = object.__new__(watchers.Watchers)

    with pytest.raises(NotImplementedError):
        await base.request("act", {})

    with pytest.raises(NotImplementedError):
        await conv.list()
    with pytest.raises(NotImplementedError):
        await conv.get("id")
    with pytest.raises(NotImplementedError):
        await conv.create("topic")
    with pytest.raises(NotImplementedError):
        await conv.delete("id")

    with pytest.raises(NotImplementedError):
        await msg.list("c")
    with pytest.raises(NotImplementedError):
        await msg.get("m")
    with pytest.raises(NotImplementedError):
        await msg.append("c", role="user", content="x")
    with pytest.raises(NotImplementedError):
        await msg.delete("m")

    with pytest.raises(NotImplementedError):
        await evt.publish("created", {})
    with pytest.raises(NotImplementedError):
        await evt.list()

    with pytest.raises(NotImplementedError):
        await life.emit("startup")

    with pytest.raises(NotImplementedError):
        await mem.query("b", "q")
    with pytest.raises(NotImplementedError):
        await mem.get("b")

    with pytest.raises(NotImplementedError):
        await rec.list("ns")
    with pytest.raises(NotImplementedError):
        await rec.get("ns", "id")
    with pytest.raises(NotImplementedError):
        await rec.put("ns", "id", {})
    with pytest.raises(NotImplementedError):
        await rec.delete("ns", "id")

    with pytest.raises(NotImplementedError):
        await revision.get("ns", "id")
    with pytest.raises(NotImplementedError):
        await revision.compare("ns", "id", 1)

    with pytest.raises(NotImplementedError):
        await watch.watch("ch", handler=lambda _event: None)
    with pytest.raises(NotImplementedError):
        await watch.unwatch("wid")
    with pytest.raises(NotImplementedError):
        await watch.poll("ch")
