"""Startup order: the plane bridges come up before any plugin can push.

An autostart plugin may call ``push_message()`` from its startup hook, and
ProactiveBridge's SUB socket takes about a second to connect in its own thread.
PUB/SUB drops for an absent subscriber, so a push inside that window is never
spoken while ``push_message()`` has already answered ``submitted=True``.

Ordering NARROWS that window rather than closing it -- the connect delay is
still there, it just starts earlier. Closing it needs the bridge to signal SUB
readiness (or to backfill from the store), which is a separate change.

An earlier version of this docstring also claimed the plane bridge refuses
records before ``start_bridge()``. Measured: it does not. ``_Bridge._enabled``
reads the MESSAGE_PLANE_BRIDGE_ENABLED config flag once at construction, not
whether ``start()`` ran, so ``publish_record`` queues normally beforehand and
the thread drains it on start.

Pinned on the SOURCE order rather than by driving a real startup: the failure is
an ordering one, and what has to hold is that these three calls keep this
relative order. Driving it would need a whole plugin server.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _startup_source() -> str:
    import plugin.server.lifecycle as lifecycle

    text = Path(lifecycle.__file__).read_text(encoding="utf-8")
    start = text.index("    async def startup(self) -> None:")
    end = text.index("\n    async def ", start + 10)
    return text[start:end]


@pytest.mark.plugin_unit
def test_both_plane_bridges_start_before_autostart_plugins() -> None:
    """Mutation: move either bridge call back below the autostart call."""
    body = _startup_source()

    positions = {}
    for name in (
        "start_bridge()",
        "start_proactive_bridge()",
        "_refresh_registry_and_start_autostart_plugins()",
    ):
        m = re.search(re.escape(name), body)
        assert m, f"{name} 不在 startup() 里了"
        positions[name] = m.start()

    autostart = positions["_refresh_registry_and_start_autostart_plugins()"]
    assert positions["start_bridge()"] < autostart, (
        "plane bridge 起在 autostart 插件之后。它不会拒收（队列在 start 之前"
        "照常收，线程起来再排空），但让写入方先就位仍是这条链应有的顺序，"
        "也避免排空被推迟到不确定的时刻"
    )
    assert positions["start_proactive_bridge()"] < autostart, (
        "proactive bridge 起在 autostart 插件之后：它的 SUB 还没连上，"
        "PUB/SUB 对缺席的订阅方是丢弃"
    )


@pytest.mark.plugin_unit
def test_the_bridges_still_start_after_the_message_plane() -> None:
    """The other side of the same ordering: they need the plane to exist.

    Moving them earlier must not overshoot -- both connect to the plane the
    step above them starts.
    """
    body = _startup_source()

    plane = body.index("_start_message_plane()")
    assert plane < body.index("start_bridge()")
    assert plane < body.index("start_proactive_bridge()")


# ── the subscriber must exist before anything can publish ──────────────


def test_the_bridge_signals_only_after_it_subscribes() -> None:
    """The event is what startup waits on, so where it is set is the contract.

    Setting it at thread start (or at ``start()``) would restore the original
    bug with a green test attached: PUB drops for an absent subscriber, and
    ``push_message()`` still answers ``submitted=True``.

    Mutation: move ``self._subscribed.set()`` above the ``connect``/
    ``SUBSCRIBE`` pair.
    """
    import ast
    import inspect
    from pathlib import Path

    from plugin.server.messaging import proactive_bridge

    tree = ast.parse(Path(inspect.getfile(proactive_bridge)).read_text(encoding="utf-8"))
    run = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run":
            run = node
    assert run is not None, "前提没成立：找不到 _run"

    subscribe_line = None
    signal_line = None
    for node in ast.walk(run):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "setsockopt_string":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value == "messages.":
                        subscribe_line = node.lineno
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "set"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "_subscribed"
            ):
                signal_line = node.lineno

    assert subscribe_line is not None, "找不到 SUBSCRIBE 'messages.'"
    assert signal_line is not None, "_run 里没有置就绪位——启动会一直等到超时"
    assert signal_line > subscribe_line, (
        "就绪位在订阅之前置了：等它等于没等，窗口原样还在"
    )


def test_a_stopped_bridge_does_not_hold_up_startup() -> None:
    """A disabled or dead bridge must return immediately, not after a timeout.

    Startup calls this on the path to launching plugins; turning a bridge that
    will never come up into a multi-second stall is a worse failure than the
    one being fixed.
    """
    from plugin.server.messaging.proactive_bridge import ProactiveBridge

    bridge = ProactiveBridge()  # never started

    assert bridge.wait_until_subscribed(timeout=30.0) is False


def test_stopping_releases_a_waiter() -> None:
    """``stop()`` has to wake anyone already blocked in the wait."""
    from plugin.server.messaging.proactive_bridge import ProactiveBridge

    class _LiveThread:
        # Not a real thread: stop() joins it, and joining the calling thread
        # raises. Only aliveness and joinability matter here.
        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            return None

    bridge = ProactiveBridge()
    bridge._thread = _LiveThread()

    bridge.stop()

    assert bridge.wait_until_subscribed(timeout=30.0) is True


def test_restarting_the_bridge_does_not_reuse_the_old_readiness() -> None:
    """``stop()`` sets the event to wake waiters; ``start()`` must clear it.

    Without the clear, a stop/start cycle leaves ``wait_until_subscribed()``
    answering True off the previous life, and the startup wait becomes a no-op
    — the original window, back, with a green test on top of it.
    """
    from plugin.server.messaging.proactive_bridge import ProactiveBridge

    class _LiveThread:
        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            return None

    bridge = ProactiveBridge()
    bridge._thread = _LiveThread()
    bridge.stop()
    assert bridge._subscribed.is_set(), "前提没成立：stop 应该唤醒等待者"

    bridge.start()
    try:
        assert not bridge._subscribed.is_set(), (
            "重启后还带着上一条命的就绪位——等它等于没等"
        )
    finally:
        bridge.stop()
