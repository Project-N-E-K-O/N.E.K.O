"""Conversation bus contracts."""

from __future__ import annotations

from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Result

from ._client_base import BusClientBase
from .types import BusConversation


class ConversationNotFoundError(RuntimeError):
    """Conversation id does not exist."""


class Conversations(BusClientBase):
    def __init__(self, *args: object, **kwargs: object):
        raise NotImplementedError("sdk_v2 contract-only facade: shared.bus.conversations not implemented")

    async def list(self, *, limit: int = 50, cursor: str | None = None, timeout: float = 10.0) -> Result[list[BusConversation], Exception]:
        raise NotImplementedError

    async def get(self, conversation_id: str, *, timeout: float = 10.0) -> Result[BusConversation, Exception]:
        raise NotImplementedError

    async def create(self, topic: str, *, metadata: JsonObject | None = None, timeout: float = 10.0) -> Result[BusConversation, Exception]:
        raise NotImplementedError

    async def delete(self, conversation_id: str, *, timeout: float = 10.0) -> Result[bool, Exception]:
        raise NotImplementedError


__all__ = ["Conversations", "ConversationNotFoundError"]
