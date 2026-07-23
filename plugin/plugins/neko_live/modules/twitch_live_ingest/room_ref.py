"""Parse public Twitch channel references without retaining URL metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


_LOGIN_RE = re.compile(r"^[a-z0-9_]{1,25}$")
_SUPPORTED_HOSTS = {"twitch.tv", "www.twitch.tv"}


@dataclass(frozen=True, slots=True)
class TwitchRoomRef:
    ok: bool
    room_ref: str = ""
    source: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        parsed = parse_twitch_room_ref(self.room_ref)
        return {
            "ok": self.ok is True and parsed.ok,
            "room_ref": parsed.room_ref if parsed.ok else "",
            "source": self.source if self.source in {"login", "url"} else "",
            "message": self.message if isinstance(self.message, str) else "",
        }


def parse_twitch_room_ref(value: Any) -> TwitchRoomRef:
    if not isinstance(value, str):
        return TwitchRoomRef(False, message="channel login must be configured")
    text = value.strip()
    if not text:
        return TwitchRoomRef(False, message="channel login must be configured")
    if "://" in text or text.lower().startswith(("twitch.tv/", "www.twitch.tv/")):
        return _from_url(text)
    return _from_login(text, source="login")


def _from_url(text: str) -> TwitchRoomRef:
    candidate = text if "://" in text else f"https://{text}"
    parsed = urlparse(candidate)
    host = str(parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    if host not in _SUPPORTED_HOSTS or parsed.query or parsed.fragment:
        return TwitchRoomRef(False, message="unsupported twitch channel url")
    if len(parts) != 1:
        return TwitchRoomRef(False, message="twitch channel url must contain one channel login")
    return _from_login(parts[0], source="url")


def _from_login(value: str, *, source: str) -> TwitchRoomRef:
    login = value.strip().lower()
    if not _LOGIN_RE.fullmatch(login):
        return TwitchRoomRef(False, message="invalid twitch channel login")
    return TwitchRoomRef(True, room_ref=login, source=source)
