"""NapCat 启动失败时 wait_for_onebot_ready 必须短路，不空等 20 秒。

背景：napcat_directory 未配置或不存在时，ensure_napcat_started 会设置
_startup_error；但 wait_for_onebot_ready 旧实现不检查该错误，仍空轮询满
timeout（20s），前端 call() 轮询上限也是 20s → 启动按钮误报 timeout。
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
    """已有 startup_error → 立即返回 False，不进入轮询循环。"""
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
    """无 startup_error、客户端未连 → 走正常轮询，超时后返回 False。"""
    plugin = _plugin(startup_error=None, qq_client=SimpleNamespace(is_connected=lambda: False))
    service = QQNapcatService(plugin)

    result = asyncio.run(service.wait_for_onebot_ready(timeout_seconds=0.5, poll_interval=0.05))

    assert result is False
    # 超时后应设置「没有客户端连接」错误
    assert "没有客户端连接" in (plugin._startup_error or "")


def test_wait_returns_true_when_connected():
    """客户端已连 → 立即返回 True 并清除 startup_error。"""
    plugin = _plugin(
        startup_error="旧错误",
        qq_client=SimpleNamespace(is_connected=lambda: True),
    )
    service = QQNapcatService(plugin)

    result = asyncio.run(service.wait_for_onebot_ready(timeout_seconds=5.0))

    assert result is True
    assert plugin._startup_error is None  # clear_startup_error 已执行


def test_ensure_started_sets_error_when_no_dir():
    """napcat_directory 未配置 → ensure_napcat_started 设置明确错误（不静默）。"""
    plugin = _plugin(qq_settings={})
    service = QQNapcatService(plugin)

    asyncio.run(service.ensure_napcat_started())

    assert "napcat_directory" in (plugin._startup_error or "")
    assert plugin._napcat_process is None
