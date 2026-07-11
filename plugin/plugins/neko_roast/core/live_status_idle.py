"""Idle-hosting eligibility calculations for NEKO Live."""

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
    recent_hosting_output_age: float | None = None,
    host_output_cooldown_seconds: float = 0.0,
) -> dict[str, Any]:
    elapsed = max(0.0, float(now) - float(last_attempt_at or 0.0))
    cooldown_remaining = 0.0
    if last_attempt_at > 0:
        cooldown_remaining = round(max(0.0, float(min_interval_seconds) - elapsed), 1)

    candidate = bool(live_state.get("idle_hosting_candidate"))
    auto_disabled = int(consecutive_failures or 0) >= int(failure_limit)
    host_output_cooldown_remaining = 0.0
    if recent_hosting_output_age is not None:
        host_output_cooldown_remaining = round(
            max(0.0, float(host_output_cooldown_seconds) - recent_hosting_output_age),
            1,
        )
    eligible = (
        candidate
        and cooldown_remaining <= 0.0
        and host_output_cooldown_remaining <= 0.0
        and not auto_disabled
    )
    reason = "eligible"
    if auto_disabled:
        reason = "auto_disabled"
    elif not candidate:
        reason = "not_candidate"
    elif cooldown_remaining > 0.0:
        reason = "minimum_interval"
    elif host_output_cooldown_remaining > 0.0:
        reason = "recent_host_output"
        cooldown_remaining = host_output_cooldown_remaining

    return {
        "eligible": eligible,
        "reason": reason,
        "candidate": candidate,
        "cooldown_remaining": cooldown_remaining,
        "host_output_cooldown_remaining": host_output_cooldown_remaining,
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
    idle_threshold = float(
        live_state.get("idle_threshold_seconds") or idle_threshold_fallback
    )
    return round(max(0.0, idle_threshold - age), 1)
