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
