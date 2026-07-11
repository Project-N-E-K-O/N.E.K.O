"""Lightweight in-memory trace timeline for NEKO Live."""

from __future__ import annotations

import time
import uuid
from typing import Any

from .viewer_preferences import safe_text


def new_trace_id() -> str:
    return "tr_" + uuid.uuid4().hex[:12]


def ensure_trace_id(event: Any) -> str:
    trace_id = safe_text(getattr(event, "trace_id", ""), max_len=80)
    if not trace_id:
        trace_id = new_trace_id()
        try:
            event.trace_id = trace_id
        except Exception:
            pass
    return trace_id


def record_timeline(
    runtime: Any,
    event: Any,
    *,
    stage: str,
    status: str,
    reason: str = "",
    route: str = "",
) -> None:
    trace_id = ensure_trace_id(event)
    _append(
        runtime,
        {
            "trace_id": trace_id,
            "at": time.time(),
            "stage": safe_text(stage, max_len=80),
            "status": safe_text(status, max_len=80),
            "reason": safe_text(reason, max_len=160),
            "route": safe_text(route, max_len=80),
            "uid": safe_text(getattr(event, "uid", ""), max_len=80),
            "source": safe_text(getattr(event, "source", ""), max_len=80),
        },
    )


def record_payload_timeline(
    runtime: Any,
    payload: dict[str, Any],
    *,
    stage: str,
    status: str,
    reason: str = "",
    route: str = "",
) -> str:
    trace_id = safe_text(payload.get("trace_id"), max_len=80) or new_trace_id()
    payload["trace_id"] = trace_id
    _append(
        runtime,
        {
            "trace_id": trace_id,
            "at": time.time(),
            "stage": safe_text(stage, max_len=80),
            "status": safe_text(status, max_len=80),
            "reason": safe_text(reason, max_len=160),
            "route": safe_text(route, max_len=80),
            "uid": safe_text(payload.get("uid"), max_len=80),
            "source": "live_payload",
        },
    )
    return trace_id


def timeline_for_trace(runtime: Any, trace_id: str, *, limit: int = 16) -> list[dict[str, Any]]:
    safe_trace = safe_text(trace_id, max_len=80)
    if not safe_trace:
        return []
    items = [
        dict(item)
        for item in getattr(runtime, "runtime_timeline", [])
        if isinstance(item, dict) and item.get("trace_id") == safe_trace
    ]
    return items[-limit:]


def _append(runtime: Any, item: dict[str, Any]) -> None:
    timeline = getattr(runtime, "runtime_timeline", None)
    if timeline is None:
        return
    try:
        timeline.append(item)
    except Exception:
        pass
