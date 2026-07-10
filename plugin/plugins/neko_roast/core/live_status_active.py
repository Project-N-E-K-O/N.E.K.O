"""Active-engagement eligibility calculations for NEKO Live."""

from __future__ import annotations

from typing import Any


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
    recent_hosting_output_age: float | None = None,
    host_output_cooldown_seconds: float = 0.0,
) -> dict[str, Any]:
    state_name = str(live_state.get("state") or "")
    idle_takeover_candidate = (
        state_name == "idle" and int(idle_takeover_streak or 0) > 0
    )
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
        recent_danmaku_cooldown = round(
            max(0.0, float(recent_danmaku_wait_seconds) - recent_danmaku_output_age),
            1,
        )
    host_output_cooldown = 0.0
    if recent_hosting_output_age is not None:
        host_output_cooldown = round(
            max(0.0, float(host_output_cooldown_seconds) - recent_hosting_output_age),
            1,
        )
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
    elif host_output_cooldown > 0.0 and not idle_takeover_candidate:
        reason = "recent_host_output"
        cooldown_remaining = host_output_cooldown
        eligible = False
    elif idle_takeover_candidate:
        reason = "idle_hosting_streak"
        eligible = True
    elif (
        idle_hosting_wait_remaining is not None
        and idle_hosting_wait_remaining <= float(idle_grace_seconds)
    ):
        reason = "approaching_idle_hosting"
        cooldown_remaining = idle_hosting_wait_remaining
        eligible = False
    return {
        "candidate": bool(candidate),
        "eligible": eligible,
        "reason": reason,
        "cooldown_remaining": cooldown_remaining,
        "minimum_interval_remaining": minimum_interval_remaining,
        "recent_danmaku_cooldown_remaining": recent_danmaku_cooldown,
        "host_output_cooldown_remaining": host_output_cooldown,
        "idle_hosting_wait_remaining": idle_hosting_wait_remaining,
        "minimum_interval_seconds": float(min_interval_seconds),
        "min_interval_seconds": float(min_interval_seconds),
    }
