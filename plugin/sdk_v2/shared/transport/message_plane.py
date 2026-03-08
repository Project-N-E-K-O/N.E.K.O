"""Message-plane transport contracts for SDK v2 shared transport."""

from __future__ import annotations

from typing import Awaitable, Protocol

from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue
from plugin.sdk_v2.shared.models import Result


class MessageHandler(Protocol):
    def __call__(self, payload: JsonObject) -> Awaitable[Result[None, Exception]]: ...


class MessagePlaneTransport:
    """Async-only message-plane transport contract."""

    def __init__(self, *args: object, **kwargs: object):
        raise NotImplementedError("sdk_v2 contract-only facade: shared.transport.message_plane not implemented")

    async def request(self, topic: str, payload: JsonObject, *, timeout: float = 10.0) -> Result[JsonObject | JsonValue | None, Exception]:
        raise NotImplementedError

    async def notify(self, topic: str, payload: JsonObject, *, timeout: float = 5.0) -> Result[None, Exception]:
        raise NotImplementedError

    async def publish(self, topic: str, payload: JsonObject, *, timeout: float = 5.0) -> Result[None, Exception]:
        raise NotImplementedError

    async def subscribe(self, topic: str, handler: MessageHandler) -> Result[None, Exception]:
        raise NotImplementedError

    async def unsubscribe(self, topic: str, handler: MessageHandler | None = None) -> Result[int, Exception]:
        raise NotImplementedError


__all__ = ["MessagePlaneTransport", "MessageHandler"]
