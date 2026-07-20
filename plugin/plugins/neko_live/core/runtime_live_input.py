"""Live input handling and result projections for the runtime."""

from __future__ import annotations

import time
from typing import Any

from .contracts import InteractionResult, PipelineStep, ViewerEvent
from .contracts_public import public_int, public_text
from .runtime_timeline import ensure_trace_id, record_timeline, timeline_for_trace
from .runtime_live_session import is_current_live_session_event


def record_result(runtime: Any, result: InteractionResult) -> None:
    if result.event.source == "developer_sandbox":
        payload = result.to_sandbox_dict()
        runtime.recent_sandbox_results.append(payload)
        runtime.event_bus.emit("sandbox_result", payload)
        return
    if not is_current_live_session_event(runtime, result.event):
        runtime.audit.record(
            "stale_live_result_discarded",
            "result belongs to an inactive live session",
            level="info",
            detail={"source": result.event.source},
        )
        return
    trace_id = ensure_trace_id(result.event)
    payload = result.to_public_dict()
    payload["response_module"] = runtime._route_from_result(payload)
    payload["event_signal"] = runtime._event_signal_from_result(payload)
    payload["trace_id"] = trace_id
    record_timeline(
        runtime,
        result.event,
        stage="result.record",
        status=result.status,
        reason=result.reason,
        route=str(payload.get("response_module") or ""),
    )
    payload["timeline"] = timeline_for_trace(runtime, trace_id)
    expose_request_metadata(payload)
    if str(payload.get("status") or "") == "pushed":
        spent_output = runtime._spent_output_text(payload)
        spent_families = runtime._spent_output_families(spent_output)
        if spent_families:
            payload["spent_output_family"] = ",".join(spent_families)
    runtime.recent_results.append(payload)
    runtime.event_bus.emit("result", payload)


def expose_request_metadata(payload: dict[str, Any]) -> None:
    request = payload.get("request")
    metadata = request.get("metadata") if isinstance(request, dict) else None
    if not isinstance(metadata, dict):
        return
    for key in (
        "danmaku_profile",
        "danmaku_reply_target",
        "danmaku_reply_shape",
        "danmaku_anchor_hint",
        "reply_length_mode",
        "room_theme",
        "meme_hint_ids",
        "meme_hint_tags",
        "support_event_type",
        "support_event_tier",
        "support_event_label",
    ):
        value = _public_metadata_text(metadata.get(key))
        if value:
            payload[key] = value


def _public_metadata_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split()).strip()
    return text[:120] if len(text) > 120 else text


async def handle_live_payload(runtime: Any, payload: dict[str, Any]) -> InteractionResult:
    event = runtime.live_provider.normalize(payload)
    signal_event_type = _signal_event_type(event)
    ensure_trace_id(event)
    remember_live_danmaku_seen(runtime, event)
    record_timeline(
        runtime,
        event,
        stage="live_input.normalize",
        status="ok",
        route=signal_event_type or event.source,
    )
    return await runtime.pipeline.handle_event(event)


def remember_live_danmaku_seen(runtime: Any, event: ViewerEvent) -> None:
    if str(event.source or "") != "live_danmaku":
        return
    runtime._last_live_danmaku_seen_at = time.time()
    runtime._last_live_danmaku_seen_type = "live_danmaku"
    if hasattr(runtime, "_hosting_without_viewer_count"):
        runtime._hosting_without_viewer_count = 0


def _signal_event_type(event: ViewerEvent) -> str:
    if not isinstance(event.raw, dict):
        return ""
    raw = event.raw.get("event_type")
    if not isinstance(raw, str):
        return ""
    return raw.strip().lower()


def record_live_signal_only_skip(runtime: Any, event: ViewerEvent, event_type: str) -> InteractionResult:
    normalized = "super_chat" if event_type == "sc" else event_type
    reason = f"live_event_signal.unsupported_{normalized}"
    result = InteractionResult(
        accepted=False,
        status="skipped",
        event=event,
        reason=reason,
        steps=[PipelineStep(runtime._signal_route_for_event_type(normalized), "skipped", reason)],
    )
    record_timeline(
        runtime,
        event,
        stage="live_input.signal_only",
        status="skipped",
        reason=reason,
        route=runtime._signal_route_for_event_type(normalized),
    )
    runtime.audit.record(
        "live_event_signal_only",
        reason,
        level="info",
        detail={"event_type": normalized, "uid": event.uid},
    )
    runtime.record_result(result)
    return result


async def lookup_live_room(runtime: Any, room_id: Any) -> dict[str, Any]:
    normalized = runtime.live_provider.normalize_room_ref(room_id)
    room_ref = _public_lookup_room_ref(normalized.get("room_ref") if isinstance(normalized, dict) else "")
    status = await runtime.live_provider.lookup_room_status(room_id)
    configured_room_ref = _public_lookup_room_ref(runtime.live_provider.configured_room_ref())
    if not configured_room_ref or room_ref == configured_room_ref:
        remember_live_room_context(runtime, status, platform=runtime.live_provider.platform, room_ref=room_ref)
    level = "info" if status.ok else "warning"
    runtime.audit.record(
        "live_room_lookup",
        status.message or "live room looked up",
        level=level,
        detail={
            "platform": runtime.live_provider.platform,
            "room_ref": room_ref,
            "room_id": status.room_id,
            "ok": status.ok,
            "live_status": status.live_status,
        },
    )
    result = status.to_dict()
    result["platform"] = runtime.live_provider.platform
    result["room_ref"] = room_ref
    return result


def remember_live_room_context(
    runtime: Any,
    status: Any,
    *,
    platform: str = "",
    room_ref: str = "",
) -> dict[str, Any]:
    """Cache public live-room metadata for prompt context without persisting it."""

    if not getattr(status, "ok", False):
        return getattr(runtime, "live_room_context", {}) if isinstance(getattr(runtime, "live_room_context", {}), dict) else {}
    context = {
        "platform": public_text(platform, max_len=40),
        "room_ref": public_text(room_ref, max_len=120),
        "room_id": public_int(getattr(status, "room_id", 0), minimum=0),
        "title": public_text(getattr(status, "title", ""), max_len=120),
        "anchor_name": public_text(getattr(status, "anchor_name", ""), max_len=80),
        "live_status": public_text(getattr(status, "live_status", ""), max_len=40),
    }
    runtime.live_room_context = {key: value for key, value in context.items() if value not in ("", 0)}
    return runtime.live_room_context


def _public_lookup_room_ref(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value) if value > 0 else ""
    if not isinstance(value, str):
        return ""
    return value.strip()
