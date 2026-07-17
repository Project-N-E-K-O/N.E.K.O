"""Control-panel and live-room actions for the runtime."""

from __future__ import annotations

from typing import Any

from .contracts import RoastConfig
from .runtime_live_listener import refresh_live_room_context


def pause(runtime: Any) -> None:
    runtime.safety_guard.pause("manual pause from control panel")


def resume(runtime: Any) -> None:
    runtime.safety_guard.resume()


def clear_queue(runtime: Any) -> None:
    runtime.safety_guard.clear_queue()
    runtime.audit.record("queue_clear", "queue cleared")


async def clear_viewer_profiles(runtime: Any) -> dict[str, Any]:
    result = await runtime.viewer_store.clear_profiles()
    if not result.get("applied"):
        runtime.audit.record(
            "viewer_profiles_clear_failed",
            "viewer profiles could not be cleared",
            level="warning",
            detail=result,
        )
        raise OSError("viewer profile store could not be cleared")
    runtime.pipeline.clear_dry_run_session_state()
    runtime.audit.record("viewer_profiles_clear", "viewer profiles cleared", detail=result)
    return result


async def delete_viewer_profile(runtime: Any, uid: str) -> dict[str, Any]:
    result = await runtime.viewer_store.delete_profile(uid)
    if not result.get("applied"):
        raise OSError("viewer profile store could not be updated")
    if not result.get("deleted"):
        raise ValueError("viewer profile was not found")
    clear_uid = getattr(getattr(runtime.pipeline, "session", None), "clear_uid", None)
    if callable(clear_uid):
        clear_uid(str(result.get("uid") or ""))
    runtime.audit.record("viewer_profile_delete", "viewer profile deleted", detail=result)
    return result


async def reset_viewer_impression(runtime: Any, uid: str) -> dict[str, Any]:
    result = await runtime.viewer_store.reset_profile_impression(uid)
    if not result.get("found"):
        raise ValueError("viewer profile was not found")
    if not result.get("applied"):
        raise OSError("viewer profile store could not be updated")
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
        "auth_mode": _public_auth_mode(
            getattr(runtime, "live_connection_auth_mode", "unknown")
        ),
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
        "authenticating",
        "connected",
        "receiving",
        "reconnecting",
        "auth_required",
        "unsupported",
        "unknown",
    }
    fallback_text = fallback.strip().lower() if isinstance(fallback, str) else ""
    if fallback_text == "auth_required":
        return fallback_text
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


def _public_auth_mode(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"authenticated", "limited_accountless", "provider_managed"}:
            return text
    return "unknown"


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
        runtime.live_connection_auth_mode = "unknown"
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


async def connect_live_room(
    runtime: Any,
    room_id: Any = 0,
    *,
    allow_accountless: bool = False,
) -> dict[str, Any]:
    if type(allow_accountless) is not bool:
        raise TypeError("allow_accountless must be a boolean")
    normalized = runtime.live_provider.normalize_room_ref(room_id)
    if not normalized.get("ok"):
        configured = runtime.live_provider.configured_room_ref()
        if configured:
            normalized = runtime.live_provider.normalize_room_ref(configured)
    if not normalized.get("ok"):
        raise ValueError(str(normalized.get("message") or "room_ref must be configured before connecting"))
    target_room_ref = str(normalized.get("room_ref") or "")
    auth_mode = await _resolve_connection_auth_mode(
        runtime,
        platform=str(normalized.get("platform") or runtime.live_provider.platform),
        allow_accountless=allow_accountless,
    )
    previous_auth_mode = getattr(runtime, "live_connection_auth_mode", "unknown")
    runtime.live_connection_auth_mode = auth_mode
    if target_room_ref != runtime.live_provider.configured_room_ref():
        try:
            await runtime.set_live_room(target_room_ref)
        except Exception:
            runtime.live_connection_auth_mode = previous_auth_mode
            raise
        runtime.live_connection_auth_mode = auth_mode
    if runtime.live_provider.is_listening() and target_room_ref == runtime.live_provider.configured_room_ref():
        return runtime.live_connection_snapshot()
    await refresh_live_room_context(runtime, target_room_ref)
    runtime.config.live_enabled = True
    started = await runtime._start_live_listener(target_room_ref)
    if not started:
        runtime.live_connection_auth_mode = "unknown"
    await runtime.sync_live_instructions()
    runtime.audit.record(
        "live_connected" if started else "live_connect_failed",
        "danmaku listener started" if started else "failed to start danmaku listener",
        level="info" if started else "warning",
        detail={
            "platform": normalized.get("platform"),
            "room_ref": target_room_ref,
            "room_id": normalized.get("room_id"),
            "auth_mode": auth_mode,
        },
    )
    return runtime.live_connection_snapshot()


async def _resolve_connection_auth_mode(
    runtime: Any,
    *,
    platform: str,
    allow_accountless: bool,
) -> str:
    if platform != "bilibili":
        if allow_accountless:
            raise ValueError("accountless fallback is only supported for Bilibili")
        return "provider_managed"

    try:
        candidate = await runtime.bili_login_status()
        status = candidate if isinstance(candidate, dict) else {}
    except Exception as exc:
        status = {
            "logged_in": False,
            "message": f"account status could not be verified: {type(exc).__name__}",
        }
    if status.get("logged_in") is True:
        return "authenticated"
    if allow_accountless:
        runtime.audit.record(
            "live_accountless_fallback_enabled",
            "limited accountless Bilibili connection enabled for this session",
            level="warning",
            detail={"platform": "bilibili", "scope": "current_connection"},
        )
        return "limited_accountless"

    stop_error = ""
    if runtime.live_provider.is_listening():
        try:
            await runtime._stop_live_listener(mark_disabled=True)
        except Exception as exc:
            stop_error = type(exc).__name__
            runtime._accepting_live_events = False
    runtime.config.live_enabled = False
    runtime.live_connection_state = "auth_required"
    runtime.live_connection_auth_mode = "unknown"
    runtime.safety_guard.set_connected(False)
    runtime.audit.record(
        "live_connection_auth_required",
        "Bilibili login required before connecting",
        level="warning",
        detail={
            "platform": "bilibili",
            "accountless_fallback": "not_confirmed",
            "listener_stop_error": stop_error,
        },
    )
    raise ValueError(
        "Bilibili login is required; sign in or explicitly confirm the limited "
        "accountless fallback for this connection"
    )


async def disconnect_live_room(runtime: Any) -> dict[str, Any]:
    try:
        await runtime._stop_live_listener(mark_disabled=True)
    finally:
        runtime.live_connection_auth_mode = "unknown"
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
