"""Every store a client can `get()` from must be replayable.

``BusRpcClientBase.get()`` attaches a ``GetNode`` carrying its ``_store_name``,
and the first chained operation (``sort``/``limit``/``filter``) invalidates the
cache so materialization goes through ``BusList._replay_plan``. A store missing
from that dispatch is not "one less feature" -- it raises
``NonReplayableTraceError`` the moment a plugin follows the documented
``frames.sort(by="timestamp", reverse=True).limit(1)`` example.

The two stores this branch adds, ``frames`` and ``conversations``, were both
missing from it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from plugin.core.bus.types import BusList, GetNode, NonReplayableTraceError


class _FakeClient:
    def __init__(self, name: str, seen: list) -> None:
        self._name = name
        self._seen = seen

    def get(self, **params):
        self._seen.append((self._name, params))
        return BusList([])


def _ctx_with(seen: list):
    bus = SimpleNamespace(
        messages=_FakeClient("messages", seen),
        events=_FakeClient("events", seen),
        lifecycle=_FakeClient("lifecycle", seen),
        frames=_FakeClient("frames", seen),
        conversations=_FakeClient("conversations", seen),
    )
    return SimpleNamespace(bus=bus)


def _replay(store: str, seen: list):
    node = GetNode(op="get", params={"bus": store, "params": {"max_count": 4}}, at=0.0)
    return BusList([])._replay_plan(_ctx_with(seen), node)


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    "store", ["messages", "events", "lifecycle", "frames", "conversations"]
)
def test_every_client_backed_store_can_be_replayed(store: str) -> None:
    """Mutation: drop the ``frames`` or ``conversations`` branch."""
    seen: list = []
    _replay(store, seen)
    assert seen == [(store, {"max_count": 4})], (
        f"{store} 的重放没有落到对应的 client 上——插件一链式调用就会抛"
    )


@pytest.mark.plugin_unit
def test_an_unknown_store_still_raises() -> None:
    """The dispatch stays a whitelist; adding stores must not open it up."""
    with pytest.raises(NonReplayableTraceError):
        _replay("not_a_store", [])


@pytest.mark.plugin_unit
def test_the_replayable_stores_match_what_clients_expose() -> None:
    """Discovery, not a hand-written list.

    A new read-only bus client added later gets a ``_store_name`` and inherits
    ``get()``; if nobody remembers this dispatch, its first chained call raises.
    This finds the clients and asserts each one's store is replayable, so the
    next store cannot be forgotten silently.
    """
    import inspect
    import pkgutil
    from importlib import import_module

    import plugin.core.bus as bus_pkg
    from plugin.core.bus._client_base import BusRpcClientBase

    names = set()
    for mod in pkgutil.iter_modules(bus_pkg.__path__):
        module = import_module(f"{bus_pkg.__name__}.{mod.name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, BusRpcClientBase)
                and obj is not BusRpcClientBase
                and getattr(obj, "_store_name", None)
            ):
                names.add(obj._store_name)

    assert names, "前提没成立：一个 bus client 都没发现"
    for store in sorted(names):
        seen: list = []
        try:
            _replay(store, seen)
        except NonReplayableTraceError:  # pragma: no cover - the failure we guard
            pytest.fail(
                f"client 暴露了 store {store!r}，但 _replay_plan 不认识它——"
                "插件对它做任何链式操作都会抛"
            )


# ── the documented chain, run the way the docs run it ──────────────────


class _StubRpc:
    """Stands in for the ZMQ round trip on both the sync and async seam."""

    RESP = {
        "ok": True,
        "result": {
            "items": [
                {"payload": {"id": "f1", "captured_at": 2.0, "source": "screen"}},
                {"payload": {"id": "f2", "captured_at": 1.0, "source": "screen"}},
                {"payload": {"id": "f3", "captured_at": 3.0, "source": "screen"}},
            ]
        },
    }

    async def request_async(self, **_kw):
        return self.RESP

    def request(self, **_kw):
        return self.RESP


def _frame_client():
    from plugin.core.bus.frames import FrameClient

    ctx = SimpleNamespace(plugin_id="demo", _mp_rpc_client=_StubRpc(), bus=None)
    client = FrameClient(ctx)
    ctx.bus = SimpleNamespace(frames=client)
    return ctx, client


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_documented_frame_chain_works_inside_an_async_handler() -> None:
    """``await bus.frames.get(...)`` then ``.sort(...).limit(1)``.

    That is the example in docs/plugins/sdk-reference.md, and a plugin handler
    is async, so this is the only way it ever runs. Without ``FrameList``
    declaring itself a snapshot the chain puts the list in lazy mode, and
    materialization synchronously calls ``FrameClient.get()`` while the loop is
    running -- which returns a coroutine, not a list:
    ``AttributeError: 'coroutine' object has no attribute 'sort'``.

    Mutation: drop ``FrameList._snapshot_chain``.
    """
    _ctx, client = _frame_client()
    frames = await client.get(max_count=9)

    latest = frames.sort(by="timestamp", reverse=True).limit(2)

    assert [r.frame_id for r in latest] == ["f3", "f1"], (
        "链式结果不对——不崩不等于排对了：lazy 模式下 sort() 根本不在本地算"
    )


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_async_and_sync_frame_chains_agree() -> None:
    """The same expression must not depend on whether a loop is running.

    A fix that only stops the crash could still leave the async path returning
    unsorted records; pinning it against the sync path is what makes that
    visible.
    """
    def _sync_leg():
        # In a worker thread on purpose: ``get()`` branches on whether a loop is
        # running, and this test function is itself a coroutine, so calling it
        # here would be the async path wearing a sync name.
        _sctx, sync_client = _frame_client()
        return [
            r.frame_id
            for r in sync_client.get(max_count=9)
            .sort(by="timestamp", reverse=True)
            .limit(2)
        ]

    sync_out = await asyncio.to_thread(_sync_leg)

    _actx, async_client = _frame_client()
    frames = await async_client.get(max_count=9)
    async_out = [r.frame_id for r in frames.sort(by="timestamp", reverse=True).limit(2)]

    assert async_out == sync_out == ["f3", "f1"]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_frames_can_still_be_reloaded_explicitly() -> None:
    """The snapshot flag must not cost the frames list its replay plan.

    ``_is_lazy_mode()`` gates chaining; ``reload_with``/``reload_with_async``
    look at ``_plan`` directly. Keeping the plan is what lets a plugin ask for
    fresh frames on purpose -- and it is why ``_replay_plan``'s ``frames``
    branch is still a live path rather than dead code.
    """
    ctx, client = _frame_client()
    frames = await client.get(max_count=9)

    assert frames._plan is not None, "计划被顺手删了——显式 reload 就没了"
    refreshed = await frames.reload_with_async(ctx)
    assert [r.frame_id for r in refreshed] == ["f1", "f2", "f3"]


def _bus_client(store: str):
    from plugin.core.bus.events import EventClient
    from plugin.core.bus.lifecycle import LifecycleClient
    from plugin.core.bus.messages import MessageClient

    id_field = {
        "messages": "message_id",
        "events": "event_id",
        "lifecycle": "lifecycle_id",
    }[store]

    class _MultiStoreStubRpc:
        def _resp(self):
            return {
                "ok": True,
                "result": {
                    "items": [
                        {"payload": {id_field: "r1", "type": "a", "timestamp": 2.0, "source": "s1"}},
                        {"payload": {id_field: "r2", "type": "b", "timestamp": 5.0, "source": "s2"}},
                        {"payload": {id_field: "r3", "type": "a", "timestamp": 4.0, "source": "s1"}},
                    ]
                },
            }

        async def request_async(self, **_kw):
            return self._resp()

        def request(self, **_kw):
            return self._resp()

    ctx = SimpleNamespace(plugin_id="demo", _mp_rpc_client=_MultiStoreStubRpc(), bus=None)
    client_cls = {
        "messages": MessageClient,
        "events": EventClient,
        "lifecycle": LifecycleClient,
    }[store]
    client = client_cls(ctx)
    ctx.bus = SimpleNamespace(**{store: client})
    return ctx, client, id_field


@pytest.mark.plugin_unit
@pytest.mark.parametrize("store", ["messages", "events", "lifecycle"])
@pytest.mark.asyncio
async def test_async_chained_bus_list_evaluates_without_coroutine_leak(store: str) -> None:
    """F0028: chained filter/sort/limit on async get() evaluates locally without coroutine leak."""
    import warnings

    def _sync_leg():
        _sctx, sync_client, id_field = _bus_client(store)
        chained = sync_client.get(max_count=10).filter(type="a").sort(by="timestamp", reverse=True).limit(1)
        return [getattr(r, id_field) for r in chained]

    sync_ids = await asyncio.to_thread(_sync_leg)

    ctx, async_client, id_field = _bus_client(store)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        snapshot = await async_client.get(max_count=10)
        chained = snapshot.filter(type="a").sort(by="timestamp", reverse=True).limit(1)
        assert len(chained) == 1
        async_ids = [getattr(r, id_field) for r in chained]
        dumped_ids = [getattr(r, id_field) for r in chained.dump_records()]

    assert async_ids == sync_ids == ["r3"]
    assert dumped_ids == ["r3"]
    assert chained._plan is not None
    reloaded = await chained.reload_with_async(ctx)
    assert [getattr(r, id_field) for r in reloaded] == ["r3"]
    leaked = [w for w in caught if issubclass(w.category, RuntimeWarning) and "was never awaited" in str(w.message)]
    assert leaked == []


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_sync_reload_inside_event_loop_closes_coroutine_and_raises() -> None:
    """Direct sync reload() inside an active event loop closes the coroutine and raises NonReplayableTraceError."""
    import warnings

    ctx, client, _ = _bus_client("events")
    snapshot = await client.get(max_count=10)
    chained = snapshot.filter(type="a")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(NonReplayableTraceError):
            chained.reload(ctx)
    leaked = [w for w in caught if issubclass(w.category, RuntimeWarning) and "was never awaited" in str(w.message)]
    assert leaked == []


@pytest.mark.plugin_unit
@pytest.mark.parametrize("store", ["messages", "events", "lifecycle"])
def test_sync_lazy_bus_list_consumed_in_asyncio_run_raises_without_leaks(store: str) -> None:
    """F0028: unmaterialized sync-lazy BusList crossed into asyncio.run() must raise cleanly."""
    import gc
    import warnings

    ctx, sync_client, id_field = _bus_client(store)
    lazy_list = (
        sync_client.get(max_count=10)
        .filter(type="a")
        .sort(by="timestamp", reverse=True)
        .limit(1)
    )
    assert lazy_list._cache_valid is False

    async def _consume() -> None:
        with pytest.raises(NonReplayableTraceError):
            len(lazy_list)
        with pytest.raises(NonReplayableTraceError):
            lazy_list.dump_records()
        with pytest.raises(NonReplayableTraceError):
            lazy_list.filter(source="s1")
        with pytest.raises(NonReplayableTraceError):
            lazy_list.sort(by="timestamp")
        with pytest.raises(NonReplayableTraceError):
            lazy_list.limit(1)
        with pytest.raises(NonReplayableTraceError):
            lazy_list.where(lambda _r: True)

        reloaded = await lazy_list.reload_with_async(ctx)
        assert [getattr(r, id_field) for r in reloaded] == ["r3"]

        await lazy_list.reload_with_async(ctx, inplace=True)
        assert len(lazy_list) == 1
        assert [getattr(r, id_field) for r in lazy_list.dump_records()] == ["r3"]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        asyncio.run(_consume())
        gc.collect()

    leaked = [
        w
        for w in caught
        if issubclass(w.category, RuntimeWarning) and "was never awaited" in str(w.message)
    ]
    assert leaked == []


@pytest.mark.plugin_unit
@pytest.mark.parametrize("store", ["messages", "events", "lifecycle"])
def test_non_replayable_async_bus_list_chained_outside_event_loop_stays_eager(store: str) -> None:
    """Results from where()/sort(key=callable) stay locally materialized when chained outside the loop."""
    ctx, client, id_field = _bus_client(store)

    async def _produce():
        snapshot = await client.get(max_count=10)
        return (
            snapshot.where(lambda r: r.type == "a"),
            snapshot.sort(key=lambda r: float(r.timestamp or 0.0), reverse=True),
        )

    where_list, key_sorted_list = asyncio.run(_produce())

    def _fail_sync_rpc(**_kw):  # pragma: no cover - must not be called
        raise AssertionError("non-replayable BusList must not replay on chained local ops")

    ctx._mp_rpc_client.request = _fail_sync_rpc

    w_lim = where_list.limit(1)
    w_filt = where_list.filter(source="s1")
    s_lim = key_sorted_list.limit(1)

    assert w_lim._cache_valid is True
    assert w_filt._cache_valid is True
    assert s_lim._cache_valid is True

    assert len(w_lim) == 1
    assert [getattr(r, id_field) for r in w_lim] == ["r1"]
    assert [getattr(r, id_field) for r in w_filt.dump_records()] == ["r1", "r3"]
    assert [getattr(r, id_field) for r in s_lim] == ["r2"]

    with pytest.raises(NonReplayableTraceError):
        w_lim.reload(ctx)
    with pytest.raises(NonReplayableTraceError):
        s_lim.reload(ctx)

    # Structured sync chains still remain lazy and replay when materialized.
    replay_ctx, sync_client, _ = _bus_client(store)
    lazy_structured = sync_client.get(max_count=10).filter(type="a").limit(1)
    assert lazy_structured._cache_valid is False
    assert len(lazy_structured) == 1
    assert [getattr(r, id_field) for r in lazy_structured.reload(replay_ctx)] == ["r1"]
