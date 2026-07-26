"""Sanitized Douyin live event model helpers."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

from ...core.contracts import LiveEvent
from .public_projection import is_public_hostname, safe_public_text
from .room_ref import parse_douyin_room_ref


ROUTABLE_EVENT_TYPES = {"danmaku", "gift", "super_chat", "guard"}
STATUS_ONLY_EVENT_TYPES = {"member", "follow", "like", "stats"}
_EVENT_TYPE_ALIASES = {
    "chat": "danmaku",
    "danmu": "danmaku",
    "danmaku": "danmaku",
    "gift": "gift",
    "sc": "super_chat",
    "superchat": "super_chat",
    "super_chat": "super_chat",
    "guard": "guard",
    "member": "member",
    "follow": "follow",
    "like": "like",
    "stats": "stats",
}
_TEXT_FIELD_LIMIT = 2048
_INT_FIELDS = {"gift_count", "gift_value"}
_UID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_SENSITIVE_UID_MARKERS = {
    "authorization",
    "cookie",
    "odin_tt",
    "sessionid",
    "sessionid_ss",
    "sid_tt",
    "sign",
    "signature",
    "token",
    "ttwid",
    "uid_tt",
    "webcast_sign",
    "x-tt-token",
}


@dataclass(slots=True)
class DouyinLiveProviderEvent:
    event_type: str = "danmaku"
    uid: str = ""
    nickname: str = ""
    text: str = ""
    avatar_url: str = ""
    room_ref: str = ""
    room_id: int = 0
    score: float = 0.0
    guard_level: int = 0
    gift_name: str = ""
    gift_count: int = 0
    gift_value: int = 0


def is_routable_event_type(event_type: Any) -> bool:
    return normalize_event_type(event_type) in ROUTABLE_EVENT_TYPES


def is_status_only_event_type(event_type: Any) -> bool:
    return normalize_event_type(event_type) in STATUS_ONLY_EVENT_TYPES


def to_provider_event(payload: Any, *, room_ref: str = "") -> DouyinLiveProviderEvent:
    safe = safe_payload(payload)
    default_event_type = "danmaku" if isinstance(payload, dict) else "unknown"
    event_type = normalize_event_type(safe.get("event_type") or safe.get("type") or default_event_type)
    return DouyinLiveProviderEvent(
        event_type=event_type,
        uid=platform_uid(safe.get("uid") or safe.get("user_id") or safe.get("open_id")),
        nickname=str(safe.get("nickname") or safe.get("user_name") or "").strip(),
        text=str(safe.get("text") or safe.get("content") or safe.get("danmaku_text") or "").strip(),
        avatar_url=str(safe.get("avatar_url") or "").strip(),
        room_ref=safe_room_ref(safe.get("room_ref"), fallback=room_ref),
        room_id=safe_int(safe.get("room_id")),
        score=event_score(event_type),
        gift_name=str(safe.get("gift_name") or "").strip(),
        gift_count=safe_int(safe.get("gift_count")),
        gift_value=safe_int(safe.get("gift_value")),
    )


def to_live_event(event: DouyinLiveProviderEvent, *, ts: float | None = None) -> LiveEvent:
    event = _safe_provider_event(event)
    payload = {
        "platform": "douyin",
        "uid": event.uid,
        "nickname": event.nickname,
        "text": event.text,
        "event_label": event.text or event.gift_name,
        "room_ref": event.room_ref,
    }
    if event.avatar_url:
        payload["avatar_url"] = event.avatar_url
    if event.room_id:
        payload["room_id"] = event.room_id
    if event.gift_name:
        payload["gift_name"] = event.gift_name
    if event.gift_count:
        payload["gift_count"] = event.gift_count
    if event.gift_value:
        payload["gift_value"] = event.gift_value
    if event.event_type in {"gift", "super_chat", "guard"}:
        payload.update(
            {
                "support_verified": True,
                "support_evidence": "douyin_bridge_typed_event",
                "provider_event_type": event.event_type,
            }
        )
    return LiveEvent(
        type=event.event_type,
        uid=event.uid,
        payload=payload,
        source="live",
        ts=ts if ts is not None else time.time(),
        raw=asdict(event),
    )


def _safe_provider_event(event: DouyinLiveProviderEvent) -> DouyinLiveProviderEvent:
    event_type = normalize_event_type(getattr(event, "event_type", "unknown"))
    return DouyinLiveProviderEvent(
        event_type=event_type,
        uid=platform_uid(getattr(event, "uid", "")),
        nickname=safe_text(getattr(event, "nickname", "")),
        text=safe_text(getattr(event, "text", "")),
        avatar_url=safe_avatar_url(getattr(event, "avatar_url", "")),
        room_ref=safe_room_ref(getattr(event, "room_ref", "")),
        room_id=safe_int(getattr(event, "room_id", 0)),
        score=event_score(event_type),
        guard_level=safe_int(getattr(event, "guard_level", 0)),
        gift_name=safe_text(getattr(event, "gift_name", "")),
        gift_count=safe_int(getattr(event, "gift_count", 0)),
        gift_value=safe_int(getattr(event, "gift_value", 0)),
    )


def normalize_event_type(value: Any) -> str:
    if value is None:
        raw = ""
    elif not isinstance(value, str):
        return "unknown"
    else:
        raw = value.strip().lower()
    if not raw:
        return "danmaku"
    return _EVENT_TYPE_ALIASES.get(raw, "unknown")


def platform_uid(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        raw = str(value) if value > 0 else ""
    elif isinstance(value, str):
        raw = value.strip()
    else:
        return ""
    if not raw:
        return ""
    stable_id = raw.removeprefix("douyin:")
    if stable_id.lower() in _SENSITIVE_UID_MARKERS or not _UID_RE.match(stable_id):
        return ""
    return f"douyin:{stable_id}"


def safe_uid(value: Any) -> str:
    uid = platform_uid(value)
    return uid.removeprefix("douyin:") if uid else ""


def event_score(event_type: str) -> float:
    if event_type == "gift":
        return 100.0
    if event_type in {"super_chat", "guard"}:
        return 300.0
    return 1.0


def safe_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    raw = dict(payload or {})
    gift = raw.get("gift") if isinstance(raw.get("gift"), dict) else {}
    allowed_text = {
        "nickname",
        "user_name",
        "text",
        "content",
        "danmaku_text",
        "room_ref",
        "target_lanlan",
        "lanlan_name",
        "gift_name",
    }
    safe = {key: safe_text(value) for key, value in raw.items() if key in allowed_text}
    for key in ("event_type", "type"):
        if key in raw:
            safe[key] = safe_event_type(raw.get(key))
    if not safe.get("event_type") and "type" in safe:
        canonical_type = normalize_event_type(safe.get("type"))
        if canonical_type in {"gift", "guard", "super_chat"}:
            safe["event_type"] = canonical_type
    safe.update({key: safe_int(raw.get(key)) for key in _INT_FIELDS if key in raw})
    if "_live_session_generation" in raw:
        generation = safe_int(raw.get("_live_session_generation"))
        if generation:
            safe["_live_session_generation"] = generation
    for key in ("uid", "user_id", "open_id"):
        if key in raw:
            safe[key] = safe_uid(raw.get(key))
    if "avatar_url" in raw:
        safe["avatar_url"] = safe_avatar_url(raw.get("avatar_url"))
    if "room_ref" in safe:
        safe["room_ref"] = safe_room_ref(safe.get("room_ref"))
    if (not safe.get("gift_name")) and ("giftName" in raw or "gift" in raw):
        gift_name = raw.get("giftName")
        if gift_name is None and isinstance(raw.get("gift"), str):
            gift_name = raw.get("gift")
        if gift_name is None:
            gift_name = _first_present(gift, ("giftName", "gift_name", "name"))
        safe["gift_name"] = safe_text(gift_name or "")
    if safe.get("gift_name") and "event_type" not in safe and "type" not in safe:
        safe["event_type"] = "gift"
    if "gift_count" not in safe and any(key in raw for key in ("num", "repeat_count", "combo_count")):
        safe["gift_count"] = _first_positive_int(raw, ("num", "repeat_count", "combo_count"))
    if (not safe.get("gift_count")) and any(key in gift for key in ("num", "repeat_count", "combo_count")):
        safe["gift_count"] = _first_positive_int(gift, ("num", "repeat_count", "combo_count"))
    if "gift_value" not in safe and any(key in raw for key in ("total_coin", "diamond_count")):
        safe["gift_value"] = _first_positive_int(raw, ("total_coin", "diamond_count", "gift_value"))
    if (not safe.get("gift_value")) and any(key in gift for key in ("total_coin", "diamond_count", "price")):
        safe["gift_value"] = _first_positive_int(gift, ("total_coin", "diamond_count", "price"))
    room_id = _room_id_from_payload(raw)
    if room_id is not None:
        safe["room_id"] = room_id
    return safe


def safe_room_ref(value: Any, *, fallback: Any = "") -> str:
    parsed = parse_douyin_room_ref(value)
    if parsed.ok:
        return parsed.room_ref
    fallback_parsed = parse_douyin_room_ref(fallback)
    return fallback_parsed.room_ref if fallback_parsed.ok else ""


def safe_avatar_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    url = safe_text(value)
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    if not is_public_hostname(hostname):
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def safe_text(value: Any) -> str:
    return safe_public_text(value, limit=_TEXT_FIELD_LIMIT)


def safe_event_type(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        return "unknown"
    return safe_public_text(value, limit=64)


def safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value >= 0 else 0
    if not isinstance(value, str):
        return 0
    text = value.strip()
    return int(text) if text.isdigit() else 0


def _first_present(source: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in source:
            return source[key]
    return None


def _first_positive_int(source: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        if key not in source:
            continue
        number = safe_int(source[key])
        if number > 0:
            return number
    return 0


def _room_id_from_payload(source: dict[str, Any]) -> int | None:
    if "room_id" in source:
        return safe_int(source["room_id"])
    if "webcast_room_id" in source:
        return safe_int(source["webcast_room_id"])
    return None
