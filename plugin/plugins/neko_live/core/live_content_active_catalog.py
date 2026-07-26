"""Compatibility aggregate for active-engagement fallback topic candidates."""

from __future__ import annotations

from typing import Any

from .live_content_active_catalog_choice import CHOICE_FALLBACK_TOPIC_CANDIDATES
from .live_content_active_catalog_callback import (
    VIEWER_CALLBACK_FALLBACK_TOPIC_CANDIDATES,
)
from .live_content_active_catalog_tease import TEASE_FALLBACK_TOPIC_CANDIDATES
from .live_content_active_catalog_challenge import (
    MICRO_CHALLENGE_FALLBACK_TOPIC_CANDIDATES,
)
from .live_content_active_catalog_mood import MOOD_FALLBACK_TOPIC_CANDIDATES

_ALL_FALLBACK_TOPIC_CANDIDATES: tuple[dict[str, Any], ...] = (
    *CHOICE_FALLBACK_TOPIC_CANDIDATES,
    *VIEWER_CALLBACK_FALLBACK_TOPIC_CANDIDATES,
    *TEASE_FALLBACK_TOPIC_CANDIDATES,
    *MICRO_CHALLENGE_FALLBACK_TOPIC_CANDIDATES,
    *MOOD_FALLBACK_TOPIC_CANDIDATES,
)

_FALLBACK_TOPIC_CANDIDATES_BY_KEY: dict[str, dict[str, Any]] = {
    item["key"]: item for item in _ALL_FALLBACK_TOPIC_CANDIDATES
}

_FALLBACK_TOPIC_CANDIDATE_KEYS: tuple[str, ...] = (
    "fallback:keyboard-busy",
    "fallback:snack-choice",
    "fallback:serious-cat",
    "fallback:tiny-confession",
    "fallback:today-mood-vote",
    "fallback:cat-radio-room",
    "fallback:night-owl-energy",
    "fallback:screen-staring-back",
    "fallback:three-word-task",
    "fallback:room-temperature-word",
    "fallback:serious-hosting",
    "fallback:danmaku-password",
    "fallback:cat-weather",
    "fallback:blanket-temperature",
    "fallback:one-word-barrage",
    "fallback:desk-item-choice",
    "fallback:cat-paw-button",
    "fallback:host-score-one-word",
    "fallback:tiny-brave-stance",
    "fallback:micro-mission-pose",
    "fallback:cat-weather-forecast",
    "fallback:night-title",
    "fallback:can-before-after",
    "fallback:reliable-three-sec",
    "fallback:weird-score",
    "fallback:desk-guardian",
    "fallback:tiny-court",
    "fallback:tail-one-char",
    "fallback:soft-business-day",
    "fallback:doorplate",
    "fallback:sleep-thief",
    "fallback:two-char-password",
    "fallback:tsundere-choice",
    "fallback:self-compliment",
    "fallback:lightstick-reflection",
    "fallback:air-filter-word",
)

ACTIVE_ENGAGEMENT_FALLBACK_TOPIC_CANDIDATES: tuple[dict[str, Any], ...] = tuple(
    _FALLBACK_TOPIC_CANDIDATES_BY_KEY[key] for key in _FALLBACK_TOPIC_CANDIDATE_KEYS
)

__all__ = ["ACTIVE_ENGAGEMENT_FALLBACK_TOPIC_CANDIDATES"]
