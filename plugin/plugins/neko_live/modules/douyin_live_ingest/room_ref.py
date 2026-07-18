"""Douyin live-room reference parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


_ROOM_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{2,128}$")
_SUPPORTED_HOSTS = {"live.douyin.com"}
_PUBLIC_SOURCES = {"url", "token"}
_SENSITIVE_AUTH_RE = re.compile(r"(?i)\bauthorization\b\s*[:=]\s*[^,;]+")
_SENSITIVE_TEXT_RE = re.compile(
    r"(?i)\b(?:cookie|authorization|x-tt-token|ttwid|odin_tt|sessionid|webcast_sign|signature|sign|token)\b"
    r"\s*[:=]\s*[^;&\s]+"
)


@dataclass(frozen=True, slots=True)
class DouyinRoomRef:
    ok: bool
    room_ref: str = ""
    source: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        parsed = parse_douyin_room_ref(self.room_ref)
        source = self.source.strip().lower() if isinstance(self.source, str) else ""
        return {
            "ok": self.ok is True and parsed.ok,
            "room_ref": parsed.room_ref if parsed.ok else "",
            "source": source if source in _PUBLIC_SOURCES else "",
            "message": _safe_message(self.message),
        }


def parse_douyin_room_ref(value: Any) -> DouyinRoomRef:
    if isinstance(value, bool):
        return DouyinRoomRef(False, message="room_ref must be configured")
    if isinstance(value, int):
        return _from_token(str(value)) if value > 0 else DouyinRoomRef(False, message="room_ref must be configured")
    if not isinstance(value, str):
        return DouyinRoomRef(False, message="room_ref must be configured")
    text = value.strip()
    if not text:
        return DouyinRoomRef(False, message="room_ref must be configured")
    if "://" in text or text.lower().startswith("live.douyin.com/"):
        return _from_url(text)
    return _from_token(text)


def _from_url(text: str) -> DouyinRoomRef:
    candidate = text if "://" in text else f"https://{text}"
    parsed = urlparse(candidate)
    host = str(parsed.netloc or "").lower()
    if host not in _SUPPORTED_HOSTS:
        return DouyinRoomRef(False, message="unsupported douyin room url")
    parts = [part for part in parsed.path.split("/") if part]
    token = parts[0] if parts else ""
    parsed_token = _from_token(token)
    if not parsed_token.ok:
        return DouyinRoomRef(False, message="douyin room url is missing a room id")
    return DouyinRoomRef(True, room_ref=parsed_token.room_ref, source="url")


def _from_token(text: str) -> DouyinRoomRef:
    token = str(text or "").strip()
    if not token:
        return DouyinRoomRef(False, message="room_ref must be configured")
    if not _ROOM_TOKEN_RE.match(token):
        return DouyinRoomRef(False, message="invalid douyin room_ref")
    return DouyinRoomRef(True, room_ref=token, source="token")


def _safe_message(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split()).strip()
    text = _SENSITIVE_AUTH_RE.sub("[redacted]", text)
    text = _SENSITIVE_TEXT_RE.sub("[redacted]", text)
    return text[:160] if len(text) > 160 else text
