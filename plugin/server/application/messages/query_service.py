from __future__ import annotations

import asyncio
import base64
import math
from collections.abc import Mapping

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.utils import now_iso
from plugin.settings import MESSAGE_QUEUE_DEFAULT_MAX_COUNT, MESSAGE_QUEUE_MAX

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


def _b64_bytes(value: object) -> str | None:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        return None
    encoded = base64.b64encode(bytes(value))
    return encoded.decode("utf-8")


def _to_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        try:
            return int(value)
        except (OverflowError, ValueError):
            return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


def _serialize_message(record: Mapping[str, object]) -> dict[str, object]:
    metadata_obj = record.get("metadata")
    metadata: dict[str, object]
    if isinstance(metadata_obj, Mapping):
        metadata = _normalize_mapping(metadata_obj, context="message.metadata")
    else:
        metadata = {}

    plugin_value = record.get("plugin_id")
    source_value = record.get("source")
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

    priority_value = _to_int(record.get("priority"))
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
        "message_id": str(record.get("message_id") or ""),
    }


def _query_messages_sync(
    *,
    plugin_id: str | None,
    max_count: int,
    priority_min: int | None,
) -> list[dict[str, object]]:
    requested_count = max_count if max_count > 0 else MESSAGE_QUEUE_DEFAULT_MAX_COUNT
    target_count = max(1, min(requested_count, MESSAGE_QUEUE_MAX))

    try:
        state.refresh_messages_cache_from_message_plane(
            limit=target_count,
            timeout=1.0,
            ttl_seconds=0.5,
            force=False,
        )
    except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError):
        logger.debug("message cache refresh skipped due to transient sync error")

    serialized_messages_reversed: list[dict[str, object]] = []
    for item in state.iter_message_records_reverse():
        if not isinstance(item, Mapping):
            continue

        if plugin_id is not None and item.get("plugin_id") != plugin_id:
            continue

        if priority_min is not None:
            priority_value = _to_int(item.get("priority"))
            if priority_value is None or priority_value < priority_min:
                continue

        normalized_record = _normalize_mapping(item, context="message_record")
        serialized_messages_reversed.append(_serialize_message(normalized_record))
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
    ) -> dict[str, object]:
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
