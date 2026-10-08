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
    """host 侧用不用 Manager，取决于 start method 能不能把代理传给子进程。

    原名 ``test_host_response_map_still_uses_shared_manager``，它不分平台地断言
    "host 仍然用共享 Manager"。那条守卫的本意是：为子进程做的改动不得顺手把 host 的
    跨进程共享弄丢。这个本意在 **fork** 上完全成立，照原样保留（下面 ``inherits=True``
    分支的断言一字未改）。

    但在 **spawn** 上它不成立：spawn 子进程是全新解释器，物理上不可能继承父进程的
    Manager 代理；而插件子进程更是明确不用——``host.py:909`` 在子进程入口
    ``_plugin_process_runner`` 里无条件调 ``mark_plugin_child_process()``，于是走
    ``state.py`` 的 ``_is_plugin_child_process`` 分支拿普通 dict（本文件的
    ``test_spawned_plugin_child_response_lookup_never_starts_manager`` 用真实 spawn
    钉住了这一点）。全产品只有一个 ``multiprocessing.Process(...)``（``host.py:2138``），
    所以没有任何进程能继承到代理。

    于是 spawn 平台上 Manager 的跨进程共享没有消费者，却要付：一次冷启动（实测
    298–358ms 轻载 / 566–792ms 重载，harness 阶段名 ``state.manager_create``）、一个
    常驻 manager 子进程（实测进程树峰值 3→2、线程 28→21、RSS −27MB），以及**每次代理
    读写到 manager 进程的 socket 往返**——后者落在 ``ev.wait(timeout=0.01)`` 那种
    轮询循环里。

    ⚠️ ``inherits=False`` 分支必须同时断言"事件仍然可用"：把表换成普通 dict 之后，
    ``_get_or_create_response_event`` 里 ``mgr is None`` 那条会返回 None，服务器就此
    失去事件唤醒、退化成纯短轮询——启动变快而响应等待变慢，且没有任何东西会红。
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

        def Event(self):  # noqa: N802 - 与 multiprocessing.Manager 同名
            return threading.Event()

    monkeypatch.setattr(state_module.multiprocessing, "Manager", _FakeManager)
    host_state = state_module.GlobalState()

    assert host_state.get_plugin_response("rid-3") is None

    if inherits:
        # fork：原有断言，一字不改。
        assert len(created) == 1
        assert host_state._plugin_response_map_manager is created[0]
        assert host_state.plugin_response_map is created[0].dicts[0]
        assert host_state.plugin_response_event_map is created[0].dicts[1]
        assert host_state._response_maps_are_local is False
    else:
        # spawn：不得建 Manager，但事件必须仍然可用（否则退化成纯短轮询）。
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
