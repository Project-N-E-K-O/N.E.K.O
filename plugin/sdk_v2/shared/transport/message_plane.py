"""Shared facade for message-plane transport."""

from __future__ import annotations

from plugin.sdk_v2.public.transport.message_plane import MessageHandler, MessagePlaneTransport as _ImplMessagePlaneTransport
from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue, PluginContextProtocol
from plugin.sdk_v2.shared.models import Err, Ok, Result


class MessagePlaneTransport:
    """Async-first message-plane facade."""

    def __init__(self, *, ctx: PluginContextProtocol | None = None):
        self._ctx = ctx
        self._impl = _ImplMessagePlaneTransport(ctx=ctx)

    @staticmethod
    def _validate_topic(topic: str) -> Result[None, Exception]:
        if not isinstance(topic, str) or topic.strip() == "":
            return Err(ValueError("topic must be non-empty"))
        return _OK_NONE

    @staticmethod
    def _validate_timeout(timeout: float) -> Result[None, Exception]:
        if timeout <= 0:
            return Err(ValueError("timeout must be > 0"))
        return _OK_NONE

    async def request(self, topic: str, payload: JsonObject, *, timeout: float = 10.0) -> Result[JsonObject | JsonValue | None, Exception]:
        topic_ok = self._validate_topic(topic)
        if isinstance(topic_ok, Err):
            return topic_ok
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return timeout_ok
        try:
            return await self._impl.request(topic, payload, timeout=timeout)
        except Exception as error:
            return Err(error)

    async def notify(self, topic: str, payload: JsonObject, *, timeout: float = 5.0) -> Result[None, Exception]:
        topic_ok = self._validate_topic(topic)
        if isinstance(topic_ok, Err):
            return topic_ok
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return timeout_ok
        try:
            return await self._impl.notify(topic, payload, timeout=timeout)
        except Exception as error:
            return Err(error)

    async def publish(self, topic: str, payload: JsonObject, *, timeout: float = 5.0) -> Result[None, Exception]:
        topic_ok = self._validate_topic(topic)
        if isinstance(topic_ok, Err):
            return topic_ok
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return timeout_ok
        try:
            return await self._impl.publish(topic, payload, timeout=timeout)
        except Exception as error:
            return Err(error)

    async def subscribe(self, topic: str, handler: MessageHandler) -> Result[None, Exception]:
        topic_ok = self._validate_topic(topic)
        if isinstance(topic_ok, Err):
            return topic_ok
        if not callable(handler):
            return Err(TypeError("handler must be callable"))
        try:
            return await self._impl.subscribe(topic, handler)
        except Exception as error:
            return Err(error)

    async def unsubscribe(self, topic: str, handler: MessageHandler | None = None) -> Result[int, Exception]:
        topic_ok = self._validate_topic(topic)
        if isinstance(topic_ok, Err):
            return topic_ok
        if handler is not None and not callable(handler):
            return Err(TypeError("handler must be callable"))
        try:
            return await self._impl.unsubscribe(topic, handler)
        except Exception as error:
            return Err(error)


_OK_NONE = Ok(None)

__all__ = ["MessagePlaneTransport", "MessageHandler"]
