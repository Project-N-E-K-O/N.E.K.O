"""Conversation bus facade."""

from __future__ import annotations

from plugin.sdk_v2.public.bus.conversations import Conversations as _ImplConversations
from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Err, Result

from ._client_base import BusClientBase
from .types import BusConversation


class ConversationNotFoundError(RuntimeError):
    """Conversation id does not exist."""


class Conversations(BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="conversations")
        self._impl = _ImplConversations(self._transport)
        self._state = self._impl._state

    async def list(self, *, limit: int = 50, cursor: str | None = None, timeout: float = 10.0) -> Result[list[BusConversation], Exception]:
        if limit <= 0:
            return Err(ValueError("limit must be > 0"))
        return await self._forward_result("bus.conversations.list", self._impl.list, limit=limit, cursor=cursor, timeout=timeout)

    async def get(self, conversation_id: str, *, timeout: float = 10.0) -> Result[BusConversation, Exception]:
        if not isinstance(conversation_id, str) or conversation_id.strip() == "":
            return Err(ConversationNotFoundError("conversation_id must be non-empty"))
        result = await self._forward_result("bus.conversations.get", self._impl.get, conversation_id, timeout=timeout)
        if isinstance(result, Err) and isinstance(result.error, RuntimeError):
            return Err(ConversationNotFoundError(str(result.error)))
        return result

    async def create(self, topic: str, *, metadata: JsonObject | None = None, timeout: float = 10.0) -> Result[BusConversation, Exception]:
        if not isinstance(topic, str) or topic.strip() == "":
            return Err(ValueError("topic must be non-empty"))
        return await self._forward_result("bus.conversations.create", self._impl.create, topic, metadata=metadata, timeout=timeout)

    async def delete(self, conversation_id: str, *, timeout: float = 10.0) -> Result[bool, Exception]:
        if not isinstance(conversation_id, str) or conversation_id.strip() == "":
            return Err(ValueError("conversation_id must be non-empty"))
        return await self._forward_result("bus.conversations.delete", self._impl.delete, conversation_id, timeout=timeout)


__all__ = ["Conversations", "ConversationNotFoundError"]
