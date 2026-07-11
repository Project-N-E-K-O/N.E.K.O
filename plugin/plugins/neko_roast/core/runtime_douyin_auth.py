"""Douyin manual-cookie credential actions for the runtime."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ..modules.douyin_live_ingest.public_projection import safe_public_text, safe_room_ref
from ..modules.douyin_live_ingest.room_ref import parse_douyin_room_ref
from ..modules.douyin_live_ingest.webcast import fetch_webcast_info
from ..stores.credential_store import CredentialStore
from .contracts import utc_now_iso


_DOUYIN_FIELDS = ("cookie", "uid", "nickname", "saved_at")
_MAX_COOKIE_LENGTH = 32768
_HEADER_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{1,63}\s*:")
_COOKIE_PAIR_RE = re.compile(r"(?:^|[;\s])[\w.-]{2,64}=")
_COOKIE_PART_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}=[^;\s]+$")
_UID_RE = re.compile(r"^(?:douyin:)?[A-Za-z0-9_.-]{1,128}$")
_SENSITIVE_TEXT_MARKERS = (
    "cookie=",
    "cookie:",
    "authorization:",
    "bearer ",
    "token=",
    "signature=",
    "sign=",
    "webcast_sign=",
    "ttwid=",
    "odin_tt=",
    "passport_csrf_token=",
    "sid_guard=",
    "sessionid=",
    "uid_tt=",
    "sessdata=",
    "bili_jct=",
    "dedeuserid=",
    "buvid3=",
)


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


def normalize_cookie(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("cookie must be text")
    text = value.strip()
    if not text:
        raise ValueError("cookie must not be empty")
    if len(text) > _MAX_COOKIE_LENGTH:
        raise ValueError("cookie is too large")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    clean_lines: list[str] = []
    for index, line in enumerate(lines):
        if index == 0 and line.lower().startswith("cookie:"):
            line = line.split(":", 1)[1].strip()
        elif _HEADER_LINE_RE.match(line):
            raise ValueError("cookie contains unsupported header lines")
        if line:
            clean_lines.append(line)
    text = " ".join(clean_lines).strip()
    if not text:
        raise ValueError("cookie must not be empty")
    parts = [part.strip() for part in text.split(";") if part.strip()]
    if not parts or any(_COOKIE_PART_RE.fullmatch(part) is None for part in parts):
        raise ValueError("cookie must contain name=value pairs")
    return "; ".join(parts)


def _safe_text(value: Any, *, limit: int = 80) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split()).strip()
    if _looks_like_credential_text(text):
        return ""
    if len(text) > limit:
        return text[:limit]
    return text


def _safe_uid(value: Any) -> str:
    text = _safe_text(value, limit=128)
    if not text:
        return ""
    return text if _UID_RE.match(text) else ""


def _looks_like_credential_text(text: str) -> bool:
    lower = text.lower()
    if not lower:
        return False
    if any(marker in lower for marker in _SENSITIVE_TEXT_MARKERS):
        return True
    return ";" in lower and bool(_COOKIE_PAIR_RE.search(lower))


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
        "uid": _safe_uid(data.get("uid")),
        "nickname": _safe_text(data.get("nickname")),
        "saved_at": _safe_text(data.get("saved_at")),
    }


async def reload_credential(runtime: Any) -> None:
    try:
        data = await runtime.douyin_credential_store.load()
        cookie = data.get("cookie") if isinstance(data, dict) else None
        runtime.douyin_credential = data if isinstance(cookie, str) and cookie.strip() else None
    except Exception:
        runtime.douyin_credential = None


async def import_cookie(runtime: Any, cookie: Any, uid: Any = "", nickname: Any = "") -> dict[str, Any]:
    try:
        normalized_cookie = normalize_cookie(cookie)
    except ValueError as exc:
        message = _safe_import_error(exc)
        runtime.douyin_credential = None
        runtime.audit.record("douyin_cookie_import_failed", message, level="warning")
        return {
            "platform": "douyin",
            "saved": False,
            "logged_in": False,
            "has_cookie": False,
            "message": message,
        }
    payload = {
        "cookie": normalized_cookie,
        "uid": _safe_uid(uid),
        "nickname": _safe_text(nickname),
        "saved_at": utc_now_iso(),
    }
    ok = await runtime.douyin_credential_store.save(payload)
    if not ok:
        runtime.douyin_credential = None
        runtime.audit.record("douyin_cookie_import_failed", "douyin cookie save failed", level="warning")
        return {"platform": "douyin", "saved": False, "logged_in": False, "has_cookie": False}
    await reload_credential(runtime)
    runtime.audit.record(
        "douyin_cookie_imported",
        "douyin cookie saved (encrypted)",
        detail={"uid": payload["uid"], "has_nickname": bool(payload["nickname"])},
    )
    status = _public_status(runtime.douyin_credential)
    status["saved"] = True
    return status


def _safe_import_error(exc: ValueError) -> str:
    message = str(exc).strip()
    allowed = {
        "cookie must be text",
        "cookie must not be empty",
        "cookie is too large",
        "cookie contains unsupported header lines",
        "cookie must contain name=value pairs",
    }
    return message if message in allowed else "invalid douyin cookie"


async def credential_status(runtime: Any) -> dict[str, Any]:
    if runtime.douyin_credential is None and runtime.douyin_credential_store.has_credential():
        await reload_credential(runtime)
    return _public_status(runtime.douyin_credential)


async def validate_cookie(runtime: Any, room_ref: Any = "") -> dict[str, Any]:
    if runtime.douyin_credential is None and runtime.douyin_credential_store.has_credential():
        await reload_credential(runtime)
    status = _public_status(runtime.douyin_credential)
    cookie = _credential_cookie(runtime.douyin_credential)
    target = _validation_room_ref(room_ref, getattr(runtime.config, "live_room_ref", ""))
    parsed = parse_douyin_room_ref(target)
    if not cookie:
        result = {
            **status,
            "checked": True,
            "valid": False,
            "room_ref": "",
            "live_status": "unknown",
            "message": "douyin cookie is required before validation",
        }
        runtime.audit.record("douyin_cookie_validate_failed", result["message"], level="warning")
        return result
    if not parsed.ok:
        result = {
            **status,
            "checked": True,
            "valid": False,
            "room_ref": "",
            "live_status": "unknown",
            "message": safe_public_text(parsed.message, limit=160),
        }
        runtime.audit.record("douyin_cookie_validate_failed", result["message"], level="warning")
        return result
    try:
        info = await asyncio.to_thread(fetch_webcast_info, parsed.room_ref, cookie=cookie)
    except Exception as exc:
        message = f"douyin cookie validation failed: {type(exc).__name__}"
        runtime.audit.record("douyin_cookie_validate_failed", message, level="warning")
        return {
            **status,
            "checked": True,
            "valid": False,
            "room_ref": safe_room_ref(parsed.room_ref),
            "live_status": "unknown",
            "message": message,
        }
    valid = bool(info.ok)
    result = {
        **status,
        "checked": True,
        "valid": valid,
        "room_ref": info.room_ref or safe_room_ref(parsed.room_ref),
        "live_status": info.live_status,
        "message": info.message or ("douyin cookie validated" if valid else "douyin room metadata unavailable"),
    }
    runtime.audit.record(
        "douyin_cookie_validated" if valid else "douyin_cookie_validate_failed",
        "douyin cookie validation completed" if valid else result["message"],
        level="info" if valid else "warning",
        detail={"room_ref": result["room_ref"], "valid": valid, "live_status": result["live_status"]},
    )
    return result


def _credential_cookie(data: dict[str, Any] | None) -> str:
    cookie = data.get("cookie") if isinstance(data, dict) else None
    return cookie.strip() if isinstance(cookie, str) else ""


def _validation_room_ref(room_ref: Any, fallback: Any) -> Any:
    if isinstance(room_ref, bool):
        return fallback
    if isinstance(room_ref, int):
        return room_ref if room_ref > 0 else fallback
    if isinstance(room_ref, str):
        text = room_ref.strip()
        return text if text else fallback
    return fallback


async def delete_cookie(runtime: Any) -> dict[str, Any]:
    removed = await runtime.douyin_credential_store.delete()
    runtime.douyin_credential = None
    runtime.audit.record("douyin_cookie_deleted", "douyin cookie removed", detail={"files": removed})
    return {
        "platform": "douyin",
        "logged_out": True,
        "removed": removed,
        "logged_in": False,
        "has_cookie": False,
    }
