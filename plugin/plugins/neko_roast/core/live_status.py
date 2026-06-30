"""Pure live status and director-state calculations for NEKO Live."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from . import recent_context


def _activity_level(config: Any) -> str:
    return str(getattr(config, "activity_level", "standard"))


def active_engagement_min_interval_seconds(config: Any) -> float:
    return {
        "quiet": 300.0,
        "active": 45.0,
        "standard": 60.0,
    }.get(_activity_level(config), 60.0)


def active_engagement_after_danmaku_interval_seconds(config: Any) -> float:
    return {
        "quiet": 210.0,
        "active": 30.0,
        "standard": 45.0,
    }.get(_activity_level(config), 45.0)


def active_engagement_idle_grace_seconds(config: Any, default: float) -> float:
    return {
        "quiet": 45.0,
        "active": 15.0,
        "standard": float(default),
    }.get(_activity_level(config), float(default))


def idle_hosting_min_interval_seconds(config: Any) -> float:
    return {
        "quiet": 180.0,
        "active": 45.0,
        "standard": 90.0,
    }.get(_activity_level(config), 90.0)


def solo_warmup_timeout_seconds(config: Any, default: float) -> float:
    return {
        "quiet": 90.0,
        "active": 30.0,
        "standard": float(default),
    }.get(_activity_level(config), float(default))


def live_state_threshold_seconds(config: Any, default_engaged: float, default_idle: float) -> tuple[float, float]:
    return {
        "quiet": (90.0, 300.0),
        "active": (30.0, 90.0),
        "standard": (float(default_engaged), float(default_idle)),
    }.get(_activity_level(config), (float(default_engaged), float(default_idle)))


def age_sec(timestamp: Any) -> float | None:
    try:
        value = float(timestamp)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return round(max(0.0, time.time() - value), 1)


def iso_age_sec(value: Any) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return round(max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds()), 1)


IsoAgeFn = Callable[[Any], float | None]


def recent_live_danmaku_output_age_sec(recent_results: Any, iso_age_fn: IsoAgeFn = iso_age_sec) -> float | None:
    for result in reversed(list(recent_results or [])):
        if not isinstance(result, dict):
            continue
        if str(result.get("status") or "") not in {"pushed", "dry_run"}:
            continue
        event = result.get("event") if isinstance(result.get("event"), dict) else {}
        if str(event.get("source") or "") != "live_danmaku":
            continue
        route = recent_context.route_from_result(result)
        if route not in {"avatar_roast", "danmaku_response", "live_danmaku"}:
            continue
        age = iso_age_fn(result.get("created_at"))
        if age is not None:
            return float(age)
    return None


def recent_live_danmaku_event_age_sec(recent_results: Any, iso_age_fn: IsoAgeFn = iso_age_sec) -> float | None:
    for result in reversed(list(recent_results or [])):
        if not isinstance(result, dict):
            continue
        event = result.get("event") if isinstance(result.get("event"), dict) else {}
        if str(event.get("source") or "") != "live_danmaku":
            continue
        age = iso_age_fn(result.get("created_at"))
        if age is not None:
            return float(age)
    return None


def last_viewer_activity_age_sec(
    rows: list[dict[str, Any]],
    recent_results: Any = None,
    iso_age_fn: IsoAgeFn = iso_age_sec,
) -> float | None:
    ages: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("id") not in {"live_ingest", "event_bus", "selection"}:
            continue
        outcome = str(row.get("last_outcome") or "").strip()
        if outcome not in {"danmaku", "live_danmaku"}:
            continue
        age = row.get("age_sec")
        if age is None:
            continue
        try:
            ages.append(float(age))
        except (TypeError, ValueError):
            continue
    recent_age = recent_live_danmaku_event_age_sec(recent_results, iso_age_fn)
    if recent_age is not None:
        ages.append(float(recent_age))
    return min(ages) if ages else None


def last_output_age_sec(
    rows: list[dict[str, Any]],
    recent_results: Any = None,
    iso_age_fn: IsoAgeFn = iso_age_sec,
) -> float | None:
    ages: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("id") not in {"pipeline", "dispatcher"}:
            continue
        age = row.get("age_sec")
        if age is None:
            continue
        try:
            ages.append(float(age))
        except (TypeError, ValueError):
            continue
    for result in reversed(list(recent_results or [])):
        if not isinstance(result, dict):
            continue
        if str(result.get("status") or "") not in {"pushed", "dry_run"}:
            continue
        age = iso_age_fn(result.get("created_at"))
        if age is not None:
            ages.append(float(age))
            break
    return min(ages) if ages else None


def live_status_summary(
    *,
    config: Any,
    live_connection: dict[str, Any],
    safety_status: str,
    cooldown_remaining: float,
    output_channel: dict[str, Any],
) -> dict[str, Any]:
    room_id = int(getattr(config, "live_room_id", 0) or live_connection.get("room_id") or 0)
    connected = bool(live_connection.get("connected"))
    output_channel_ready = bool(output_channel.get("ready"))
    output_channel_reason = str(output_channel.get("reason") or "")
    output_channel_detail = str(output_channel.get("detail") or "")

    summary = "ready_to_stream"
    reason = "ready"
    can_output = True

    if room_id <= 0:
        summary = "cannot_stream"
        reason = "room_not_configured"
        can_output = False
    elif not bool(getattr(config, "live_enabled", False)):
        summary = "cannot_stream"
        reason = "live_disabled"
        can_output = False
    elif not connected:
        summary = "cannot_stream"
        reason = "live_ingest_disconnected"
        can_output = False
    elif safety_status == "paused":
        summary = "temporarily_not_speaking"
        reason = "manual_paused"
        can_output = False
    elif safety_status == "tripped":
        summary = "cannot_stream"
        reason = "safety_tripped"
        can_output = False
    elif safety_status == "degraded":
        summary = "temporarily_not_speaking"
        reason = "safety_degraded"
        can_output = False
    elif bool(getattr(config, "dry_run", True)):
        summary = "test_only"
        reason = "dry_run"
        can_output = False
    elif not output_channel_ready:
        summary = "cannot_stream"
        reason = output_channel_reason or "output_channel_unavailable"
        can_output = False
    elif cooldown_remaining > 0:
        summary = "temporarily_not_speaking"
        reason = "cooldown"
        can_output = False

    return {
        "summary": summary,
        "reason": reason,
        "can_output": can_output,
        "room_id": room_id,
        "connected": connected,
        "dry_run": bool(getattr(config, "dry_run", True)),
        "safety_status": safety_status,
        "cooldown_remaining": round(float(cooldown_remaining or 0.0), 1),
        "output_channel_ready": output_channel_ready,
        "output_channel_reason": output_channel_reason,
        "output_channel_detail": output_channel_detail,
    }


def live_state_summary(
    *,
    config: Any,
    live_status: dict[str, Any],
    health_rows: list[dict[str, Any]],
    recent_results: Any,
    warmup_observed: bool,
    warmup_elapsed: float | None,
    engaged_threshold: float,
    idle_threshold: float,
    warmup_timeout_seconds: float,
    iso_age_fn: IsoAgeFn = iso_age_sec,
) -> dict[str, Any]:
    safety_status = str(live_status.get("safety_status") or "")
    live_mode = str(getattr(config, "live_mode", "co_stream"))
    mode_role = "solo_host" if live_mode == "solo_stream" else "companion"

    state = "engaged"
    reason = "recent_activity"
    last_viewer_activity_age = last_viewer_activity_age_sec(health_rows, recent_results, iso_age_fn)
    last_output_age = last_output_age_sec(health_rows, recent_results, iso_age_fn)
    warmup_timeout = warmup_elapsed is not None and warmup_elapsed >= float(warmup_timeout_seconds)

    if live_status.get("summary") == "cannot_stream" or safety_status in {"tripped", "degraded", "disconnected"}:
        state = "blocked"
        reason = "blocked_by_live_status"
    elif safety_status == "paused":
        state = "paused"
        reason = "manual_paused"
    elif (
        last_viewer_activity_age is None
        and live_mode == "solo_stream"
        and not warmup_observed
        and not warmup_timeout
    ):
        state = "warmup"
        reason = "solo_stream_warmup"
    elif last_viewer_activity_age is None or last_viewer_activity_age > idle_threshold:
        state = "idle"
        reason = "no_recent_activity"
    elif last_viewer_activity_age > engaged_threshold:
        state = "quiet"
        reason = "quiet_activity_gap"

    idle_hosting_candidate = (
        live_mode == "solo_stream"
        and state == "idle"
        and live_status.get("summary") in {"ready_to_stream", "test_only"}
        and float(live_status.get("cooldown_remaining") or 0.0) <= 0.0
    )
    warmup_hosting_candidate = (
        live_mode == "solo_stream"
        and state == "warmup"
        and live_status.get("summary") in {"ready_to_stream", "test_only"}
        and float(live_status.get("cooldown_remaining") or 0.0) <= 0.0
    )

    return {
        "state": state,
        "reason": reason,
        "mode": live_mode,
        "mode_role": mode_role,
        "warmup_hosting_candidate": warmup_hosting_candidate,
        "idle_hosting_candidate": idle_hosting_candidate,
        "last_activity_age_sec": last_viewer_activity_age,
        "last_viewer_activity_age_sec": last_viewer_activity_age,
        "last_output_age_sec": last_output_age,
        "engaged_threshold_seconds": float(engaged_threshold),
        "idle_threshold_seconds": float(idle_threshold),
        "warmup_elapsed_sec": warmup_elapsed,
        "warmup_timeout_seconds": float(warmup_timeout_seconds),
    }


def idle_hosting_status(
    *,
    live_state: dict[str, Any],
    now: float,
    last_attempt_at: float,
    min_interval_seconds: float,
    consecutive_failures: int,
    failure_limit: int,
) -> dict[str, Any]:
    elapsed = max(0.0, float(now) - float(last_attempt_at or 0.0))
    cooldown_remaining = 0.0
    if last_attempt_at > 0:
        cooldown_remaining = round(max(0.0, float(min_interval_seconds) - elapsed), 1)

    candidate = bool(live_state.get("idle_hosting_candidate"))
    auto_disabled = int(consecutive_failures or 0) >= int(failure_limit)
    eligible = candidate and cooldown_remaining <= 0.0 and not auto_disabled
    reason = "eligible"
    if auto_disabled:
        reason = "auto_disabled"
    elif not candidate:
        reason = "not_candidate"
    elif cooldown_remaining > 0.0:
        reason = "minimum_interval"

    return {
        "eligible": eligible,
        "reason": reason,
        "candidate": candidate,
        "cooldown_remaining": cooldown_remaining,
        "min_interval_seconds": float(min_interval_seconds),
        "consecutive_failures": int(consecutive_failures or 0),
    }


def idle_hosting_wait_remaining_for_quiet_state(
    live_state: dict[str, Any],
    *,
    idle_threshold_fallback: float,
) -> float | None:
    if str(live_state.get("state") or "") != "quiet":
        return None
    viewer_age = live_state.get("last_viewer_activity_age_sec")
    if viewer_age is None:
        return None
    try:
        age = float(viewer_age)
    except (TypeError, ValueError):
        return None
    idle_threshold = float(live_state.get("idle_threshold_seconds") or idle_threshold_fallback)
    return round(max(0.0, idle_threshold - age), 1)


def active_engagement_status(
    *,
    config: Any,
    live_status: dict[str, Any],
    live_state: dict[str, Any],
    now: float,
    last_attempt_at: float,
    min_interval_seconds: float,
    recent_danmaku_output_age: float | None,
    recent_danmaku_wait_seconds: float,
    idle_hosting_wait_remaining: float | None,
    idle_grace_seconds: float,
    idle_takeover_streak: int,
) -> dict[str, Any]:
    state_name = str(live_state.get("state") or "")
    idle_takeover_candidate = state_name == "idle" and int(idle_takeover_streak or 0) > 0
    live_mode = str(getattr(config, "live_mode", "co_stream"))
    candidate = (
        live_mode == "solo_stream"
        and (state_name == "quiet" or idle_takeover_candidate)
        and live_status.get("summary") in {"ready_to_stream", "test_only"}
        and float(live_status.get("cooldown_remaining") or 0.0) <= 0.0
    )
    elapsed = max(0.0, float(now) - float(last_attempt_at or 0.0))
    cooldown_remaining = 0.0
    if last_attempt_at > 0:
        cooldown_remaining = round(max(0.0, float(min_interval_seconds) - elapsed), 1)
    minimum_interval_remaining = cooldown_remaining
    recent_danmaku_cooldown = 0.0
    if recent_danmaku_output_age is not None:
        recent_danmaku_cooldown = round(max(0.0, float(recent_danmaku_wait_seconds) - recent_danmaku_output_age), 1)
    eligible = bool(candidate)
    reason = "eligible"
    if live_mode != "solo_stream":
        reason = "not_solo_stream"
    elif state_name in {"paused", "blocked"}:
        reason = state_name
    elif state_name not in {"quiet", "idle"}:
        reason = "not_quiet"
    elif state_name == "idle" and not idle_takeover_candidate:
        reason = "not_quiet"
    elif live_status.get("summary") not in {"ready_to_stream", "test_only"}:
        reason = str(live_status.get("reason") or "live_status_not_ready")
    elif float(live_status.get("cooldown_remaining") or 0.0) > 0.0:
        reason = "cooldown"
    elif cooldown_remaining > 0.0:
        reason = "minimum_interval"
        eligible = False
    elif recent_danmaku_cooldown > 0.0:
        reason = "recent_danmaku_output"
        cooldown_remaining = recent_danmaku_cooldown
        eligible = False
    elif idle_hosting_wait_remaining is not None and idle_hosting_wait_remaining <= float(idle_grace_seconds):
        reason = "approaching_idle_hosting"
        cooldown_remaining = idle_hosting_wait_remaining
        eligible = False
    elif idle_takeover_candidate:
        reason = "idle_hosting_streak"
    return {
        "candidate": bool(candidate),
        "eligible": eligible,
        "reason": reason,
        "cooldown_remaining": cooldown_remaining,
        "minimum_interval_remaining": minimum_interval_remaining,
        "recent_danmaku_cooldown_remaining": recent_danmaku_cooldown,
        "idle_hosting_wait_remaining": idle_hosting_wait_remaining,
        "minimum_interval_seconds": float(min_interval_seconds),
        "min_interval_seconds": float(min_interval_seconds),
    }


def live_director_status(
    *,
    config: Any,
    live_status: dict[str, Any],
    live_state: dict[str, Any],
    idle_hosting_status: dict[str, Any],
    active_engagement_status: dict[str, Any],
) -> dict[str, Any]:
    mode = str(live_state.get("mode") or getattr(config, "live_mode", "co_stream"))
    state_name = str(live_state.get("state") or "")

    next_auto_action = "none"
    eligible = False
    reason = "waiting_for_viewer"
    cooldown_remaining = 0.0
    min_interval_seconds = 0.0

    if mode != "solo_stream":
        reason = "companion_mode"
    elif state_name == "paused":
        reason = "paused"
    elif state_name == "blocked":
        reason = "blocked"
    elif state_name == "warmup":
        next_auto_action = "warmup_hosting"
        eligible = bool(live_state.get("warmup_hosting_candidate"))
        reason = "solo_warmup" if eligible else "warmup_hosting_not_ready"
    elif state_name == "quiet":
        if str(active_engagement_status.get("reason") or "") == "approaching_idle_hosting":
            next_auto_action = "idle_hosting"
            eligible = False
            reason = "approaching_idle_hosting"
            cooldown_remaining = float(active_engagement_status.get("idle_hosting_wait_remaining") or 0.0)
            min_interval_seconds = float(idle_hosting_status.get("min_interval_seconds") or 0.0)
        else:
            next_auto_action = "active_engagement"
            eligible = bool(active_engagement_status.get("eligible"))
            reason = "solo_quiet" if eligible else str(active_engagement_status.get("reason") or "active_engagement_not_ready")
            cooldown_remaining = float(active_engagement_status.get("cooldown_remaining") or 0.0)
            min_interval_seconds = float(active_engagement_status.get("min_interval_seconds") or 0.0)
    elif state_name == "idle":
        if str(active_engagement_status.get("reason") or "") == "idle_hosting_streak":
            next_auto_action = "active_engagement"
            eligible = bool(active_engagement_status.get("eligible"))
            reason = "idle_hosting_streak" if eligible else str(active_engagement_status.get("reason") or "active_engagement_not_ready")
            cooldown_remaining = float(active_engagement_status.get("cooldown_remaining") or 0.0)
            min_interval_seconds = float(active_engagement_status.get("min_interval_seconds") or 0.0)
        else:
            next_auto_action = "idle_hosting"
            eligible = bool(idle_hosting_status.get("eligible"))
            reason = "solo_idle" if eligible else str(idle_hosting_status.get("reason") or "idle_hosting_not_ready")
            cooldown_remaining = float(idle_hosting_status.get("cooldown_remaining") or 0.0)
            min_interval_seconds = float(idle_hosting_status.get("min_interval_seconds") or 0.0)
    elif state_name == "engaged":
        reason = "recent_activity"

    return {
        "next_auto_action": next_auto_action,
        "eligible": eligible,
        "reason": reason,
        "cooldown_remaining": round(max(0.0, cooldown_remaining), 1),
        "min_interval_seconds": float(min_interval_seconds),
        "mode": mode,
        "live_state": state_name,
    }


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
        {"id": "pacing_control", "status": blocked_status, "reason": _activity_level(config)},
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
