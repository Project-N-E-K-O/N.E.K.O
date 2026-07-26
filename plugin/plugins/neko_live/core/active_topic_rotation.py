"""Rotation and de-duplication helpers for active engagement topics."""

from __future__ import annotations

from collections import deque
from difflib import SequenceMatcher
import re


_ACTIVE_TOPIC_NORMALIZE_RE = re.compile(r"[\W_]+", re.UNICODE)
_ACTIVE_TOPIC_SIMILARITY_THRESHOLD = 0.78
_ACTIVE_TOPIC_MIN_NORMALIZED_CHARS = 6


def has_active_engagement_streak(values: deque[str], value: str, count: int) -> bool:
    if count <= 0 or len(values) < count:
        return False
    tail = list(values)[-count:]
    return all(str(item or "") == value for item in tail)


def normalize_active_topic_title(text: str) -> str:
    return _ACTIVE_TOPIC_NORMALIZE_RE.sub("", str(text or "").casefold())


def is_similar_active_topic_title(title: str, recent_titles: deque[str] | list[str] | tuple[str, ...]) -> bool:
    normalized = normalize_active_topic_title(title)
    if len(normalized) < _ACTIVE_TOPIC_MIN_NORMALIZED_CHARS:
        return False
    for previous in recent_titles:
        previous_normalized = normalize_active_topic_title(previous)
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
