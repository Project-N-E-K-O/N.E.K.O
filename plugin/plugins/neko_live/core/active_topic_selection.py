"""Backward-compatible active-topic selection facade."""

from __future__ import annotations

from .active_topic_builder import build_topic, remember_topic
from .active_topic_candidate_picker import (
    choose_candidate,
    choose_fallback_candidate,
    choose_fresh_candidate,
    clear_topic_cache,
)

__all__ = [
    "build_topic",
    "choose_candidate",
    "choose_fallback_candidate",
    "choose_fresh_candidate",
    "clear_topic_cache",
    "remember_topic",
]
