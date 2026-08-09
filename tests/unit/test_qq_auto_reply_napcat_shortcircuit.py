"""wait_for_onebot_ready must short-circuit when NapCat startup fails, not idle-wait 20s.

Background: when napcat_directory is unset or missing, ensure_napcat_started sets
_startup_error; but the old wait_for_onebot_ready ignored it and polled for the full
timeout (20s), and the frontend call() also polls with a 20s cap, so the start button
reported a spurious timeout.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.napcat_service import QQNapcatService


def _plugin(*, qq_settings=None, startup_error=None, qq_client=None):
    return SimpleNamespace(
        _qq_settings=dict(qq_settings or {}),
        _startup_error=startup_error,
        _napcat_process=None,
        _manages_napcat_process=False,
        qq_client=qq_client,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        _emit_log=lambda *a, **k: None,
    )


def test_wait_shortcircuits_when_startup_error_already_set():
    """With startup_error already set, returns False immediately without polling."""
    plugin = _plugin(startup_error="启动器不存在: xxx")
    service = QQNapcatService(plugin)

    # 若不走短路，会等满 timeout；这里验证立即返回
    import time
    start = time.monotonic()
    result = asyncio.run(service.wait_for_onebot_ready(timeout_seconds=5.0))
    elapsed = time.monotonic() - start

    assert result is False
    assert elapsed < 2.0  # 远小于 5s timeout，证明短路生效


def test_wait_polls_when_no_error_but_no_client():
    """Without startup_error and no client connected, polls normally and returns False on timeout."""
    plugin = _plugin(startup_error=None, qq_client=SimpleNamespace(is_connected=lambda: False))
    service = QQNapcatService(plugin)

    result = asyncio.run(service.wait_for_onebot_ready(timeout_seconds=0.5, poll_interval=0.05))

    assert result is False
    # 超时后应设置「没有客户端连接」错误
    assert "没有客户端连接" in (plugin._startup_error or "")


def test_wait_returns_true_when_connected():
    """With a client connected, returns True immediately and clears startup_error."""
    plugin = _plugin(
        startup_error="旧错误",
        qq_client=SimpleNamespace(is_connected=lambda: True),
    )
    service = QQNapcatService(plugin)

    result = asyncio.run(service.wait_for_onebot_ready(timeout_seconds=5.0))

    assert result is True
    assert plugin._startup_error is None  # clear_startup_error 已执行


def test_ensure_started_sets_error_when_no_dir():
    """When napcat_directory is unset, ensure_napcat_started sets an explicit error (not silent)."""
    plugin = _plugin(qq_settings={})
    service = QQNapcatService(plugin)

    asyncio.run(service.ensure_napcat_started())

    assert "napcat_directory" in (plugin._startup_error or "")
    assert plugin._napcat_process is None
