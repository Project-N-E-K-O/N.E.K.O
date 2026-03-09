"""Message bus facade."""

from __future__ import annotations

from plugin.sdk_v2.public.bus.messages import Messages as _ImplMessages
from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue
from plugin.sdk_v2.shared.models import Err, Result

from ._client_base import BusClientBase
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


class Messages(BusFacadeMixin, BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="messages")
        self._impl = _ImplMessages(self._transport)
        self._state = self._impl._state

    async def list(self, conversation_id: str, *, limit: int = 100, cursor: str | None = None, timeout: float = 10.0) -> Result[list[BusMessage], Exception]:
        if not isinstance(conversation_id, str) or conversation_id.strip() == "":
            return Err(MessageValidationError("conversation_id must be non-empty"))
        if limit <= 0:
            return Err(MessageValidationError("limit must be > 0"))
        return await self._call("bus.messages.list", self._impl.list, conversation_id, limit=limit, cursor=cursor, timeout=timeout)

    async def get(self, message_id: str, *, timeout: float = 10.0) -> Result[BusMessage, Exception]:
        if not isinstance(message_id, str) or message_id.strip() == "":
            return Err(MessageValidationError("message_id must be non-empty"))
        result = await self._call("bus.messages.get", self._impl.get, message_id, timeout=timeout)
        if isinstance(result, Err):
            return Err(MessageValidationError(str(result.error)))
        return result

    async def append(self, conversation_id: str, *, role: str, content: JsonValue, metadata: JsonObject | None = None, timeout: float = 10.0) -> Result[BusMessage, Exception]:
        if not isinstance(conversation_id, str) or conversation_id.strip() == "":
            return Err(MessageValidationError("conversation_id must be non-empty"))
        if not isinstance(role, str) or role.strip() == "":
            return Err(MessageValidationError("role must be non-empty"))
        result = await self._call("bus.messages.append", self._impl.append, conversation_id, role=role, content=content, metadata=metadata, timeout=timeout)
        if isinstance(result, Err):
            return Err(MessageValidationError(str(result.error)))
        return result

    async def delete(self, message_id: str, *, timeout: float = 10.0) -> Result[bool, Exception]:
        if not isinstance(message_id, str) or message_id.strip() == "":
            return Err(MessageValidationError("message_id must be non-empty"))
        return await self._call("bus.messages.delete", self._impl.delete, message_id, timeout=timeout)

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
        items: list[MessageRecord] = []
        for message in self._impl._state.messages.values():
            items.append(MessageRecord(**message.dump()))
        return MessageList(items[:max_count])

    async def get_message_plane_all_async(self, **kwargs) -> MessageList:
        return await self.get_message_plane_all(**kwargs)

    async def get_by_conversation(self, conversation_id: str, *, max_count: int = 100, timeout: float = 5.0) -> MessageList:
        return await self.get(conversation_id=conversation_id, max_count=max_count, timeout=timeout)

    async def get_by_conversation_async(self, conversation_id: str, *, max_count: int = 100, timeout: float = 5.0) -> MessageList:
        return await self.get_by_conversation(conversation_id, max_count=max_count, timeout=timeout)


__all__ = ["Messages", "MessageValidationError", "MessageRecord", "MessageList", "_LocalMessageCache", "MessageClient"]
