"""Core live-status summary calculations for NEKO Live."""

from __future__ import annotations

from typing import Any

from .contracts import normalize_live_platform, parse_room_id
from .live_status_timing import (
    IsoAgeFn,
    iso_age_sec,
    last_output_age_sec,
    last_viewer_activity_age_sec,
)


def live_status_summary(
    *,
    config: Any,
    live_connection: dict[str, Any],
    safety_status: str,
    cooldown_remaining: float,
    output_channel: dict[str, Any],
) -> dict[str, Any]:
    platform = normalize_live_platform(live_connection.get("platform") or getattr(config, "live_platform", "bilibili"))
    room_ref = _public_room_ref(
        live_connection.get("room_ref"),
        getattr(config, "live_room_ref", ""),
    )
    room_id = (
        _public_room_id(
            live_connection.get("room_id"),
            getattr(config, "live_room_id", 0),
            room_ref,
        )
        if platform == "bilibili"
        else 0
    )
    connected = bool(live_connection.get("connected"))
    room_live_status = _public_live_status(live_connection.get("live_status"))
    output_channel_ready = bool(output_channel.get("ready"))
    output_channel_reason = str(output_channel.get("reason") or "")
    output_channel_detail = str(output_channel.get("detail") or "")

    summary = "ready_to_stream"
    reason = "ready"
    can_output = True

    if room_id <= 0 and not room_ref:
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
    elif room_live_status == "offline" and not bool(getattr(config, "dry_run", True)):
        summary = "cannot_stream"
        reason = "live_room_offline"
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
        "platform": platform,
        "room_ref": room_ref,
        "room_id": room_id,
        "connected": connected,
        "live_status": room_live_status,
        "dry_run": bool(getattr(config, "dry_run", True)),
        "safety_status": safety_status,
        "cooldown_remaining": round(float(cooldown_remaining or 0.0), 1),
        "output_channel_ready": output_channel_ready,
        "output_channel_reason": output_channel_reason,
        "output_channel_detail": output_channel_detail,
    }


def _public_room_ref(primary: Any, fallback: Any = "") -> str:
    for value in (primary, fallback):
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            if value > 0:
                return str(value)
            continue
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return ""


def _public_room_id(primary: Any, fallback: Any = 0, room_ref: str = "") -> int:
    for value in (primary, fallback, room_ref):
        room_id = parse_room_id(value)
        if room_id > 0:
            return room_id
    return 0


def _public_live_status(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip().lower()
    if text in {"live", "offline", "unknown"}:
        return text
    return ""


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
    last_viewer_activity_age = last_viewer_activity_age_sec(
        health_rows, recent_results, iso_age_fn
    )
    last_output_age = last_output_age_sec(health_rows, recent_results, iso_age_fn)
    warmup_timeout = warmup_elapsed is not None and warmup_elapsed >= float(
        warmup_timeout_seconds
    )

    if live_status.get("summary") == "cannot_stream" or safety_status in {
        "tripped",
        "degraded",
        "disconnected",
    }:
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
