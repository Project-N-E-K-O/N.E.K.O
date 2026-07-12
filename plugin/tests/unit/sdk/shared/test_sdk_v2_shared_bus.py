"""Tests for sdk bus_context — the runtime anti-corruption layer.

These tests complement test_sdk_shared_core_coverage.py by covering
SdkBusList operations, namespace buses, and record edge cases that the
core coverage file does not exercise.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import threading
from types import SimpleNamespace
from typing import get_args

import pytest

from plugin.core.bus.types import BusList as CoreBusList
from plugin.core.bus import types as core_bus_types
from plugin.core.bus import bus_list as core_bus_list_module
from plugin.core.bus import messages as core_bus_messages
from plugin.core.bus import rev as core_bus_rev
from plugin.core.bus.watchers import BusListWatcher
from plugin.core.bus.events import EventClient, EventList
from plugin.core.bus.lifecycle import LifecycleClient, LifecycleList
from plugin.core.bus.messages import MessageClient, MessageList
from plugin.message_plane import protocol as message_plane_protocol
from plugin.message_plane.rpc_server import MessagePlaneRpcServer
from plugin.message_plane.protocol import RpcOp
from plugin.message_plane.stores import TopicStore
from plugin.sdk.shared.core.bus_context import (
    SdkBusContext,
    SdkBusConversationRecord,
    SdkBusEventRecord,
    SdkBusLifecycleRecord,
    SdkBusList,
    SdkBusMemoryRecord,
    SdkBusMessageRecord,
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
        "sorted",
        "try_filter",
    )
    for bus_list_type in (CoreBusList, SdkBusList):
        for name in removed_names:
            assert not hasattr(bus_list_type, name)
    assert not hasattr(core_bus_types, "BinaryNode")
    assert not hasattr(core_bus_types, "BusFilterResult")
    assert importlib.util.find_spec("plugin.core.bus.records") is None
    assert importlib.util.find_spec("plugin.core.bus.protocols") is None

    assert not hasattr(MessagePlaneRpcServer, "_apply_unary_op")
    assert not hasattr(MessagePlaneRpcServer, "_eval_plan")
    assert not hasattr(core_bus_list_module, "_get_field_from_record")
    assert not hasattr(core_bus_list_module.BusListCore, "_get_field")


def test_get_message_plane_all_does_not_reappear() -> None:
    assert not hasattr(MessageClient, "get_message_plane_all")
    assert not hasattr(SdkMessagesBus, "get_message_plane_all")
    assert not hasattr(TopicStore, "get_since")
    assert "bus.get_since" not in get_args(RpcOp)


def test_removed_bus_fast_paths_do_not_reappear() -> None:
    assert "bus.replay" not in get_args(RpcOp)
    assert not hasattr(message_plane_protocol, "BusReplayArgs")
    assert not hasattr(message_plane_protocol, "BusReplayResult")
    schemas = message_plane_protocol.export_json_schemas()
    assert "bus_replay_args" not in schemas
    assert "bus_replay_result" not in schemas
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
        "_freeze_plan_value",
        "_replay_cache_key_get",
        "_replay_cache_key_unary",
        "_message_plane_replay_rpc",
        "_rebuild_records_from_plane_items",
    ):
        assert not hasattr(core_bus_list_module, name)
    for name in ("_collect_get_nodes", "_serialize_plan"):
        assert not hasattr(core_bus_types, name)
    assert core_bus_list_module._build_bus_subscribe_request("messages") == {
        "bus": "messages",
        "rules": ["add", "del", "change"],
        "deliver": "delta",
    }
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


def test_replayable_filters_on_the_same_field_keep_intersection_semantics() -> None:
    payloads = [
        {"message_id": "m-a", "type": "text", "source": "a"},
        {"message_id": "m-b", "type": "text", "source": "b"},
    ]

    class _RpcClient:
        def request(self, *, op: str, args: dict[str, object], timeout: float) -> dict[str, object]:
            del op, timeout
            source = args.get("source")
            selected = [payload for payload in payloads if source is None or payload["source"] == source]
            return {
                "ok": True,
                "error": None,
                "result": {"items": [{"payload": payload} for payload in selected]},
            }

    ctx = SimpleNamespace(plugin_id="demo", _mp_rpc_client=_RpcClient())
    messages = MessageClient(ctx)
    ctx.bus = SimpleNamespace(messages=messages)

    snapshot = messages.get(max_count=20)
    assert isinstance(snapshot, MessageList)
    filtered = snapshot.filter(source="a").filter(source="b")

    assert list(filtered) == []
    assert list(filtered.reload()) == []


def test_event_and_lifecycle_lists_reload_through_public_clients() -> None:
    payloads = {
        "events": [{"event_id": "event-a", "type": "click"}],
        "lifecycle": [{"lifecycle_id": "lifecycle-a", "type": "startup"}],
    }

    class _RpcClient:
        def request(self, *, op: str, args: dict[str, object], timeout: float) -> dict[str, object]:
            del op, timeout
            store = str(args["store"])
            return {
                "ok": True,
                "error": None,
                "result": {"items": [{"payload": payload} for payload in payloads[store]]},
            }

    ctx = SimpleNamespace(plugin_id="demo", _mp_rpc_client=_RpcClient())
    events = EventClient(ctx)
    lifecycle = LifecycleClient(ctx)
    ctx.bus = SimpleNamespace(events=events, lifecycle=lifecycle)

    event_snapshot = events.get(max_count=20)
    lifecycle_snapshot = lifecycle.get(max_count=20)
    assert isinstance(event_snapshot, EventList)
    assert isinstance(lifecycle_snapshot, LifecycleList)

    payloads["events"].append({"event_id": "event-b", "type": "hover"})
    payloads["lifecycle"].append({"lifecycle_id": "lifecycle-b", "type": "shutdown"})

    assert [record.event_id for record in event_snapshot.reload()] == ["event-a", "event-b"]
    assert [record.lifecycle_id for record in lifecycle_snapshot.reload()] == [
        "lifecycle-a",
        "lifecycle-b",
    ]


@pytest.mark.asyncio
async def test_remote_bus_change_refreshes_watcher_inside_async_plugin_loop() -> None:
    payloads = [{"message_id": "m-a", "type": "text", "source": "a"}]

    class _RpcClient:
        @staticmethod
        def _response() -> dict[str, object]:
            return {
                "ok": True,
                "error": None,
                "result": {"items": [{"payload": dict(payload)} for payload in payloads]},
            }

        def request(self, *, op: str, args: dict[str, object], timeout: float) -> dict[str, object]:
            del op, args, timeout
            return self._response()

        async def request_async(
            self,
            *,
            op: str,
            args: dict[str, object],
            timeout: float,
        ) -> dict[str, object]:
            del op, args, timeout
            return self._response()

    def _request_and_wait(**kwargs: object) -> dict[str, object]:
        return {"sub_id": "async-watcher"} if kwargs.get("request_type") == "BUS_SUBSCRIBE" else {"ok": True}

    ctx = SimpleNamespace(
        plugin_id="demo",
        _mp_rpc_client=_RpcClient(),
        _plugin_comm_queue=object(),
        _send_request_and_wait=_request_and_wait,
    )
    messages = MessageClient(ctx)
    ctx.bus = SimpleNamespace(messages=messages)

    snapshot = await messages.get(max_count=20)
    watcher = snapshot.watch(ctx)
    changed = asyncio.Event()
    plugin_loop = asyncio.get_running_loop()
    callback_loops: list[asyncio.AbstractEventLoop] = []
    observed_sources: list[str | None] = []

    @watcher.subscribe(on="add")
    def _on_add(delta: object) -> None:
        callback_loops.append(asyncio.get_running_loop())
        current = getattr(delta, "current")
        observed_sources.extend(item.source for item in current)
        changed.set()

    watcher.start()
    try:
        payloads.append({"message_id": "m-b", "type": "text", "source": "b"})
        core_bus_rev.dispatch_bus_change(
            sub_id="async-watcher",
            bus="messages",
            op="add",
            delta={"message_id": "m-b"},
        )
        await asyncio.wait_for(changed.wait(), timeout=1.0)
        assert observed_sources == ["a", "b"]
        assert callback_loops == [plugin_loop]
    finally:
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_stop_suppresses_callback_from_inflight_refresh() -> None:
    payloads = [{"message_id": "m-a", "type": "text", "source": "a"}]
    reload_started = threading.Event()
    release_reload = threading.Event()

    class _RpcClient:
        @staticmethod
        def _response() -> dict[str, object]:
            return {
                "ok": True,
                "error": None,
                "result": {"items": [{"payload": dict(payload)} for payload in payloads]},
            }

        def request(self, *, op: str, args: dict[str, object], timeout: float) -> dict[str, object]:
            del op, args, timeout
            reload_started.set()
            assert release_reload.wait(timeout=1.0)
            return self._response()

        async def request_async(
            self,
            *,
            op: str,
            args: dict[str, object],
            timeout: float,
        ) -> dict[str, object]:
            del op, args, timeout
            return self._response()

    def _request_and_wait(**kwargs: object) -> dict[str, object]:
        return {"sub_id": "stopped-watcher"} if kwargs.get("request_type") == "BUS_SUBSCRIBE" else {"ok": True}

    ctx = SimpleNamespace(
        plugin_id="demo",
        _mp_rpc_client=_RpcClient(),
        _plugin_comm_queue=object(),
        _send_request_and_wait=_request_and_wait,
    )
    messages = MessageClient(ctx)
    ctx.bus = SimpleNamespace(messages=messages)

    snapshot = await messages.get(max_count=20)
    watcher = snapshot.watch(ctx)
    callbacks: list[object] = []

    @watcher.subscribe(on="add")
    def _on_add(delta: object) -> None:
        callbacks.append(delta)

    watcher.start()
    try:
        payloads.append({"message_id": "m-b", "type": "text", "source": "b"})
        core_bus_rev.dispatch_bus_change(
            sub_id="stopped-watcher",
            bus="messages",
            op="add",
            delta={"message_id": "m-b"},
        )
        assert await asyncio.to_thread(reload_started.wait, 1.0)

        watcher.stop()
        release_reload.set()
        await asyncio.sleep(0.05)

        assert callbacks == []
    finally:
        release_reload.set()
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_coalesces_burst_changes_into_one_async_refresh() -> None:
    payloads = [{"message_id": "m-a", "type": "text", "source": "a"}]

    class _RpcClient:
        def __init__(self) -> None:
            self.sync_requests = 0

        def _response(self) -> dict[str, object]:
            return {
                "ok": True,
                "error": None,
                "result": {"items": [{"payload": dict(payload)} for payload in payloads]},
            }

        def request(self, *, op: str, args: dict[str, object], timeout: float) -> dict[str, object]:
            del op, args, timeout
            self.sync_requests += 1
            return self._response()

        async def request_async(
            self,
            *,
            op: str,
            args: dict[str, object],
            timeout: float,
        ) -> dict[str, object]:
            del op, args, timeout
            return self._response()

    rpc = _RpcClient()

    def _request_and_wait(**kwargs: object) -> dict[str, object]:
        return {"sub_id": "burst-watcher"} if kwargs.get("request_type") == "BUS_SUBSCRIBE" else {"ok": True}

    ctx = SimpleNamespace(
        plugin_id="demo",
        _mp_rpc_client=rpc,
        _plugin_comm_queue=object(),
        _send_request_and_wait=_request_and_wait,
    )
    messages = MessageClient(ctx)
    ctx.bus = SimpleNamespace(messages=messages)

    snapshot = await messages.get(max_count=20)
    watcher = snapshot.watch(ctx)
    changed = asyncio.Event()
    callbacks: list[object] = []

    @watcher.subscribe(on="add")
    def _on_add(delta: object) -> None:
        callbacks.append(delta)
        changed.set()

    watcher.start()
    try:
        payloads.append({"message_id": "m-b", "type": "text", "source": "b"})
        for sequence in range(100):
            core_bus_rev.dispatch_bus_change(
                sub_id="burst-watcher",
                bus="messages",
                op="add",
                delta={"message_id": "m-b", "sequence": sequence},
            )

        await asyncio.wait_for(changed.wait(), timeout=1.0)
        await asyncio.sleep(0.1)

        assert rpc.sync_requests == 1
        assert len(callbacks) == 1
    finally:
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_processes_pending_change_after_transient_refresh_failure() -> None:
    payloads = [{"message_id": "m-a", "type": "text", "source": "a"}]
    first_reload_started = threading.Event()
    release_first_reload = threading.Event()

    class _RpcClient:
        def __init__(self) -> None:
            self.sync_requests = 0

        @staticmethod
        def _response() -> dict[str, object]:
            return {
                "ok": True,
                "error": None,
                "result": {"items": [{"payload": dict(payload)} for payload in payloads]},
            }

        def request(self, *, op: str, args: dict[str, object], timeout: float) -> dict[str, object]:
            del op, args, timeout
            self.sync_requests += 1
            if self.sync_requests == 1:
                first_reload_started.set()
                assert release_first_reload.wait(timeout=1.0)
                raise RuntimeError("transient reload failure")
            return self._response()

        async def request_async(
            self,
            *,
            op: str,
            args: dict[str, object],
            timeout: float,
        ) -> dict[str, object]:
            del op, args, timeout
            return self._response()

    rpc = _RpcClient()

    def _request_and_wait(**kwargs: object) -> dict[str, object]:
        return {"sub_id": "retry-watcher"} if kwargs.get("request_type") == "BUS_SUBSCRIBE" else {"ok": True}

    ctx = SimpleNamespace(
        plugin_id="demo",
        _mp_rpc_client=rpc,
        _plugin_comm_queue=object(),
        _send_request_and_wait=_request_and_wait,
    )
    messages = MessageClient(ctx)
    ctx.bus = SimpleNamespace(messages=messages)

    snapshot = await messages.get(max_count=20)
    watcher = snapshot.watch(ctx)
    changed = asyncio.Event()
    callbacks: list[object] = []

    @watcher.subscribe(on="add")
    def _on_add(delta: object) -> None:
        callbacks.append(delta)
        changed.set()

    watcher.start()
    try:
        core_bus_rev.dispatch_bus_change(
            sub_id="retry-watcher",
            bus="messages",
            op="add",
            delta={"sequence": 1},
        )
        assert await asyncio.to_thread(first_reload_started.wait, 1.0)

        payloads.append({"message_id": "m-b", "type": "text", "source": "b"})
        core_bus_rev.dispatch_bus_change(
            sub_id="retry-watcher",
            bus="messages",
            op="add",
            delta={"message_id": "m-b", "sequence": 2},
        )
        release_first_reload.set()

        await asyncio.wait_for(changed.wait(), timeout=1.0)

        assert rpc.sync_requests == 2
        assert len(callbacks) == 1
    finally:
        release_first_reload.set()
        watcher.stop()


@pytest.mark.asyncio
async def test_debounced_watcher_drops_timer_scheduled_before_stop_and_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads = [{"message_id": "m-a", "type": "text", "source": "a"}]
    timer_constructor_started = threading.Event()
    release_timer_constructor = threading.Event()
    real_timer = threading.Timer
    started_timers = 0

    class _BlockedTimer:
        def __init__(self, _delay: float, callback: object) -> None:
            timer_constructor_started.set()
            assert release_timer_constructor.wait(timeout=1.0)
            self._delegate = real_timer(0.0, callback)

        def start(self) -> None:
            nonlocal started_timers
            started_timers += 1
            self._delegate.start()

        def cancel(self) -> None:
            self._delegate.cancel()

    monkeypatch.setattr(threading, "Timer", _BlockedTimer)

    class _RpcClient:
        @staticmethod
        def _response() -> dict[str, object]:
            return {
                "ok": True,
                "error": None,
                "result": {"items": [{"payload": dict(payload)} for payload in payloads]},
            }

        def request(self, *, op: str, args: dict[str, object], timeout: float) -> dict[str, object]:
            del op, args, timeout
            return self._response()

        async def request_async(
            self,
            *,
            op: str,
            args: dict[str, object],
            timeout: float,
        ) -> dict[str, object]:
            del op, args, timeout
            return self._response()

    def _request_and_wait(**kwargs: object) -> dict[str, object]:
        return {"sub_id": "debounced-restart"} if kwargs.get("request_type") == "BUS_SUBSCRIBE" else {"ok": True}

    ctx = SimpleNamespace(
        plugin_id="demo",
        _mp_rpc_client=_RpcClient(),
        _plugin_comm_queue=object(),
        _send_request_and_wait=_request_and_wait,
    )
    messages = MessageClient(ctx)
    ctx.bus = SimpleNamespace(messages=messages)

    snapshot = await messages.get(max_count=20)
    watcher = snapshot.watch(ctx, debounce_ms=25)
    callbacks: list[object] = []

    @watcher.subscribe(on="add")
    def _on_add(delta: object) -> None:
        callbacks.append(delta)

    watcher.start()
    schedule_task: asyncio.Task[None] | None = None
    try:
        payloads.append({"message_id": "m-b", "type": "text", "source": "b"})
        schedule_task = asyncio.create_task(
            asyncio.to_thread(
                core_bus_rev.dispatch_bus_change,
                sub_id="debounced-restart",
                bus="messages",
                op="add",
                delta={"message_id": "m-b"},
            )
        )
        assert await asyncio.to_thread(timer_constructor_started.wait, 1.0)

        watcher.stop()
        watcher.start()
        release_timer_constructor.set()
        await schedule_task
        await asyncio.sleep(0.05)

        assert started_timers == 0
        assert callbacks == []
    finally:
        release_timer_constructor.set()
        if schedule_task is not None:
            await schedule_task
        watcher.stop()


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

    def test_filter_callable_with_kwargs_does_not_keep_replay_plan(self) -> None:
        class _ReplayableRawList:
            def __init__(self, records: list[dict[str, object]]) -> None:
                self.records = records

            def __iter__(self):
                return iter(self.records)

            def filter(self, *, strict=True, **kwargs):
                del strict
                return _ReplayableRawList([
                    item
                    for item in self.records
                    if all(item.get(key) == value for key, value in kwargs.items())
                ])

            def watch(self, *_args, **_kwargs):
                return object()

        items = SdkBusList.from_raw(
            _ReplayableRawList([
                {"type": "t", "source": "a", "priority": 1},
                {"type": "t", "source": "a", "priority": 5},
                {"type": "t", "source": "b", "priority": 5},
            ]),
            namespace="messages",
            record_factory=SdkBusMessageRecord,
            host_ctx=object(),
        )

        filtered = items.filter(lambda item: item.priority > 2, source="a")

        assert [item.priority for item in filtered] == [5]
        with pytest.raises(TypeError, match=r"watch\(\) is not available"):
            filtered.watch()

    def test_where(self) -> None:
        items = _make_list([SdkBusMessageRecord(type="t", priority=1), SdkBusMessageRecord(type="t", priority=2)])
        result = items.where(lambda r: r.priority == 2)
        assert len(result) == 1

    def test_sort_by_public_record_field(self) -> None:
        items = _make_list([
            SdkBusMessageRecord(type="t", priority=1),
            SdkBusMessageRecord(type="t", priority=5),
            SdkBusMessageRecord(type="t", priority=3),
        ])

        result = items.sort(by="priority", reverse=True)

        assert [item.priority for item in result] == [5, 3, 1]

    def test_local_sort_matches_core_for_mixed_numeric_values(self) -> None:
        records = [
            SdkBusMessageRecord(type="false", priority=False),
            SdkBusMessageRecord(type="zero", priority=0),
            SdkBusMessageRecord(type="true", priority=True),
            SdkBusMessageRecord(type="one", priority=1),
        ]
        core_items = CoreBusList(records)
        sdk_items = _make_list(records)

        core_sorted = core_items.sort(by="priority")
        sdk_sorted = sdk_items.sort(by="priority")

        assert [item.type for item in core_sorted] == ["false", "zero", "true", "one"]
        assert [item.type for item in sdk_sorted] == [item.type for item in core_sorted]

    @pytest.mark.parametrize(
        ("cast", "expected"),
        [
            ("int", ["none", "bad", "two", "ten"]),
            ("float", ["none", "bad", "two", "ten"]),
            ("str", ["none", "ten", "two", "bad"]),
        ],
    )
    def test_local_sort_casts_match_core(self, cast: str, expected: list[str]) -> None:
        records = [
            SdkBusMessageRecord(type="ten", priority="10"),
            SdkBusMessageRecord(type="two", priority="2"),
            SdkBusMessageRecord(type="none", priority=None),
            SdkBusMessageRecord(type="bad", priority="bad"),
        ]
        core_items = CoreBusList(records)
        sdk_items = _make_list(records)

        core_sorted = core_items.sort(by="priority", cast=cast)
        sdk_sorted = sdk_items.sort(by="priority", cast=cast)

        assert [item.type for item in core_sorted] == expected
        assert [item.type for item in sdk_sorted] == expected

    def test_sort_by_field_delegates_to_replayable_raw_list(self) -> None:
        class _ReplayableRawList:
            def __init__(self, records: list[dict[str, object]]) -> None:
                self.records = records
                self.sort_calls: list[dict[str, object]] = []

            def __iter__(self):
                return iter(self.records)

            def sort(self, *, by=None, cast=None, reverse=False):
                self.sort_calls.append({"by": by, "cast": cast, "reverse": reverse})
                return _ReplayableRawList(
                    sorted(self.records, key=lambda item: item[str(by)], reverse=reverse)
                )

            def watch(self, *_args, **_kwargs):
                return object()

        raw = _ReplayableRawList([
            {"type": "t", "priority": 1},
            {"type": "t", "priority": 5},
        ])
        items = SdkBusList.from_raw(
            raw,
            namespace="messages",
            record_factory=SdkBusMessageRecord,
            host_ctx=object(),
        )

        result = items.sort(by="priority", cast="int", reverse=True)

        assert raw.sort_calls == [{"by": "priority", "cast": "int", "reverse": True}]
        assert [item.priority for item in result] == [5, 1]
        assert callable(getattr(result._raw_list, "watch", None))

    def test_sort_with_callable_is_local_and_non_replayable(self) -> None:
        items = _make_list([
            SdkBusMessageRecord(type="t", priority=1),
            SdkBusMessageRecord(type="t", priority=5),
        ])

        result = items.sort(key=lambda item: item.priority, reverse=True)

        assert [item.priority for item in result] == [5, 1]
        with pytest.raises(TypeError, match=r"watch\(\) is not available"):
            result.watch()

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
