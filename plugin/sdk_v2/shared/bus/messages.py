"""Message bus facade."""

from __future__ import annotations

import time
from collections.abc import Mapping

from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue
from plugin.sdk_v2.shared.models import Err, Ok, Result
from plugin.sdk_v2.shared.models.exceptions import BusErrorLike, ConversationNotFoundError, InvalidArgumentError, MessageValidationError, NotFoundError

from ._facade import BusFacadeMixin
from .types import BusList, BusMessage


class MessageRecord(BusMessage):
    pass


class MessageList(BusList[MessageRecord]):
    pass


class LocalMessageCache:
    def __init__(self) -> None:
        self._items: list[MessageRecord] = []

    def on_delta(self, delta) -> None:
        self._items = [*delta.current.items]

    def tail(self, count: int = 20) -> list[MessageRecord]:
        if count <= 0:
            return []
        return list(self._items[-count:])


class Messages(BusFacadeMixin):
    def __init__(self, _transport=None):
        self._setup(_transport, namespace="messages")

    async def _do_list(self, conversation_id: str, *, limit: int = 100, cursor: str | None = None, timeout: float = 10.0) -> Result[list[BusMessage], BusErrorLike]:
        items = sorted(
            (item for item in self._state.messages.values() if item.conversation_id == conversation_id),
            key=lambda item: ((item.timestamp or 0.0), item.id),
        )
        start_index = 0
        if cursor is not None:
            matched_index = next((index for index, item in enumerate(items) if item.id == cursor), None)
            if matched_index is not None:
                start_index = matched_index + 1
        return Ok(items[start_index:start_index + limit])

    async def _do_list_all(self, *, limit: int = 100, timeout: float = 10.0) -> Result[list[BusMessage], BusErrorLike]:
        return Ok(list(self._state.messages.values())[:limit])

    async def _do_get(self, message_id: str, *, timeout: float = 10.0) -> Result[BusMessage, BusErrorLike]:
        item = self._state.messages.get(message_id)
        return Ok(item) if item is not None else Err(NotFoundError(message_id))

    async def _do_append(self, conversation_id: str, *, role: str, content: JsonValue, metadata: JsonObject | None = None, timeout: float = 10.0) -> Result[BusMessage, BusErrorLike]:
        if conversation_id not in self._state.conversations:
            return Err(ConversationNotFoundError("conversation does not exist"))
        if metadata is None:
            normalized_metadata: JsonObject = {}
        elif isinstance(metadata, Mapping) and all(isinstance(key, str) for key in metadata):
            normalized_metadata = dict(metadata)
        else:
            return Err(InvalidArgumentError("metadata must be a mapping with string keys"))
        item = BusMessage(
            id=self._state.next_id("message"),
            conversation_id=conversation_id,
            role=role,
            content=content,
            timestamp=time.time(),
            metadata=normalized_metadata,
        )
        self._state.messages[item.id] = item
        return Ok(item)

    async def _do_delete(self, message_id: str, *, timeout: float = 10.0) -> Result[bool, BusErrorLike]:
        return Ok(self._state.messages.pop(message_id, None) is not None)

    async def list(self, conversation_id: str, *, limit: int = 100, cursor: str | None = None, timeout: float = 10.0) -> Result[list[BusMessage], BusErrorLike]:
        conv_ok = self._require_non_empty_str("conversation_id", conversation_id, MessageValidationError)
        if isinstance(conv_ok, Err):
            return conv_ok
        limit_ok = self._require_positive_int("limit", limit, MessageValidationError)
        if isinstance(limit_ok, Err):
            return limit_ok
        return await self._call("bus.messages.list", self._do_list, conv_ok.value, limit=limit_ok.value, cursor=cursor, timeout=timeout)

    async def list_all(self, *, limit: int = 100, timeout: float = 10.0) -> Result[list[BusMessage], BusErrorLike]:
        limit_ok = self._require_positive_int("limit", limit, MessageValidationError)
        if isinstance(limit_ok, Err):
            return limit_ok
        return await self._call("bus.messages.list_all", self._do_list_all, limit=limit_ok.value, timeout=timeout)

    async def get(self, message_id: str, *, timeout: float = 10.0) -> Result[BusMessage, BusErrorLike]:
        msg_ok = self._require_non_empty_str("message_id", message_id, MessageValidationError)
        if isinstance(msg_ok, Err):
            return msg_ok
        return await self._call("bus.messages.get", self._do_get, msg_ok.value, timeout=timeout)

    async def append(self, conversation_id: str, *, role: str, content: JsonValue, metadata: JsonObject | None = None, timeout: float = 10.0) -> Result[BusMessage, BusErrorLike]:
        conv_ok = self._require_non_empty_str("conversation_id", conversation_id, MessageValidationError)
        if isinstance(conv_ok, Err):
            return conv_ok
        role_ok = self._require_non_empty_str("role", role, MessageValidationError)
        if isinstance(role_ok, Err):
            return role_ok
        return await self._call("bus.messages.append", self._do_append, conv_ok.value, role=role_ok.value, content=content, metadata=metadata, timeout=timeout)

    async def delete(self, message_id: str, *, timeout: float = 10.0) -> Result[bool, BusErrorLike]:
        msg_ok = self._require_non_empty_str("message_id", message_id, MessageValidationError)
        if isinstance(msg_ok, Err):
            return msg_ok
        return await self._call("bus.messages.delete", self._do_delete, msg_ok.value, timeout=timeout)

    async def get_by_conversation(self, conversation_id: str, *, limit: int = 100, timeout: float = 10.0) -> Result[list[BusMessage], BusErrorLike]:
        return await self.list(conversation_id, limit=limit, timeout=timeout)


class MessageClient:
    def __init__(self, _transport=None):
        self._impl = Messages(_transport)

    async def get(self, *, conversation_id: str, max_count: int = 100, timeout: float = 5.0) -> MessageList:
        listed = await self._impl.list(conversation_id, limit=max_count, timeout=timeout)
        if isinstance(listed, Err):
            error = listed.error
            if isinstance(error, Exception):
                raise error
            raise RuntimeError(str(error))
        return MessageList([MessageRecord(**item.dump()) for item in listed.value])

    async def get_message_plane_all(self, *, max_count: int = 100, timeout: float = 5.0) -> MessageList:
        listed = await self._impl.list_all(limit=max_count, timeout=timeout)
        if isinstance(listed, Err):
            error = listed.error
            if isinstance(error, Exception):
                raise error
            raise RuntimeError(str(error))
        return MessageList([MessageRecord(**message.dump()) for message in listed.value])

    async def get_by_conversation(self, conversation_id: str, *, max_count: int = 100, timeout: float = 5.0) -> MessageList:
        return await self.get(conversation_id=conversation_id, max_count=max_count, timeout=timeout)


__all__ = ["LocalMessageCache", "MessageClient", "MessageList", "MessageRecord", "MessageValidationError", "Messages"]
