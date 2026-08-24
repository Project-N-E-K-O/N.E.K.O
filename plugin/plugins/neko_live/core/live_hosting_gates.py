"""Gate helpers for warmup and idle hosting."""

from __future__ import annotations

from typing import Any


def hosting_live_state(runtime: Any) -> dict[str, Any]:
    live_connection = runtime.live_connection_snapshot()
    live_status = runtime.live_status_summary(live_connection)
    health_rows = runtime.runtime_health_rows()
    return runtime.live_state_summary(live_status, health_rows)


def idle_hosting_skip_reason(live_mode: str, live_state: dict[str, Any]) -> str | None:
    if live_mode != "solo_stream":
        return "idle_hosting.not_solo_stream"
    state = str(live_state.get("state") or "")
    if state == "paused":
        return "idle_hosting.paused"
    if state == "blocked":
        return "idle_hosting.blocked"
    if state != "idle":
        return "idle_hosting.not_idle"
    if not bool(live_state.get("idle_hosting_candidate")):
        return "idle_hosting.not_candidate"
    return None


def warmup_hosting_skip_reason(
    live_mode: str, live_state: dict[str, Any]
) -> str | None:
    if live_mode != "solo_stream":
        return "warmup_hosting.not_solo_stream"
    state = str(live_state.get("state") or "")
    if state == "paused":
        return "warmup_hosting.paused"
    if state == "blocked":
        return "warmup_hosting.blocked"
    if state != "warmup":
        return "warmup_hosting.not_warmup"
    if not bool(live_state.get("warmup_hosting_candidate")):
        return "warmup_hosting.not_candidate"
    return None


def idle_hosting_material_skip_reason(runtime: Any, *, automatic: bool) -> str | None:
    if not automatic:
        return None
    if _recent_hosting_streak_since_viewer_activity(runtime) >= 2:
        return "idle_hosting.no_viewer_response"
    if not _latest_actual_output_is_idle_hosting(runtime):
        return None
    return "idle_hosting.no_fresh_material"


def _recent_hosting_streak_since_viewer_activity(runtime: Any) -> int:
    streak = 0
    for result in reversed(list(getattr(runtime, "recent_results", []) or [])):
        if not isinstance(result, dict):
            continue
        event = result.get("event") if isinstance(result.get("event"), dict) else {}
        if str(event.get("source") or "") == "live_danmaku":
            return streak
        if str(result.get("status") or "") != "pushed":
            continue
        route = runtime._route_from_result(result)
        if route in {"warmup_hosting", "idle_hosting", "active_engagement"}:
            streak += 1
            continue
        if streak:
            return streak
    return streak


def idle_hosting_auto_ready(
    *,
    consecutive_failures: int,
    failure_limit: int,
    now: float,
    last_attempt_at: float,
    min_interval_seconds: float,
) -> bool:
    if consecutive_failures >= failure_limit:
        return False
    return now - last_attempt_at >= min_interval_seconds


def idle_hosting_candidate(live_state: dict[str, Any]) -> bool:
    return bool(live_state.get("idle_hosting_candidate"))


def warmup_hosting_candidate(live_state: dict[str, Any]) -> bool:
    return bool(live_state.get("warmup_hosting_candidate"))


def _latest_actual_output_is_idle_hosting(runtime: Any) -> bool:
    for result in reversed(list(getattr(runtime, "recent_results", []) or [])):
        if not isinstance(result, dict):
            continue
        if str(result.get("status") or "") != "pushed":
            continue
        event = result.get("event") if isinstance(result.get("event"), dict) else {}
        if str(event.get("source") or "") == "live_danmaku":
            return False
        route = runtime._route_from_result(result)
        if route == "idle_hosting":
            return True
        if route in {
            "warmup_hosting",
            "active_engagement",
            "avatar_roast",
            "danmaku_response",
            "live_support_events",
        }:
            return False
    return False
