"""Startup order: the plane bridges come up before any plugin can push.

An autostart plugin may call ``push_message()`` from its startup hook. If the
bridges are not up yet, ``enqueue_delta`` refuses the record (the bridge is
disabled) and ProactiveBridge's SUB socket is not connected either -- PUB/SUB
drops for an absent subscriber. The push still answers ``submitted=True``, so
the author sees success and the character says nothing.

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
        "plane bridge 起在 autostart 插件之后：它们在启动钩子里推的消息会被"
        "一个尚未启用的 bridge 拒收，而 push_message() 已经回了 submitted=True"
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
