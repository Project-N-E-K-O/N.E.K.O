"""Twitch Device Code Flow and encrypted credential runtime actions."""

from __future__ import annotations

from typing import Any

from ..adapters.twitch_auth_service import TwitchAuthService
from ..stores.credential_store import CredentialStore


_TWITCH_FIELDS = (
    "access_token",
    "refresh_token",
    "client_id",
    "user_id",
    "login",
    "display_name",
    "scopes",
    "expires_at",
)


def create_credential_store(plugin: Any, audit: Any) -> CredentialStore:
    return CredentialStore(plugin, audit, namespace="twitch", fields=_TWITCH_FIELDS)


def create_auth_service(runtime: Any) -> TwitchAuthService:
    return TwitchAuthService(
        logger=getattr(getattr(runtime, "plugin", None), "logger", None),
        credential_provider=runtime.twitch_credential_store.load,
        credential_saver=runtime.twitch_credential_store.save,
        credential_reloader=runtime.reload_twitch_credential,
    )


async def reload_credential(runtime: Any) -> None:
    try:
        data = await runtime.twitch_credential_store.load()
    except Exception:
        data = None
    runtime.twitch_credential = data if _credential_present(data) else None


async def start_device_authorization(runtime: Any) -> dict[str, Any]:
    result = await runtime.twitch_auth.start_device_authorization(_client_id(runtime))
    runtime.audit.record(
        "twitch_device_authorization_started" if result.get("started") is True else "twitch_device_authorization_failed",
        "twitch device authorization started" if result.get("started") is True else str(result.get("message") or "twitch device authorization failed"),
        level="info" if result.get("started") is True else "warning",
    )
    return result


async def check_device_authorization(runtime: Any) -> dict[str, Any]:
    result = await runtime.twitch_auth.check_device_authorization(_client_id(runtime))
    if result.get("logged_in") is True:
        runtime.audit.record(
            "twitch_authorized",
            "twitch credential saved (encrypted)",
            detail={"user_id": result.get("user_id", ""), "login": result.get("login", "")},
        )
    elif result.get("pending") is not True:
        runtime.audit.record(
            "twitch_authorization_failed",
            str(result.get("message") or "twitch authorization failed"),
            level="warning",
        )
    return result


async def cancel_device_authorization(runtime: Any) -> dict[str, Any]:
    result = await runtime.twitch_auth.cancel_device_authorization(_client_id(runtime))
    cancelled = result.get("cancelled") is True
    completed = result.get("logged_in") is True
    runtime.audit.record(
        (
            "twitch_device_authorization_cancelled"
            if cancelled
            else "twitch_device_authorization_completed_before_cancel"
            if completed
            else "twitch_device_authorization_cancel_skipped"
        ),
        (
            "twitch device authorization cancelled"
            if cancelled
            else "twitch device authorization completed before cancellation"
            if completed
            else "twitch device authorization was not active"
        ),
        detail={"active_session": cancelled},
    )
    return result


async def credential_status(runtime: Any) -> dict[str, Any]:
    auth = getattr(runtime, "twitch_auth", None)
    pending = auth.device_authorization_status(_client_id(runtime)) if auth is not None else None
    if pending is not None:
        return pending
    if runtime.twitch_credential is None and runtime.twitch_credential_store.has_credential():
        await reload_credential(runtime)
    data = runtime.twitch_credential
    if not _credential_present(data):
        return {
            "platform": "twitch",
            "logged_in": False,
            "pending": False,
            "authorization_state": "unauthorized",
            "login": "",
            "user_id": "",
            "scopes": [],
        }
    return {
        "platform": "twitch",
        "logged_in": False,
        "authorization_state": "unverified",
        "login": "",
        "user_id": "",
        "scopes": [],
    }


async def validate_credential(runtime: Any) -> dict[str, Any]:
    result = await runtime.twitch_auth.check_credential(_client_id(runtime))
    runtime.audit.record(
        "twitch_credential_validated" if result.get("logged_in") is True else "twitch_credential_validation_failed",
        "twitch credential validated" if result.get("logged_in") is True else str(result.get("message") or "twitch credential invalid"),
        level="info" if result.get("logged_in") is True else "warning",
        detail={"refreshed": result.get("refreshed") is True},
    )
    return result


async def logout(runtime: Any) -> dict[str, Any]:
    auth = getattr(runtime, "twitch_auth", None)
    if auth is not None:
        await auth.cancel_device_authorization(_client_id(runtime))
    removed = await runtime.twitch_credential_store.delete()
    runtime.twitch_credential = None
    runtime.audit.record("twitch_logout", "twitch credential removed", detail={"files": removed})
    return {
        "platform": "twitch",
        "logged_out": True,
        "logged_in": False,
        "pending": False,
        "authorization_state": "unauthorized",
        "removed": removed,
    }


def _client_id(runtime: Any) -> Any:
    return getattr(getattr(runtime, "config", None), "twitch_client_id", "")


def _credential_present(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("access_token"), str)
        and bool(data["access_token"].strip())
        and isinstance(data.get("refresh_token"), str)
        and bool(data["refresh_token"].strip())
    )


def _public_text(value: Any, limit: int) -> str:
    return " ".join(value.split()).strip()[:limit] if isinstance(value, str) else ""
