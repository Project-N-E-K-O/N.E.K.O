"""Shared safe public projection helpers for Douyin live ingest."""

from __future__ import annotations

import ipaddress
import math
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

from .room_ref import parse_douyin_room_ref

SENSITIVE_AUTH_RE = re.compile(r"(?i)\bauthorization\b\s*[:=]\s*[^,;]+")
SENSITIVE_TEXT_RE = re.compile(
    r"(?i)\b(?:"
    r"cookie|authorization|x-tt-token|ttwid|odin_tt|sessionid|sessionid_ss|sid_tt|uid_tt|"
    r"webcast_sign|signature|sign|token"
    r")\b\s*[:=]\s*[^;&\s]+"
)


def safe_public_bool(value: Any) -> bool:
    return value is True


def safe_room_ref(value: Any) -> str:
    parsed = parse_douyin_room_ref(value)
    return parsed.room_ref if parsed.ok else ""


def safe_webcast_room_id(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value) if value > 0 else ""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return text if text.isdigit() else ""


def safe_public_text(value: Any, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split()).strip()
    text = SENSITIVE_AUTH_RE.sub("[redacted]", text)
    text = SENSITIVE_TEXT_RE.sub("[redacted]", text)
    return text[:limit] if len(text) > limit else text


def safe_public_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value >= 0 else 0
    if not isinstance(value, str):
        return 0
    text = value.strip()
    if not text.isdigit():
        return 0
    return int(text)


def safe_public_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else 0.0
    if not isinstance(value, str):
        return 0.0
    try:
        number = float(value.strip())
    except ValueError:
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0


def is_public_hostname(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    hostname = value.strip().lower()
    if not hostname:
        return False
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return "." in hostname
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def safe_ws_endpoint(value: Any, *, default: str) -> str:
    if not isinstance(value, str):
        return default
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"ws", "wss"} or not parsed.netloc or not parsed.hostname:
        return default
    if parsed.username or parsed.password:
        return default
    if not is_public_hostname(parsed.hostname):
        return default
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def safe_listed_tokens(value: Any, *, allowed: tuple[str, ...]) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    observed = {raw.strip() for raw in value if isinstance(raw, str)}
    return [item for item in allowed if item in observed]
