from __future__ import annotations

import asyncio
from collections.abc import Mapping

from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.utils import now_iso
from plugin.server.services import get_messages_from_queue

logger = get_logger("server.application.messages.query")


def _normalize_mapping(raw: Mapping[object, object], *, context: str) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ServerDomainError(
                code="INVALID_DATA_SHAPE",
                message=f"{context} contains non-string key",
                status_code=500,
                details={"key_type": type(key).__name__},
            )
        normalized[key] = value
    return normalized


def _normalize_messages(raw_messages: list[object]) -> list[dict[str, object]]:
    normalized_messages: list[dict[str, object]] = []
    for index, item in enumerate(raw_messages):
        if not isinstance(item, Mapping):
            raise ServerDomainError(
                code="INVALID_DATA_SHAPE",
                message="message item is not an object",
                status_code=500,
                details={"index": index, "item_type": type(item).__name__},
            )
        normalized_messages.append(_normalize_mapping(item, context=f"messages[{index}]"))
    return normalized_messages


class MessageQueryService:
    async def get_plugin_messages(
        self,
        *,
        plugin_id: str | None,
        max_count: int,
        priority_min: int | None,
    ) -> dict[str, object]:
        try:
            raw_messages = await asyncio.to_thread(
                get_messages_from_queue,
                plugin_id=plugin_id,
                max_count=max_count,
                priority_min=priority_min,
            )
            if not isinstance(raw_messages, list):
                raise ServerDomainError(
                    code="INVALID_DATA_SHAPE",
                    message="messages result is not an array",
                    status_code=500,
                    details={"result_type": type(raw_messages).__name__},
                )

            normalized_messages = _normalize_messages(raw_messages)
            return {
                "messages": normalized_messages,
                "count": len(normalized_messages),
                "time": now_iso(),
            }
        except ServerDomainError:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            logger.error(
                "get_plugin_messages failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="MESSAGE_QUERY_FAILED",
                message="Failed to get plugin messages",
                status_code=500,
                details={
                    "plugin_id": plugin_id or "",
                    "error_type": type(exc).__name__,
                },
            ) from exc
