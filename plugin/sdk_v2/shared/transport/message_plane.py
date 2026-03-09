"""Shared facade for message-plane transport."""

from __future__ import annotations

from plugin.sdk_v2.public.transport.message_plane import MessageHandler, MessagePlaneTransport as _ImplMessagePlaneTransport
from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue, PluginContextProtocol
from plugin.sdk_v2.shared.models import Result


class MessagePlaneTransport:
    """Async-first message-plane facade."""

    def __init__(self, *, ctx: PluginContextProtocol | None = None):
        self._impl = _ImplMessagePlaneTransport(ctx=ctx)

    async def request(self, topic: str, payload: JsonObject, *, timeout: float = 10.0) -> Result[JsonObject | JsonValue | None, Exception]:
        return await self._impl.request(topic, payload, timeout=timeout)

    async def notify(self, topic: str, payload: JsonObject, *, timeout: float = 5.0) -> Result[None, Exception]:
        return await self._impl.notify(topic, payload, timeout=timeout)

    async def publish(self, topic: str, payload: JsonObject, *, timeout: float = 5.0) -> Result[None, Exception]:
        return await self._impl.publish(topic, payload, timeout=timeout)

    async def subscribe(self, topic: str, handler: MessageHandler) -> Result[None, Exception]:
        return await self._impl.subscribe(topic, handler)

    async def unsubscribe(self, topic: str, handler: MessageHandler | None = None) -> Result[int, Exception]:
        return await self._impl.unsubscribe(topic, handler)


__all__ = ["MessagePlaneTransport", "MessageHandler"]
