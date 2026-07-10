"""Control-panel and live-room actions for the runtime."""

from __future__ import annotations

from typing import Any

from .contracts import RoastConfig


def pause(runtime: Any) -> None:
    runtime.safety_guard.pause("manual pause from control panel")


def resume(runtime: Any) -> None:
    runtime.safety_guard.resume()


def clear_queue(runtime: Any) -> None:
    runtime.safety_guard.clear_queue()
    runtime.audit.record("queue_clear", "queue cleared")


async def clear_viewer_profiles(runtime: Any) -> dict[str, Any]:
    runtime._require_developer_mode()
    result = await runtime.viewer_store.clear_profiles()
    runtime.pipeline.clear_dry_run_session_state()
    runtime.audit.record("viewer_profiles_clear", "viewer profiles cleared", detail=result)
    return result


async def delete_viewer_profile(runtime: Any, uid: str) -> dict[str, Any]:
    runtime._require_developer_mode()
    result = await runtime.viewer_store.delete_profile(uid)
    clear_uid = getattr(getattr(runtime.pipeline, "session", None), "clear_uid", None)
    if callable(clear_uid):
        clear_uid(str(result.get("uid") or ""))
    runtime.audit.record("viewer_profile_delete", "viewer profile deleted", detail=result)
    return result


async def reset_viewer_impression(runtime: Any, uid: str) -> dict[str, Any]:
    runtime._require_developer_mode()
    result = await runtime.viewer_store.reset_profile_impression(uid)
    runtime.audit.record("viewer_profile_impression_reset", "viewer profile impression reset", detail=result)
    return result


def live_connection_snapshot(runtime: Any) -> dict[str, Any]:
    platform = runtime.live_provider.platform
    room_ref = runtime.live_provider.configured_room_ref()
    room_id = runtime.live_provider.configured_room_id()
    listener_state = runtime.live_provider.listener_state()
    state = _public_listener_state(listener_state.get("state"), getattr(runtime, "live_connection_state", ""))
    viewer_count = _public_viewer_count(listener_state.get("viewer_count"))
    connected = state in ("receiving", "connected")
    snapshot = {
        "platform": platform,
        "room_ref": room_ref,
        "room_id": room_id,
        "state": state,
        "connected": connected,
        "listening": connected and runtime.config.live_enabled,
        "viewer_count": viewer_count,
    }
    room_context = getattr(runtime, "live_room_context", {})
    if isinstance(room_context, dict):
        for key in ("title", "anchor_name", "live_status"):
            value = _public_optional_text(room_context.get(key))
            if value:
                snapshot[key] = value
    last_error = _public_optional_text(listener_state.get("last_error"))
    if last_error:
        snapshot["last_error"] = last_error
    for key in ("connection_plan", "reconnect"):
        value = listener_state.get(key)
        if isinstance(value, dict) and value:
            snapshot[key] = value
    return snapshot


def _public_listener_state(primary: Any, fallback: Any = "") -> str:
    allowed = {
        "disconnected",
        "connecting",
        "connected",
        "receiving",
        "reconnecting",
        "auth_required",
        "unsupported",
        "unknown",
    }
    for value in (primary, fallback):
        if isinstance(value, str):
            text = value.strip().lower()
            if text in allowed:
                return text
    return "disconnected"


def _public_viewer_count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    if isinstance(value, str):
        text = value.strip()
        return int(text) if text.isdigit() else 0
    return 0


def _public_optional_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()[:200]


async def set_live_room(runtime: Any, room_id: Any) -> RoastConfig:
    normalized = runtime.live_provider.normalize_room_ref(room_id)
    if not normalized.get("ok"):
        raise ValueError(str(normalized.get("message") or "room_ref must be configured"))
    update = {"live_room_ref": str(normalized.get("room_ref") or "")}
    if normalized.get("platform") == "bilibili":
        update["live_room_id"] = int(normalized.get("room_id") or 0)
    old_room_ref = runtime.live_provider.configured_room_ref()
    config = await runtime.update_config(update)
    if old_room_ref != str(normalized.get("room_ref") or "") and not runtime.live_provider.is_listening():
        runtime.live_connection_state = "disconnected"
        runtime.safety_guard.set_connected(False)
    runtime.audit.record(
        "live_room_set",
        "live room updated",
        detail={
            "platform": normalized.get("platform"),
            "room_ref": normalized.get("room_ref"),
            "room_id": normalized.get("room_id"),
        },
    )
    return config


async def connect_live_room(runtime: Any, room_id: Any = 0) -> dict[str, Any]:
    normalized = runtime.live_provider.normalize_room_ref(room_id)
    if not normalized.get("ok"):
        configured = runtime.live_provider.configured_room_ref()
        if configured:
            normalized = runtime.live_provider.normalize_room_ref(configured)
    if not normalized.get("ok"):
        raise ValueError(str(normalized.get("message") or "room_ref must be configured before connecting"))
    target_room_ref = str(normalized.get("room_ref") or "")
    if target_room_ref != runtime.live_provider.configured_room_ref():
        await runtime.set_live_room(target_room_ref)
        if runtime.live_provider.is_listening() and target_room_ref == runtime.live_provider.configured_room_ref():
            return runtime.live_connection_snapshot()
    await _refresh_live_room_context(runtime, target_room_ref)
    runtime.config.live_enabled = True
    started = await runtime._start_live_listener(target_room_ref)
    await runtime.sync_live_instructions()
    runtime.audit.record(
        "live_connected" if started else "live_connect_failed",
        "danmaku listener started" if started else "failed to start danmaku listener",
        level="info" if started else "warning",
        detail={
            "platform": normalized.get("platform"),
            "room_ref": target_room_ref,
            "room_id": normalized.get("room_id"),
        },
    )
    return runtime.live_connection_snapshot()


async def _refresh_live_room_context(runtime: Any, room_ref: str) -> None:
    try:
        status = await runtime.live_provider.lookup_room_status(room_ref)
    except Exception as exc:
        runtime.audit.record("live_room_context_lookup_failed", str(exc)[:200], level="warning")
        return
    remember = getattr(runtime, "remember_live_room_context", None)
    if callable(remember):
        remember(status, platform=runtime.live_provider.platform, room_ref=room_ref)
        return
    if getattr(status, "ok", False):
        runtime.live_room_context = {
            "room_ref": str(room_ref or ""),
            "room_id": int(getattr(status, "room_id", 0) or 0),
            "title": _public_optional_text(getattr(status, "title", "")),
            "anchor_name": _public_optional_text(getattr(status, "anchor_name", "")),
            "live_status": _public_optional_text(getattr(status, "live_status", "")) or "unknown",
        }


async def disconnect_live_room(runtime: Any) -> dict[str, Any]:
    await runtime._stop_live_listener(mark_disabled=True)
    runtime.audit.record(
        "live_disconnected",
        "live ingest marked disconnected",
        detail={
            "platform": runtime.live_provider.platform,
            "room_ref": runtime.live_provider.configured_room_ref(),
            "room_id": runtime.live_provider.configured_room_id(),
        },
    )
    return runtime.live_connection_snapshot()
