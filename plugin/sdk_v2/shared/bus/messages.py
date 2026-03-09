"""Message bus facade."""

from __future__ import annotations

from plugin.sdk_v2.public.bus.messages import Messages as _ImplMessages
from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue
from plugin.sdk_v2.shared.models import Err, Result

from ._facade import BusFacadeMixin
from .types import BusList, BusMessage


class MessageValidationError(RuntimeError):
    """Invalid message payload for append/list operations."""


class MessageRecord(BusMessage):
    pass


class MessageList(BusList[MessageRecord]):
    pass


class _LocalMessageCache:
    def __init__(self) -> None:
        self._items: list[MessageRecord] = []

    def on_delta(self, delta) -> None:
        self._items = [*delta.current.items]

    def tail(self, count: int = 20) -> list[MessageRecord]:
        return list(self._items[-count:])


class Messages(BusFacadeMixin):
    def __init__(self, _transport=None):
        self._setup_impl(_ImplMessages, _transport, namespace="messages")

    async def list(self, conversation_id: str, *, limit: int = 100, cursor: str | None = None, timeout: float = 10.0) -> Result[list[BusMessage], Exception]:
        conv_ok = self._require_non_empty_str("conversation_id", conversation_id, MessageValidationError)
        if isinstance(conv_ok, Err):
            return conv_ok
        limit_ok = self._require_positive_int("limit", limit, MessageValidationError)
        if isinstance(limit_ok, Err):
            return limit_ok
        return await self._call("bus.messages.list", self._impl.list, conv_ok, limit=limit_ok, cursor=cursor, timeout=timeout)

    async def get(self, message_id: str, *, timeout: float = 10.0) -> Result[BusMessage, Exception]:
        msg_ok = self._require_non_empty_str("message_id", message_id, MessageValidationError)
        if isinstance(msg_ok, Err):
            return msg_ok
        return await self._call("bus.messages.get", self._impl.get, msg_ok, timeout=timeout, error_mapper=lambda e: MessageValidationError(str(e)))

    async def append(self, conversation_id: str, *, role: str, content: JsonValue, metadata: JsonObject | None = None, timeout: float = 10.0) -> Result[BusMessage, Exception]:
        conv_ok = self._require_non_empty_str("conversation_id", conversation_id, MessageValidationError)
        if isinstance(conv_ok, Err):
            return conv_ok
        role_ok = self._require_non_empty_str("role", role, MessageValidationError)
        if isinstance(role_ok, Err):
            return role_ok
        return await self._call("bus.messages.append", self._impl.append, conv_ok, role=role_ok, content=content, metadata=metadata, timeout=timeout, error_mapper=lambda e: MessageValidationError(str(e)))

    async def delete(self, message_id: str, *, timeout: float = 10.0) -> Result[bool, Exception]:
        msg_ok = self._require_non_empty_str("message_id", message_id, MessageValidationError)
        if isinstance(msg_ok, Err):
            return msg_ok
        return await self._call("bus.messages.delete", self._impl.delete, msg_ok, timeout=timeout)

    async def get_by_conversation(self, conversation_id: str, *, limit: int = 100, timeout: float = 10.0) -> Result[list[BusMessage], Exception]:
        return await self.list(conversation_id, limit=limit, timeout=timeout)


class MessageClient:
    def __init__(self, _transport=None):
        self._impl = Messages(_transport)

    async def get(self, *, conversation_id: str, max_count: int = 100, timeout: float = 5.0) -> MessageList:
        listed = await self._impl.list(conversation_id, limit=max_count, timeout=timeout)
        return MessageList([MessageRecord(**item.dump()) for item in listed.unwrap_or([])])

    async def get_async(self, **kwargs) -> MessageList:
        return await self.get(**kwargs)

    async def get_message_plane_all(self, *, max_count: int = 100, timeout: float = 5.0) -> MessageList:
        return MessageList([MessageRecord(**message.dump()) for message in list(self._impl._state.messages.values())[:max_count]])

    async def get_message_plane_all_async(self, **kwargs) -> MessageList:
        return await self.get_message_plane_all(**kwargs)

    async def get_by_conversation(self, conversation_id: str, *, max_count: int = 100, timeout: float = 5.0) -> MessageList:
        return await self.get(conversation_id=conversation_id, max_count=max_count, timeout=timeout)

    async def get_by_conversation_async(self, conversation_id: str, *, max_count: int = 100, timeout: float = 5.0) -> MessageList:
        return await self.get_by_conversation(conversation_id, max_count=max_count, timeout=timeout)


__all__ = ["Messages", "MessageValidationError", "MessageRecord", "MessageList", "_LocalMessageCache", "MessageClient"]
