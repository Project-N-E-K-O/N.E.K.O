"""响应表：只有 fork 平台才需要 multiprocessing.Manager。

Windows 一律 spawn（``app/main_server/__init__..py:56`` 的 ``set_start_method("fork")``
被 ``sys.platform != "win32"`` 挡着），spawn 子进程是全新解释器，**不可能**继承父进程
的 Manager 代理。而插件子进程更是明确不用：``host.py:909`` 在子进程入口
``_plugin_process_runner`` 里无条件调 ``state.mark_plugin_child_process()``，于是
``plugin_response_map`` 走 ``_is_plugin_child_process`` 分支拿普通 dict、回应走 ZMQ。

所以在 spawn 平台上，Manager 的跨进程共享**没有任何消费者**，却要付三笔钱：

* 一次 ``multiprocessing.Manager()`` 冷启动 —— 实测 ~320ms，占冷启动的 10%
  （harness 阶段名 ``state.manager_create``）
* 一个常驻的 manager 子进程（进程树 +1、RSS 增加）
* **每次代理读写都是一次到 manager 进程的 socket 往返**，而
  ``_get_or_create_response_event`` 造出来的 Event 是在
  ``while True: ev.wait(timeout=0.01)`` 这样的轮询循环里被反复等的

最容易踩的坑是第二条派生出来的：把 map 换成普通 dict 之后，
``_get_or_create_response_event`` 里 ``mgr is None`` 那条**会返回 None**，于是服务器
失去事件唤醒、退化成纯短轮询——启动变快了，响应等待却变慢了，而且没有任何东西会红。
所以这里既钉"不建 Manager"，也钉"必须给出一个能用的 threading.Event"。

变异清单（每条都应有测试变红）：
* ``_start_method_inherits_manager_proxies`` 恒返回 True → 1 红
* 去掉 ``_response_maps_are_local`` 分支、让 ``mgr is None`` 直接 return None → 3 红
* 把子进程也改成返回 threading.Event → 4 红
* 拿不到 start method 时改成返回 False（激进而非保守）→ 6 红
"""

from __future__ import annotations

import multiprocessing
import sys
import threading
import time

import pytest

# ⚠️ 不能用 ``from plugin.core import state``：``plugin/core/__init__.py`` 把同名的
# 单例对象也导出了，那个名字会**遮蔽子模块**（#3242 的提交信息里专门提到
# "plugin.core.state stays eager because its name collides with the submodule"）。
# 拿到的是 GlobalState 实例而不是模块，monkeypatch 会以 AttributeError 失败。
import plugin.core.state  # noqa: F401  - 只为把模块放进 sys.modules
from plugin.core.state import GlobalState

state_module = sys.modules["plugin.core.state"]

pytestmark = pytest.mark.plugin_unit


@pytest.fixture
def no_real_manager(monkeypatch: pytest.MonkeyPatch):
    """别让测试真起一个 manager 子进程：记录调用并返回一个假 Manager。"""
    calls: list[str] = []

    class _FakeDict(dict):
        pass

    class _FakeEvent:
        def __init__(self) -> None:
            self._ev = threading.Event()

        def set(self) -> None:
            self._ev.set()

        def wait(self, timeout=None):
            return self._ev.wait(timeout)

        def clear(self) -> None:
            self._ev.clear()

    class _FakeManager:
        def dict(self):
            calls.append("dict")
            return _FakeDict()

        def Event(self):  # noqa: N802 - 与 multiprocessing.Manager 同名
            calls.append("Event")
            return _FakeEvent()

    def _fake_manager_factory(*args, **kwargs):
        calls.append("Manager")
        return _FakeManager()

    monkeypatch.setattr(state_module.multiprocessing, "Manager", _fake_manager_factory)
    return calls


# ── 1. spawn 平台：不建 Manager ─────────────────────────────────────────


def test_no_manager_is_created_when_proxies_cannot_be_inherited(
    monkeypatch: pytest.MonkeyPatch, no_real_manager
) -> None:
    """变异：让 _start_method_inherits_manager_proxies 恒返回 True。"""
    monkeypatch.setattr(state_module, "_start_method_inherits_manager_proxies", lambda: False)

    s = GlobalState()
    m = s.plugin_response_map

    assert no_real_manager == [], (
        f"spawn 平台上仍然创建了 Manager：{no_real_manager}——白付 ~320ms 冷启动 + 一个常驻子进程"
    )
    assert type(m) is dict, f"应该是普通 dict，实际是 {type(m).__name__}"
    assert s._plugin_response_map_manager is None
    assert s._response_maps_are_local is True
    assert type(s.plugin_response_event_map) is dict


def test_the_manager_is_still_used_under_fork(
    monkeypatch: pytest.MonkeyPatch, no_real_manager
) -> None:
    """另一侧的守卫：fork 平台必须照旧用 Manager，否则子进程继承到的是私有副本。"""
    monkeypatch.setattr(state_module, "_start_method_inherits_manager_proxies", lambda: True)

    s = GlobalState()
    _ = s.plugin_response_map

    assert "Manager" in no_real_manager, "fork 平台不再创建 Manager —— POSIX 行为被改动了"
    assert s._response_maps_are_local is False
    assert s._plugin_response_map_manager is not None


def test_the_platform_helper_answers_false_on_spawn_and_true_on_fork(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(state_module.multiprocessing, "get_start_method", lambda: "spawn")
    assert state_module._start_method_inherits_manager_proxies() is False
    monkeypatch.setattr(state_module.multiprocessing, "get_start_method", lambda: "fork")
    assert state_module._start_method_inherits_manager_proxies() is True
    monkeypatch.setattr(state_module.multiprocessing, "get_start_method", lambda: "forkserver")
    assert state_module._start_method_inherits_manager_proxies() is False


# ── 2. 本地表必须仍然给出可用的事件（最容易静默退化的一处）──────────────


def test_local_maps_still_get_a_usable_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """变异：去掉 ``_response_maps_are_local`` 分支，让 ``mgr is None`` 直接 return None。

    那样服务器就没有事件唤醒了，``wait_for_plugin_response`` 会退化成纯短轮询：
    启动是变快了，但每次插件响应的等待都变慢，而且没有任何东西会红。
    """
    monkeypatch.setattr(state_module, "_start_method_inherits_manager_proxies", lambda: False)

    s = GlobalState()
    _ = s.plugin_response_map
    ev = s._get_or_create_response_event("req-1")

    assert ev is not None, (
        "本地表下拿不到事件 —— 服务器会退化成纯短轮询等插件响应"
    )
    assert isinstance(ev, threading.Event), f"应该是 threading.Event，实际是 {type(ev).__name__}"
    # 同一个 request_id 必须复用同一个事件，否则 set 的和 wait 的不是一个
    assert s._get_or_create_response_event("req-1") is ev
    assert s._get_or_create_response_event("req-2") is not ev


def test_the_local_event_actually_wakes_a_waiter(monkeypatch: pytest.MonkeyPatch) -> None:
    """事件必须真的能跨线程唤醒——这是它替代 mgr.Event() 的全部理由。"""
    monkeypatch.setattr(state_module, "_start_method_inherits_manager_proxies", lambda: False)

    s = GlobalState()
    _ = s.plugin_response_map
    ev = s._get_or_create_response_event("req-wake")
    assert ev.wait(timeout=0.01) is False, "前提没成立：事件不该已经是 set 状态"

    threading.Thread(target=lambda: (time.sleep(0.05), ev.set()), daemon=True).start()
    started = time.perf_counter()
    assert ev.wait(timeout=3.0) is True, "事件没能唤醒等待者"
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"唤醒用了 {elapsed:.2f}s，不像事件、像在轮询"


def test_a_plugin_child_still_gets_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """变异：把子进程也改成返回 threading.Event。

    子进程的回应走 ZMQ，没有任何人会 set 这个事件；给它一个永远等不到的事件，
    比直接返回 None（让调用方走短轮询后备）更糟。这是既有行为，不能改。
    """
    monkeypatch.setattr(state_module, "_start_method_inherits_manager_proxies", lambda: False)

    child = GlobalState()
    child.mark_plugin_child_process()
    _ = child.plugin_response_map

    assert type(child._plugin_response_map) is dict
    assert child._get_or_create_response_event("req-child") is None, (
        "子进程拿到了事件——没人会 set 它，等于把短轮询后备换成永久等待"
    )


def test_the_helper_is_conservative_when_the_start_method_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """变异：拿不到 start method 时返回 False（激进）而不是 True（保守）。

    猜错的代价不对称：判成"不需要 Manager"却在 fork 平台上跑，子进程会拿到私有副本，
    表现为跨进程响应永久等不到——难查且静默。判成"需要"只是慢一点。
    """
    def _boom():
        raise RuntimeError("context has already been set")

    monkeypatch.setattr(state_module.multiprocessing, "get_start_method", _boom)
    assert state_module._start_method_inherits_manager_proxies() is True


# ── 3. 读写语义 ────────────────────────────────────────────────────────


def test_local_map_supports_the_response_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    """set_plugin_response 写、wait 侧读，换成普通 dict 后语义必须一致。"""
    monkeypatch.setattr(state_module, "_start_method_inherits_manager_proxies", lambda: False)

    s = GlobalState()
    m = s.plugin_response_map
    m["rid-A"] = {"response": {"ok": 1}, "expire_time": time.time() + 30}
    assert m.get("rid-A", {}).get("response") == {"ok": 1}

    em = s.plugin_response_event_map
    ev = s._get_or_create_response_event("rid-A")
    em["rid-A"] = ev
    assert em.get("rid-A") is ev
    # 事件表与响应表是两个独立对象，不能是同一个 dict
    assert em is not m


def test_real_platform_agrees_with_the_helper() -> None:
    """钉住前提：本机（Windows）就是 spawn，所以这条优化在用户的机器上真的生效。

    如果哪天 Windows 也改成 fork，这个测试会红，提醒重新评估——而不是让优化静默失效。
    """
    import sys

    method = multiprocessing.get_start_method()
    if sys.platform == "win32":
        assert method == "spawn", f"Windows 上的 start method 变成了 {method}"
        assert state_module._start_method_inherits_manager_proxies() is False
