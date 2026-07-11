"""Lightweight in-memory trace timeline for NEKO Live."""

from __future__ import annotations

import time
import uuid
from typing import Any


def new_trace_id() -> str:
    return "tr_" + uuid.uuid4().hex[:12]


def ensure_trace_id(event: Any) -> str:
    trace_id = _safe_text(getattr(event, "trace_id", ""), max_len=80)
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
            "stage": _safe_text(stage, max_len=80),
            "status": _safe_text(status, max_len=80),
            "reason": _safe_text(reason, max_len=160),
            "route": _safe_text(route, max_len=80),
            "uid": _safe_text(getattr(event, "uid", ""), max_len=80),
            "source": _safe_text(getattr(event, "source", ""), max_len=80),
        },
    )


def _append(runtime: Any, item: dict[str, Any]) -> None:
    timeline = getattr(runtime, "runtime_timeline", None)
    if timeline is None:
        return
    try:
        timeline.append(item)
    except Exception:
        pass


def _safe_text(value: Any, *, max_len: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    return text[:max_len]
