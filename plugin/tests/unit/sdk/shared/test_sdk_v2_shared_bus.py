"""Tests for sdk bus_context — the runtime anti-corruption layer.

These tests complement test_sdk_shared_core_coverage.py by covering
SdkBusList operations, namespace buses, and record edge cases that the
core coverage file does not exercise.
"""

from __future__ import annotations

import inspect
from typing import get_args

import pytest

from plugin.core.bus.types import BusList as CoreBusList
from plugin.core.bus import records as core_bus_records
from plugin.core.bus import types as core_bus_types
from plugin.core.bus import bus_list as core_bus_list_module
from plugin.core.bus import messages as core_bus_messages
from plugin.core.bus import rev as core_bus_rev
from plugin.core.bus.watchers import BusListWatcher
from plugin.core.bus.messages import MessageClient
from plugin.message_plane.rpc_server import MessagePlaneRpcServer
from plugin.message_plane.protocol import RpcOp
from plugin.message_plane.stores import TopicStore
from plugin.sdk.shared.core.bus_context import (
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


def test_removed_bus_query_dsl_does_not_reappear() -> None:
    removed_names = (
        "where_in",
        "where_eq",
        "where_contains",
        "where_regex",
        "where_gt",
        "where_ge",
        "where_lt",
        "where_le",
        "merge",
        "__add__",
        "intersection",
        "intersect",
        "__and__",
        "difference",
        "subtract",
        "__sub__",
    )
    for bus_list_type in (CoreBusList, SdkBusList):
        for name in removed_names:
            assert not hasattr(bus_list_type, name)
    assert not hasattr(core_bus_types, "BinaryNode")
    assert not hasattr(core_bus_records, "BinaryNode")

    rpc_server = object.__new__(MessagePlaneRpcServer)
    for op in removed_names[:8]:
        assert rpc_server._apply_unary_op([], op=op, params={}) is None
    assert rpc_server._eval_plan(None, {"kind": "binary", "op": "merge"}) is None


def test_get_message_plane_all_does_not_reappear() -> None:
    assert not hasattr(MessageClient, "get_message_plane_all")
    assert not hasattr(SdkMessagesBus, "get_message_plane_all")
    assert not hasattr(TopicStore, "get_since")
    assert "bus.get_since" not in get_args(RpcOp)


def test_removed_bus_fast_paths_do_not_reappear() -> None:
    assert "fast_mode" not in inspect.signature(CoreBusList).parameters
    assert not hasattr(CoreBusList, "fast_mode")
    assert not hasattr(CoreBusList([]), "_reload_cursor_ts")
    for method_name in ("reload", "reload_with", "reload_with_async"):
        assert "incremental" not in inspect.signature(getattr(CoreBusList, method_name)).parameters
    for method_name in ("get", "get_async"):
        assert "no_fallback" not in inspect.signature(getattr(MessageClient, method_name)).parameters
    for name in ("_LocalMessageCache", "_LOCAL_CACHE", "_ensure_local_cache", "_try_local_cache"):
        assert not hasattr(core_bus_messages, name)
    for name in (
        "_try_incremental_local",
        "_resolve_watcher_refresh",
        "_extract_unary_plan_ops",
        "_apply_watcher_ops_local",
        "_record_from_raw_by_bus",
    ):
        assert not hasattr(core_bus_list_module, name)
    assert not hasattr(BusListWatcher, "_try_incremental")
    for name in (
        "register_bus_change_listener",
        "_ensure_bus_rev_subscription",
        "_get_bus_rev",
        "_get_recent_deltas",
        "_BUS_LATEST_REV",
        "_BUS_RECENT_DELTAS",
    ):
        assert not hasattr(core_bus_rev, name)


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

    def test_limit(self) -> None:
        items = _make_list([SdkBusMessageRecord(type="t") for _ in range(5)])
        assert len(items.limit(3)) == 3

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
