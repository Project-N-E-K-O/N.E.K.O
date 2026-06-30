"""Route limits and metadata helpers for NEKO Live replies."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


REPLY_TARGET_CHARS = 14
DEFAULT_DISPATCH_REPLY_CHARS = 28
DISPATCH_REPLY_CHAR_LIMITS = {
    "warmup_hosting": 56,
    "idle_hosting": 64,
    "active_engagement": 72,
}
ROUTE_CEILINGS = {
    "avatar_roast": 32,
    "danmaku_response": 28,
    "warmup_hosting": 56,
    "idle_hosting": 64,
    "active_engagement": 72,
}
ROUTE_NOTES = {
    "avatar_roast": (
        "For avatar_roast: connect the viewer's first message with the avatar/name, "
        "but keep it as one sharp first-appearance line."
    ),
    "danmaku_response": (
        "For danmaku_response: answer only the current danmaku; do not mention avatar, "
        "ID, first appearance, or previous replies."
    ),
    "warmup_hosting": (
        "For warmup_hosting: usually say one small opening line; if the beat is charming, "
        "two short sentences are allowed."
    ),
    "idle_hosting": (
        "For idle_hosting: make one small hosting beat. It can occasionally be a tiny two-sentence "
        "aside, but not a full monologue or survey."
    ),
    "active_engagement": (
        "For active_engagement: offer one concrete reply hook; do not say generic phrases "
        "like everyone interact or tell me what you want. If the topic is technical, "
        "game-specific, a guide/tutorial, or unfamiliar, make only a small surface reaction "
        "instead of inventing an expert A/B question. If the material is genuinely fun, "
        "a tiny two-sentence riff is allowed."
    ),
}
HOST_MODULES = {"warmup_hosting", "idle_hosting", "active_engagement"}
def is_live_reply_metadata(metadata: Mapping[str, Any] | None) -> bool:
    return isinstance(metadata, Mapping) and metadata.get("live_reply_contract") == "short_tts_line"


def max_reply_chars_for_module(module: str) -> int:
    return DISPATCH_REPLY_CHAR_LIMITS.get(str(module or "").strip(), DEFAULT_DISPATCH_REPLY_CHARS)


def build_reply_metadata(
    *,
    uid: str,
    live_mode: str,
    response_module_hint: str,
    demo: bool = False,
) -> dict[str, Any]:
    return {
        "plugin": "neko_roast",
        "uid": uid,
        "live_mode": live_mode,
        "demo": bool(demo),
        "live_reply_contract": "short_tts_line",
        "max_reply_chars": max_reply_chars_for_module(response_module_hint),
        "response_module_hint": response_module_hint,
    }


def coerce_live_reply_limit(value: Any) -> int | None:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return None
    if limit <= 0:
        return None
    return min(limit, 80)


def reply_limit_from_metadata(metadata: Mapping[str, Any] | None) -> int | None:
    if not is_live_reply_metadata(metadata):
        return None
    module = response_module(metadata)
    metadata_limit = coerce_live_reply_limit((metadata or {}).get("max_reply_chars"))
    module_limit = ROUTE_CEILINGS.get(module)
    candidates = [value for value in (metadata_limit, module_limit) if value]
    return min(candidates) if candidates else None


def response_module(metadata: Mapping[str, Any] | None) -> str:
    if not isinstance(metadata, Mapping):
        return ""
    return str(metadata.get("response_module_hint") or "").strip()


