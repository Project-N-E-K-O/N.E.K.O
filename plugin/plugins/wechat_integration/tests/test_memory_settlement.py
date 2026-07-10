import asyncio
from unittest.mock import AsyncMock, patch

from plugin.plugins.wechat_integration import WechatIntegrationPlugin


async def test_settle_memory_session_settles_cached_turns_without_reposting_history():
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.post.return_value.is_success = True

    with patch("httpx.AsyncClient", return_value=client):
        await WechatIntegrationPlugin._settle_memory_session("neko", reason="idle_timeout")

    url = client.post.await_args.args[0]
    payload = client.post.await_args.kwargs["json"]
    assert url.endswith("/settle/neko")
    assert payload == {"input_history": "[]"}


async def test_shutdown_settles_each_active_cached_session_before_closing_client():
    plugin = object.__new__(WechatIntegrationPlugin)
    plugin._shutdown_event = asyncio.Event()
    plugin._message_task = None
    plugin._running = True
    plugin.wechat_client = type("Client", (), {"close": AsyncMock()})()
    plugin.logger = type("Logger", (), {"warning": lambda *_args, **_kwargs: None})()
    plugin._wechat_sessions = {
        "u1": {"her_name": "neko", "memory_enabled": True, "history": [{"role": "user"}]},
        "u2": {"her_name": "other", "memory_enabled": True, "history": [{"role": "user"}]},
    }

    with patch.object(WechatIntegrationPlugin, "_settle_memory_session", new=AsyncMock()) as settle:
        await plugin.shutdown()

    assert plugin._shutdown_event.is_set()
    assert plugin._wechat_sessions == {}
    assert settle.await_count == 2
    assert {call.args[0] for call in settle.await_args_list} == {"neko", "other"}
    assert plugin.wechat_client is None
