"""Conversation bus facade."""

from __future__ import annotations

from collections.abc import Mapping

from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Err, Ok, Result
from plugin.sdk_v2.shared.models.exceptions import BusErrorLike, ConversationNotFoundError, InvalidArgumentError

from ._facade import BusFacadeMixin
from .types import BusConversation, BusList


class ConversationRecord(BusConversation):
    pass


class ConversationList(BusList[ConversationRecord]):
    pass


class Conversations(BusFacadeMixin):
    def __init__(self, _transport=None):
        self._setup(_transport, namespace="conversations")

    async def _do_list(self, *, limit: int = 50, cursor: str | None = None, timeout: float = 10.0) -> Result[list[BusConversation], BusErrorLike]:
        items = list(self._state.conversations.values())
        start_index = 0
        if cursor is not None:
            matched_index = next((index for index, item in enumerate(items) if item.id == cursor), None)
            if matched_index is not None:
                start_index = matched_index + 1
        return Ok(items[start_index:start_index + limit])

    async def _do_get(self, conversation_id: str, *, timeout: float = 10.0) -> Result[BusConversation, BusErrorLike]:
        item = self._state.conversations.get(conversation_id)
        return Ok(item) if item is not None else Err(ConversationNotFoundError(conversation_id))

    async def _do_create(self, topic: str, *, metadata: JsonObject | None = None, timeout: float = 10.0) -> Result[BusConversation, BusErrorLike]:
        if metadata is None:
            normalized_metadata: JsonObject = {}
        elif isinstance(metadata, Mapping) and all(isinstance(key, str) for key in metadata):
            normalized_metadata = dict(metadata)
        else:
            return Err(InvalidArgumentError("metadata must be a mapping with string keys"))
        item = BusConversation(id=self._state.next_id("conversation"), topic=topic, metadata=normalized_metadata)
        self._state.conversations[item.id] = item
        return Ok(item)

    async def _do_delete(self, conversation_id: str, *, timeout: float = 10.0) -> Result[bool, BusErrorLike]:
        removed = self._state.conversations.pop(conversation_id, None) is not None
        if removed:
            self._state.messages = {k: v for k, v in self._state.messages.items() if v.conversation_id != conversation_id}
        return Ok(removed)

    async def list(self, *, limit: int = 50, cursor: str | None = None, timeout: float = 10.0) -> Result[list[BusConversation], BusErrorLike]:
        limit_ok = self._require_positive_int("limit", limit)
        if isinstance(limit_ok, Err):
            return limit_ok
        return await self._call("bus.conversations.list", self._do_list, limit=limit_ok.value, cursor=cursor, timeout=timeout)

    async def get(self, conversation_id: str, *, timeout: float = 10.0) -> Result[BusConversation, BusErrorLike]:
        conv_ok = self._require_non_empty_str("conversation_id", conversation_id, ConversationNotFoundError)
        if isinstance(conv_ok, Err):
            return conv_ok
        return await self._call("bus.conversations.get", self._do_get, conv_ok.value, timeout=timeout)

    async def create(self, topic: str, *, metadata: JsonObject | None = None, timeout: float = 10.0) -> Result[BusConversation, BusErrorLike]:
        topic_ok = self._require_non_empty_str("topic", topic)
        if isinstance(topic_ok, Err):
            return topic_ok
        if metadata is None:
            normalized_metadata: JsonObject | None = None
        elif isinstance(metadata, Mapping) and all(isinstance(key, str) for key in metadata):
            normalized_metadata = dict(metadata)
        else:
            return Err(InvalidArgumentError("metadata must be a mapping with string keys"))
        return await self._call("bus.conversations.create", self._do_create, topic_ok.value, metadata=normalized_metadata, timeout=timeout)

    async def delete(self, conversation_id: str, *, timeout: float = 10.0) -> Result[bool, BusErrorLike]:
        conv_ok = self._require_non_empty_str("conversation_id", conversation_id)
        if isinstance(conv_ok, Err):
            return conv_ok
        return await self._call("bus.conversations.delete", self._do_delete, conv_ok.value, timeout=timeout)

class ConversationClient:
    def __init__(self, _transport=None):
        self._transport = _transport
        self._impl = Conversations(_transport)

    async def get(self, *, conversation_id: str | None = None, max_count: int = 50, timeout: float = 5.0) -> ConversationList:
        if conversation_id is not None:
            item = await self._impl.get(conversation_id, timeout=timeout)
            if isinstance(item, Ok):
                return ConversationList([ConversationRecord(**item.value.dump())])
            error = item.error
            if isinstance(error, Exception):
                raise error
            raise RuntimeError(str(error))
        listed = await self._impl.list(limit=max_count, timeout=timeout)
        if isinstance(listed, Err):
            error = listed.error
            if isinstance(error, Exception):
                raise error
            raise RuntimeError(str(error))
        return ConversationList([ConversationRecord(**item.dump()) for item in listed.value])

__all__ = ["ConversationClient", "ConversationList", "ConversationNotFoundError", "ConversationRecord", "Conversations"]
