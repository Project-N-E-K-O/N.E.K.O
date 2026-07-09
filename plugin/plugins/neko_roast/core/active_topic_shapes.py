"""Shape rotation helpers for active-engagement topics."""

from __future__ import annotations

from collections import deque

from .active_topic_rotation import has_active_engagement_streak


ACTIVE_TOPIC_SHAPES = ("either_or", "light_stance", "tiny_tease", "small_challenge")


def next_active_topic_shape(index: int) -> tuple[str, int]:
    shape = ACTIVE_TOPIC_SHAPES[int(index or 0) % len(ACTIVE_TOPIC_SHAPES)]
    return shape, int(index or 0) + 1


def guarded_active_topic_shape(
    shape: str,
    recent_shapes: deque[str],
) -> tuple[str, str]:
    normalized = shape if shape in ACTIVE_TOPIC_SHAPES else ACTIVE_TOPIC_SHAPES[0]
    if not has_active_engagement_streak(recent_shapes, normalized, 2):
        return normalized, ""
    for candidate in ACTIVE_TOPIC_SHAPES:
        if candidate != normalized and not has_active_engagement_streak(recent_shapes, candidate, 1):
            return candidate, "recent_shape_streak"
    for candidate in ACTIVE_TOPIC_SHAPES:
        if candidate != normalized:
            return candidate, "recent_shape_streak"
    return normalized, "recent_shape_streak"
