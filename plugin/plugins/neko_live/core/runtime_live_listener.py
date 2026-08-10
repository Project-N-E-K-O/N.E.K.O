"""Live-listener reconciliation helpers for runtime config changes."""

from __future__ import annotations

import asyncio
from typing import Any

from .contracts import normalize_live_platform
from .runtime_live_input import remember_live_room_context
from .runtime_live_session import begin_live_session, invalidate_live_session
from .runtime_live_target import (
    capture_live_target,
    release_live_target,
    release_live_target_if_scene_restored,
)


def begin_live_listener_operation(runtime: Any) -> int:
    """Claim ownership of the next listener state transition."""

    operation_id = int(getattr(runtime, "_live_listener_operation_id", 0) or 0) + 1
    runtime._live_listener_operation_id = operation_id
    return operation_id


def is_current_live_listener_operation(runtime: Any, operation_id: int) -> bool:
    return int(getattr(runtime, "_live_listener_operation_id", 0) or 0) == operation_id


def _disable_recent_chat_tool(runtime: Any) -> None:
    """Keep recent chat out of the model tool loop during live sessions."""

    sync = getattr(getattr(runtime, "plugin", None), "_set_recent_chat_tool_enabled", None)
    if not callable(sync):
        return
    try:
        sync(False)
    except Exception as exc:
        runtime.audit.record(
            "recent_chat_tool_sync_failed",
            f"recent chat tool sync failed: {type(exc).__name__}",
            level="warning",
        )


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
    _disable_recent_chat_tool(runtime)
    invalidate_live_session(runtime)
    try:
        await _stop_captured_provider(old_provider or runtime.live_provider)
    except Exception as exc:
        runtime.config.live_enabled = False
        runtime.live_connection_state = "disconnected"
        runtime.live_connection_auth_mode = "unknown"
        runtime.safety_guard.set_connected(False)
        await runtime.restore_instructions(force=True)
        release_live_target_if_scene_restored(runtime)
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
        release_live_target_if_scene_restored(runtime)
        return
    if not runtime.config.live_enabled:
        runtime.live_connection_state = "disconnected"
        runtime.live_connection_auth_mode = "unknown"
        runtime.safety_guard.set_connected(False)
        release_live_target_if_scene_restored(runtime)
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
            release_live_target_if_scene_restored(runtime)
            runtime.audit.record(
                "live_reconnect_auth_required",
                "Bilibili login required before reconnecting after config change",
                level="warning",
                detail={"platform": platform, "room_ref": room_ref},
            )
            return
        runtime.live_connection_auth_mode = "authenticated"
    elif platform == "twitch":
        # Twitch validates the cached user token again while starting the new
        # client, resolving the target channel and creating EventSub
        # subscriptions.  Keep the public mode unknown until that succeeds;
        # treating Twitch like a provider-managed transport loses the explicit
        # authorization boundary used by the normal connect action.
        runtime.live_connection_auth_mode = "unknown"
    else:
        runtime.live_connection_auth_mode = "provider_managed"
    await refresh_live_room_context(runtime, room_ref)
    started = await start_live_listener(runtime, room_ref)
    runtime._accepting_live_events = bool(started)
    if started:
        if platform == "twitch":
            runtime.live_connection_auth_mode = "authenticated"
        await runtime.sync_live_instructions(force=True)
    else:
        await runtime.restore_instructions(force=True)
        release_live_target_if_scene_restored(runtime)
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
    await _await_pending_live_listener_cleanup(runtime)
    retry_pending_clear = getattr(
        getattr(runtime, "live_events", None),
        "retry_pending_context_clear",
        None,
    )
    if callable(retry_pending_clear):
        try:
            retry_pending_clear()
        except Exception as exc:
            runtime.audit.record(
                "ambient_context_clear_retry_failed",
                f"pending passive context clear retry failed: {type(exc).__name__}",
                level="warning",
            )
    capture_live_target(runtime)
    operation_id = begin_live_listener_operation(runtime)
    runtime._accepting_live_events = False
    # Tool calls can hold the realtime voice turn open after the plugin has
    # already returned. Passive next-turn snapshots provide the same facts
    # without entering that recovery path, so also remove any stale tool here.
    _disable_recent_chat_tool(runtime)
    try:
        started = await runtime.live_provider.start_listening(room_ref)
    except asyncio.CancelledError:
        if is_current_live_listener_operation(runtime, operation_id):
            invalidate_live_session(runtime)
            runtime.live_connection_state = "disconnected"
            runtime.live_connection_auth_mode = "unknown"
            runtime.config.live_enabled = False
            runtime.safety_guard.set_connected(False)
            runtime._accepting_live_events = False
            if not bool(getattr(runtime, "instructions_injected", False)):
                release_live_target(runtime)
        raise
    except Exception as exc:
        started = False
        if is_current_live_listener_operation(runtime, operation_id):
            runtime.audit.record(
                "live_listener_start_failed",
                f"listener start failed: {type(exc).__name__}",
                level="warning",
            )
    if not is_current_live_listener_operation(runtime, operation_id):
        runtime.audit.record(
            "live_listener_start_superseded",
            "listener start completion ignored because a newer control operation owns state",
            detail={"operation_id": operation_id},
        )
        return bool(
            getattr(runtime, "_accepting_live_events", False)
            and getattr(runtime, "live_connection_state", "") == "connected"
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
    if not started and not bool(getattr(runtime, "instructions_injected", False)):
        release_live_target(runtime)
    if started:
        refresh = getattr(
            getattr(runtime, "live_events", None),
            "schedule_session_context_refresh",
            None,
        )
        if callable(refresh):
            try:
                refresh()
            except Exception as exc:
                record = getattr(getattr(runtime, "audit", None), "record", None)
                if callable(record):
                    record(
                        "live_session_context_refresh_failed",
                        f"session context refresh failed: {type(exc).__name__}",
                        level="warning",
                    )
    return started


async def stop_live_listener(runtime: Any, *, mark_disabled: bool = True) -> None:
    operation_id = begin_live_listener_operation(runtime)
    runtime._accepting_live_events = False
    _disable_recent_chat_tool(runtime)
    invalidate_live_session(runtime)
    try:
        await runtime.live_provider.stop_listening()
    finally:
        if is_current_live_listener_operation(runtime, operation_id):
            try:
                if mark_disabled:
                    runtime.config.live_enabled = False
                    _clear_connected_room_status(runtime)
                    await runtime.restore_instructions()
            finally:
                if is_current_live_listener_operation(runtime, operation_id):
                    runtime.live_connection_state = "disconnected"
                    runtime.live_connection_auth_mode = "unknown"
                    runtime._live_listener_started_at = 0.0
                    runtime.safety_guard.set_connected(False)
                    if mark_disabled:
                        release_live_target_if_scene_restored(runtime)


async def handle_unexpected_live_listener_stop(
    runtime: Any,
    *,
    connection_state: str = "disconnected",
) -> None:
    """Converge runtime state after a provider stops without an explicit user action."""
    operation_id = _mark_unexpected_live_listener_stop(
        runtime,
        connection_state=connection_state,
    )
    try:
        await runtime.restore_instructions(force=True)
    finally:
        if is_current_live_listener_operation(runtime, operation_id):
            release_live_target_if_scene_restored(runtime)


def schedule_unexpected_live_listener_stop(
    runtime: Any,
    *,
    connection_state: str = "disconnected",
) -> "asyncio.Task[Any] | None":
    """Immediately close event ownership, then restore host state off-callback."""

    current = getattr(runtime, "_live_listener_cleanup_task", None)
    if current is not None and not current.done():
        return current
    operation_id = _mark_unexpected_live_listener_stop(
        runtime,
        connection_state=connection_state,
    )

    async def restore() -> None:
        try:
            await runtime.restore_instructions(force=True)
        finally:
            if is_current_live_listener_operation(runtime, operation_id):
                release_live_target_if_scene_restored(runtime)

    task = asyncio.create_task(restore())
    runtime._live_listener_cleanup_task = task

    def cleanup(done: "asyncio.Task[Any]") -> None:
        if getattr(runtime, "_live_listener_cleanup_task", None) is done:
            runtime._live_listener_cleanup_task = None
        if done.cancelled():
            return
        try:
            error = done.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            runtime.audit.record(
                "live_listener_cleanup_failed",
                f"unexpected listener cleanup failed: {type(error).__name__}",
                level="warning",
            )

    task.add_done_callback(cleanup)
    return task


async def _await_pending_live_listener_cleanup(runtime: Any) -> None:
    """Finish an older disconnect restore before a new listener owns the scene."""

    task = getattr(runtime, "_live_listener_cleanup_task", None)
    if task is None or task.done() or task is asyncio.current_task():
        return
    await asyncio.shield(task)


def _mark_unexpected_live_listener_stop(
    runtime: Any,
    *,
    connection_state: str,
) -> int:
    operation_id = begin_live_listener_operation(runtime)
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
    return operation_id


async def _stop_captured_provider(provider: Any) -> None:
    stopper = getattr(provider, "stop_listening", None)
    if callable(stopper):
        await stopper()


async def refresh_live_room_context(runtime: Any, room_ref: str) -> dict[str, Any]:
    """Replace room metadata without retaining fields from the previous room."""

    platform = runtime.live_provider.platform
    if not _owns_live_room_context_target(runtime, platform, room_ref):
        current = getattr(runtime, "live_room_context", {})
        return current if isinstance(current, dict) else {}
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
        if not _owns_live_room_context_target(runtime, platform, room_ref):
            return runtime.live_room_context
        runtime.audit.record(
            "live_room_context_lookup_failed",
            f"room context lookup failed: {type(exc).__name__}",
            level="warning",
            detail={"platform": platform, "room_ref": str(room_ref or "")[:120]},
        )
        return runtime.live_room_context
    if not _owns_live_room_context_target(runtime, platform, room_ref):
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


def _owns_live_room_context_target(runtime: Any, platform: str, room_ref: Any) -> bool:
    provider = getattr(runtime, "live_provider", None)
    if provider is None or getattr(provider, "platform", "") != platform:
        return False
    configured = getattr(provider, "configured_room_ref", None)
    if not callable(configured):
        return False
    try:
        current_room_ref = configured()
    except Exception:
        return False
    return str(current_room_ref or "").strip() == str(room_ref or "").strip()


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
