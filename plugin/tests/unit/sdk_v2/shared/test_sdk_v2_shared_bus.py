"""Tests for sdk_v2 bus_context — the runtime anti-corruption layer.

These tests complement test_sdk_v2_shared_core_coverage.py by covering
SdkBusList operations, namespace buses, and record edge cases that the
core coverage file does not exercise.
"""

from __future__ import annotations

import pytest

from plugin.sdk_v2.shared.core.bus_context import (
    SdkBusContext,
    SdkBusConversationRecord,
    SdkBusDelta,
    SdkBusEventRecord,
    SdkBusLifecycleRecord,
    SdkBusList,
    SdkBusMemoryRecord,
    SdkBusMessageRecord,
    SdkBusWatcher,
    SdkConversationsBus,
    SdkEventsBus,
    SdkLifecycleBus,
    SdkMemoryBus,
    SdkMessagesBus,
    ensure_sdk_bus_context,
)


# ---------------------------------------------------------------------------
# Record from_raw / dump / key / version
# ---------------------------------------------------------------------------


class TestMessageRecord:
    def test_from_raw_mapping(self) -> None:
        rec = SdkBusMessageRecord.from_raw({"message_id": "m1", "type": "text", "time": 1.5, "source": "demo"})
        assert rec.message_id == "m1"
        assert rec.timestamp == 1.5
        assert rec.source == "demo"

    def test_from_raw_object(self) -> None:
        class _Obj:
            message_id = "m2"
            type = "text"
            time = 2.0
            source = "obj"
        rec = SdkBusMessageRecord.from_raw(_Obj())
        assert rec.message_id == "m2"

    def test_dump_roundtrip(self) -> None:
        rec = SdkBusMessageRecord(type="text", message_id="m1", source="s")
        d = rec.dump()
        assert d["message_id"] == "m1"
        assert d["source"] == "s"

    def test_key_with_id(self) -> None:
        assert SdkBusMessageRecord(type="t", message_id="m1").key() == "m1"

    def test_key_fallback(self) -> None:
        rec = SdkBusMessageRecord(type="t", source="s", timestamp=1.0)
        assert rec.key() == "s:1.0"

    def test_version(self) -> None:
        assert SdkBusMessageRecord(type="t", timestamp=3.7).version() == 3
        assert SdkBusMessageRecord(type="t").version() is None


class TestEventRecord:
    def test_from_raw(self) -> None:
        rec = SdkBusEventRecord.from_raw({"event_type": "click", "received_at": 5.0, "trace_id": "e1"})
        assert rec.type == "click"
        assert rec.timestamp == 5.0
        assert rec.event_id == "e1"

    def test_key_and_version(self) -> None:
        rec = SdkBusEventRecord(type="ev", event_id="e1", timestamp=2.0)
        assert rec.key() == "e1"
        assert rec.version() == 2


class TestLifecycleRecord:
    def test_from_raw(self) -> None:
        rec = SdkBusLifecycleRecord.from_raw({"type": "startup", "at": 10.0, "lifecycle_id": "lc1"})
        assert rec.type == "startup"
        assert rec.timestamp == 10.0
        assert rec.lifecycle_id == "lc1"


class TestConversationRecord:
    def test_from_raw_with_metadata_fields(self) -> None:
        rec = SdkBusConversationRecord.from_raw({
            "conversation_id": "c1",
            "metadata": {"turn_type": "user", "lanlan_name": "neko"},
        })
        assert rec.conversation_id == "c1"
        assert rec.turn_type == "user"
        assert rec.lanlan_name == "neko"


class TestMemoryRecord:
    def test_from_raw_mapping(self) -> None:
        rec = SdkBusMemoryRecord.from_raw({"id": "mem1", "rev": 3, "data": "x"})
        assert rec.key() == "mem1"
        assert rec.version() == 3

    def test_from_raw_scalar(self) -> None:
        rec = SdkBusMemoryRecord.from_raw("hello")
        assert rec.dump() == {"value": "hello"}


# ---------------------------------------------------------------------------
# SdkBusList operations
# ---------------------------------------------------------------------------


def _make_list(records: list[SdkBusMessageRecord]) -> SdkBusList[SdkBusMessageRecord]:
    return SdkBusList(records, namespace="messages", record_factory=SdkBusMessageRecord, host_ctx=object())


class TestSdkBusList:
    def test_iter_len_getitem(self) -> None:
        items = _make_list([SdkBusMessageRecord(type="t", source="a"), SdkBusMessageRecord(type="t", source="b")])
        assert len(items) == 2
        assert items[0].source == "a"
        assert list(items)[1].source == "b"

    def test_count_and_size(self) -> None:
        items = _make_list([SdkBusMessageRecord(type="t")])
        assert items.count() == 1
        assert items.size() == 1

    def test_dump(self) -> None:
        items = _make_list([SdkBusMessageRecord(type="t", source="demo")])
        dumped = items.dump()
        assert len(dumped) == 1
        assert dumped[0]["source"] == "demo"

    def test_filter_callable(self) -> None:
        items = _make_list([
            SdkBusMessageRecord(type="t", priority=1),
            SdkBusMessageRecord(type="t", priority=5),
        ])
        filtered = items.filter(lambda r: r.priority > 2)
        assert len(filtered) == 1
        assert filtered[0].priority == 5

    def test_filter_kwargs(self) -> None:
        items = _make_list([
            SdkBusMessageRecord(type="t", source="a"),
            SdkBusMessageRecord(type="t", source="b"),
        ])
        filtered = items.filter(source="a")
        assert len(filtered) == 1

    def test_where(self) -> None:
        items = _make_list([SdkBusMessageRecord(type="t", priority=1), SdkBusMessageRecord(type="t", priority=2)])
        result = items.where(lambda r: r.priority == 2)
        assert len(result) == 1

    def test_where_in(self) -> None:
        items = _make_list([
            SdkBusMessageRecord(type="t", source="a"),
            SdkBusMessageRecord(type="t", source="b"),
            SdkBusMessageRecord(type="t", source="c"),
        ])
        result = items.where_in("source", ["a", "c"])
        assert len(result) == 2

    def test_limit(self) -> None:
        items = _make_list([SdkBusMessageRecord(type="t") for _ in range(5)])
        assert len(items.limit(3)) == 3

    def test_add(self) -> None:
        a = _make_list([SdkBusMessageRecord(type="t", message_id="m1")])
        b = _make_list([SdkBusMessageRecord(type="t", message_id="m2")])
        merged = a + b
        assert len(merged) == 2

    def test_and_intersection(self) -> None:
        a = _make_list([SdkBusMessageRecord(type="t", message_id="m1"), SdkBusMessageRecord(type="t", message_id="m2")])
        b = _make_list([SdkBusMessageRecord(type="t", message_id="m2")])
        result = a & b
        assert len(result) == 1

    def test_explain(self) -> None:
        items = _make_list([])
        assert "messages" in items.explain()

    def test_from_raw_with_iterable(self) -> None:
        raw = [{"type": "text", "message_id": "r1"}, {"type": "text", "message_id": "r2"}]
        result = SdkBusList.from_raw(raw, namespace="messages", record_factory=SdkBusMessageRecord, host_ctx=object())
        assert len(result) == 2

    def test_from_raw_none(self) -> None:
        result = SdkBusList.from_raw(None, namespace="messages", record_factory=SdkBusMessageRecord, host_ctx=object())
        assert len(result) == 0


# ---------------------------------------------------------------------------
# SdkBusContext & ensure
# ---------------------------------------------------------------------------


class TestSdkBusContext:
    def test_construction_with_empty_bus(self) -> None:
        ctx = SdkBusContext(object(), host_ctx=object())
        assert isinstance(ctx.messages, SdkMessagesBus)
        assert isinstance(ctx.events, SdkEventsBus)
        assert isinstance(ctx.lifecycle, SdkLifecycleBus)
        assert isinstance(ctx.conversations, SdkConversationsBus)
        assert isinstance(ctx.memory, SdkMemoryBus)

    def test_ensure_passthrough(self) -> None:
        ctx = SdkBusContext(object(), host_ctx=object())
        assert ensure_sdk_bus_context(ctx, host_ctx=object()) is ctx

    def test_ensure_wraps_raw(self) -> None:
        result = ensure_sdk_bus_context(object(), host_ctx=object())
        assert isinstance(result, SdkBusContext)

    def test_ensure_wraps_none(self) -> None:
        result = ensure_sdk_bus_context(None, host_ctx=object())
        assert isinstance(result, SdkBusContext)
