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


def test_ensure_started_no_dir_does_not_set_hard_error():
    """未配置 napcat_directory = 不自动启动，不是硬失败：不设错误、不启动进程。

    空目录表示用户可能手动启动 NapCat/OneBot，ensure_napcat 仍应能通过
    wait_for_onebot_ready 连接；设硬错误会阻断手动启动场景。
    """
    plugin = _plugin(qq_settings={})
    service = QQNapcatService(plugin)

    asyncio.run(service.ensure_napcat_started())

    assert plugin._startup_error is None
    assert plugin._napcat_process is None


class _LateConnectingClient:
    """前 N 次 is_connected() 返回 False，之后返回 True（模拟 OneBot 延迟连接）。"""

    def __init__(self, delay_polls: int = 2):
        self._delay = delay_polls

    def is_connected(self) -> bool:
        if self._delay > 0:
            self._delay -= 1
            return False
        return True


def test_transient_timeout_does_not_shortcircuit_retry():
    """瞬时「没有客户端连接」超时不短路：重试应继续轮询，等迟来的连接。"""
    plugin = _plugin(
        startup_error=QQNapcatService.TRANSIENT_TIMEOUT_ERROR,
        qq_client=_LateConnectingClient(delay_polls=2),
    )
    service = QQNapcatService(plugin)

    result = asyncio.run(
        service.wait_for_onebot_ready(timeout_seconds=1.0, poll_interval=0.05)
    )

    assert result is True
    assert plugin._startup_error is None


def test_late_connection_across_deadline_is_detected():
    """sleep 跨过 deadline 返回时若 OneBot 已连上，应返回 True 而非误报超时。

    while 条件检查后 sleep 可能跨过 deadline 才返回；此时连接可能已建立，
    退出循环前的终检必须能发现，避免把就绪状态误设成 TRANSIENT_TIMEOUT_ERROR。
    """
    plugin = _plugin(
        startup_error=None,
        qq_client=_LateConnectingClient(delay_polls=3),
    )
    service = QQNapcatService(plugin)

    result = asyncio.run(
        service.wait_for_onebot_ready(timeout_seconds=0.3, poll_interval=0.02)
    )

    assert result is True
    assert plugin._startup_error is None


def test_hard_error_still_shortcircuits_retry():
    """硬失败（启动器不存在）仍立即短路，重试不空等。"""
    plugin = _plugin(
        startup_error="启动器不存在: xxx",
        qq_client=SimpleNamespace(is_connected=lambda: False),  # NapCat 未连
    )
    service = QQNapcatService(plugin)

    import time
    start = time.monotonic()
    result = asyncio.run(
        service.wait_for_onebot_ready(timeout_seconds=1.0, poll_interval=0.05)
    )
    elapsed = time.monotonic() - start

    assert result is False
    assert elapsed < 0.5  # 短路生效，未空等
    assert "启动器不存在" in (plugin._startup_error or "")
