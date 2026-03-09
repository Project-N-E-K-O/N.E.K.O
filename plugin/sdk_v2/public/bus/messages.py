from __future__ import annotations

import time

from plugin.sdk_v2.shared.bus.types import BusMessage
from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue
from plugin.sdk_v2.shared.models import Err, Ok, Result

from ._client_base import BusClientBase


class Messages(BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="messages")

    async def list(self, conversation_id: str, *, limit: int = 100, cursor: str | None = None, timeout: float = 10.0) -> Result[list[BusMessage], Exception]:
        items = [item for item in self._state.messages.values() if item.conversation_id == conversation_id]
        return Ok(items[:limit])

    async def get(self, message_id: str, *, timeout: float = 10.0) -> Result[BusMessage, Exception]:
        item = self._state.messages.get(message_id)
        return Ok(item) if item is not None else Err(RuntimeError(message_id))

    async def append(self, conversation_id: str, *, role: str, content: JsonValue, metadata: JsonObject | None = None, timeout: float = 10.0) -> Result[BusMessage, Exception]:
        if conversation_id not in self._state.conversations:
            return Err(RuntimeError("conversation does not exist"))
        item = BusMessage(id=self._state.next_id("message"), conversation_id=conversation_id, role=role, content=content, timestamp=time.time(), metadata=dict(metadata or {}))
        self._state.messages[item.id] = item
        return Ok(item)

    async def delete(self, message_id: str, *, timeout: float = 10.0) -> Result[bool, Exception]:
        return Ok(self._state.messages.pop(message_id, None) is not None)


__all__ = ["Messages"]
