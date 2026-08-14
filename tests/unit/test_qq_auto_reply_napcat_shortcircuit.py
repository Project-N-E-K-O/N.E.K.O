"""wait_for_onebot_ready must short-circuit on hard startup failure, not idle-wait 20s.

Background: a hard failure (napcat_directory configured but missing, launcher absent,
process won't start) sets _startup_error, and wait_for_onebot_ready must return
immediately instead of polling the full timeout (20s) — the frontend call() also polls
with a 20s cap, so the start button would report a spurious timeout.

An unconfigured napcat_directory is NOT a hard failure: the user may start NapCat
manually, so ensure_napcat_started stays silent and wait_for_onebot_ready still polls
for the OneBot connection that a manual launch provides.
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


def test_ensure_started_no_dir_stays_silent_for_manual_launch():
    """Unset napcat_directory → no error, no launch: the user may start NapCat manually,
    and wait_for_onebot_ready still polls for that OneBot connection."""
    plugin = _plugin(qq_settings={})
    service = QQNapcatService(plugin)

    asyncio.run(service.ensure_napcat_started())

    assert plugin._startup_error is None
    assert plugin._napcat_process is None
    # Unconfigured is not a hard failure: wait_for_onebot_ready keeps polling for a manually started OneBot
    assert not service.has_hard_startup_error()


def test_ensure_started_sets_hard_error_when_configured_dir_missing():
    """Configured-but-missing napcat_directory is a hard failure: ensure_napcat_started
    sets an explicit error (not silent), which short-circuits wait_for_onebot_ready."""
    plugin = _plugin(qq_settings={"napcat_directory": "C:/does/not/exist"})
    service = QQNapcatService(plugin)

    asyncio.run(service.ensure_napcat_started())

    assert "launcher" in (plugin._startup_error or "").lower()
    assert plugin._napcat_process is None
    # Hard failure: wait_for_onebot_ready short-circuits immediately instead of polling
    assert service.has_hard_startup_error()


def test_forward_mode_missing_launcher_is_not_hard_error():
    """Forward mode treats a missing local launcher as best-effort (warning, not a
    hard error) -- NapCat may be remote/manually started, so bootstrap() must not
    fail just because no local launcher is configured."""
    plugin = _plugin(qq_settings={
        "napcat_directory": "C:/does/not/exist",
        "qq_connection_mode": "napcat_forward",
    })
    service = QQNapcatService(plugin)

    asyncio.run(service.ensure_napcat_started())

    assert plugin._napcat_process is None
    assert not service.has_hard_startup_error()


def test_transient_timeout_recognized_after_mode_switch():
    """A forward-mode timeout text written before switching to reverse must still be
    treated as transient (not a hard failure), so wait_for_onebot_ready keeps polling.

    Regression: has_hard_startup_error() used to compare the saved error against the
    CURRENT mode's transient text -- after a forward timeout, switching to reverse made
    the old forward text look like a hard failure and short-circuited the polling.
    """
    plugin = _plugin(qq_settings={"qq_connection_mode": "napcat_forward"})
    service = QQNapcatService(plugin)
    # Forward mode writes the forward transient timeout text
    service._set_startup_error(service.FORWARD_TRANSIENT_TIMEOUT_ERROR)
    assert not service.has_hard_startup_error()
    # Switch to reverse: the old forward text must not be re-classified as hard
    plugin._qq_settings["qq_connection_mode"] = "napcat"
    assert not service.has_hard_startup_error()
    # Reverse's own transient text is also transient under any mode
    plugin._startup_error = service.TRANSIENT_TIMEOUT_ERROR
    assert not service.has_hard_startup_error()
