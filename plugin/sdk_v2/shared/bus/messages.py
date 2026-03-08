"""Message bus contracts."""

from __future__ import annotations

from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue
from plugin.sdk_v2.shared.models import Result

from ._client_base import BusClientBase
from .types import BusMessage


class MessageValidationError(RuntimeError):
    """Invalid message payload for append/list operations."""


class Messages(BusClientBase):
    def __init__(self, *args: object, **kwargs: object):
        raise NotImplementedError("sdk_v2 contract-only facade: shared.bus.messages not implemented")

    async def list(self, conversation_id: str, *, limit: int = 100, cursor: str | None = None, timeout: float = 10.0) -> Result[list[BusMessage], Exception]:
        raise NotImplementedError

    async def get(self, message_id: str, *, timeout: float = 10.0) -> Result[BusMessage, Exception]:
        raise NotImplementedError

    async def append(
        self,
        conversation_id: str,
        *,
        role: str,
        content: JsonValue,
        metadata: JsonObject | None = None,
        timeout: float = 10.0,
    ) -> Result[BusMessage, Exception]:
        raise NotImplementedError

    async def delete(self, message_id: str, *, timeout: float = 10.0) -> Result[bool, Exception]:
        raise NotImplementedError


__all__ = ["Messages", "MessageValidationError"]
