"""Shared safety and similarity rules for plugin-owned live material."""

from __future__ import annotations

from collections.abc import Iterable
from difflib import SequenceMatcher
import re


_NORMALIZE_RE = re.compile(r"[\W_]+", re.UNICODE)
_SIMILARITY_THRESHOLD = 0.78
_MIN_NORMALIZED_CHARS = 6
_LOW_CONFIDENCE_TERMS = (
    "\u6838\u7535",
    "\u6838\u7535\u7ad9",
    "\u6838\u8f90\u5c04",
    "\u8f90\u5c04",
    "\u7206\u7834",
    "\u7206\u70b8",
    "\u52b3\u6539",
    "\u516c\u5f00\u793a\u4f17",
    "\u793a\u4f17",
    "\u5904\u5211",
    "\u60e9\u7f5a",
    "\u5ba1\u5224",
    "\u653b\u7565",
    "\u6559\u7a0b",
    "\u4e13\u5bb6",
    "\u61c2\u5f88\u591a",
    "\u8dd1\u4ee3\u7801",
    "\u903b\u8f91\u7535\u8def",
    "\u6f0f\u52fa",
    "nuclear",
    "radiation",
    "punish",
    "trial",
    "expert",
)
_TECHNICAL_TOPIC_MARKERS = (
    "\u6cf0\u62c9\u745e\u4e9a",
    "\u6211\u7684\u4e16\u754c",
    "\u661f\u9732\u8c37",
    "\u660e\u65e5\u65b9\u821f",
    "\u539f\u795e",
    "\u5d29\u574f",
    "\u7edd\u533a\u96f6",
    "\u4ee3\u7801",
    "\u7f16\u7a0b",
    "\u7535\u8def",
    "\u673a\u5236",
    "\u914d\u88c5",
    "\u914d\u65b9",
    "terraria",
    "minecraft",
    "code",
    "circuit",
)


def is_clean_live_material(material: dict | None) -> bool:
    if not isinstance(material, dict):
        return False
    values = [
        str(material.get(field) or "").strip()
        for field in ("title", "hint", "reply_affordance", "live_column")
    ]
    return any(values) and all(_is_clean_live_material_text(value) for value in values if value)


def is_similar_live_material_title(title: str, recent_titles: Iterable[str]) -> bool:
    normalized = _normalize_title(title)
    if len(normalized) < _MIN_NORMALIZED_CHARS:
        return False
    for previous in recent_titles:
        previous_normalized = _normalize_title(previous)
        if len(previous_normalized) < _MIN_NORMALIZED_CHARS:
            continue
        if normalized == previous_normalized:
            return True
        shorter, longer = sorted((normalized, previous_normalized), key=len)
        if shorter in longer:
            return True
        if SequenceMatcher(None, normalized, previous_normalized).ratio() >= _SIMILARITY_THRESHOLD:
            return True
    return False


def _normalize_title(text: str) -> str:
    return _NORMALIZE_RE.sub("", str(text or "").casefold())


def _is_clean_live_material_text(text: str) -> bool:
    compact = " ".join(str(text or "").strip().split())
    if not compact:
        return False
    lowered = compact.casefold()
    dense = "".join(
        ch for ch in lowered if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
    )
    mojibake_markers = ("\ufffd", "\u951f", "\u95bb", "\u940f", "\u941a", "\u7f01", "\u6fee", "\u95b8", "\u95bf", "\u95b3")
    if any(marker in compact for marker in mojibake_markers):
        return False
    if compact.count('"') % 2:
        return False
    if any(
        term.casefold() in lowered or term.casefold() in dense
        for term in _LOW_CONFIDENCE_TERMS
    ):
        return False
    if any(marker in lowered or marker in dense for marker in _TECHNICAL_TOPIC_MARKERS):
        return False
    return not any(
        term in lowered for term in ("public shaming", "labor camp", "punishment")
    )
