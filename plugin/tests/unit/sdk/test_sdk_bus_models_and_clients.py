from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugin.sdk.bus.conversations import ConversationClient, ConversationRecord
from plugin.sdk.bus.events import EventRecord
from plugin.sdk.bus.lifecycle import LifecycleRecord
from plugin.sdk.bus.memory import MemoryList, MemoryRecord
from plugin.sdk.bus.messages import MessageRecord
from plugin.sdk.bus.records import BinaryNode, GetNode, UnaryNode, parse_iso_timestamp
from plugin.sdk.bus.types import BusList, BusRecord


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
