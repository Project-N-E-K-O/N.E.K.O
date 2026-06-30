"""Material profile and repetition rules for active engagement."""

from __future__ import annotations

from collections import deque
from difflib import SequenceMatcher
import re


_ACTIVE_TOPIC_NORMALIZE_RE = re.compile(r"[\W_]+", re.UNICODE)
_ACTIVE_TOPIC_SIMILARITY_THRESHOLD = 0.78
_ACTIVE_TOPIC_MIN_NORMALIZED_CHARS = 6

def _has_active_engagement_streak(values: deque[str], value: str, count: int) -> bool:
    if count <= 0 or len(values) < count:
        return False
    tail = list(values)[-count:]
    return all(str(item or "") == value for item in tail)

def _normalize_active_topic_title(text: str) -> str:
    return _ACTIVE_TOPIC_NORMALIZE_RE.sub("", str(text or "").casefold())

def _is_similar_active_topic_title(title: str, recent_titles: deque[str] | list[str] | tuple[str, ...]) -> bool:
    normalized = _normalize_active_topic_title(title)
    if len(normalized) < _ACTIVE_TOPIC_MIN_NORMALIZED_CHARS:
        return False
    for previous in recent_titles:
        previous_normalized = _normalize_active_topic_title(previous)
        if len(previous_normalized) < _ACTIVE_TOPIC_MIN_NORMALIZED_CHARS:
            continue
        if normalized == previous_normalized:
            return True
        shorter, longer = (
            (normalized, previous_normalized)
            if len(normalized) <= len(previous_normalized)
            else (previous_normalized, normalized)
        )
        if len(shorter) >= _ACTIVE_TOPIC_MIN_NORMALIZED_CHARS and shorter in longer:
            return True
        if SequenceMatcher(None, normalized, previous_normalized).ratio() >= _ACTIVE_TOPIC_SIMILARITY_THRESHOLD:
            return True
    return False

def _host_material_family(material: dict | None) -> str:
    if not isinstance(material, dict):
        return ""
    combined = " ".join(
        str(material.get(field) or "")
        for field in ("key", "title", "fun_axis", "shape", "preferred_shape")
    )
    lowered = combined.lower()
    dense = "".join(ch for ch in lowered if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    if not dense:
        return ""
    if any(marker in dense for marker in ("oneword", "onechar", "password", "\u4e00\u4e2a\u5b57", "\u4e00\u4e2a\u8bcd", "\u4e09\u5b57", "\u6697\u53f7")):
        return "short_callback"
    if any(marker in dense for marker in ("choice", "eitheror", "ab", "\u4e8c\u9009\u4e00", "\u8fd8\u662f", "\u9009\u4e00")):
        return "choice_vote"
    if any(marker in dense for marker in ("serious", "hosting", "hostscore", "\u4e3b\u64ad\u529b", "\u6b63\u7ecf\u4e3b\u64ad", "\u50cf\u4e0d\u50cf\u4e3b\u64ad")):
        return "host_self_test"
    if any(marker in dense for marker in ("keyboard", "screen", "desk", "\u952e\u76d8", "\u5c4f\u5e55", "\u684c\u9762", "\u6c34\u676f", "\u96f6\u98df")):
        return "object_scene"
    if any(
        marker in dense
        for marker in (
            "radio",
            "blanket",
            "weather",
            "temperature",
            "mood",
            "stamp",
            "\u7535\u53f0",
            "\u6bdb\u6bef",
            "\u6e29\u5ea6",
            "\u6674\u5929",
            "\u5c0f\u96e8",
            "\u6c14\u6c1b",
            "\u72b6\u6001",
            "\u5fc3\u60c5",
            "\u7ae0",
        )
    ):
        return "room_mood"
    if any(marker in dense for marker in ("tease", "\u5410\u69fd", "\u88ab\u81ea\u5df1", "\u5148\u522b\u7b11")):
        return "tease"
    if any(marker in dense for marker in ("challenge", "mission", "\u6311\u6218", "\u4efb\u52a1", "\u59ff\u52bf")):
        return "micro_challenge"
    axis = str(material.get("fun_axis") or "").strip()
    shape = str(material.get("shape") or material.get("preferred_shape") or "").strip()
    return axis or shape

def _active_topic_material_profile(title: str) -> dict[str, str]:
    compact = " ".join(str(title or "").strip().split())
    lowered = compact.lower()
    dense = "".join(ch for ch in lowered if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    if not dense:
        return {}
    if any(
        marker in dense
        for marker in (
            "choice",
            "pick",
            "vs",
            "\u4e8c\u9009\u4e00",
            "\u4e8c\u62e9\u4e00",
            "\u9009\u4e00",
            "\u54ea\u8fb9",
            "\u8fd8\u662f",
            "\u6295\u7968",
        )
    ):
        return {
            "preferred_shape": "either_or",
            "fun_axis": "choice",
            "live_column": "NEKO micro poll",
            "reply_affordance": "viewer can pick one concrete side",
            "hint": "Turn this material into one concrete A/B choice; do not ask a broad topic question.",
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
            "\u6d4b\u8bd5",
            "\u8bd5\u8bd5",
            "\u6b63\u7ecf",
            "\u5047\u88c5",
        )
    ):
        return {
            "preferred_shape": "small_challenge",
            "fun_axis": "micro_challenge",
            "live_column": "NEKO three-second challenge",
            "reply_affordance": "viewer can answer the tiny challenge in a few words",
            "hint": "Turn this material into one tiny low-pressure challenge; stop before it becomes a segment.",
        }
    if any(
        marker in dense
        for marker in (
            "tease",
            "funny",
            "weird",
            "sleepy",
            "suspicious",
            "\u5410\u69fd",
            "\u79bb\u8c31",
            "\u5077\u5077",
            "\u6253\u76f9",
            "\u7b11",
            "\u5947\u602a",
            "\u600e\u4e48\u8fd9\u4e48",
            "\u786c\u6491",
        )
    ):
        return {
            "preferred_shape": "tiny_tease",
            "fun_axis": "tease",
            "live_column": "NEKO tiny verdict",
            "reply_affordance": "viewer can tease NEKO or the topic back",
            "hint": "Turn this material into one tiny playful tease; do not make it a news recap.",
        }
    if any(
        marker in dense
        for marker in (
            "keyboard",
            "screen",
            "desk",
            "snack",
            "drink",
            "\u952e\u76d8",
            "\u5c4f\u5e55",
            "\u684c\u9762",
            "\u6c34\u676f",
            "\u996e\u6599",
            "\u96f6\u98df",
        )
    ):
        return {
            "preferred_shape": "tiny_tease",
            "fun_axis": "object_scene",
            "live_column": "NEKO room observation",
            "reply_affordance": "viewer can answer with one small object or room detail",
            "hint": "Turn this material into one tiny room observation; do not pretend to know details beyond the title.",
        }
    if any(
        marker in dense
        for marker in (
            "mood",
            "room",
            "radio",
            "weather",
            "temperature",
            "\u6c14\u6c1b",
            "\u5c0f\u7535\u53f0",
            "\u7535\u53f0",
            "\u6e29\u5ea6",
            "\u6674\u5929",
            "\u5c0f\u96e8",
            "\u72b6\u6001",
        )
    ):
        return {
            "preferred_shape": "light_stance",
            "fun_axis": "mood",
            "live_column": "NEKO mood card",
            "reply_affordance": "viewer can agree or answer with one small mood word",
            "hint": "Turn this material into one tiny NEKO stance or mood image; keep it easy to answer.",
        }
    return {}

