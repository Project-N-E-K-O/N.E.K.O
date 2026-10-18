from __future__ import annotations

import importlib
import multiprocessing
from types import SimpleNamespace

import pytest

from plugin.core import host as host_module


# plugin.core.state is shadowed by the `state` singleton on the package.
state_module = importlib.import_module("plugin.core.state")

pytestmark = pytest.mark.plugin_unit


def _forbid_manager(monkeypatch):
    def _fail():
        raise AssertionError("plugin child must not start a multiprocessing.Manager")

    monkeypatch.setattr(state_module.multiprocessing, "Manager", _fail)


def test_plugin_child_response_lookup_never_starts_manager(monkeypatch):
    _forbid_manager(monkeypatch)
    child_state = state_module.GlobalState()
    child_state.mark_plugin_child_process()

    assert child_state.get_plugin_response("rid-1") is None
    assert child_state.peek_plugin_response("rid-1") is None
    assert child_state.plugin_response_map == {}
    assert child_state.plugin_response_event_map == {}
    assert child_state._plugin_response_map_manager is None


def test_plugin_child_keeps_inherited_response_map(monkeypatch):
    _forbid_manager(monkeypatch)
    child_state = state_module.GlobalState()
    inherited = {"rid-2": {"response": {"ok": True}, "expire_time": float("inf")}}
    child_state._plugin_response_map = inherited
    child_state.mark_plugin_child_process()

    assert child_state.get_plugin_response("rid-2") == {"ok": True}
    assert inherited == {}


@pytest.mark.parametrize("inherits", [True, False])
def test_host_response_map_matches_the_start_method(monkeypatch, inherits):
    """Whether the host uses a Manager depends on whether the start method can pass the proxy to a child process.

Original name: ``test_host_response_map_still_uses_shared_manager``. It asserted
\"host still uses a shared Manager\" on every platform. The guard ensures changes for child processes do not
\"host still uses a shared Manager\" on every platform. The guard exists to ensure that changes made for child processes
do not accidentally remove the host's cross-process sharing. This intent is fully valid under **fork**,
so keep the original assertion unchanged (the ``inherits=True`` branch below remains unchanged).

But this does not hold under **spawn**: a spawned child is a fresh interpreter and physically cannot inherit its parent's
Manager proxy; plugin child processes explicitly avoid one as well — at the ``_plugin_process_runner`` entry,
``host.py:909`` unconditionally calls ``mark_plugin_child_process()``, which takes the ordinary-dict
``_is_plugin_child_process`` branch in ``state.py`` (pinned by the real-spawn test
``test_spawned_plugin_child_response_lookup_never_starts_manager`` in this file). The entire product has only one
``multiprocessing.Process(...)`` (``host.py:2138``), so no process can inherit the proxy.

Therefore, on spawn platforms the Manager's cross-process sharing has no consumers, but still costs a cold start
(measured 298–358 ms under light load / 566–792 ms under heavy load, harness phase ``state.manager_create``),
a persistent manager child process (measured process-tree peak 3→2, threads 28→21, RSS −27 MB), and **a socket
round trip to the manager process for every proxy read/write** — the latter occurs inside polling loops such as
``ev.wait(timeout=0.01)``.

⚠️ The ``inherits=False`` branch must also assert that \"events remain usable\": after replacing the table with an
ordinary dict, the ``mgr is None`` branch in ``_get_or_create_response_event`` returns None, and the server loses
event wakeups and falls back to tight polling — startup becomes faster, but response waits become slower, with no failure.
    """
    import threading

    monkeypatch.setattr(
        state_module, "_start_method_inherits_manager_proxies", lambda: inherits
    )
    created: list[object] = []

    class _FakeManager:
        def __init__(self):
            self.dicts: list[dict] = []
            created.append(self)

        def dict(self):
            shared: dict = {}
            self.dicts.append(shared)
            return shared

        def Event(self):  # noqa: N802 - same name as multiprocessing.Manager
            return threading.Event()

    monkeypatch.setattr(state_module.multiprocessing, "Manager", _FakeManager)
    host_state = state_module.GlobalState()

    assert host_state.get_plugin_response("rid-3") is None

    if inherits:
        # fork: existing assertion, unchanged.
        assert len(created) == 1
        assert host_state._plugin_response_map_manager is created[0]
        assert host_state.plugin_response_map is created[0].dicts[0]
        assert host_state.plugin_response_event_map is created[0].dicts[1]
        assert host_state._response_maps_are_local is False
    else:
        # spawn: must not create a Manager, but events must remain usable (otherwise it degrades to tight polling).
        assert created == [], f"spawn 平台仍创建了 Manager：{created}"
        assert host_state._plugin_response_map_manager is None
        assert type(host_state.plugin_response_map) is dict
        assert type(host_state.plugin_response_event_map) is dict
        assert host_state._response_maps_are_local is True
        ev = host_state._get_or_create_response_event("rid-3")
        assert isinstance(ev, threading.Event), (
            f"spawn 平台拿不到可用事件（得到 {ev!r}）——服务器会退化成纯短轮询等插件响应"
        )
        assert host_state._get_or_create_response_event("rid-3") is ev


def test_plugin_process_runner_marks_child_before_serving(monkeypatch):
    marks: list[bool] = []

    class _StopRunner(Exception):
        pass

    def _mark():
        marks.append(True)
        raise _StopRunner

    monkeypatch.setattr(host_module, "state", SimpleNamespace(mark_plugin_child_process=_mark))

    with pytest.raises(_StopRunner):
        host_module._plugin_process_runner(
            "demo", "plugins.demo:Plugin", "plugin.toml", "ipc://down", "ipc://up", uplink_token="token",
        )
    assert marks == [True]


def _probe_spawned_child_response_lookup(result_queue) -> None:
    # Runs in a fresh spawn interpreter: no inherited proxies, real module state.
    import multiprocessing as mp

    from plugin.core.state import state as child_state

    started: list[bool] = []
    original_manager = mp.Manager

    def _tracking_manager(*args, **kwargs):
        started.append(True)
        return original_manager(*args, **kwargs)

    mp.Manager = _tracking_manager
    try:
        child_state.mark_plugin_child_process()
        lookup = (
            child_state.get_plugin_response("rid-spawn"),
            child_state.peek_plugin_response("rid-spawn"),
            type(child_state.plugin_response_map).__name__,
            type(child_state.plugin_response_event_map).__name__,
        )
        result_queue.put((started, child_state._plugin_response_map_manager is None, lookup))
    finally:
        mp.Manager = original_manager


def test_spawned_plugin_child_response_lookup_never_starts_manager():
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(target=_probe_spawned_child_response_lookup, args=(result_queue,))
    process.start()
    try:
        started, no_manager, lookup = result_queue.get(timeout=60)
    finally:
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0
    assert started == []
    assert no_manager is True
    assert lookup == (None, None, "dict", "dict")
