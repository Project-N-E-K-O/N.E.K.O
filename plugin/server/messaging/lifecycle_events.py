from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.utils.time_utils import now_iso

logger = get_logger("server.messaging.lifecycle_events")


def _normalize_lifecycle_event(event: Mapping[str, object]) -> dict[str, object]:
    normalized = dict(event)

    trace_id_obj = normalized.get("trace_id")
    trace_id = trace_id_obj if isinstance(trace_id_obj, str) and trace_id_obj else str(uuid.uuid4())
    normalized["trace_id"] = trace_id

    lifecycle_id_obj = normalized.get("lifecycle_id")
    if not isinstance(lifecycle_id_obj, str) or not lifecycle_id_obj:
        normalized["lifecycle_id"] = trace_id

    event_time = normalized.get("time")
    if not isinstance(event_time, str) or not event_time:
        normalized["time"] = now_iso()

    return normalized


def emit_lifecycle_event(event: Mapping[str, object]) -> None:
    normalized_event = _normalize_lifecycle_event(event)
    lifecycle_queue = state.lifecycle_queue

    try:
        lifecycle_queue.put_nowait(normalized_event)
    except asyncio.QueueFull:
        try:
            lifecycle_queue.get_nowait()
            lifecycle_queue.put_nowait(normalized_event)
        except (asyncio.QueueEmpty, asyncio.QueueFull, RuntimeError, AttributeError):
            logger.warning("lifecycle queue overflow; failed to enqueue latest event")
    except (RuntimeError, AttributeError):
        logger.warning("lifecycle queue unavailable during emit")

    try:
        state.append_lifecycle_record(normalized_event)
    except (RuntimeError, ValueError, TypeError, AttributeError):
        event_type = normalized_event.get("type")
        logger.warning("failed to append lifecycle record: event_type={}", event_type)
