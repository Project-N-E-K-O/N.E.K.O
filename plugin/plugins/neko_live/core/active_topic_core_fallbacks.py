"""Safe defaults for active-topic dependencies introduced by later slices."""

from __future__ import annotations

from collections import deque
from difflib import SequenceMatcher
import re
from typing import Any


_SHAPES = ("either_or", "light_stance", "tiny_tease", "small_challenge")
_NORMALIZE_RE = re.compile(r"[\W_]+", re.UNICODE)


def fallback_topic_candidates() -> list[dict[str, Any]]:
    return [
        {
            "source": "fallback",
            "key": "fallback:room-mood",
            "title": "the live room mood",
            "preferred_shape": "either_or",
            "fun_axis": "choice",
            "live_column": "NEKO micro poll",
            "reply_affordance": "viewer can answer with one concrete side",
            "hint": "Offer one small A/B choice about the current room mood.",
        }
    ]


def next_active_topic_shape(index: int) -> tuple[str, int]:
    normalized_index = int(index or 0)
    return _SHAPES[normalized_index % len(_SHAPES)], normalized_index + 1


def guarded_active_topic_shape(
    shape: str,
    recent_shapes: deque[str],
) -> tuple[str, str]:
    normalized = shape if shape in _SHAPES else _SHAPES[0]
    if not has_active_engagement_streak(recent_shapes, normalized, 2):
        return normalized, ""
    for candidate in _SHAPES:
        if candidate != normalized and not has_active_engagement_streak(
            recent_shapes, candidate, 1
        ):
            return candidate, "recent_shape_streak"
    for candidate in _SHAPES:
        if candidate != normalized:
            return candidate, "recent_shape_streak"
    return normalized, "recent_shape_streak"


def has_active_engagement_streak(values: deque[str], value: str, count: int) -> bool:
    if count <= 0 or len(values) < count:
        return False
    return all(str(item or "") == value for item in list(values)[-count:])


def normalize_active_topic_title(text: str) -> str:
    return _NORMALIZE_RE.sub("", str(text or "").casefold())


def is_similar_active_topic_title(
    title: str,
    recent_titles: deque[str] | list[str] | tuple[str, ...],
) -> bool:
    normalized = normalize_active_topic_title(title)
    if len(normalized) < 6:
        return False
    for previous in recent_titles:
        previous_normalized = normalize_active_topic_title(previous)
        if len(previous_normalized) < 6:
            continue
        shorter, longer = sorted((normalized, previous_normalized), key=len)
        if normalized == previous_normalized or shorter in longer:
            return True
        if SequenceMatcher(None, normalized, previous_normalized).ratio() >= 0.78:
            return True
    return False


def host_material_family(material: dict[str, Any] | None) -> str:
    if not isinstance(material, dict):
        return ""
    explicit = str(material.get("family") or "").strip()
    if explicit:
        return explicit
    axis = str(material.get("fun_axis") or "").strip()
    shape = str(material.get("shape") or material.get("preferred_shape") or "").strip()
    return axis or shape


def active_topic_material_profile(title: str) -> dict[str, str]:
    dense = "".join(
        ch
        for ch in str(title or "").casefold()
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
    )
    if not dense:
        return {}
    if any(
        marker in dense
        for marker in (
            "choice",
            "pick",
            "vs",
            "\u9009\u4e00",
            "\u8fd8\u662f",
            "\u6295\u7968",
        )
    ):
        return {
            "preferred_shape": "either_or",
            "fun_axis": "choice",
            "live_column": "NEKO micro poll",
            "reply_affordance": "viewer can answer with one concrete side",
            "hint": "Turn this material into one concrete A/B choice.",
        }
    if any(
        marker in dense
        for marker in (
            "challenge",
            "mission",
            "score",
            "rate",
            "\u6311\u6218",
            "\u4efb\u52a1",
            "\u6253\u5206",
        )
    ):
        return {
            "preferred_shape": "small_challenge",
            "fun_axis": "micro_challenge",
            "live_column": "NEKO three-second challenge",
            "reply_affordance": "viewer can answer in a few words",
            "hint": "Turn this material into one tiny low-pressure challenge.",
        }
    if any(
        marker in dense
        for marker in (
            "tease",
            "funny",
            "weird",
            "\u5410\u69fd",
            "\u79bb\u8c31",
            "\u5947\u602a",
        )
    ):
        return {
            "preferred_shape": "tiny_tease",
            "fun_axis": "tease",
            "live_column": "NEKO tiny verdict",
            "reply_affordance": "viewer can tease NEKO back",
            "hint": "Turn this material into one tiny playful tease.",
        }
    return {}


def active_topic_pack(material: dict[str, Any] | None) -> str:
    if not isinstance(material, dict):
        return ""
    explicit = str(material.get("topic_pack") or "").strip()
    return explicit or host_material_family(material) or "general"
