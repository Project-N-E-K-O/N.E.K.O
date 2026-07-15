"""Live-listener reconciliation helpers for runtime config changes."""

from __future__ import annotations

from typing import Any

from .contracts import normalize_live_platform


async def reconcile_live_listener_after_config(
    runtime: Any,
    clean: dict[str, Any],
    *,
    old_room_id: int,
    old_platform: str = "bilibili",
    old_room_ref: str = "",
    was_listening: bool,
    old_provider: Any = None,
) -> None:
    if not was_listening:
        return
    room_ref = runtime.live_provider.configured_room_ref()
    platform = runtime.live_provider.platform
    old_platform = normalize_live_platform(old_platform)
    current_room_id = runtime.live_provider.configured_room_id()
    previous_room_id = old_room_id if old_platform == "bilibili" else 0
    room_changed = bool({"live_room_id", "live_room_ref", "live_platform"} & set(clean)) and (
        current_room_id != previous_room_id
        or room_ref != old_room_ref
        or platform != old_platform
    )
    disabled = "live_enabled" in clean and not bool(runtime.config.live_enabled)
    if not room_changed and not disabled:
        return
    runtime._accepting_live_events = False
    await _stop_captured_provider(old_provider or runtime.live_provider)
    runtime.live_audience_session.finish_session()
    if disabled or not room_ref:
        runtime.live_events.reset()
        runtime.config.live_enabled = False
        runtime.live_connection_state = "disconnected"
        runtime.live_connection_auth_mode = "unknown"
        runtime.safety_guard.set_connected(False)
        _clear_connected_room_status(runtime)
        await runtime.restore_instructions(force=True)
        return
    if not runtime.config.live_enabled:
        runtime.live_connection_state = "disconnected"
        runtime.live_connection_auth_mode = "unknown"
        runtime.safety_guard.set_connected(False)
        return
    started = await start_live_listener(runtime, room_ref)
    runtime._accepting_live_events = bool(started)
    runtime.audit.record(
        "live_reconnected" if started else "live_reconnect_failed",
        (
            "danmaku listener restarted for room change"
            if started
            else "failed to restart danmaku listener for room change"
        ),
        level="info" if started else "warning",
        detail={
            "platform": platform,
            "room_ref": room_ref,
            "room_id": current_room_id,
            "previous_room_id": previous_room_id,
            "previous_room_ref": old_room_ref,
            "previous_platform": old_platform,
        },
    )


async def start_live_listener(runtime: Any, room_ref: Any) -> bool:
    runtime._accepting_live_events = False
    started = await runtime.live_provider.start_listening(room_ref)
    if started:
        runtime.live_audience_session.start_session()
        runtime.pipeline.clear_dry_run_session_state()
        runtime._live_listener_started_at = float(runtime._live_state_now())
        runtime._idle_hosting_consecutive_failures = 0
    runtime.live_connection_state = "connected" if started else "disconnected"
    if not started:
        runtime.live_connection_auth_mode = "unknown"
    runtime.config.live_enabled = bool(started)
    runtime.safety_guard.set_connected(started)
    runtime._accepting_live_events = bool(started)
    return started


async def stop_live_listener(runtime: Any, *, mark_disabled: bool = True) -> None:
    runtime._accepting_live_events = False
    await runtime.live_provider.stop_listening()
    runtime.live_audience_session.finish_session()
    runtime.live_events.reset()
    if mark_disabled:
        runtime.config.live_enabled = False
        _clear_connected_room_status(runtime)
        await runtime.restore_instructions(force=True)
    runtime.live_connection_state = "disconnected"
    runtime.live_connection_auth_mode = "unknown"
    runtime._live_listener_started_at = 0.0
    runtime.safety_guard.set_connected(False)


async def _stop_captured_provider(provider: Any) -> None:
    stopper = getattr(provider, "stop_listening", None)
    if callable(stopper):
        await stopper()


def sync_douyin_listener_state(runtime: Any, state: Any) -> None:
    provider = getattr(runtime, "live_provider", None)
    if getattr(provider, "platform", "") != "douyin":
        return
    connected = str(state or "").strip().lower() in {"connected", "receiving"}
    runtime.live_connection_state = "connected" if connected else "disconnected"
    runtime.safety_guard.set_connected(connected)
    if not connected:
        runtime._live_listener_started_at = 0.0
        runtime.live_connection_auth_mode = "unknown"


def _clear_connected_room_status(runtime: Any) -> None:
    room_context = getattr(runtime, "live_room_context", None)
    if isinstance(room_context, dict):
        runtime.live_room_context = {
            "live_status": "unknown",
        }
