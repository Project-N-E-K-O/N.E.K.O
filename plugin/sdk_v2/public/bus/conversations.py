from __future__ import annotations

from plugin.sdk_v2.shared.bus.types import BusConversation
from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Err, Ok, Result

from ._client_base import BusClientBase


class Conversations(BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="conversations")

    async def list(self, *, limit: int = 50, cursor: str | None = None, timeout: float = 10.0) -> Result[list[BusConversation], Exception]:
        items = list(self._state.conversations.values())
        return Ok(items[:limit])

    async def get(self, conversation_id: str, *, timeout: float = 10.0) -> Result[BusConversation, Exception]:
        item = self._state.conversations.get(conversation_id)
        return Ok(item) if item is not None else Err(RuntimeError(conversation_id))

    async def create(self, topic: str, *, metadata: JsonObject | None = None, timeout: float = 10.0) -> Result[BusConversation, Exception]:
        item = BusConversation(id=self._state.next_id("conversation"), topic=topic, metadata=dict(metadata or {}))
        self._state.conversations[item.id] = item
        return Ok(item)

    async def delete(self, conversation_id: str, *, timeout: float = 10.0) -> Result[bool, Exception]:
        removed = self._state.conversations.pop(conversation_id, None) is not None
        if removed:
            self._state.messages = {k: v for k, v in self._state.messages.items() if v.conversation_id != conversation_id}
        return Ok(removed)


__all__ = ["Conversations"]
