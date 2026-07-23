"""Project TwitchIO events into NEKO Live's credential-free envelope."""

from __future__ import annotations

import time
from typing import Any

from ...core.contracts import LiveEvent
from .room_ref import parse_twitch_room_ref


_SUPPORTED_NOTIFICATION_TYPES = {"sub", "resub", "sub_gift", "community_sub_gift"}


def project_chat_message(message: Any, *, room_ref: Any, ts: float | None = None) -> LiveEvent | None:
    parsed = parse_twitch_room_ref(room_ref)
    chatter = getattr(message, "chatter", None)
    chatter_id = _numeric_id(getattr(chatter, "id", None))
    login = _login(getattr(chatter, "name", None))
    nickname = _text(getattr(chatter, "display_name", None), 80) or login
    text = _text(getattr(message, "text", None), 500)
    if not parsed.ok or not chatter_id or not login or not text:
        return None
    uid = f"twitch:{chatter_id}"
    message_id = _text(getattr(message, "id", None), 80)
    bits = _positive_int(getattr(getattr(message, "cheer", None), "bits", None))
    if bits and not message_id:
        return None
    payload = {
        "event_type": "danmaku",
        "uid": uid,
        "nickname": nickname,
        "chatter_login": login,
        "danmaku_text": text,
        "text": text,
        "message_id": message_id,
        "room_ref": parsed.room_ref,
    }
    event_type = "gift" if bits else "danmaku"
    payload["event_type"] = event_type
    if bits:
        payload.update(
            {
                "gift_name": "Twitch Bits",
                "gift_count": 1,
                "gift_value": bits,
                "coin_type": "gold",
                "support_verified": True,
                "support_evidence": "twitch_eventsub_typed_event",
                "provider_event_id": payload["message_id"],
                "provider_event_type": "TWITCH_CHEER",
            }
        )
    return LiveEvent(
        type=event_type,
        uid=uid,
        payload=payload,
        source="live",
        ts=float(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) else time.time(),
        raw=None,
    )


def project_chat_notification(notification: Any, *, room_ref: Any, ts: float | None = None) -> LiveEvent | None:
    parsed = parse_twitch_room_ref(room_ref)
    notice_type = _text(getattr(notification, "notice_type", None), 48).lower()
    detail = getattr(notification, notice_type, None)
    if not parsed.ok or notice_type not in _SUPPORTED_NOTIFICATION_TYPES or detail is None:
        return None
    if notice_type == "sub_gift" and _text(getattr(detail, "community_gift_id", None), 80):
        return None

    event_id = _text(getattr(notification, "id", None), 80)
    if not event_id:
        return None
    anonymous = getattr(notification, "anonymous", None) is True
    chatter = getattr(notification, "chatter", None)
    if anonymous:
        uid = "twitch:anonymous"
        login = ""
        nickname = "Anonymous"
    else:
        chatter_id = _numeric_id(getattr(chatter, "id", None))
        login = _login(getattr(chatter, "name", None))
        nickname = _text(getattr(chatter, "display_name", None), 80) or login
        if not chatter_id or not login:
            return None
        uid = f"twitch:{chatter_id}"

    tier = _subscription_tier(getattr(detail, "tier", None))
    if not tier:
        return None
    count = _positive_int(getattr(detail, "total", None)) if notice_type == "community_sub_gift" else 1
    if not count:
        return None
    labels = {
        "sub": "subscription",
        "resub": "resubscription",
        "sub_gift": "gift subscription",
        "community_sub_gift": "gift subscriptions",
    }
    provider_types = {
        "sub": "TWITCH_SUB",
        "resub": "TWITCH_RESUB",
        "sub_gift": "TWITCH_SUB_GIFT",
        "community_sub_gift": "TWITCH_COMMUNITY_SUB_GIFT",
    }
    text = _text(getattr(notification, "text", None), 500) or _text(
        getattr(notification, "system_message", None),
        500,
    )
    payload = {
        "event_type": "gift",
        "uid": uid,
        "nickname": nickname,
        "chatter_login": login,
        "danmaku_text": text,
        "text": text,
        "message_id": event_id,
        "room_ref": parsed.room_ref,
        "gift_name": f"Twitch Tier {tier} {labels[notice_type]}",
        "gift_count": count,
        "coin_type": "gold",
        "support_verified": True,
        "support_evidence": "twitch_eventsub_typed_event",
        "provider_event_id": event_id,
        "provider_event_type": provider_types[notice_type],
    }
    return LiveEvent(
        type="gift",
        uid=uid,
        payload=payload,
        source="live",
        ts=float(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) else time.time(),
        raw=None,
    )


def chat_notification_skip_reason(notification: Any, *, room_ref: Any) -> str:
    """Classify a rejected notification without retaining provider payload data."""
    if not parse_twitch_room_ref(room_ref).ok:
        return "ingest.invalid_twitch_projection"
    notice_type = _text(getattr(notification, "notice_type", None), 48).lower()
    detail = getattr(notification, notice_type, None)
    if notice_type not in _SUPPORTED_NOTIFICATION_TYPES:
        return "ingest.ignored_twitch_notification"
    if notice_type == "sub_gift" and detail is not None:
        if _text(getattr(detail, "community_gift_id", None), 80):
            return "ingest.ignored_twitch_notification"
    return "ingest.invalid_twitch_projection"


def _numeric_id(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    return text if text.isascii() and text.isdigit() and len(text) <= 32 else ""


def _login(value: Any) -> str:
    text = value.strip().lower() if isinstance(value, str) else ""
    return text if 0 < len(text) <= 25 and text.isascii() and text.replace("_", "").isalnum() else ""


def _text(value: Any, limit: int) -> str:
    return " ".join(value.split()).strip()[:limit] if isinstance(value, str) else ""


def _positive_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 10_000_000 else 0


def _subscription_tier(value: Any) -> str:
    return {"1000": "1", "2000": "2", "3000": "3"}.get(_text(value, 4), "")
