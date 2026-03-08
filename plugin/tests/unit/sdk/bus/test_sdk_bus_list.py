from __future__ import annotations

import json
import os
import sys
from types import ModuleType, SimpleNamespace

import ormsgpack
import pytest

import plugin.sdk.bus.bus_list as bl


class _Rec:
    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        self.raw = kwargs.pop("raw", {})
        self._dump = kwargs.pop("dump_data", {})
        for k, v in kwargs.items():
            setattr(self, k, v)

    def dump(self):  # noqa: ANN201
        if self._dump == "raise":
            raise RuntimeError("dump err")
        return dict(self._dump)


@pytest.mark.plugin_unit
def test_bus_list_helpers_basic_and_filters() -> None:
    r1 = _Rec(message_id="m1", raw={"priority": 1}, dump_data={"x": 1})
    r2 = _Rec(raw={"event_id": "e2"}, dump_data={"a": 2})
    r3 = _Rec(dump_data={"z": 9})
    assert bl._dedupe_key_from_record(r1) == ("message_id", "m1")
    assert bl._dedupe_key_from_record(r2) == ("event_id", "e2")
    assert bl._dedupe_key_from_record(r3)[0] == "dump"
    assert bl._dedupe_key_from_record(_Rec(dump_data="raise"))[0] == "object"

    assert bl._sort_bus_value(None) == (2, "")
    assert bl._sort_bus_value(3) == (0, 3)
    assert bl._sort_bus_value("x") == (1, "x")

    assert bl._get_sort_field_from_record(_Rec(priority=3), "priority") == 3
    assert bl._get_sort_field_from_record(_Rec(raw={"p": 2}), "p") == 2
    assert bl._get_sort_field_from_record(_Rec(dump_data={"k": "v"}), "k") == "v"
    assert bl._get_sort_field_from_record(_Rec(dump_data="raise"), "x") is None

    assert bl._get_field_from_record(_Rec(a=1), "a") == 1
    assert bl._get_field_from_record(_Rec(raw={"b": 2}), "b") == 2
    assert bl._get_field_from_record(_Rec(raw=None, dump_data={"c": 3}), "c") == 3
    assert bl._get_field_from_record(_Rec(dump_data="raise"), "x") is None

    left = [_Rec(message_id="a"), _Rec(message_id="b"), _Rec(message_id="a")]
    right = [_Rec(message_id="b"), _Rec(message_id="c")]
    assert len(bl._merge_unique_items(left, right, bl._dedupe_key_from_record)) == 3
    assert len(bl._intersection_unique_items(left, right, bl._dedupe_key_from_record)) == 1
    assert len(bl._difference_unique_items(left, right, bl._dedupe_key_from_record)) == 1

    items = [_Rec(v="2"), _Rec(v="x"), _Rec(v=None)]
    out_gt = bl._filter_items_by_compare(
        items=items,
        field="v",
        target=1,
        cast_value=lambda x: int(x) if str(x).isdigit() else -1,
        get_field=lambda it, f: getattr(it, f, None),
        mode="gt",
    )
    assert len(out_gt) == 1
    assert len(
        bl._filter_items_by_contains(
            items=items, field="v", needle="2", get_field=lambda it, f: getattr(it, f, None)
        )
    ) == 1

    import re

    assert len(
        bl._filter_items_by_regex(
            items=items,
            field="v",
            compiled=re.compile(r"^2$"),
            get_field=lambda it, f: getattr(it, f, None),
            strict=False,
            error_factory=lambda e: ValueError(str(e)),
        )
    ) == 1


@pytest.mark.plugin_unit
def test_bus_list_watcher_utility_helpers() -> None:
    refreshed = [_Rec(message_id="1"), _Rec(message_id="2")]
    added, removed, new_keys, fired, kind = bl._compute_watcher_delta(
        op="add",
        refreshed_items=refreshed,
        last_keys={("message_id", "1"), ("message_id", "x")},
        dedupe_key=bl._dedupe_key_from_record,
    )
    assert len(added) == 1
    assert len(removed) == 1
    assert "change" in fired and kind == "add"

    calls: list[str] = []
    bl._dispatch_watcher_callbacks(
        [
            (lambda d: calls.append("ok"), ("add",)),
            (lambda d: (_ for _ in ()).throw(RuntimeError("x")), ("add",)),
        ],
        ["add"],
        object(),
    )
    assert calls == ["ok"]

    assert bl._resolve_watcher_refresh(
        op="x",
        payload={},
        try_incremental=lambda *_: None,
        reload_full=lambda: "full",
    ) == "full"

    lock = SimpleNamespace(__enter__=lambda self: None, __exit__=lambda self, *a: False)
    # use a real lock-like object path
    class _Lock:
        def __enter__(self):
            return None

        def __exit__(self, *a):
            return False

    cbs: list[tuple] = []
    bl._register_watcher_callback(cbs, _Lock(), lambda d: None, ("add",))
    assert len(bl._snapshot_watcher_callbacks(cbs, _Lock())) == 1
    assert bl._normalize_watch_rules("add") == ("add",)
    assert bl._normalize_watch_rules(["add", "del"]) == ("add", "del")


class _Core(bl.BusListCore):
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def reload_with(self, ctx=None, *, inplace=False, incremental=False):  # noqa: ANN001, ANN201
        self.calls.append((ctx, inplace, incremental))
        return {"ctx": ctx, "inplace": inplace, "incremental": incremental}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_bus_list_core_methods() -> None:
    c = _Core()
    assert c.reload(incremental=True)["incremental"] is True
    assert (await c.reload_async(incremental=True))["incremental"] is True
    assert (await c.reload_async("ctx", incremental=True))["ctx"] == "ctx"
    assert c._dedupe_key(_Rec(message_id="x")) == ("message_id", "x")
    assert c._sort_value(1) == (0, 1)
    assert c._get_sort_field(_Rec(raw={"k": 1}), "k") == 1
    assert c._get_field(_Rec(raw={"k": 2}), "k") == 2
    assert c._cast_value("3", "int") == 3
    assert (await c.reload_with_async(inplace=True))["inplace"] is True
    assert (await c.reload_with_async("ctx", incremental=True))["ctx"] == "ctx"


class _Watcher(bl.BusListWatcherCore):
    def __init__(self) -> None:
        import threading

        self._callbacks = []
        self._lock = threading.Lock()
        self._unsub = None
        self._sub_id = None
        self._ctx = SimpleNamespace(_plugin_comm_queue=object(), _send_request_and_wait=self._send_req)
        self._bus = "messages"
        self._list = SimpleNamespace(trace_tree_dump=lambda: {"x": 1})
        self.events: list[str] = []

    def _watcher_set(self, sub_id: str) -> None:
        self.events.append(f"set:{sub_id}")

    def _watcher_pop(self, sub_id: str) -> None:
        self.events.append(f"pop:{sub_id}")

    def _schedule_tick(self, op: str, payload=None) -> None:  # noqa: ANN001
        self.events.append(f"tick:{op}")

    def _send_req(self, **kwargs):  # noqa: ANN003, ANN201
        if kwargs.get("method_name") == "bus_subscribe":
            return {"sub_id": "sid-1"}
        return {"ok": True}


@pytest.mark.plugin_unit
def test_bus_list_watcher_core_start_stop_and_subscribe(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _Watcher()

    @w.subscribe(on=("add", "change"))
    def _cb(delta):  # noqa: ANN001
        return None

    assert len(w._callbacks) == 1
    assert w.start() is w
    assert w._sub_id == "sid-1"
    w.stop()
    assert any(x.startswith("pop:") for x in w.events)

    # fallback to state subscribe path
    called: list[str] = []
    fake_state_mod = ModuleType("plugin.core.state")
    fake_state_mod.state = SimpleNamespace(
        bus_change_hub=SimpleNamespace(
            subscribe=lambda bus, cb: (called.append(bus), lambda: called.append("unsub"))[1]
        )
    )
    monkeypatch.setitem(sys.modules, "plugin.core.state", fake_state_mod)
    w2 = _Watcher()
    w2._ctx = SimpleNamespace()
    w2.start()
    assert called and called[0] == "messages"
    w2.stop()
    assert "unsub" in called


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_bus_list_watcher_core_async_start_stop() -> None:
    w = _Watcher()
    out = await w.start_async()
    assert out is w
    assert w._sub_id == "sid-1"
    await w.stop_async()
    assert w._sub_id is None


@pytest.mark.plugin_unit
def test_bus_list_plan_and_cache_helpers_and_incremental(monkeypatch: pytest.MonkeyPatch) -> None:
    assert bl._build_bus_subscribe_request("m", {"p": 1})["bus"] == "m"
    assert bl._extract_sub_id({"sub_id": "x"}) == "x"
    assert bl._extract_sub_id({}) is None
    assert bl._build_bus_unsubscribe_request("m", "s") == {"bus": "m", "sub_id": "s"}
    assert bl._freeze_plan_value({"a": [1, {2, 3}]})
    assert bl._seed_key_from_params("m", {"a": 1, "since_ts": 2})["params"] == {"a": 1}
    assert bl._replay_cache_key_get("m", {"a": 1})[0] == "get"
    assert bl._replay_cache_key_unary("op", {"a": 1}, "k")[0] == "unary"
    assert bl._replay_cache_key_binary("op", {"a": 1}, "l", "r")[0] == "binary"

    base_items = [_Rec(message_id="1"), _Rec(message_id="2")]
    ops = [("where_eq", {"field": "message_id", "value": "1"})]
    out = bl._try_incremental_local(
        op="add",
        payload={"record": {"message_id": "3"}},
        bus="messages",
        ops=ops,
        current_items=base_items,
        record_from_raw=lambda r: _Rec(**r),
        apply_ops_local=lambda items, _ops: [x for x in items if getattr(x, "message_id", "") == "1"],
        dedupe_key=bl._dedupe_key_from_record,
    )
    assert out is not None
    assert bl._cast_bus_value("2", "int") == 2
    assert bl._cast_bus_value("x", "int") == 0
    assert bl._cast_bus_value("2.5", "float") == 2.5
    assert bl._cast_bus_value(None, "str") == ""
    assert bl._cast_bus_value("x", "unknown") == "x"

    class _Timer:
        def __init__(self) -> None:
            self.canceled = False

        def cancel(self) -> None:
            self.canceled = True

    t = _Timer()
    bl._cancel_timer_best_effort(t)
    assert t.canceled is True


@pytest.mark.plugin_unit
def test_bus_list_schedule_injected_plan_and_record_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    # debounced and non-debounced watcher tick
    class _W:
        def __init__(self) -> None:
            import threading

            self._debounce_ms = 0.0
            self._lock = threading.Lock()
            self.calls: list[tuple[str, dict | None]] = []
            self._pending_op = None
            self._pending_payload = None
            self._debounce_timer = None

        def _tick(self, op, payload):  # noqa: ANN001
            self.calls.append((op, payload))

    w = _W()
    bl._schedule_watcher_tick_debounced(w, "add", {"x": 1})
    assert w.calls[-1][0] == "add"

    # force except path (threading import failure)
    orig_import = __import__

    def _fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "threading":
            raise ImportError("no threading")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _fake_import)
    bl._schedule_watcher_tick_debounced(w, "change", None)
    monkeypatch.setattr("builtins.__import__", orig_import)

    # injected callback mapping
    got: list[dict] = []

    def _fn(quickdump):  # noqa: ANN001
        got.append({"quickdump": quickdump})

    cb = bl._build_watcher_injected_callback(_fn)
    delta = SimpleNamespace(added=[_Rec(dump_data={"x": 1})], removed=[], current=[], kind="add")
    cb(delta)
    assert got and "quickdump" in got[-1]

    # plan ops and bus infer
    leaf = SimpleNamespace(params={"bus": "messages"})
    unary = SimpleNamespace(op="limit", params={"n": 1}, child=leaf)
    assert bl._extract_unary_plan_ops(unary) is not None
    assert bl._infer_bus_from_plan(unary, conflict_error=RuntimeError) == "messages"

    left = SimpleNamespace(params={"bus": "messages"})
    right = SimpleNamespace(params={"bus": "events"})
    binary = SimpleNamespace(left=left, right=right)
    with pytest.raises(RuntimeError):
        bl._infer_bus_from_plan(binary, conflict_error=RuntimeError)

    # local op applier
    class _L:
        def filter(self, **kwargs):  # noqa: ANN003, ANN201
            return self

        def limit(self, n):  # noqa: ANN001, ANN201
            return self

        def sort(self, **kwargs):  # noqa: ANN003, ANN201
            return self

        def where_in(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN201
            return self

        def where_eq(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN201
            return self

        def where_contains(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN201
            return self

        def where_regex(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN201
            return self

        def where_gt(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN201
            return self

        def where_ge(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN201
            return self

        def where_lt(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN201
            return self

        def where_le(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN201
            return self

    lst = _L()
    assert bl._apply_watcher_ops_local(lst, [("limit", {"n": 1}), ("where_eq", {"field": "x", "value": 1})]) is lst
    assert bl._apply_watcher_ops_local(lst, [("sort", {"key": object()})]) is None
    assert bl._apply_watcher_ops_local(lst, [("where", {})]) is None

    # record_from_raw helper with injected modules
    mod_m = ModuleType("plugin.sdk.bus.messages")
    mod_m.MessageRecord = SimpleNamespace(from_raw=lambda raw: ("m", raw))
    mod_e = ModuleType("plugin.sdk.bus.events")
    mod_e.EventRecord = SimpleNamespace(from_raw=lambda raw: ("e", raw))
    mod_l = ModuleType("plugin.sdk.bus.lifecycle")
    mod_l.LifecycleRecord = SimpleNamespace(from_raw=lambda raw: ("l", raw))
    monkeypatch.setitem(sys.modules, "plugin.sdk.bus.messages", mod_m)
    monkeypatch.setitem(sys.modules, "plugin.sdk.bus.events", mod_e)
    monkeypatch.setitem(sys.modules, "plugin.sdk.bus.lifecycle", mod_l)
    assert bl._record_from_raw_by_bus("messages", {"x": 1}) == ("m", {"x": 1})
    assert bl._record_from_raw_by_bus("events", {"x": 1}) == ("e", {"x": 1})
    assert bl._record_from_raw_by_bus("lifecycle", {"x": 1}) == ("l", {"x": 1})
    assert bl._record_from_raw_by_bus("none", {"x": 1}) is None


@pytest.mark.plugin_unit
def test_bus_list_replay_rpc_and_rebuild_and_getattr(monkeypatch: pytest.MonkeyPatch) -> None:
    # rebuild records from plane items with injected modules
    mod_m = ModuleType("plugin.sdk.bus.messages")
    mod_m.MessageRecord = SimpleNamespace(
        from_index=lambda idx, payload=None: ("mi", idx, payload),
        from_raw=lambda raw: ("mr", raw),
    )
    mod_e = ModuleType("plugin.sdk.bus.events")
    mod_e.EventRecord = SimpleNamespace(
        from_index=lambda idx, payload=None: ("ei", idx, payload),
        from_raw=lambda raw: ("er", raw),
    )
    mod_l = ModuleType("plugin.sdk.bus.lifecycle")
    mod_l.LifecycleRecord = SimpleNamespace(
        from_index=lambda idx, payload=None: ("li", idx, payload),
        from_raw=lambda raw: ("lr", raw),
    )
    monkeypatch.setitem(sys.modules, "plugin.sdk.bus.messages", mod_m)
    monkeypatch.setitem(sys.modules, "plugin.sdk.bus.events", mod_e)
    monkeypatch.setitem(sys.modules, "plugin.sdk.bus.lifecycle", mod_l)
    items = [{"index": {"id": "1"}, "payload": {"a": 1}}, {"payload": {"b": 2}}]
    assert len(bl._rebuild_records_from_plane_items("messages", items)) == 2
    assert len(bl._rebuild_records_from_plane_items("events", items)) == 2
    assert len(bl._rebuild_records_from_plane_items("lifecycle", items)) == 2
    assert bl._rebuild_records_from_plane_items("x", items) == []

    # replay rpc
    class _Sock:
        def __init__(self):
            self.sent = []
            self._responses = [ormsgpack.packb({"req_id": "bad", "ok": True}), ormsgpack.packb({"req_id": "replay:pid:1", "ok": True, "result": {"items": [{"x": 1}]}})]

        def setsockopt(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def connect(self, endpoint):  # noqa: ANN001
            self.endpoint = endpoint

        def send(self, raw, flags=0):  # noqa: ANN001
            self.sent.append(raw)

        def poll(self, timeout, flags):  # noqa: ANN001
            return 1

        def recv(self, flags=0):  # noqa: ANN001
            return self._responses.pop(0)

    class _Ctx:
        @classmethod
        def instance(cls):  # noqa: ANN206
            return SimpleNamespace(socket=lambda typ: _Sock())

    fake_zmq = SimpleNamespace(Context=_Ctx, DEALER=1, IDENTITY=2, LINGER=3, POLLIN=4)
    monkeypatch.setitem(sys.modules, "zmq", fake_zmq)
    settings_mod = ModuleType("plugin.settings")
    settings_mod.MESSAGE_PLANE_ZMQ_RPC_ENDPOINT = "ipc://plane"
    monkeypatch.setitem(sys.modules, "plugin.settings", settings_mod)
    monkeypatch.setattr(os, "getenv", lambda *a, **k: "1")

    class _UUID:
        def __str__(self) -> str:
            return "1"

    import uuid

    monkeypatch.setattr(uuid, "uuid4", lambda: _UUID())
    ctx = SimpleNamespace(plugin_id="pid")
    out = bl._message_plane_replay_rpc(
        ctx=ctx,
        bus="messages",
        plan=SimpleNamespace(),
        timeout=0.5,
        serialize_plan=lambda p: {"op": "x"},
    )
    assert out == [{"x": 1}]

    assert bl.__getattr__("BusList") is not None
    with pytest.raises(AttributeError):
        bl.__getattr__("missing")


import os
import sys
import threading
import uuid
from contextlib import suppress
from types import ModuleType, SimpleNamespace

import ormsgpack
import pytest

import plugin.sdk.bus.bus_list as bl


class _BadGetAttr:
    def __getattr__(self, name):  # noqa: ANN001
        raise RuntimeError("bad getattr")

    def dump(self):  # noqa: ANN201
        raise RuntimeError("bad dump")


@pytest.mark.plugin_unit
def test_bus_list_basic_error_edges() -> None:
    assert bl._dedupe_key_from_record(_BadGetAttr())[0] == "object"
    assert bl._get_sort_field_from_record(_BadGetAttr(), "x") is None
    assert bl._get_field_from_record(_BadGetAttr(), "x") is None

    class _Cmp:
        def __gt__(self, other):  # noqa: ANN001
            raise RuntimeError("x")

        __ge__ = __gt__
        __lt__ = __gt__
        __le__ = __gt__

    out = bl._filter_items_by_compare(
        items=[SimpleNamespace(v=1)],
        field="v",
        target=_Cmp(),
        cast_value=lambda x: x,
        get_field=lambda it, f: getattr(it, f, None),
        mode="gt",
    )
    assert out == []

    class _BadStr:
        def __str__(self) -> str:
            raise RuntimeError("x")

    assert bl._filter_items_by_contains(
        items=[SimpleNamespace(v=_BadStr())],
        field="v",
        needle="x",
        get_field=lambda it, f: getattr(it, f, None),
    ) == []

    class _Compiled:
        def search(self, text):  # noqa: ANN001
            raise RuntimeError("x")

    with pytest.raises(ValueError):
        bl._filter_items_by_regex(
            items=[SimpleNamespace(v="x")],
            field="v",
            compiled=_Compiled(),
            get_field=lambda it, f: getattr(it, f, None),
            strict=True,
            error_factory=lambda e: ValueError("regex"),
        )

    assert bl._resolve_watcher_refresh(
        op="x",
        payload={},
        try_incremental=lambda *_: (_ for _ in ()).throw(RuntimeError("x")),
        reload_full=lambda: "full",
    ) == "full"
    assert bl._snapshot_watcher_callbacks([], None) == []
    cbs: list[tuple] = []
    bl._register_watcher_callback(cbs, None, lambda d: None, ("add",))
    assert len(cbs) == 1


@pytest.mark.plugin_unit
def test_bus_list_core_and_watcher_not_implemented_and_start_stop_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    core = bl.BusListCore()
    with pytest.raises(NotImplementedError):
        core.reload_with()
    with pytest.raises(NotImplementedError):
        bl.BusListWatcherCore()._watcher_set("x")  # type: ignore[misc]
    with pytest.raises(NotImplementedError):
        bl.BusListWatcherCore()._watcher_pop("x")  # type: ignore[misc]
    with pytest.raises(NotImplementedError):
        bl.BusListWatcherCore()._schedule_tick("x")  # type: ignore[misc]

    class _W(bl.BusListWatcherCore):
        def __init__(self) -> None:
            self._callbacks = []
            self._lock = None
            self._unsub = None
            self._sub_id = None
            self._ctx = SimpleNamespace(_plugin_comm_queue=object(), _send_request_and_wait=lambda **k: {})
            self._bus = "messages"
            self._list = SimpleNamespace(trace_tree_dump=lambda: {"p": 1})

        def _watcher_set(self, sub_id: str) -> None:
            self.last = sub_id

        def _watcher_pop(self, sub_id: str) -> None:
            self.pop = sub_id

        def _schedule_tick(self, op: str, payload=None) -> None:  # noqa: ANN001
            raise RuntimeError("x")

    w = _W()
    with pytest.raises(RuntimeError):
        w.start()
    w._sub_id = "sid"
    w._ctx = SimpleNamespace(_plugin_comm_queue=object(), _send_request_and_wait=lambda **k: (_ for _ in ()).throw(RuntimeError("x")))
    w.stop()
    assert w._sub_id is None

    called = {"n": 0}
    w2 = _W()
    w2._ctx = SimpleNamespace()
    w2._unsub = lambda: called.__setitem__("n", 1)
    w2.stop()
    assert called["n"] == 1
    assert w2._unsub is None


@pytest.mark.plugin_unit
def test_bus_list_freeze_cast_timer_and_unary_edges() -> None:
    class _BadRepr:
        def __repr__(self) -> str:
            raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        bl._freeze_plan_value({"x": _BadRepr()})
    assert bl._cast_bus_value("x", "float") == 0.0
    assert bl._cast_bus_value(_BadRepr(), "str") == ""

    class _T:
        def cancel(self) -> None:
            raise RuntimeError("x")

    bl._cancel_timer_best_effort(_T())

    assert bl._extract_unary_plan_ops(None) is None
    assert bl._extract_unary_plan_ops(SimpleNamespace(left=1, right=2)) is None
    assert bl._extract_unary_plan_ops(SimpleNamespace(op="x", child=None, params={})) is None
    assert bl._extract_unary_plan_ops(SimpleNamespace(op="x", child=SimpleNamespace(), params={})) is None

    assert bl._infer_bus_from_plan(None, conflict_error=RuntimeError) == ""
    assert bl._infer_bus_from_plan(SimpleNamespace(params={"bus": "m"}), conflict_error=RuntimeError) == "m"
    assert bl._infer_bus_from_plan(SimpleNamespace(child=SimpleNamespace(params={"bus": "m"})), conflict_error=RuntimeError) == "m"


@pytest.mark.plugin_unit
def test_bus_list_try_incremental_and_apply_ops_edges() -> None:
    assert bl._try_incremental_local(
        op="add",
        payload=None,
        bus="messages",
        ops=[],
        current_items=[],
        record_from_raw=lambda r: r,
        apply_ops_local=lambda i, o: i,
        dedupe_key=lambda x: ("k", "v"),
    ) is None
    assert bl._try_incremental_local(
        op="add",
        payload={},
        bus="messages",
        ops=None,
        current_items=[],
        record_from_raw=lambda r: r,
        apply_ops_local=lambda i, o: i,
        dedupe_key=lambda x: ("k", "v"),
    ) is None
    assert bl._try_incremental_local(
        op="add",
        payload={"record": 1},
        bus="messages",
        ops=[],
        current_items=[],
        record_from_raw=lambda r: r,
        apply_ops_local=lambda i, o: i,
        dedupe_key=lambda x: ("k", "v"),
    ) is None
    assert bl._try_incremental_local(
        op="add",
        payload={"record": {}},
        bus="messages",
        ops=[],
        current_items=[],
        record_from_raw=lambda r: None,
        apply_ops_local=lambda i, o: i,
        dedupe_key=lambda x: ("k", "v"),
    ) is None

    base = [SimpleNamespace(message_id="1"), SimpleNamespace(message_id="2")]
    ops = [("limit", {"n": 1})]
    assert bl._try_incremental_local(
        op="del",
        payload={"message_id": "1"},
        bus="messages",
        ops=ops,
        current_items=base,
        record_from_raw=lambda r: r,
        apply_ops_local=lambda i, o: i,
        dedupe_key=lambda x: ("message_id", getattr(x, "message_id", "")),
    ) is None
    assert bl._try_incremental_local(
        op="del",
        payload={},
        bus="messages",
        ops=[],
        current_items=base,
        record_from_raw=lambda r: r,
        apply_ops_local=lambda i, o: i,
        dedupe_key=lambda x: ("message_id", getattr(x, "message_id", "")),
    ) is None

    class _L:
        def filter(self, **kwargs):  # noqa: ANN003, ANN201
            return self

        def limit(self, n):  # noqa: ANN001, ANN201
            return self

        def sort(self, **kwargs):  # noqa: ANN003, ANN201
            return self

        def where_in(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN201
            return self

        def where_eq(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN201
            return self

        def where_contains(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN201
            return self

        def where_regex(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN201
            return self

        def where_gt(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN201
            return self

        def where_ge(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN201
            return self

        def where_lt(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN201
            return self

        def where_le(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN201
            return self

    lst = _L()
    ops_all = [
        ("filter", {"strict": False}),
        ("limit", {"n": 1}),
        ("sort", {"by": "x", "cast": "int", "reverse": False}),
        ("where_in", {"field": "x", "values": [1]}),
        ("where_eq", {"field": "x", "value": 1}),
        ("where_contains", {"field": "x", "value": "a"}),
        ("where_regex", {"field": "x", "pattern": "a", "strict": False}),
        ("where_gt", {"field": "x", "value": 1, "cast": "int"}),
        ("where_ge", {"field": "x", "value": 1, "cast": "int"}),
        ("where_lt", {"field": "x", "value": 1, "cast": "int"}),
        ("where_le", {"field": "x", "value": 1, "cast": "int"}),
    ]
    assert bl._apply_watcher_ops_local(lst, ops_all) is lst


@pytest.mark.plugin_unit
def test_bus_list_debounce_injected_and_record_conversion_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    class _W:
        def __init__(self) -> None:
            self._debounce_ms = 1.0
            self._lock = None
            self._pending_op = None
            self._pending_payload = None
            self._debounce_timer = None
            self.calls: list[tuple[str, object]] = []

        def _tick(self, op, payload):  # noqa: ANN001
            self.calls.append((op, payload))

    w = _W()

    class _Timer:
        def __init__(self, delay, fn):  # noqa: ANN001
            self.fn = fn
            self.daemon = False

        def start(self) -> None:
            self.fn()

    fake_threading = ModuleType("threading")
    fake_threading.Timer = _Timer
    monkeypatch.setitem(sys.modules, "threading", fake_threading)
    bl._schedule_watcher_tick_debounced(w, "add", {"x": 1})
    assert w.calls and w.calls[-1][0] in {"add", "change"}

    monkeypatch.setattr(bl.inspect, "signature", lambda fn: (_ for _ in ()).throw(RuntimeError("x")))
    fn = lambda d: d  # noqa: E731
    assert bl._build_watcher_injected_callback(fn) is fn

    # _dump_record fail and fallback fn(delta)
    class _R:
        def dump(self):  # noqa: ANN201
            raise RuntimeError("x")

    called = {"n": 0}

    def _must_fallback(delta):  # noqa: ANN001
        called["n"] += 1

    cb = bl._build_watcher_injected_callback(_must_fallback)
    cb(SimpleNamespace(added=[_R()], removed=[], current=[], kind="add"))
    assert called["n"] == 1

    # record conversion exception path
    bad_m = ModuleType("plugin.sdk.bus.messages")
    bad_m.MessageRecord = SimpleNamespace(from_raw=lambda raw: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setitem(sys.modules, "plugin.sdk.bus.messages", bad_m)
    assert bl._record_from_raw_by_bus("messages", {"x": 1}) is None


@pytest.mark.plugin_unit
def test_bus_list_message_plane_replay_rpc_error_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    # import zmq fail
    import builtins

    orig_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "zmq":
            raise ImportError("no-zmq")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    assert bl._message_plane_replay_rpc(
        ctx=SimpleNamespace(plugin_id="p"),
        bus="messages",
        plan={},
        timeout=0.01,
        serialize_plan=lambda p: {"x": 1},
    ) is None
    monkeypatch.setattr(builtins, "__import__", orig_import)

    settings_mod = ModuleType("plugin.settings")
    settings_mod.MESSAGE_PLANE_ZMQ_RPC_ENDPOINT = ""
    monkeypatch.setitem(sys.modules, "plugin.settings", settings_mod)
    assert bl._message_plane_replay_rpc(
        ctx=SimpleNamespace(plugin_id="p"),
        bus="messages",
        plan={},
        timeout=0.01,
        serialize_plan=lambda p: {"x": 1},
    ) is None


@pytest.mark.plugin_unit
def test_bus_list_rebuild_reload_and_intersection_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    bad_m = ModuleType("plugin.sdk.bus.messages")
    bad_m.MessageRecord = SimpleNamespace(from_index=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setitem(sys.modules, "plugin.sdk.bus.messages", bad_m)
    assert bl._rebuild_records_from_plane_items("messages", [{"index": {"a": 1}}]) == []

    target = SimpleNamespace(_items=[], _ctx=None, _cache_valid=False, plugin_id="old")
    refreshed = SimpleNamespace(plugin_id="new", dump_records=lambda: [1, 2, 3])
    bl._apply_reload_inplace_basic(target, refreshed, ctx="ctx")
    assert target._items == [1, 2, 3]
    assert target._ctx == "ctx"
    assert target._cache_valid is True
    assert target.plugin_id == "new"

    left = [SimpleNamespace(message_id="1"), SimpleNamespace(message_id="1"), SimpleNamespace(message_id="2")]
    right = [SimpleNamespace(message_id="1")]
    out = bl._intersection_unique_items(
        left,
        right,
        dedupe_key=lambda x: ("message_id", getattr(x, "message_id", "")),
    )
    assert [x.message_id for x in out] == ["1"]


@pytest.mark.plugin_unit
def test_bus_list_watcher_core_more_start_stop_edges() -> None:
    class _W(bl.BusListWatcherCore):
        def __init__(self) -> None:
            self._callbacks = []
            self._lock = threading.Lock()
            self._unsub = None
            self._sub_id = None
            self._ctx = SimpleNamespace()
            self._bus = "messages"
            self._list = SimpleNamespace(trace_tree_dump=lambda: {})
            self.ticked = 0

        def _watcher_set(self, sub_id: str) -> None:
            return None

        def _watcher_pop(self, sub_id: str) -> None:
            return None

        def _schedule_tick(self, op: str, payload=None) -> None:  # noqa: ANN001
            self.ticked += 1
            raise RuntimeError("tick err")

        def _state_subscribe(self, bus: str, on_event):  # type: ignore[override]
            on_event("add", {"x": 1})
            return lambda: None

    w = _W()
    assert w.start() is w
    assert w.ticked == 1

    w2 = _W()
    w2._unsub = lambda: None
    w2._sub_id = "already"
    assert w2.start() is w2

    w3 = _W()
    w3._unsub = None
    w3.stop()


@pytest.mark.plugin_unit
def test_bus_list_get_field_try_incremental_and_infer_edges() -> None:
    class _NoField:
        raw = None

        def dump(self):  # noqa: ANN201
            return []

    assert bl._get_field_from_record(_NoField(), "x") is None

    assert bl._try_incremental_local(
        op="add",
        payload={"record": {"x": 1}},
        bus="messages",
        ops=None,
        current_items=[],
        record_from_raw=lambda r: r,
        apply_ops_local=lambda i, o: i,
        dedupe_key=lambda x: ("k", "v"),
    ) is None

    base_e = [SimpleNamespace(event_id="e1"), SimpleNamespace(event_id="e2")]
    out_e = bl._try_incremental_local(
        op="del",
        payload={"event_id": "e1"},
        bus="events",
        ops=[],
        current_items=base_e,
        record_from_raw=lambda r: r,
        apply_ops_local=lambda i, o: i,
        dedupe_key=lambda x: ("event_id", getattr(x, "event_id", "")),
    )
    assert len(out_e or []) == 1

    base_l = [SimpleNamespace(lifecycle_id="l1"), SimpleNamespace(lifecycle_id="l2")]
    out_l = bl._try_incremental_local(
        op="del",
        payload={"lifecycle_id": "l1"},
        bus="lifecycle",
        ops=[],
        current_items=base_l,
        record_from_raw=lambda r: r,
        apply_ops_local=lambda i, o: i,
        dedupe_key=lambda x: ("lifecycle_id", getattr(x, "lifecycle_id", "")),
    )
    assert len(out_l or []) == 1

    assert bl._try_incremental_local(
        op="del",
        payload={},
        bus="events",
        ops=[],
        current_items=base_e,
        record_from_raw=lambda r: r,
        apply_ops_local=lambda i, o: i,
        dedupe_key=lambda x: ("event_id", getattr(x, "event_id", "")),
    ) is None

    left = SimpleNamespace(params={"bus": ""})
    right = SimpleNamespace(params={"bus": "events"})
    assert bl._infer_bus_from_plan(SimpleNamespace(left=left, right=right), conflict_error=RuntimeError) == "events"


@pytest.mark.plugin_unit
def test_bus_list_regex_debounce_and_injected_callback_more_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Compiled:
        def search(self, _text):  # noqa: ANN001
            raise RuntimeError("x")

    assert bl._filter_items_by_regex(
        items=[SimpleNamespace(v="x")],
        field="v",
        compiled=_Compiled(),
        get_field=lambda it, f: getattr(it, f, None),
        strict=False,
        error_factory=lambda e: ValueError(str(e)),
    ) == []

    class _W:
        def __init__(self) -> None:
            self._debounce_ms = 1.0
            self._lock = threading.Lock()
            self._pending_op = None
            self._pending_payload = None
            self._debounce_timer = None
            self.calls: list[tuple[str, object]] = []

        def _tick(self, op, payload):  # noqa: ANN001
            self.calls.append((op, payload))

    class _Timer:
        def __init__(self, _delay, fn):  # noqa: ANN001
            self.fn = fn
            self.daemon = False

        def start(self) -> None:
            self.fn()

    w = _W()
    fake_threading = ModuleType("threading")
    fake_threading.Timer = _Timer
    monkeypatch.setitem(sys.modules, "threading", fake_threading)
    bl._schedule_watcher_tick_debounced(w, "change", {"y": 2})
    assert w.calls

    class _TimerBad:
        def __init__(self, *_a, **_k) -> None:
            raise RuntimeError("timer fail")

    fake_threading2 = ModuleType("threading")
    fake_threading2.Timer = _TimerBad
    monkeypatch.setitem(sys.modules, "threading", fake_threading2)
    bl._schedule_watcher_tick_debounced(w, "add", {"z": 3})
    assert w.calls[-1][0] == "add"

    class _RecGood:
        def dump(self):  # noqa: ANN201
            return {"ok": 1}

    class _RecBad:
        def dump(self):  # noqa: ANN201
            raise RuntimeError("dump fail")

    got_kwargs: list[object] = []

    def _fn_kwargs(quickdump, kind=None):  # noqa: ANN001
        got_kwargs.append(quickdump)

    cb_kwargs = bl._build_watcher_injected_callback(_fn_kwargs)
    delta = SimpleNamespace(added=[_RecGood(), _RecBad()], removed=[], current=[], kind="add")
    cb_kwargs(delta)
    assert got_kwargs and isinstance(got_kwargs[0], tuple)

    got_required: list[object] = []

    def _fn_required(unknown):  # noqa: ANN001
        got_required.append(unknown)

    cb_required = bl._build_watcher_injected_callback(_fn_required)
    cb_required(delta)
    assert got_required and got_required[0] is delta

    got_fallback: list[object] = []

    def _fn_raise(delta=None, quickdump=None):  # noqa: ANN001
        if quickdump is not None:
            raise RuntimeError("kw err")
        got_fallback.append(delta)

    cb_raise = bl._build_watcher_injected_callback(_fn_raise)
    cb_raise(delta)
    assert got_fallback and got_fallback[0] is delta


def _install_replay_env(monkeypatch: pytest.MonkeyPatch, sock_obj: object, endpoint: str = "ipc://plane") -> None:
    class _Ctx:
        @classmethod
        def instance(cls):  # noqa: ANN206
            return SimpleNamespace(socket=lambda _typ: sock_obj)

    fake_zmq = SimpleNamespace(Context=_Ctx, DEALER=1, IDENTITY=2, LINGER=3, POLLIN=4)
    monkeypatch.setitem(sys.modules, "zmq", fake_zmq)

    settings_mod = ModuleType("plugin.settings")
    settings_mod.MESSAGE_PLANE_ZMQ_RPC_ENDPOINT = endpoint
    monkeypatch.setitem(sys.modules, "plugin.settings", settings_mod)
    monkeypatch.setattr(uuid, "uuid4", lambda: "u1")


@pytest.mark.plugin_unit
def test_bus_list_replay_rpc_remaining_error_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Sock:
        def __init__(self, *, poll_val=1, recv_val=b"", send_err=False, poll_err=False, recv_err=False) -> None:
            self.poll_val = poll_val
            self.recv_val = recv_val
            self.send_err = send_err
            self.poll_err = poll_err
            self.recv_err = recv_err

        def setsockopt(self, *_a, **_k) -> None:
            return None

        def connect(self, _endpoint):  # noqa: ANN001
            return None

        def send(self, _raw, flags=0):  # noqa: ANN001
            if self.send_err:
                raise RuntimeError("send")

        def poll(self, timeout, flags):  # noqa: ANN001
            if self.poll_err:
                raise RuntimeError("poll")
            return self.poll_val

        def recv(self, flags=0):  # noqa: ANN001
            if self.recv_err:
                raise RuntimeError("recv")
            return self.recv_val

    _install_replay_env(monkeypatch, _Sock(), endpoint="ipc://plane")
    assert bl._message_plane_replay_rpc(
        ctx=SimpleNamespace(plugin_id="p"),
        bus="messages",
        plan={},
        timeout=0.01,
        serialize_plan=lambda _p: None,
    ) is None

    class _CtxAttrFail:
        plugin_id = "p"

        def __setattr__(self, name, value):  # noqa: ANN001
            if name in {"_mp_replay_tls", "_mp_replay_sock"}:
                raise RuntimeError("set fail")
            object.__setattr__(self, name, value)

        def __getattr__(self, name):  # noqa: ANN001
            if name in {"_mp_replay_sock"}:
                raise RuntimeError("get fail")
            raise AttributeError(name)

    _install_replay_env(monkeypatch, _Sock(recv_val=ormsgpack.packb({"req_id": "x"})))
    assert bl._message_plane_replay_rpc(
        ctx=_CtxAttrFail(),
        bus="messages",
        plan={},
        timeout=0.0001,
        serialize_plan=lambda _p: {"x": 1},
    ) is None

    class _TLSBad:
        def __setattr__(self, _n, _v) -> None:
            raise RuntimeError("tls set")

    ctx_tls = SimpleNamespace(plugin_id="p", _mp_replay_tls=_TLSBad())
    _install_replay_env(monkeypatch, _Sock(send_err=True))
    assert bl._message_plane_replay_rpc(
        ctx=ctx_tls,
        bus="messages",
        plan={},
        timeout=0.01,
        serialize_plan=lambda _p: {"x": 1},
    ) is None

    _install_replay_env(monkeypatch, _Sock(recv_val=b"{}"))
    original_packb = ormsgpack.packb
    monkeypatch.setattr("os.getenv", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("env")))
    monkeypatch.setattr(ormsgpack, "packb", lambda _x: (_ for _ in ()).throw(RuntimeError("pack")))
    assert bl._message_plane_replay_rpc(
        ctx=SimpleNamespace(plugin_id="p"),
        bus="messages",
        plan={},
        timeout=0.01,
        serialize_plan=lambda _p: {"x": 1},
    ) is None

    monkeypatch.setattr(ormsgpack, "packb", original_packb)
    _install_replay_env(monkeypatch, _Sock(poll_err=True))
    assert bl._message_plane_replay_rpc(
        ctx=SimpleNamespace(plugin_id="p"),
        bus="messages",
        plan={},
        timeout=0.01,
        serialize_plan=lambda _p: {"x": 1},
    ) is None

    _install_replay_env(monkeypatch, _Sock(recv_err=True))
    assert bl._message_plane_replay_rpc(
        ctx=SimpleNamespace(plugin_id="p"),
        bus="messages",
        plan={},
        timeout=0.01,
        serialize_plan=lambda _p: {"x": 1},
    ) is None

    _install_replay_env(monkeypatch, _Sock(recv_val=b"\xff"))
    assert bl._message_plane_replay_rpc(
        ctx=SimpleNamespace(plugin_id="p"),
        bus="messages",
        plan={},
        timeout=0.0001,
        serialize_plan=lambda _p: {"x": 1},
    ) is None

    req = "replay:p:u1"
    _install_replay_env(monkeypatch, _Sock(recv_val=ormsgpack.packb({"req_id": req, "ok": False})))
    assert bl._message_plane_replay_rpc(
        ctx=SimpleNamespace(plugin_id="p"),
        bus="messages",
        plan={},
        timeout=0.01,
        serialize_plan=lambda _p: {"x": 1},
    ) is None

    _install_replay_env(monkeypatch, _Sock(recv_val=ormsgpack.packb({"req_id": req, "ok": True, "result": 1})))
    assert bl._message_plane_replay_rpc(
        ctx=SimpleNamespace(plugin_id="p"),
        bus="messages",
        plan={},
        timeout=0.01,
        serialize_plan=lambda _p: {"x": 1},
    ) is None

    _install_replay_env(monkeypatch, _Sock(recv_val=ormsgpack.packb({"req_id": req, "ok": True, "result": {"items": 1}})))
    assert bl._message_plane_replay_rpc(
        ctx=SimpleNamespace(plugin_id="p"),
        bus="messages",
        plan={},
        timeout=0.01,
        serialize_plan=lambda _p: {"x": 1},
    ) is None

    _install_replay_env(monkeypatch, _Sock())
    assert bl._message_plane_replay_rpc(
        ctx=SimpleNamespace(plugin_id="p"),
        bus="messages",
        plan={},
        timeout=0.01,
        serialize_plan=lambda _p: (_ for _ in ()).throw(RuntimeError("outer")),
    ) is None


@pytest.mark.plugin_unit
def test_bus_list_last_branches_for_100(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SockSeq:
        def __init__(self) -> None:
            self.poll_calls = 0
            self.recv_calls = 0

        def setsockopt(self, *_a, **_k) -> None:
            return None

        def connect(self, _endpoint):  # noqa: ANN001
            return None

        def send(self, _raw, flags=0):  # noqa: ANN001
            return None

        def poll(self, timeout, flags):  # noqa: ANN001
            self.poll_calls += 1
            if self.poll_calls == 1:
                return 0
            if self.poll_calls == 2:
                return 1
            raise RuntimeError("poll stop")

        def recv(self, flags=0):  # noqa: ANN001
            self.recv_calls += 1
            return b"\xff"

    _install_replay_env(monkeypatch, _SockSeq())
    assert bl._message_plane_replay_rpc(
        ctx=SimpleNamespace(plugin_id="p"),
        bus="messages",
        plan={},
        timeout=0.01,
        serialize_plan=lambda _p: {"x": 1},
    ) is None

    assert bl._try_incremental_local(
        op="del",
        payload={"x": 1},
        bus="unknown",
        ops=[],
        current_items=[SimpleNamespace(message_id="1")],
        record_from_raw=lambda r: r,
        apply_ops_local=lambda i, o: i,
        dedupe_key=lambda x: ("message_id", getattr(x, "message_id", "")),
    ) is None
    assert bl._try_incremental_local(
        op="change",
        payload={"x": 1},
        bus="messages",
        ops=[],
        current_items=[],
        record_from_raw=lambda r: r,
        apply_ops_local=lambda i, o: i,
        dedupe_key=lambda x: ("message_id", getattr(x, "message_id", "")),
    ) is None

    class _NoDump:
        pass

    calls: list[object] = []

    def _fn_var(quickdump, *args):  # noqa: ANN001, ANN002
        calls.append(quickdump)

    cb_var = bl._build_watcher_injected_callback(_fn_var)
    cb_var(SimpleNamespace(added=[_NoDump()], removed=[], current=[], kind="add"))
    assert calls and calls[0][0].__class__ is _NoDump

    def _fn_missing(quickdump, missing):  # noqa: ANN001
        return (quickdump, missing)

    cb_missing = bl._build_watcher_injected_callback(_fn_missing)
    delta = SimpleNamespace(added=[], removed=[], current=[], kind="change")
    with pytest.raises(TypeError):
        cb_missing(delta)

    calls_missing_ok: list[object] = []

    def _fn_missing_ok(unknown, *args):  # noqa: ANN001, ANN002
        calls_missing_ok.append(unknown)

    cb_missing_ok = bl._build_watcher_injected_callback(_fn_missing_ok)
    cb_missing_ok(delta)
    assert calls_missing_ok and calls_missing_ok[0] is delta

    assert bl._infer_bus_from_plan(object(), conflict_error=RuntimeError) == ""


@pytest.mark.plugin_unit
def test_bus_list_replay_unpack_and_json_decode_both_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Sock:
        def __init__(self) -> None:
            self._poll_n = 0

        def setsockopt(self, *_a, **_k) -> None:
            return None

        def connect(self, _endpoint):  # noqa: ANN001
            return None

        def send(self, _raw, flags=0):  # noqa: ANN001
            return None

        def poll(self, timeout, flags):  # noqa: ANN001
            self._poll_n += 1
            if self._poll_n == 1:
                return 1
            raise RuntimeError("stop")

        def recv(self, flags=0):  # noqa: ANN001
            return object()

    _install_replay_env(monkeypatch, _Sock())
    assert bl._message_plane_replay_rpc(
        ctx=SimpleNamespace(plugin_id="p"),
        bus="messages",
        plan={},
        timeout=0.01,
        serialize_plan=lambda _p: {"x": 1},
    ) is None
