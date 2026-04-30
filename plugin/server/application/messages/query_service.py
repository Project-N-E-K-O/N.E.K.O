from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
from typing import cast

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.application.contracts import MessageQueryResponse, SerializedMessage
from plugin.server.domain.errors import ServerDomainError
from plugin.server.domain.normalization import (
    coerce_optional_int,
    normalize_mapping,
    normalize_mapping_list,
)
from plugin.utils.time_utils import now_iso
from plugin.settings import MESSAGE_QUEUE_DEFAULT_MAX_COUNT, MESSAGE_QUEUE_MAX

logger = get_logger("server.application.messages.query")


def _normalize_messages(raw_messages: list[object]) -> list[SerializedMessage]:
    normalized_messages = normalize_mapping_list(raw_messages, context="messages")
    return [cast(SerializedMessage, item) for item in normalized_messages]


def _b64_bytes(value: object) -> str | None:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        return None
    encoded = base64.b64encode(bytes(value))
    return encoded.decode("utf-8")


def _serialize_message(record: Mapping[str, object]) -> SerializedMessage:
    metadata_obj = record.get("metadata")
    metadata: dict[str, object]
    if isinstance(metadata_obj, Mapping):
        metadata = normalize_mapping(metadata_obj, context="message.metadata")
    else:
        metadata = {}

    plugin_value = record.get("plugin_id")
    source_value = record.get("source")
    # TODO(v0.9): drop ``description`` from the serialized response together
    # with the legacy push_message kwarg.  v2 push_message has no
    # ``description`` field; the synthesised value is empty for native v2
    # callers and only useful as a label for old plugins that still pass
    # ``description=``.  See docs/changelog/plugin-push-message-v2.md.
    description_value = record.get("description")

    message_type_value = record.get("message_type")
    fallback_type_value = record.get("type")
    binary_url_value = record.get("binary_url")
    if isinstance(message_type_value, str) and message_type_value:
        message_type = message_type_value
    elif isinstance(fallback_type_value, str) and fallback_type_value:
        message_type = fallback_type_value
    else:
        message_type = "text"

    priority_value = coerce_optional_int(record.get("priority"))
    timestamp_value = record.get("time")

    return {
        "plugin_id": plugin_value if isinstance(plugin_value, str) else "",
        "source": source_value if isinstance(source_value, str) else "",
        "description": description_value if isinstance(description_value, str) else "",
        "priority": priority_value if priority_value is not None else 0,
        "message_type": message_type,
        "content": record.get("content"),
        "binary_data": _b64_bytes(record.get("binary_data")),
        "binary_url": binary_url_value if isinstance(binary_url_value, str) else "",
        "metadata": metadata,
        "timestamp": timestamp_value if isinstance(timestamp_value, str) and timestamp_value else now_iso(),
        "message_id": (
            ""
            if record.get("message_id") is None
            else str(record.get("message_id"))
        ),
    }


def _query_messages_sync(
    *,
    plugin_id: str | None,
    max_count: int,
    priority_min: int | None,
) -> list[SerializedMessage]:
    requested_count = max_count if max_count > 0 else MESSAGE_QUEUE_DEFAULT_MAX_COUNT
    target_count = max(1, min(requested_count, MESSAGE_QUEUE_MAX))
    refresh_limit = (
        MESSAGE_QUEUE_MAX
        if (plugin_id is not None or priority_min is not None)
        else target_count
    )

    try:
        state.refresh_messages_cache_from_message_plane(
            limit=refresh_limit,
            timeout=1.0,
            ttl_seconds=0.5,
            force=False,
        )
    except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError):
        logger.debug("message cache refresh skipped due to transient sync error")

    serialized_messages_reversed: list[SerializedMessage] = []
    for item in state.iter_message_records_reverse():
        if not isinstance(item, Mapping):
            continue

        if plugin_id is not None and item.get("plugin_id") != plugin_id:
            continue

        if priority_min is not None:
            priority_value = coerce_optional_int(item.get("priority"))
            if priority_value is None or priority_value < priority_min:
                continue

        try:
            normalized_record = normalize_mapping(item, context="message_record")
            serialized_messages_reversed.append(_serialize_message(normalized_record))
        except ServerDomainError as exc:
            logger.debug(
                "skip malformed message record: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            continue
        if len(serialized_messages_reversed) >= target_count:
            break

    serialized_messages_reversed.reverse()
    return serialized_messages_reversed


class MessageQueryService:
    async def get_plugin_messages(
        self,
        *,
        plugin_id: str | None,
        max_count: int,
        priority_min: int | None,
    ) -> MessageQueryResponse:
        try:
            raw_messages = await asyncio.to_thread(
                _query_messages_sync,
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
