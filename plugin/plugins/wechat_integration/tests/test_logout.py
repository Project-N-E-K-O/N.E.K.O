import asyncio
from unittest.mock import AsyncMock

from plugin.plugins.wechat_integration import LoginSession, WechatIntegrationPlugin


async def test_logout_stops_monitor_and_clears_local_login_state():
    plugin = object.__new__(WechatIntegrationPlugin)
    plugin._settings = {
        "token": "secret-token",
        "account_id": "account-1",
        "user_id": "user-1",
        "sync_buf": "sync-data",
    }
    plugin._login_session = LoginSession("qr", "qr-content")
    plugin._qr_expired_count = 2
    plugin._sync_buf = "sync-data"
    plugin._shutdown_event = asyncio.Event()
    plugin._running = True
    plugin._message_task = object()
    plugin._context_tokens = {"user-1": "context-token"}
    plugin._wechat_sessions = {"user-1": {"history": []}}
    plugin.wechat_client = type("Client", (), {"token": "secret-token"})()
    plugin.stop_auto_reply = AsyncMock()
    plugin._persist_config = AsyncMock(return_value=True)
    plugin._build_dashboard_state = lambda: {"login": {"logged_in": False}}
    plugin.logger = type("Logger", (), {"info": lambda *_args, **_kwargs: None})()

    await plugin.logout()

    plugin.stop_auto_reply.assert_awaited_once()
    plugin._persist_config.assert_awaited_once()
    assert plugin._login_session is None
    assert plugin._shutdown_event.is_set()
    assert plugin._running is False
    assert plugin._message_task is None
    assert plugin._qr_expired_count == 0
    assert plugin._sync_buf == ""
    assert plugin._context_tokens == {}
    assert plugin._wechat_sessions == {}
    assert plugin.wechat_client.token is None
    assert plugin._settings["token"] == ""
    assert plugin._settings["account_id"] == ""
    assert plugin._settings["user_id"] == ""
    assert plugin._settings["sync_buf"] == ""
