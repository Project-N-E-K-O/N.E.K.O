import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from plugin.plugins.wechat_integration import LoginSession, WechatIntegrationPlugin


def _logger():
    return type("Logger", (), {
        "info": lambda *_args, **_kwargs: None,
        "warning": lambda *_args, **_kwargs: None,
    })()


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
    plugin._auth_state_lock = asyncio.Lock()
    plugin._running = True
    plugin._message_task = object()
    plugin._context_tokens = {"user-1": "context-token"}
    plugin._wechat_sessions = {
        "user-1": {
            "her_name": "neko",
            "memory_enabled": True,
            "history": [{"role": "user", "content": "hello"}],
        }
    }
    plugin.wechat_client = type("Client", (), {"token": "secret-token"})()
    plugin.stop_auto_reply = AsyncMock()

    async def settle(_her_name, *, reason):
        assert reason == "logout"
        assert "user-1" in plugin._wechat_sessions
        return True

    plugin._settle_memory_session = AsyncMock(side_effect=settle)

    async def persist(settings):
        plugin._settings = settings
        return True

    plugin._persist_config = AsyncMock(side_effect=persist)
    plugin._build_dashboard_state = lambda: {"login": {"logged_in": False}}
    plugin.logger = _logger()

    await plugin.logout()

    plugin.stop_auto_reply.assert_awaited_once()
    plugin._persist_config.assert_awaited_once()
    plugin._settle_memory_session.assert_awaited_once_with("neko", reason="logout")
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


async def test_logout_keeps_runtime_login_when_credential_write_fails():
    plugin = object.__new__(WechatIntegrationPlugin)
    plugin._settings = {
        "token": "secret-token",
        "account_id": "account-1",
        "user_id": "user-1",
        "sync_buf": "sync-data",
    }
    login_session = LoginSession("qr", "qr-content")
    plugin._login_session = login_session
    plugin._auth_state_lock = asyncio.Lock()
    plugin.wechat_client = type("Client", (), {"token": "secret-token"})()
    plugin.stop_auto_reply = AsyncMock()
    plugin._persist_config = AsyncMock(return_value=False)
    plugin.i18n = type("I18n", (), {"t": lambda _self, _key, default=None: default})()

    await plugin.logout()

    plugin._persist_config.assert_awaited_once()
    persisted_candidate = plugin._persist_config.await_args.args[0]
    assert persisted_candidate["token"] == ""
    assert persisted_candidate["account_id"] == ""
    assert persisted_candidate["user_id"] == ""
    assert persisted_candidate["sync_buf"] == ""
    plugin.stop_auto_reply.assert_not_awaited()
    assert plugin._settings["token"] == "secret-token"
    assert plugin._login_session is login_session
    assert plugin.wechat_client.token == "secret-token"


async def test_poll_login_status_ignores_session_cleared_while_request_is_in_flight():
    plugin = object.__new__(WechatIntegrationPlugin)
    login_session = LoginSession("qr", "qr-content")
    plugin._login_session = login_session
    started = asyncio.Event()
    release = asyncio.Event()

    async def poll_qrcode_status(_qrcode):
        started.set()
        await release.wait()
        return {"status": "wait"}

    plugin.wechat_client = SimpleNamespace(poll_qrcode_status=poll_qrcode_status)
    plugin._build_dashboard_state = lambda: {"login": {"logged_in": False}}

    poll_task = asyncio.create_task(plugin.poll_login_status())
    await started.wait()
    plugin._login_session = None
    release.set()
    await poll_task

    assert plugin._login_session is None
