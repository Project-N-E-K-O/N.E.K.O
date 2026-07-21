"""Lightweight in-memory trace timeline for NEKO Live."""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from typing import Any

from .viewer_preferences import safe_text

_REASON_CODES = {
    "accepted", "allowed", "already_roasted", "batch_welcome", "blocked",
    "cooldown", "dispatcher.dry_run", "dispatcher.failed", "dispatcher.pushed",
    "dispatcher.skipped", "dry_run", "live_disabled", "live_ingest_disconnected",
    "ingest.duplicate_support_event", "live_room_offline", "manual_paused",
    "missing_uid", "normalized", "ok",
    "output_channel_unavailable", "ready", "room_not_configured", "safety_degraded",
    "safety_tripped",
}


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
            "reason": _reason_code(reason),
            "route": safe_text(route, max_len=80),
            "uid": _opaque_uid(runtime, getattr(event, "uid", "")),
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
            "reason": _reason_code(reason),
            "route": safe_text(route, max_len=80),
            "uid": _opaque_uid(runtime, payload.get("uid")),
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


def _opaque_uid(runtime: Any, value: Any) -> str:
    uid = safe_text(value, max_len=80)
    salt = getattr(runtime, "_timeline_salt", b"")
    if not uid or not isinstance(salt, bytes) or not salt:
        return ""
    digest = hmac.new(salt, uid.encode("utf-8"), hashlib.sha256).hexdigest()
    return "viewer_" + digest[:12]


def _reason_code(value: Any) -> str:
    reason = safe_text(value, max_len=160)
    if reason in _REASON_CODES:
        return reason
    lowered = reason.lower()
    if lowered.startswith("selected "):
        event_type = safe_text(lowered.removeprefix("selected "), max_len=32)
        return f"selected.{event_type}" if event_type.replace("_", "").isalnum() else "selected"
    for prefix in ("signal_only.", "support."):
        if lowered.startswith(prefix) and lowered.replace(".", "").replace("_", "").isalnum():
            return lowered
    if lowered.startswith("support "):
        event_type = safe_text(lowered.removeprefix("support "), max_len=32)
        return f"support.{event_type}" if event_type.replace("_", "").isalnum() else "support"
    if lowered.startswith("dry_run("):
        return "dispatcher.dry_run"
    if lowered.startswith("queued_to_neko("):
        return "dispatcher.pushed"
    if lowered.startswith("skipped_to_neko("):
        return "dispatcher.skipped"
    if reason.endswith(("Error", "Exception")):
        return "exception"
    return ""
