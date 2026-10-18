"""Response maps require multiprocessing.Manager only on fork platforms.

Windows always uses spawn (``app/main_server/__init__..py:56`` guards ``set_start_method("fork")``
with ``sys.platform != "win32"``). Spawned children start fresh interpreters and cannot inherit parent
Manager proxies. Plugin children explicitly avoid them: ``host.py:909`` unconditionally calls
``state.mark_plugin_child_process()`` in ``_plugin_process_runner``, making
``plugin_response_map`` use a plain dict through ``_is_plugin_child_process`` and ZMQ for responses.

Thus Manager's cross-process sharing has no spawn-platform consumers, yet incurs three costs:

* Cold ``multiprocessing.Manager()`` startup, measured around 320 ms or 10% of cold startup
  (harness stage ``state.manager_create``).
* A resident manager subprocess, adding a process and RSS.
* Every proxy read/write requires a socket round trip to the manager, and
  ``_get_or_create_response_event`` creates Events repeatedly awaited in
  polling loops like ``while True: ev.wait(timeout=0.01)``.

The easiest pitfall follows from the second consequence: after replacing maps with ordinary dicts,
``_get_or_create_response_event`` returns None when ``mgr is None``, removing event wakeups and
reducing the server to short polling. Startup improves but response waiting slows without any failure signal.
Therefore test both avoiding Manager creation and providing usable threading.Event objects.

Mutation checklist; each must fail tests:
* Always return True from ``_start_method_inherits_manager_proxies`` -> test 1 fails.
* Remove ``_response_maps_are_local`` and return None when ``mgr is None`` -> test 3 fails.
* Return threading.Event in children too -> test 4 fails.
* Return False rather than conservative True for unknown start methods -> test 6 fails.
"""

from __future__ import annotations

import multiprocessing
import sys
import threading
import time

import pytest

# Never use ``from plugin.core import state``: ``plugin/core/__init__.py`` exports the same-named
# singleton, shadowing the submodule (explicitly noted in #3242's commit message).
# "plugin.core.state stays eager because its name collides with the submodule"）。
# It returns a GlobalState instance instead of the module, making monkeypatch fail with AttributeError.
import plugin.core.state  # noqa: F401 - Imported only to populate sys.modules.
from plugin.core.state import GlobalState

state_module = sys.modules["plugin.core.state"]

pytestmark = pytest.mark.plugin_unit


@pytest.fixture
def no_real_manager(monkeypatch: pytest.MonkeyPatch):
    """Avoid actually starting manager subprocesses; record calls and return a fake Manager."""
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

        def Event(self):  # noqa: N802 - Match multiprocessing.Manager's name.
            calls.append("Event")
            return _FakeEvent()

    def _fake_manager_factory(*args, **kwargs):
        calls.append("Manager")
        return _FakeManager()

    monkeypatch.setattr(state_module.multiprocessing, "Manager", _fake_manager_factory)
    return calls


# -- 1. Spawn platforms do not create Manager --


def test_no_manager_is_created_when_proxies_cannot_be_inherited(
    monkeypatch: pytest.MonkeyPatch, no_real_manager
) -> None:
    """Mutation: make _start_method_inherits_manager_proxies always return True."""
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
    """Opposite-side guard: fork must still use Manager, or children inherit private copies."""
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


# -- 2. Local maps must still yield usable events, the likeliest silent regression --


def test_local_maps_still_get_a_usable_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: remove ``_response_maps_are_local`` and return None for ``mgr is None``.

    The server loses event wakeups and wait_for_plugin_response degrades to short polling:
    startup improves, but every plugin response waits longer without any failure signal.
    """
    monkeypatch.setattr(state_module, "_start_method_inherits_manager_proxies", lambda: False)

    s = GlobalState()
    _ = s.plugin_response_map
    ev = s._get_or_create_response_event("req-1")

    assert ev is not None, (
        "本地表下拿不到事件 —— 服务器会退化成纯短轮询等插件响应"
    )
    assert isinstance(ev, threading.Event), f"应该是 threading.Event，实际是 {type(ev).__name__}"
    # Reuse the same event for one request_id, or setters and waiters act on different events.
    assert s._get_or_create_response_event("req-1") is ev
    assert s._get_or_create_response_event("req-2") is not ev


def test_the_local_event_actually_wakes_a_waiter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Events must genuinely wake across threads; that is their entire purpose replacing mgr.Event()."""
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
    """Mutation: return threading.Event in children too.

    Child responses use ZMQ, so nobody sets this event. An event that can never signal is worse
    than None, which permits callers' short-poll fallback. Preserve existing behavior.
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
    """Mutation: unknown start methods return aggressive False rather than conservative True.

    Mistake costs are asymmetric: wrongly omitting Manager on fork gives children private copies,
    silently waiting forever for cross-process responses. Wrongly requiring it merely costs time.
    """
    def _boom():
        raise RuntimeError("context has already been set")

    monkeypatch.setattr(state_module.multiprocessing, "get_start_method", _boom)
    assert state_module._start_method_inherits_manager_proxies() is True


# -- 3. Read/write semantics --


def test_local_map_supports_the_response_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    """set_plugin_response writes and wait reads must retain identical semantics with ordinary dicts."""
    monkeypatch.setattr(state_module, "_start_method_inherits_manager_proxies", lambda: False)

    s = GlobalState()
    m = s.plugin_response_map
    m["rid-A"] = {"response": {"ok": 1}, "expire_time": time.time() + 30}
    assert m.get("rid-A", {}).get("response") == {"ok": 1}

    em = s.plugin_response_event_map
    ev = s._get_or_create_response_event("rid-A")
    em["rid-A"] = ev
    assert em.get("rid-A") is ev
    # Event and response maps must be distinct objects, not one dict.
    assert em is not m


def test_real_platform_agrees_with_the_helper() -> None:
    """Pin the premise: local Windows uses spawn, so this optimization actually applies on the user's machine.

    If Windows ever switches to fork, fail here to prompt reevaluation instead of silently losing the optimization.
    """
    import sys

    method = multiprocessing.get_start_method()
    if sys.platform == "win32":
        assert method == "spawn", f"Windows 上的 start method 变成了 {method}"
        assert state_module._start_method_inherits_manager_proxies() is False


def test_local_response_state_is_reset_for_the_next_server_run(monkeypatch):
    monkeypatch.setattr(state_module, "_start_method_inherits_manager_proxies", lambda: False)
    s = GlobalState()
    s.set_plugin_response("previous-run", {"old": True})
    old_map = s.plugin_response_map
    old_event = s._get_or_create_response_event("pending")
    old_notify = s.plugin_response_notify_event
    assert old_notify.is_set()

    s.close_plugin_resources()
    s.close_plugin_resources()  # Shutdown remains idempotent.

    assert s._response_maps_are_local is False
    assert s._plugin_response_map is None
    assert s._plugin_response_event_map is None
    assert s._plugin_response_notify_event is None
    assert s.get_plugin_response("previous-run") is None
    assert s.plugin_response_map is not old_map
    assert s._get_or_create_response_event("pending") is not old_event
    assert s.plugin_response_notify_event is not old_notify
    assert not s.plugin_response_notify_event.is_set()
