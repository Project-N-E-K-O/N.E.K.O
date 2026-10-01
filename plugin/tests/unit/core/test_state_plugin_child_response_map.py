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


def test_host_response_map_still_uses_shared_manager(monkeypatch):
    created: list[object] = []

    class _FakeManager:
        def __init__(self):
            self.dicts: list[dict] = []
            created.append(self)

        def dict(self):
            shared: dict = {}
            self.dicts.append(shared)
            return shared

    monkeypatch.setattr(state_module.multiprocessing, "Manager", _FakeManager)
    host_state = state_module.GlobalState()

    assert host_state.get_plugin_response("rid-3") is None
    assert len(created) == 1
    assert host_state._plugin_response_map_manager is created[0]
    assert host_state.plugin_response_map is created[0].dicts[0]
    assert host_state.plugin_response_event_map is created[0].dicts[1]


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
