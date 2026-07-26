"""Live-listener reconciliation helpers for runtime config changes."""

from __future__ import annotations

from typing import Any

from .contracts import normalize_live_platform
from .runtime_live_input import remember_live_room_context
from .runtime_live_session import begin_live_session, invalidate_live_session


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
    invalidate_live_session(runtime)
    try:
        await _stop_captured_provider(old_provider or runtime.live_provider)
    except Exception as exc:
        runtime.config.live_enabled = False
        runtime.live_connection_state = "disconnected"
        runtime.live_connection_auth_mode = "unknown"
        runtime.safety_guard.set_connected(False)
        await runtime.restore_instructions(force=True)
        runtime.audit.record(
            "live_reconnect_stop_failed",
            f"previous listener stop failed: {type(exc).__name__}",
            level="warning",
        )
        return
    if disabled or not room_ref:
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
    if platform == "bilibili":
        try:
            login_status = await runtime.bili_login_status()
        except Exception:
            login_status = {}
        if not isinstance(login_status, dict) or login_status.get("logged_in") is not True:
            runtime.config.live_enabled = False
            runtime.live_connection_state = "auth_required"
            runtime.live_connection_auth_mode = "unknown"
            runtime.safety_guard.set_connected(False)
            await runtime.restore_instructions(force=True)
            runtime.audit.record(
                "live_reconnect_auth_required",
                "Bilibili login required before reconnecting after config change",
                level="warning",
                detail={"platform": platform, "room_ref": room_ref},
            )
            return
        runtime.live_connection_auth_mode = "authenticated"
    else:
        runtime.live_connection_auth_mode = "provider_managed"
    await refresh_live_room_context(runtime, room_ref)
    started = await start_live_listener(runtime, room_ref)
    runtime._accepting_live_events = bool(started)
    if started:
        await runtime.sync_live_instructions(force=True)
    else:
        await runtime.restore_instructions(force=True)
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
    try:
        started = await runtime.live_provider.start_listening(room_ref)
    except Exception as exc:
        started = False
        runtime.audit.record(
            "live_listener_start_failed",
            f"listener start failed: {type(exc).__name__}",
            level="warning",
        )
    if started:
        begin_live_session(runtime)
        runtime._live_listener_started_at = float(runtime._live_state_now())
    runtime.live_connection_state = "connected" if started else "disconnected"
    if not started:
        runtime.live_connection_auth_mode = "unknown"
    runtime.config.live_enabled = bool(started)
    runtime.safety_guard.set_connected(started)
    runtime._accepting_live_events = bool(started)
    return started


async def stop_live_listener(runtime: Any, *, mark_disabled: bool = True) -> None:
    runtime._accepting_live_events = False
    invalidate_live_session(runtime)
    try:
        await runtime.live_provider.stop_listening()
    finally:
        try:
            if mark_disabled:
                runtime.config.live_enabled = False
                _clear_connected_room_status(runtime)
                await runtime.restore_instructions(force=True)
        finally:
            runtime.live_connection_state = "disconnected"
            runtime.live_connection_auth_mode = "unknown"
            runtime._live_listener_started_at = 0.0
            runtime.safety_guard.set_connected(False)


async def handle_unexpected_live_listener_stop(
    runtime: Any,
    *,
    connection_state: str = "disconnected",
) -> None:
    """Converge runtime state after a provider stops without an explicit user action."""
    runtime._accepting_live_events = False
    invalidate_live_session(runtime)
    runtime.config.live_enabled = False
    _clear_connected_room_status(runtime)
    runtime.live_connection_state = (
        connection_state if connection_state == "auth_required" else "disconnected"
    )
    runtime.live_connection_auth_mode = "unknown"
    runtime._live_listener_started_at = 0.0
    runtime.safety_guard.set_connected(False)
    await runtime.restore_instructions(force=True)


async def _stop_captured_provider(provider: Any) -> None:
    stopper = getattr(provider, "stop_listening", None)
    if callable(stopper):
        await stopper()


async def refresh_live_room_context(runtime: Any, room_ref: str) -> dict[str, Any]:
    """Replace room metadata without retaining fields from the previous room."""

    platform = runtime.live_provider.platform
    room_id = runtime.live_provider.configured_room_id()
    minimal_context: dict[str, Any] = {
        "platform": platform,
        "room_ref": str(room_ref or "").strip(),
        "live_status": "unknown",
    }
    if room_id > 0:
        minimal_context["room_id"] = room_id
    runtime.live_room_context = minimal_context
    try:
        status = await runtime.live_provider.lookup_room_status(room_ref)
    except Exception as exc:
        runtime.audit.record(
            "live_room_context_lookup_failed",
            f"room context lookup failed: {type(exc).__name__}",
            level="warning",
            detail={"platform": platform, "room_ref": str(room_ref or "")[:120]},
        )
        return runtime.live_room_context
    if not getattr(status, "ok", False):
        runtime.audit.record(
            "live_room_context_lookup_failed",
            str(getattr(status, "message", "") or "room context unavailable")[:200],
            level="warning",
            detail={"platform": platform, "room_ref": str(room_ref or "")[:120]},
        )
        return runtime.live_room_context
    return remember_live_room_context(
        runtime,
        status,
        platform=platform,
        room_ref=room_ref,
    )


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
