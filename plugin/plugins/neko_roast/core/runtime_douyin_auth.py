"""Minimal Douyin credential boundary for early runtime slices.

The Douyin bridge slice replaces this shim with cookie validation and room
lookup support. This file keeps RoastRuntime importable before that slice.
"""

from __future__ import annotations

from typing import Any

from ..stores.credential_store import CredentialStore
from .contracts import utc_now_iso


_DOUYIN_FIELDS = ("cookie", "uid", "nickname", "saved_at")


class _EarlyDouyinCredentialStore:
    def has_credential(self) -> bool:
        return False

    async def load(self) -> dict[str, Any] | None:
        return None

    async def save(self, payload: dict[str, Any]) -> bool:
        _ = payload
        return False

    async def delete(self) -> list[str]:
        return []


def create_credential_store(plugin: Any, audit: Any) -> CredentialStore:
    try:
        return CredentialStore(plugin, audit, namespace="douyin", fields=_DOUYIN_FIELDS)
    except TypeError:
        audit.record(
            "douyin_credential_store_unavailable",
            "namespaced credential store is provided by the Douyin bridge slice",
            level="warning",
        )
        return _EarlyDouyinCredentialStore()


async def reload_credential(runtime: Any) -> None:
    try:
        data = await runtime.douyin_credential_store.load()
        cookie = data.get("cookie") if isinstance(data, dict) else None
        runtime.douyin_credential = data if isinstance(cookie, str) and cookie.strip() else None
    except Exception:
        runtime.douyin_credential = None


async def import_cookie(
    runtime: Any,
    cookie: Any,
    uid: Any = "",
    nickname: Any = "",
) -> dict[str, Any]:
    if not isinstance(cookie, str) or not cookie.strip():
        runtime.douyin_credential = None
        return {
            "platform": "douyin",
            "saved": False,
            "logged_in": False,
            "has_cookie": False,
            "message": "cookie must not be empty",
        }
    payload = {
        "cookie": cookie.strip(),
        "uid": _safe_text(uid, limit=128),
        "nickname": _safe_text(nickname, limit=80),
        "saved_at": utc_now_iso(),
    }
    ok = await runtime.douyin_credential_store.save(payload)
    if not ok:
        runtime.douyin_credential = None
        return {"platform": "douyin", "saved": False, "logged_in": False, "has_cookie": False}
    await reload_credential(runtime)
    status = _public_status(runtime.douyin_credential)
    status["saved"] = True
    return status


async def credential_status(runtime: Any) -> dict[str, Any]:
    if runtime.douyin_credential is None and runtime.douyin_credential_store.has_credential():
        await reload_credential(runtime)
    return _public_status(runtime.douyin_credential)


async def validate_cookie(runtime: Any, room_ref: Any = "") -> dict[str, Any]:
    _ = room_ref
    status = await credential_status(runtime)
    return {
        **status,
        "checked": True,
        "valid": False,
        "room_ref": "",
        "live_status": "unknown",
        "message": "douyin validation is provided by the Douyin bridge slice",
    }


async def delete_cookie(runtime: Any) -> dict[str, Any]:
    ok = await runtime.douyin_credential_store.delete()
    runtime.douyin_credential = None
    return {
        "platform": "douyin",
        "deleted": bool(ok),
        "logged_in": False,
        "has_cookie": False,
        "uid": "",
        "nickname": "",
        "saved_at": "",
    }


def _public_status(data: dict[str, Any] | None) -> dict[str, Any]:
    cookie = data.get("cookie") if isinstance(data, dict) else None
    if not isinstance(cookie, str) or not cookie.strip():
        return {
            "platform": "douyin",
            "logged_in": False,
            "has_cookie": False,
            "uid": "",
            "nickname": "",
            "saved_at": "",
        }
    return {
        "platform": "douyin",
        "logged_in": True,
        "has_cookie": True,
        "uid": _safe_text(data.get("uid"), limit=128),
        "nickname": _safe_text(data.get("nickname"), limit=80),
        "saved_at": _safe_text(data.get("saved_at"), limit=80),
    }


def _safe_text(value: Any, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split()).strip()
    return text[:limit]
