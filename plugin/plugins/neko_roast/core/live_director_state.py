"""Eligibility summaries for solo-stream automatic hosting."""

from __future__ import annotations

from typing import Any


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


