"""Async SDK client for ordinary conversation messages."""

from __future__ import annotations

from typing import TYPE_CHECKING

from plugin.sdk.shared.contracts.conversations import ConversationMessage
from plugin.sdk.shared.models.exceptions import TransportError

if TYPE_CHECKING:
    from plugin.sdk.shared.core.bus_context import SdkBusList


class SdkConversationsV2Bus:
    def __init__(self, raw_adapter: object, *, host_ctx: object) -> None:
        self._raw_adapter = raw_adapter
        self._host_ctx = host_ctx

    async def get(
        self,
        *,
        character: str | None = None,
        role: str | None = None,
        kind: str | None = None,
        input_source: str | None = None,
        origin_kind: str | None = None,
        max_count: int = 50,
        order: str = "asc",
        since_ts: float | None = None,
        timeout: float = 5.0,
    ) -> SdkBusList[ConversationMessage]:
        from plugin.sdk.shared.core.bus_context import SdkBusList

        method = getattr(self._raw_adapter, "get", None)
        if not callable(method):
            raise TransportError(
                "conversations_v2 is not available on this host",
                code="CAPABILITY_UNAVAILABLE",
            )
        items = await method(
            character=character,
            role=role,
            kind=kind,
            input_source=input_source,
            origin_kind=origin_kind,
            max_count=max_count,
            order=order,
            since_ts=since_ts,
            timeout=timeout,
        )
        return SdkBusList.from_raw(
            items,
            namespace="conversations_v2",
            record_factory=ConversationMessage,
            host_ctx=self._host_ctx,
        )

    async def get_message(
        self, message_id: str, *, timeout: float = 5.0
    ) -> ConversationMessage | None:
        method = getattr(self._raw_adapter, "get_message", None)
        if not callable(method):
            raise TransportError(
                "conversations_v2 is not available on this host",
                code="CAPABILITY_UNAVAILABLE",
            )
        item = await method(message_id, timeout=timeout)
        return ConversationMessage.from_payload(item) if item is not None else None

    def watch(
        self,
        *,
        character: str | None = None,
        role: str | None = None,
        kind: str | None = None,
        input_source: str | None = None,
        origin_kind: str | None = None,
        view_limit: int | None = None,
    ):
        from plugin.sdk.shared.conversations.watcher import ConversationWatcher

        return ConversationWatcher(
            self._raw_adapter,
            self._host_ctx,
            filters={
                "character": character,
                "role": role,
                "kind": kind,
                "input_source": input_source,
                "origin_kind": origin_kind,
            },
            view_limit=view_limit,
        )

    async def get_reply(self, reply_id: str, *, timeout: float = 5.0):
        from plugin.sdk.shared.contracts.conversations import ReplyDetail

        return await self._get_detail(
            "CONVERSATION_REPLY_GET", "reply_id", reply_id, timeout, ReplyDetail
        )

    async def get_interaction(self, input_id: str, *, timeout: float = 5.0):
        from plugin.sdk.shared.contracts.conversations import InteractionDetail

        return await self._get_detail(
            "CONVERSATION_INTERACTION_GET",
            "input_id",
            input_id,
            timeout,
            InteractionDetail,
        )

    async def _get_detail(self, operation, key, value, timeout, factory):
        import asyncio
        import math
        import time

        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be nonempty")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be a positive finite number")
        sender = getattr(self._host_ctx, "_send_request_and_wait_async", None)
        if not callable(sender):
            raise TransportError(
                "conversations_v2 detail is unavailable", code="CAPABILITY_UNAVAILABLE"
            )
        deadline = time.monotonic() + timeout
        try:
            async with asyncio.timeout(timeout):
                result = await sender(
                    method_name=operation.lower(),
                    request_type=operation,
                    request_data={key: value},
                    timeout=max(0, deadline - time.monotonic()),
                    wrap_result=False,
                )
                decoded = factory.from_payload(result) if result is not None else None
                if time.monotonic() >= deadline:
                    raise TimeoutError("conversation detail request timed out")
                return decoded
        except Exception as exc:
            if getattr(exc, "code", None) == "UNKNOWN_OPERATION":
                raise TransportError(
                    str(exc),
                    code="CAPABILITY_UNAVAILABLE",
                    details=getattr(exc, "details", None),
                ) from exc
            raise
