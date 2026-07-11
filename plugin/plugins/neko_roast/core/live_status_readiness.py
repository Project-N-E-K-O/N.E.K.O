"""Readiness and speech-explanation projections for NEKO Live."""

from __future__ import annotations

from typing import Any

from .live_status_timing import IsoAgeFn, activity_level, iso_age_sec


def solo_test_readiness(
    *,
    config: Any,
    live_status: dict[str, Any],
    live_state: dict[str, Any],
    live_director_status: dict[str, Any],
    profile_count: int,
    warmup_observed: bool,
) -> dict[str, Any]:
    mode = str(live_state.get("mode") or getattr(config, "live_mode", "co_stream"))
    status_summary = str(live_status.get("summary") or "")
    is_solo = mode == "solo_stream"
    live_ready = status_summary in {"ready_to_stream", "test_only"}
    ready = bool(is_solo and live_ready)
    if not is_solo:
        summary = "not_solo_stream"
    elif not live_ready:
        summary = "live_not_ready"
    elif bool(live_status.get("dry_run")):
        summary = "ready_for_test"
    else:
        summary = "ready_for_live_test"

    blocked_status = "ready" if ready else "blocked"
    items = [
        {"id": "preflight", "status": "ready" if live_ready else "blocked", "reason": str(live_status.get("reason") or "")},
        {
            "id": "test_isolation",
            "status": "warning" if int(profile_count or 0) > 0 else blocked_status,
            "reason": "viewer_profiles_present" if int(profile_count or 0) > 0 else ("clean" if ready else summary),
        },
        {
            "id": "warmup_hosting",
            "status": "observed" if warmup_observed else blocked_status,
            "reason": "observed" if warmup_observed else ("available" if ready else summary),
        },
        {"id": "avatar_roast", "status": blocked_status, "reason": "available" if ready else summary},
        {"id": "danmaku_response", "status": blocked_status, "reason": "available" if ready else summary},
        {
            "id": "active_engagement",
            "status": blocked_status,
            "reason": str(live_director_status.get("reason") or "available") if ready else summary,
        },
        {"id": "idle_hosting", "status": blocked_status, "reason": "available" if ready else summary},
        {"id": "pacing_control", "status": blocked_status, "reason": activity_level(config)},
    ]
    return {
        "ready": ready,
        "summary": summary,
        "mode": mode,
        "dry_run": bool(live_status.get("dry_run")),
        "profile_count": int(profile_count or 0),
        "next_auto_action": str(live_director_status.get("next_auto_action") or "none"),
        "items": items,
    }


def speech_explanation(
    *,
    live_status: dict[str, Any],
    live_state: dict[str, Any],
    latest_result: dict[str, Any] | None,
    iso_age_fn: IsoAgeFn = iso_age_sec,
) -> dict[str, Any]:
    latest = latest_result if isinstance(latest_result, dict) else {}
    latest_status = str(latest.get("status") or "")
    latest_reason = str(latest.get("reason") or "")
    latest_age = iso_age_fn(latest.get("created_at")) if latest else None
    latest_latency = latest.get("response_latency_ms") if latest else None
    latest_event = latest.get("event") if isinstance(latest.get("event"), dict) else {}
    latest_source = str(latest_event.get("source") or "") if isinstance(latest_event, dict) else ""

    status_summary = str(live_status.get("summary") or "cannot_stream")
    status_reason = str(live_status.get("reason") or "room_not_configured")
    state_name = str(live_state.get("state") or "")
    state_reason = str(live_state.get("reason") or "")

    summary = "ready"
    reason = "ready"
    if status_summary == "cannot_stream":
        summary = "cannot_stream"
        reason = status_reason
    elif status_summary == "test_only":
        summary = "test_only"
        reason = status_reason
    elif status_summary == "temporarily_not_speaking":
        summary = "temporarily_not_speaking"
        reason = status_reason
    elif bool(live_state.get("warmup_hosting_candidate")):
        summary = "waiting_for_activity"
        reason = "solo_stream_warmup"
    elif bool(live_state.get("idle_hosting_candidate")):
        summary = "waiting_for_activity"
        reason = "idle_hosting_candidate"
    elif state_name in {"warmup", "quiet", "idle"}:
        summary = "waiting_for_activity"
        reason = state_reason or state_name
    elif latest_status == "pushed":
        summary = "recently_spoke"
        reason = "recent_output"
    elif latest_status == "skipped":
        summary = "recently_skipped"
        reason = "recently_skipped"
    elif latest_status == "failed":
        summary = "failed"
        reason = "failed"
    elif latest_status == "dry_run":
        summary = "test_only"
        reason = latest_reason or "dispatcher.dry_run"

    return {
        "summary": summary,
        "reason": reason,
        "live_status_summary": status_summary,
        "live_status_reason": status_reason,
        "live_state": state_name,
        "live_state_reason": state_reason,
        "cooldown_remaining": round(float(live_status.get("cooldown_remaining") or 0.0), 1),
        "warmup_hosting_candidate": bool(live_state.get("warmup_hosting_candidate")),
        "idle_hosting_candidate": bool(live_state.get("idle_hosting_candidate")),
        "last_result_status": latest_status,
        "last_result_reason": latest_reason,
        "last_result_source": latest_source,
        "last_result_age_sec": latest_age,
        "last_result_latency_ms": latest_latency,
    }
