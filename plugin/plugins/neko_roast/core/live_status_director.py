"""Pure director-state calculations for solo-stream hosting."""

from __future__ import annotations

from typing import Any

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
        if (
            str(active_engagement_status.get("reason") or "")
            == "approaching_idle_hosting"
        ):
            next_auto_action = "idle_hosting"
            eligible = False
            reason = "approaching_idle_hosting"
            cooldown_remaining = float(
                active_engagement_status.get("idle_hosting_wait_remaining") or 0.0
            )
            min_interval_seconds = float(
                idle_hosting_status.get("min_interval_seconds") or 0.0
            )
        else:
            next_auto_action = "active_engagement"
            eligible = bool(active_engagement_status.get("eligible"))
            reason = (
                "solo_quiet"
                if eligible
                else str(
                    active_engagement_status.get("reason")
                    or "active_engagement_not_ready"
                )
            )
            cooldown_remaining = float(
                active_engagement_status.get("cooldown_remaining") or 0.0
            )
            min_interval_seconds = float(
                active_engagement_status.get("min_interval_seconds") or 0.0
            )
    elif state_name == "idle":
        active_reason = str(active_engagement_status.get("reason") or "")
        if active_reason == "no_viewer_response":
            next_auto_action = "none"
            eligible = False
            reason = "no_viewer_response"
        elif active_reason == "idle_hosting_streak":
            next_auto_action = "active_engagement"
            eligible = bool(active_engagement_status.get("eligible"))
            reason = (
                "idle_hosting_streak"
                if eligible
                else str(
                    active_engagement_status.get("reason")
                    or "active_engagement_not_ready"
                )
            )
            cooldown_remaining = float(
                active_engagement_status.get("cooldown_remaining") or 0.0
            )
            min_interval_seconds = float(
                active_engagement_status.get("min_interval_seconds") or 0.0
            )
        else:
            next_auto_action = "idle_hosting"
            eligible = bool(idle_hosting_status.get("eligible"))
            reason = (
                "solo_idle"
                if eligible
                else str(idle_hosting_status.get("reason") or "idle_hosting_not_ready")
            )
            cooldown_remaining = float(
                idle_hosting_status.get("cooldown_remaining") or 0.0
            )
            min_interval_seconds = float(
                idle_hosting_status.get("min_interval_seconds") or 0.0
            )
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
