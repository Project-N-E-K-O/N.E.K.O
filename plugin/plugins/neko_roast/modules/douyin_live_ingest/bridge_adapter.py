"""Adapters from external Douyin bridge JSON to provider payloads."""

from __future__ import annotations

from typing import Any

from ..live_bridge.transport import local_bridge_url
from .event_model import safe_payload
from .room_ref import parse_douyin_room_ref


_EVENT_ALIASES = {
    "1": "danmaku",
    "chat": "danmaku",
    "danmu": "danmaku",
    "danmaku": "danmaku",
    "message": "danmaku",
    "webcastchatmessage": "danmaku",
    "2": "like",
    "like": "like",
    "digg": "like",
    "webcastlikemessage": "like",
    "3": "member",
    "member": "member",
    "enter": "member",
    "enterroom": "member",
    "webcastmembermessage": "member",
    "4": "follow",
    "follow": "follow",
    "social": "follow",
    "subscribe": "follow",
    "webcastsocialmessage": "follow",
    "5": "gift",
    "gift": "gift",
    "webcastgiftmessage": "gift",
    "webcastlightgiftmessage": "gift",
    "webcastlinkercontributemessage": "gift",
    "webcastprofitinteractionscoremessage": "gift",
    "guard": "guard",
    "webcastguardmessage": "guard",
    "sc": "super_chat",
    "superchat": "super_chat",
    "webcastsuperchatmessage": "super_chat",
    "6": "stats",
    "stats": "stats",
    "roomstats": "stats",
    "roomuserseq": "stats",
    "webcastroomstatsmessage": "stats",
    "webcastroomuserseqmessage": "stats",
}
_STABLE_USER_ID_KEYS = (
    "webcastUid",
    "webcast_uid",
    "webcastUserId",
    "webcast_user_id",
    "open_id",
    "openId",
    "sec_uid",
    "secUid",
)
_USER_ID_KEYS = ("uid", "id", "idStr", "id_str", "user_id", "userId", *_STABLE_USER_ID_KEYS)
_USER_NAME_KEYS = ("nickname", "nickName", "nick_name", "user_name", "userName", "name")
_USER_AVATAR_KEYS = ("avatar_url", "avatar", "avatarUrl", "avatar_thumb", "avatarThumb")
_GIFT_NAME_KEYS = ("gift_name", "giftName", "gift_name_str", "name", "displayName", "display_name", "describe")
_GIFT_COUNT_KEYS = (
    "gift_count",
    "giftCount",
    "count",
    "num",
    "repeat_count",
    "repeatCount",
    "combo_count",
    "comboCount",
    "repeatEnd",
)
_GIFT_VALUE_KEYS = (
    "gift_value",
    "giftValue",
    "total_score",
    "totalScore",
    "totalScoreRealStr",
    "totalScoreStr",
    "total_coin",
    "totalCoin",
    "diamond",
    "diamond_count",
    "diamondCount",
    "price",
    "cost",
)
_USER_PATH_PARENTS = (
    ("user",),
    ("User",),
    ("author",),
    ("sender",),
    ("fromUser",),
    ("from_user",),
    ("common", "user"),
    ("Common", "User"),
)
_GIFT_PATH_PARENTS = (
    ("gift",),
    ("Gift",),
    ("giftInfo",),
    ("gift_info",),
    ("GiftInfo",),
    ("giftDetail",),
    ("gift_detail",),
    ("GiftDetail",),
)


class DouyinLiveBridgeAdapter:
    """Adapter for jwwsjlm/douyinLive style local WebSocket messages."""

    adapter_id = "douyinlive"

    def __init__(self, *, base_url: str = "ws://127.0.0.1:1088/ws") -> None:
        self._base_url = base_url

    def bridge_url(self, room_ref: str) -> str:
        return local_bridge_url(self._base_url, room_ref)

    def map_message(self, message: Any, *, room_ref: str) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for item in _iter_message_objects(message):
            payload = _payload_from_message(item, room_ref=room_ref)
            if payload:
                payloads.append(payload)
        return payloads


def _iter_message_objects(message: Any):
    if isinstance(message, dict):
        yield _message_with_nested_payload(message)
        if _has_type_marker(message):
            return
        for key in ("payload", "event", "body"):
            nested = message.get(key)
            if isinstance(nested, dict):
                if _has_type_marker(nested):
                    continue
                yield nested
        data = message.get("data")
        if isinstance(data, dict):
            if _has_type_marker(data):
                return
            yield data
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    yield item
    elif isinstance(message, list):
        for item in message:
            if isinstance(item, dict):
                yield item


def _message_with_nested_payload(message: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key in ("data", "payload", "event", "body"):
        nested = message.get(key)
        if isinstance(nested, dict):
            merged.update(nested)
    merged.update(message)
    return merged


def _payload_from_message(item: dict[str, Any], *, room_ref: str) -> dict[str, Any]:
    event_type = _event_type(item)
    if not event_type:
        return {}
    payload = {
        "event_type": event_type,
        "room_ref": _room_ref(item, fallback=room_ref),
        "uid": _user_id(item),
        "nickname": _first_from_paths(item, _paths(_USER_NAME_KEYS, _USER_PATH_PARENTS)),
        "text": _first(item, "text", "content", "msg", "message", "danmaku_text", "danmakuText"),
        "avatar_url": _avatar_url(item, event_type=event_type),
        "gift_name": _gift_name(item),
        "gift_count": _first_from_paths(item, _paths(_GIFT_COUNT_KEYS, _GIFT_PATH_PARENTS)),
        "gift_value": _first_from_paths(item, _paths(_GIFT_VALUE_KEYS, _GIFT_PATH_PARENTS)),
        "room_id": _first(item, "room_id", "roomId", "webcast_room_id", "webcastRoomId"),
    }
    return safe_payload(payload)


def _event_type(item: dict[str, Any]) -> str:
    method_token = _method_token(item)
    if method_token:
        event_type = _EVENT_ALIASES.get(method_token, "")
        if event_type:
            return event_type
    raw_keys = ("event_type", "eventType", "type")
    if not method_token:
        raw_keys += ("msg_type", "msgType")
    raw = _first(item, *raw_keys)
    if isinstance(raw, int) and not isinstance(raw, bool):
        token = str(raw)
    elif isinstance(raw, str):
        token = raw.strip().lower().replace("_", "")
    else:
        token = ""
    if not token:
        # Some bridges send reduced payloads without a type field.
        token = "gift" if _gift_name(item) else ""
    if not token and not method_token:
        token = "chat" if _first(item, "text", "content", "msg", "message") else ""
    return _EVENT_ALIASES.get(token, "")


def _method_token(item: dict[str, Any]) -> str:
    method = _first(item, "method", "Method")
    if isinstance(method, str) and method.strip():
        return method.strip().lower().replace("_", "")
    return ""


def _has_type_marker(item: dict[str, Any]) -> bool:
    return any(key in item for key in ("event_type", "eventType", "type", "msg_type", "msgType", "method", "Method"))


def _room_ref(item: dict[str, Any], *, fallback: str) -> str:
    raw = _first(item, "room_ref", "roomRef", "rid", "live_id", "liveId", "web_rid", "webRid")
    parsed = parse_douyin_room_ref(raw)
    if parsed.ok:
        return parsed.room_ref
    parsed = parse_douyin_room_ref(fallback)
    return parsed.room_ref if parsed.ok else ""


def _first(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source:
            return source[key]
    return None


def _user_id(source: dict[str, Any]) -> Any:
    if _method_token(source) == "webcastlinkercontributemessage":
        value = _first_contributor_user_id(source)
        if value is not None:
            return value
    stable_value = _first_from_paths(source, _paths(_STABLE_USER_ID_KEYS, _USER_PATH_PARENTS))
    if stable_value is not None:
        return stable_value
    return _first_from_paths(source, _paths(_USER_ID_KEYS, _USER_PATH_PARENTS))


def _first_contributor_user_id(source: dict[str, Any]) -> Any:
    contributors = source.get("userContributeList") or source.get("user_contribute_list")
    if not isinstance(contributors, list):
        return None
    for item in contributors:
        if isinstance(item, dict):
            value = _first(item, *_STABLE_USER_ID_KEYS)
            if value is not None:
                return value
            value = _first(item, "uid", "id", "idStr", "id_str", "user_id", "userId", "open_id", "openId")
            if value is not None:
                return value
    return None


def _gift_name(source: dict[str, Any]) -> Any:
    value = _first(source, *_GIFT_NAME_KEYS)
    if value is not None:
        return value
    value = _first_from_paths(source, _paths(_GIFT_NAME_KEYS, _GIFT_PATH_PARENTS))
    if value is not None:
        return value
    gift = source.get("gift")
    return gift if isinstance(gift, str) else None


def _avatar_url(source: dict[str, Any], *, event_type: str) -> Any:
    # For douyinLive messages, a top-level avatarThumb can belong to rendered
    # display text rather than the danmaku author. Prefer nested user fields.
    nested_paths = tuple(parent + (key,) for parent in _USER_PATH_PARENTS for key in _USER_AVATAR_KEYS)
    value = _first_url_from_paths(source, nested_paths)
    if value is not None:
        return value
    if event_type == "gift":
        return None
    return _first_url_from_paths(source, tuple((key,) for key in _USER_AVATAR_KEYS))


def _first_url_from_paths(source: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> Any:
    for path in paths:
        value = _value_from_path(source, path)
        url = _image_url(value)
        if url is not None:
            return url
    return None


def _image_url(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    url_list = value.get("urlList") or value.get("url_list") or value.get("urls")
    if isinstance(url_list, list):
        for item in url_list:
            if isinstance(item, str) and item.strip():
                return item
    for key in ("url", "uri"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item
    return None


def _paths(keys: tuple[str, ...], parents: tuple[tuple[str, ...], ...]) -> tuple[tuple[str, ...], ...]:
    direct = tuple((key,) for key in keys)
    nested = tuple(parent + (key,) for parent in parents for key in keys)
    return direct + nested


def _first_from_paths(source: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> Any:
    for path in paths:
        current = _value_from_path(source, path)
        if current is not None:
            return current
    return None


def _value_from_path(source: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = source
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current
