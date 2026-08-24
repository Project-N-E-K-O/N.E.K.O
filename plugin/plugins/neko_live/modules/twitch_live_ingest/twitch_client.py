"""TwitchIO client configured for NEKO-owned token persistence."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
import twitchio
from twitchio.eventsub import websockets as twitchio_websockets


class _ProxyAwareEventSubAiohttp:
    _neko_trust_env = True

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    def ClientSession(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["trust_env"] = True
        return self._delegate.ClientSession(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def _enable_eventsub_environment_proxy() -> None:
    # TwitchIO 3.2.2 creates both initial and reconnect EventSub sessions
    # through this module reference instead of the Client's injected session.
    current = twitchio_websockets.aiohttp
    if getattr(current, "_neko_trust_env", False) is True:
        return
    twitchio_websockets.aiohttp = _ProxyAwareEventSubAiohttp(current)


class NekoTwitchClient(twitchio.Client):
    def __init__(
        self,
        *,
        client_id: str,
        on_message: Callable[[Any], Awaitable[None]],
        on_chat_notification: Callable[[Any], Awaitable[None]],
        on_token_refreshed: Callable[[Any], Awaitable[None]],
    ) -> None:
        _enable_eventsub_environment_proxy()
        self._neko_http_session = aiohttp.ClientSession(trust_env=True)
        super().__init__(
            client_id=client_id,
            client_secret="",
            fetch_client_user=False,
            session=self._neko_http_session,
        )
        self._neko_on_message = on_message
        self._neko_on_chat_notification = on_chat_notification
        self._neko_on_token_refreshed = on_token_refreshed

    async def close(self, **options: Any) -> None:
        try:
            await super().close(**options)
        finally:
            if not self._neko_http_session.closed:
                await self._neko_http_session.close()

    async def event_message(self, payload: Any) -> None:
        await self._neko_on_message(payload)

    async def event_chat_notification(self, payload: Any) -> None:
        await self._neko_on_chat_notification(payload)

    async def event_token_refreshed(self, payload: Any) -> None:
        await self._neko_on_token_refreshed(payload)


def create_twitch_client(**kwargs: Any) -> NekoTwitchClient:
    return NekoTwitchClient(**kwargs)
