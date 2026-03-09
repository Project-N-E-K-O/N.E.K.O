"""Message bus facade."""

from __future__ import annotations

from plugin.sdk_v2.public.bus.messages import Messages as _ImplMessages
from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue
from plugin.sdk_v2.shared.models import Err, Result

from ._client_base import BusClientBase
from .types import BusMessage


class MessageValidationError(RuntimeError):
    """Invalid message payload for append/list operations."""


class Messages(BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="messages")
        self._impl = _ImplMessages(self._transport)
        self._state = self._impl._state

    async def list(self, conversation_id: str, *, limit: int = 100, cursor: str | None = None, timeout: float = 10.0) -> Result[list[BusMessage], Exception]:
        if not isinstance(conversation_id, str) or conversation_id.strip() == "":
            return Err(MessageValidationError("conversation_id must be non-empty"))
        if limit <= 0:
            return Err(MessageValidationError("limit must be > 0"))
        return await self._forward_result("bus.messages.list", self._impl.list, conversation_id, limit=limit, cursor=cursor, timeout=timeout)

    async def get(self, message_id: str, *, timeout: float = 10.0) -> Result[BusMessage, Exception]:
        if not isinstance(message_id, str) or message_id.strip() == "":
            return Err(MessageValidationError("message_id must be non-empty"))
        result = await self._forward_result("bus.messages.get", self._impl.get, message_id, timeout=timeout)
        if isinstance(result, Err) and isinstance(result.error, RuntimeError):
            return Err(MessageValidationError(str(result.error)))
        return result

    async def append(self, conversation_id: str, *, role: str, content: JsonValue, metadata: JsonObject | None = None, timeout: float = 10.0) -> Result[BusMessage, Exception]:
        if not isinstance(conversation_id, str) or conversation_id.strip() == "":
            return Err(MessageValidationError("conversation_id must be non-empty"))
        if not isinstance(role, str) or role.strip() == "":
            return Err(MessageValidationError("role must be non-empty"))
        result = await self._forward_result("bus.messages.append", self._impl.append, conversation_id, role=role, content=content, metadata=metadata, timeout=timeout)
        if isinstance(result, Err) and isinstance(result.error, RuntimeError):
            return Err(MessageValidationError(str(result.error)))
        return result

    async def delete(self, message_id: str, *, timeout: float = 10.0) -> Result[bool, Exception]:
        if not isinstance(message_id, str) or message_id.strip() == "":
            return Err(MessageValidationError("message_id must be non-empty"))
        return await self._forward_result("bus.messages.delete", self._impl.delete, message_id, timeout=timeout)


__all__ = ["Messages", "MessageValidationError"]
