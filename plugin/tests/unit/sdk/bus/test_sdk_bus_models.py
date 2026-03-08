from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugin.sdk.bus.conversations import ConversationClient, ConversationRecord
from plugin.sdk.bus.messages import MessageClient
from plugin.sdk.bus.events import EventRecord
from plugin.sdk.bus.lifecycle import LifecycleRecord
from plugin.sdk.bus.memory import MemoryList, MemoryRecord
from plugin.sdk.bus.messages import MessageRecord
from plugin.sdk.bus.records import BinaryNode, GetNode, UnaryNode, parse_iso_timestamp
from plugin.sdk.bus.types import BusList, BusRecord
from plugin.sdk.bus.watchers import list_subscription_async


@pytest.mark.plugin_unit
def test_bus_record_and_trace_nodes_dump() -> None:
    assert parse_iso_timestamp("2026-01-01T00:00:00Z") is not None

    base = BusRecord(kind="x", type="y", timestamp=1.0, metadata={"a": 1}, raw={"r": 1})
    assert base.dump()["kind"] == "x"

    n1 = GetNode(op="get", params={"bus": "m"}, at=1.0)
    n2 = UnaryNode(op="limit", params={"n": 1}, at=2.0, child=n1)
    n3 = BinaryNode(op="merge", params={}, at=3.0, left=n1, right=n2)
    assert n1.dump()["kind"] == "get"
    assert "->" in n2.explain()
    assert "merge" in n3.explain()


@pytest.mark.plugin_unit
def test_bus_list_operations() -> None:
    r1 = BusRecord(kind="message", type="a", timestamp=1.0, plugin_id="p1", source="s", priority=1, content="hello")
    r2 = BusRecord(kind="message", type="b", timestamp=2.0, plugin_id="p2", source="s", priority=2, content="world")
    lst = BusList([r1, r2])

    assert lst.count() == 2
    assert lst.size() == 2
    assert len(lst.dump()) == 2
    assert len(lst.dump_records()) == 2
    assert lst.where_eq("plugin_id", "p1").count() == 1
    assert lst.where_in("plugin_id", ["p1", "x"]).count() == 1
    assert lst.where_contains("content", "wor").count() == 1
    assert lst.where_regex("content", "^h").count() == 1
    assert lst.where_gt("priority", 1).count() == 1
    assert lst.where_ge("priority", 2).count() == 1
    assert lst.where_lt("priority", 2).count() == 1
    assert lst.where_le("priority", 1).count() == 1
    assert lst.limit(1).count() == 1
    assert lst.sort(by="timestamp").dump()[0]["timestamp"] == 1.0
    assert lst.sorted(by="timestamp", reverse=True).dump()[0]["timestamp"] == 2.0
    assert lst.intersection(BusList([r2])).count() == 1
    assert lst.difference(BusList([r2])).count() == 1
    assert lst.merge(BusList([r1])).count() >= 2


@pytest.mark.plugin_unit
def test_record_converters_dump() -> None:
    m = MessageRecord.from_raw({"plugin_id": "p", "message_id": 1, "message_type": "text", "timestamp": 1})
    assert m.dump()["message_id"] == "1"

    e = EventRecord.from_raw({"plugin_id": "p", "event_id": "e1", "timestamp": 1, "args": {"x": 1}})
    assert e.dump()["event_id"] in {"e1", None}

    lifecycle_record = LifecycleRecord.from_raw({"plugin_id": "p", "lifecycle_id": "l1", "timestamp": 1})
    assert lifecycle_record.dump()["lifecycle_id"] in {"l1", None}

    mr = MemoryRecord.from_raw({"plugin_id": "p", "type": "t", "_ts": 1}, bucket_id="b1")
    assert mr.dump()["bucket_id"] == "b1"

    cr = ConversationRecord.from_raw(
        {
            "plugin_id": "p",
            "timestamp": 1,
            "metadata": {"conversation_id": "c1", "turn_type": "turn_end", "message_count": 2},
        }
    )
    assert cr.conversation_id == "c1"


@pytest.mark.plugin_unit
def test_memory_list_filter_where_limit() -> None:
    items = [
        MemoryRecord.from_raw({"plugin_id": "p1", "content": "a", "_ts": 1}, bucket_id="b"),
        MemoryRecord.from_raw({"plugin_id": "p2", "content": "b", "_ts": 2}, bucket_id="b"),
    ]
    ml = MemoryList(items, bucket_id="b")
    assert ml.where_eq("plugin_id", "p1").count() == 1
    assert ml.limit(1).count() == 1


@pytest.mark.plugin_unit
def test_conversation_client_get_sync_and_by_id(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Rpc:
        def request(self, *, op: str, args: dict[str, object], timeout: float):
            return {
                "ok": True,
                "result": {
                    "items": [
                        {
                            "index": {
                                "timestamp": 1,
                                "plugin_id": "p",
                                "source": "s",
                                "type": "conversation",
                                "id": "c1",
                                "conversation_id": "c1",
                            },
                            "payload": {"metadata": {"turn_type": "turn_end", "message_count": 1}},
                        }
                    ]
                },
            }

    ctx = SimpleNamespace(plugin_id="demo")
    client = ConversationClient(ctx=ctx)
    monkeypatch.setattr("plugin.sdk.bus.conversations._ensure_rpc", lambda _: _Rpc())

    lst = client.get(conversation_id="c1", max_count=10)
    assert lst.count() == 1

    one = client.get_by_id("c1", max_count=10)
    assert one.count() == 1


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_conversation_client_get_by_id_async(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Rpc:
        def request(self, *, op: str, args: dict[str, object], timeout: float):
            return {
                "ok": True,
                "result": {"items": [{"index": {"timestamp": 1, "type": "conversation", "id": "c1"}}]},
            }

    ctx = SimpleNamespace(plugin_id="demo")
    client = ConversationClient(ctx=ctx)
    monkeypatch.setattr("plugin.sdk.bus.conversations._ensure_rpc", lambda _: _Rpc())
    one = await client.get_by_id_async("c1", max_count=10)
    assert one.count() == 1


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_message_client_async_convenience_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    class _AsyncRpc:
        async def request_async(self, *, op: str, args: dict[str, object], timeout: float):
            return {
                "ok": True,
                "result": {
                    "items": [
                        {"seq": 1, "payload": {"plugin_id": "demo", "content": "hello", "priority": 1, "message_id": "m1"}}
                    ]
                },
            }

    class _BusRpc:
        async def request_async(self, *, op: str, args: dict[str, object], timeout: float):
            return {
                "ok": True,
                "result": {"items": [{"payload": {"plugin_id": "demo", "content": "x", "message_id": "m2"}}]},
            }

    ctx = SimpleNamespace(plugin_id="demo")
    client = MessageClient(ctx=ctx)
    monkeypatch.setattr("plugin.sdk.bus.messages._MessagePlaneRpcClient", lambda **kwargs: _AsyncRpc())
    monkeypatch.setattr("plugin.sdk.bus.messages._ensure_rpc", lambda _: _BusRpc())

    all_list = await client.get_message_plane_all_async(max_items=10, page_limit=2, raw=True)
    assert all_list.count() == 1

    by_conv = await client.get_by_conversation_async("c1", max_count=10)
    assert by_conv.count() == 1


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_bus_list_reload_async_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    from plugin.sdk.bus import bus_list as bl_module

    lst = BusList([])
    with pytest.raises(TypeError):
        await lst.reload_async()

    async def _fake_reload_async(self, ctx=None, *, incremental=False):  # type: ignore[no-untyped-def]
        return BusList([], ctx=ctx)

    monkeypatch.setattr(bl_module.BusListCore, "reload_async", _fake_reload_async)
    out = await lst.reload_async(ctx=SimpleNamespace(bus="x"), incremental=True)
    assert isinstance(out, BusList)


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_list_subscription_async_helper() -> None:
    class _W:
        def subscribe(self, *, on=("add",)):  # noqa: ANN001, ANN201
            def _deco(fn):  # noqa: ANN001, ANN201
                return fn
            return _deco

    dec = await list_subscription_async(_W(), on=("add", "change"))
    assert callable(dec)
