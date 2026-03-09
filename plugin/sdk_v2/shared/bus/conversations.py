"""Conversation bus facade."""

from __future__ import annotations

from plugin.sdk_v2.public.bus.conversations import Conversations as _ImplConversations
from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Err, Ok, Result

from ._facade import BusFacadeMixin
from .types import BusConversation, BusList


class ConversationNotFoundError(RuntimeError):
    """Conversation id does not exist."""


class ConversationRecord(BusConversation):
    pass


class ConversationList(BusList[ConversationRecord]):
    pass


class Conversations(BusFacadeMixin):
    def __init__(self, _transport=None):
        self._setup_impl(_ImplConversations, _transport, namespace="conversations")

    async def list(self, *, limit: int = 50, cursor: str | None = None, timeout: float = 10.0) -> Result[list[BusConversation], Exception]:
        limit_ok = self._require_positive_int("limit", limit)
        if isinstance(limit_ok, Err):
            return limit_ok
        return await self._call("bus.conversations.list", self._impl.list, limit=limit_ok, cursor=cursor, timeout=timeout)

    async def get(self, conversation_id: str, *, timeout: float = 10.0) -> Result[BusConversation, Exception]:
        conv_ok = self._require_non_empty_str("conversation_id", conversation_id, ConversationNotFoundError)
        if isinstance(conv_ok, Err):
            return conv_ok
        return await self._call("bus.conversations.get", self._impl.get, conv_ok, timeout=timeout, error_mapper=lambda e: ConversationNotFoundError(str(e)))

    async def create(self, topic: str, *, metadata: JsonObject | None = None, timeout: float = 10.0) -> Result[BusConversation, Exception]:
        topic_ok = self._require_non_empty_str("topic", topic)
        if isinstance(topic_ok, Err):
            return topic_ok
        return await self._call("bus.conversations.create", self._impl.create, topic_ok, metadata=metadata, timeout=timeout)

    async def delete(self, conversation_id: str, *, timeout: float = 10.0) -> Result[bool, Exception]:
        conv_ok = self._require_non_empty_str("conversation_id", conversation_id)
        if isinstance(conv_ok, Err):
            return conv_ok
        return await self._call("bus.conversations.delete", self._impl.delete, conv_ok, timeout=timeout)

    async def get_by_id(self, conversation_id: str, *, timeout: float = 10.0) -> Result[BusConversation, Exception]:
        return await self.get(conversation_id, timeout=timeout)


class ConversationClient:
    def __init__(self, _transport=None):
        self._transport = _transport
        self._impl = Conversations(_transport)

    async def get(self, *, conversation_id: str | None = None, max_count: int = 50, since_ts: float | None = None, timeout: float = 5.0) -> ConversationList:
        if conversation_id:
            item = await self._impl.get(conversation_id, timeout=timeout)
            return ConversationList([ConversationRecord(**item.unwrap().dump())] if isinstance(item, Ok) else [])
        listed = await self._impl.list(limit=max_count, timeout=timeout)
        return ConversationList([ConversationRecord(**item.dump()) for item in listed.unwrap_or([])])

    async def get_async(self, **kwargs) -> ConversationList:
        return await self.get(**kwargs)

    async def get_by_id(self, conversation_id: str, *, max_count: int = 50, timeout: float = 5.0) -> ConversationList:
        return await self.get(conversation_id=conversation_id, max_count=max_count, timeout=timeout)

    async def get_by_id_async(self, conversation_id: str, *, max_count: int = 50, timeout: float = 5.0) -> ConversationList:
        return await self.get_by_id(conversation_id, max_count=max_count, timeout=timeout)


__all__ = ["Conversations", "ConversationNotFoundError", "ConversationRecord", "ConversationList", "ConversationClient"]
