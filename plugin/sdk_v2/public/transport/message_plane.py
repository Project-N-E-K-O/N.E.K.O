"""Internal message-plane transport implementation for SDK v2."""

from __future__ import annotations

from collections import defaultdict
from typing import Awaitable, Protocol

from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue, PluginContextProtocol
from plugin.sdk_v2.shared.models import Err, Ok, Result


class MessageHandler(Protocol):
    def __call__(self, payload: JsonObject) -> Awaitable[Result[None, Exception]]: ...


class MessagePlaneTransport:
    """Best-effort async message-plane transport facade.

    `publish` / `notify` delegate to the plugin context push_message pipeline when
    available. `subscribe` / `unsubscribe` are in-process registrations for now.
    `request` currently returns an error result unless a host implementation is
    injected on the context.
    """

    def __init__(self, *, ctx: PluginContextProtocol | None = None):
        self._ctx = ctx
        self._handlers: dict[str, list[MessageHandler]] = defaultdict(list)

    async def request(self, topic: str, payload: JsonObject, *, timeout: float = 10.0) -> Result[JsonObject | JsonValue | None, Exception]:
        try:
            requester = getattr(self._ctx, "message_plane_request_async", None) if self._ctx is not None else None
            if callable(requester):
                result = await requester(topic=topic, payload=payload, timeout=timeout)
                if isinstance(result, (dict, list, str, int, float, bool)) or result is None:
                    return Ok(result)
                return Ok({"result": result})
            return Err(RuntimeError("message-plane request is not available on ctx"))
        except Exception as error:
            return Err(error)

    async def notify(self, topic: str, payload: JsonObject, *, timeout: float = 5.0) -> Result[None, Exception]:
        return await self.publish(topic, payload, timeout=timeout)

    async def publish(self, topic: str, payload: JsonObject, *, timeout: float = 5.0) -> Result[None, Exception]:
        try:
            if self._ctx is not None:
                push = getattr(self._ctx, "push_message_async", None)
                if callable(push):
                    await push(text=topic, description=topic, metadata={"payload": payload, "topic": topic}, timeout=timeout)
            for handler in list(self._handlers.get(topic, [])):
                result = await handler(payload)
                if isinstance(result, Err):
                    return result
            return Ok(None)
        except Exception as error:
            return Err(error)

    async def subscribe(self, topic: str, handler: MessageHandler) -> Result[None, Exception]:
        try:
            self._handlers[topic].append(handler)
            return Ok(None)
        except Exception as error:
            return Err(error)

    async def unsubscribe(self, topic: str, handler: MessageHandler | None = None) -> Result[int, Exception]:
        try:
            handlers = self._handlers.get(topic, [])
            if handler is None:
                count = len(handlers)
                self._handlers.pop(topic, None)
                return Ok(count)
            removed = 0
            remaining: list[MessageHandler] = []
            for item in handlers:
                if item is handler:
                    removed += 1
                else:
                    remaining.append(item)
            if remaining:
                self._handlers[topic] = remaining
            else:
                self._handlers.pop(topic, None)
            return Ok(removed)
        except Exception as error:
            return Err(error)


__all__ = ["MessagePlaneTransport", "MessageHandler"]
