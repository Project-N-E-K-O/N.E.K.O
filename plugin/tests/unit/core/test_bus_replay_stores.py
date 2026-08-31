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
