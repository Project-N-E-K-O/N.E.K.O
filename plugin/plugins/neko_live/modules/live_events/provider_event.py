"""Provider-neutral helpers for rich live events."""

from __future__ import annotations

import ipaddress
import math
import re
from typing import Any
from urllib.parse import urlparse, urlunparse


_BILI_TYPE_LABELS = {
    "MSG_DANMAKU": "danmaku",
    "MSG_GIFT": "gift",
    "MSG_SUPER_CHAT": "super_chat",
    "MSG_GUARD_BUY": "guard",
}
_EVENT_TYPE_ALIASES = {
    "chat": "danmaku",
    "danmu": "danmaku",
    "danmaku": "danmaku",
    "gift": "gift",
    "sc": "super_chat",
    "superchat": "super_chat",
    "super_chat": "super_chat",
    "guard": "guard",
}

_ROUTABLE_EVENT_TYPES = {"danmaku", "gift", "super_chat", "guard"}
_SIGNAL_ONLY_EVENT_TYPES = {"gift", "super_chat", "guard"}
_SAFE_PUBLIC_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SAFE_ROOM_REF_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SENSITIVE_ROOM_REF_MARKERS = (
    "cookie",
    "authorization",
    "token",
    "signature",
    "webcast_sign",
    "ttwid",
    "odin_tt",
    "sessionid",
)
_PUBLIC_SIGNAL_TEXT_MAX = 80
_SENSITIVE_SIGNAL_PAIR_RE = re.compile(
    r"\b(cookie|token|signature|webcast_sign|ttwid|odin_tt|sessionid)\b\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)
_SENSITIVE_AUTH_RE = re.compile(r"\bauthorization\b\s*[:=]\s*[^,;]+", re.IGNORECASE)
_PUBLIC_LABEL_TEXT_MAX = 64
_PUBLIC_EVENT_TEXT_MAX = 512
_PUBLIC_PROMPT_TEXT_MAX = 120
_SUPPORT_EVIDENCE = {
    "bilibili_typed_command",
    "douyin_bridge_typed_event",
    "manual_live_simulation",
    "twitch_eventsub_typed_event",
}
_SUPPORT_COIN_TYPES = {"gold", "silver"}


def event_type(event: Any) -> str:
    explicit = _public_event_type_text(_field(event, "event_type") or _field(event, "type"))
    if explicit:
        return _normalize_event_type(explicit)
    raw = _field(event, "raw")
    if isinstance(raw, dict):
        raw_type = _public_event_type_text(raw.get("event_type") or raw.get("type"))
        if raw_type:
            return _normalize_event_type(raw_type)
    msg_type = _field(event, "msg_type")
    name = str(getattr(msg_type, "name", "") or "").strip()
    if name in _BILI_TYPE_LABELS:
        return _BILI_TYPE_LABELS[name]
    text = str(msg_type or "").strip()
    for key, label in _BILI_TYPE_LABELS.items():
        if key in text:
            return label
    return _normalize_event_type(text.lower()) if text else "unknown"


def is_routable(event: Any) -> bool:
    return event_type(event) in _ROUTABLE_EVENT_TYPES


def is_signal_only(event: Any) -> bool:
    return event_type(event) in _SIGNAL_ONLY_EVENT_TYPES


def event_uid(event: Any) -> str:
    text = _public_token_text(_field(event, "uid"), allow_positive_int=True)
    return text if _is_safe_public_token(text) else ""


def event_nickname(event: Any) -> str:
    return public_text(_field(event, "nickname"), max_length=_PUBLIC_LABEL_TEXT_MAX)


def event_text(event: Any) -> str:
    return public_text(_field(event, "text") or _field(event, "danmaku_text") or "", max_length=_PUBLIC_EVENT_TEXT_MAX)


def event_prompt_text(event: Any) -> str:
    return public_text(event_text(event), max_length=_PUBLIC_PROMPT_TEXT_MAX)


def event_avatar_url(event: Any) -> str:
    return safe_public_url(_field(event, "face_url") or _field(event, "avatar_url") or "")


def event_room_id(event: Any) -> int:
    return _optional_non_negative_int(_field(event, "room_id")) or 0


def event_room_ref(event: Any) -> str:
    text = _public_token_text(_field(event, "room_ref"), allow_positive_int=True)
    return text if _is_safe_public_token(text, pattern=_SAFE_ROOM_REF_RE) else ""


def event_guard_level(event: Any) -> int:
    return _optional_non_negative_int(_field(event, "guard_level")) or 0


def event_score(event: Any) -> float:
    scorer = getattr(event, "get_score", None)
    if callable(scorer):
        try:
            return _safe_non_negative_score(scorer())
        except Exception:
            return 0.0
    try:
        return _safe_non_negative_score(_field(event, "score"))
    except Exception:
        return 0.0


def event_session_generation(event: Any) -> int:
    value = getattr(event, "session_generation", 0)
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def event_signal_fields(event: Any) -> dict[str, Any]:
    """Return optional public fields for signal-only live events."""
    gift = _field(event, "gift")
    gift_name = public_text(
        _field(event, "gift_name")
        or _field(event, "giftName")
        or _field(gift, "gift_name")
        or _field(gift, "giftName")
        or "",
        max_length=_PUBLIC_SIGNAL_TEXT_MAX,
    )
    payload: dict[str, Any] = {}
    if gift_name:
        payload["gift_name"] = gift_name
    count = _optional_non_negative_int(_field(event, "gift_count"), _field(event, "num"), _field(gift, "num"))
    if count is not None:
        payload["gift_count"] = count
    value = _optional_non_negative_int(
        _field(event, "gift_value"),
        _field(event, "total_coin"),
        _field(gift, "total_coin"),
        _field(gift, "price"),
    )
    if value is not None:
        payload["gift_value"] = value
    return payload


def event_support_fields(event: Any) -> dict[str, Any]:
    """Return verified provider metadata used only for support-event scheduling."""
    if _field(event, "support_verified") is not True:
        return {}
    evidence = public_text(_field(event, "support_evidence"), max_length=48)
    if evidence not in _SUPPORT_EVIDENCE:
        return {}

    payload: dict[str, Any] = {
        "support_verified": True,
        "support_evidence": evidence,
    }
    for key in ("provider_event_id", "provider_event_type", "combo_id"):
        token = _public_token_text(_field(event, key), allow_positive_int=True)
        if _is_safe_public_token(token):
            payload[key] = token

    timestamp_ms = _optional_non_negative_int(_field(event, "provider_timestamp_ms"))
    if timestamp_ms is not None:
        payload["provider_timestamp_ms"] = timestamp_ms
    combo_count = _optional_non_negative_int(_field(event, "combo_count"))
    if combo_count is not None:
        payload["combo_count"] = combo_count
    combo_end = _field(event, "combo_end")
    if isinstance(combo_end, bool):
        payload["combo_end"] = combo_end
    coin_type = public_text(_field(event, "coin_type"), max_length=16).lower()
    if coin_type in _SUPPORT_COIN_TYPES:
        payload["coin_type"] = coin_type
    return payload


def public_text(value: Any, *, max_length: int = _PUBLIC_PROMPT_TEXT_MAX) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if not text:
        return ""
    text = _SENSITIVE_AUTH_RE.sub("[redacted]", text)
    text = _SENSITIVE_SIGNAL_PAIR_RE.sub("[redacted]", text)
    return text[: max(0, int(max_length))]


def safe_public_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    url = value.strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    if not _is_public_hostname(parsed.hostname):
        return ""
    path = parsed.path or ""
    if _SENSITIVE_AUTH_RE.search(path) or _SENSITIVE_SIGNAL_PAIR_RE.search(path):
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _field(event: Any, name: str) -> Any:
    if isinstance(event, dict):
        value = event.get(name)
        payload = event.get("payload")
    else:
        value = getattr(event, name, None)
        payload = getattr(event, "payload", None)
    if value is not None:
        return value
    if isinstance(payload, dict):
        return payload.get(name)
    return None


def _normalize_event_type(value: str) -> str:
    raw = str(value or "").strip().lower()
    return _EVENT_TYPE_ALIASES.get(raw, raw or "unknown")


def _public_token_text(value: Any, *, allow_positive_int: bool = False) -> str:
    if isinstance(value, bool):
        return ""
    if allow_positive_int and isinstance(value, int):
        return str(value) if value > 0 else ""
    if not isinstance(value, str):
        return ""
    return value.strip()


def _public_event_type_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _is_public_hostname(hostname: str) -> bool:
    host = str(hostname or "").strip().strip("[]").lower()
    if not host or host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _is_safe_public_token(value: str, *, pattern: re.Pattern[str] = _SAFE_PUBLIC_ID_RE) -> bool:
    if not value or "?" in value or "#" in value or "/" in value:
        return False
    lower = value.lower()
    if any(marker in lower for marker in _SENSITIVE_ROOM_REF_MARKERS):
        return False
    return bool(pattern.match(value))


def _safe_non_negative_score(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        score = float(value)
    elif isinstance(value, str):
        try:
            score = float(value.strip())
        except ValueError:
            return 0.0
    else:
        return 0.0
    if not math.isfinite(score) or score < 0:
        return 0.0
    return score


def _optional_non_negative_int(*values: Any) -> int | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            if value >= 0:
                return value
            continue
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text.isdigit():
            continue
        return int(text)
    return None
