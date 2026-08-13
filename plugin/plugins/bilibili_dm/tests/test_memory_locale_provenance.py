from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from plugin.plugins.bilibili_dm import BiliDMPlugin


def _client_with_response(response):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.get.return_value = response
    client.post.return_value = response
    return client


async def test_bilibili_bootstrap_omits_process_fallback_locale():
    response = SimpleNamespace(is_success=True, text="")
    client = _client_with_response(response)
    plugin = object.__new__(BiliDMPlugin)
    plugin.logger = SimpleNamespace(info=lambda *_args: None, warning=lambda *_args: None)

    with (
        patch("httpx.AsyncClient", return_value=client),
        patch("utils.language_utils.get_global_language", return_value="en"),
    ):
        await plugin._build_session_instructions(
            "Neko",
            "Master",
            "character prompt",
            {},
            "admin",
            "1001",
            "Master",
        )

    assert client.get.await_args.kwargs == {}


async def test_bilibili_memory_write_omits_process_fallback_locale():
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"status": "ok"},
    )
    client = _client_with_response(response)
    plugin = object.__new__(BiliDMPlugin)

    with (
        patch("httpx.AsyncClient", return_value=client),
        patch("utils.language_utils.get_global_language_full", return_value="en"),
    ):
        result = await plugin._post_memory_history(
            "cache",
            "Neko",
            [{"role": "user", "content": "hello"}],
        )

    assert result == {"status": "ok"}
    assert client.post.await_args.kwargs["json"] == {
        "input_history": '[{"role": "user", "content": "hello"}]',
    }
