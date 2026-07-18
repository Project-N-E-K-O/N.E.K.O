"""Public live-content material accessors for NEKO Live."""

from __future__ import annotations

from typing import Any

from .live_content_materials import (
    active_engagement_fallback_topic_candidates as _active_engagement_fallback_topic_candidates,
)
from .live_content_materials import idle_hosting_beat_candidates as _idle_hosting_beat_candidates


def idle_hosting_beat_candidates() -> list[dict[str, Any]]:
    return _idle_hosting_beat_candidates()


def active_engagement_fallback_topic_candidates() -> list[dict[str, Any]]:
    return _active_engagement_fallback_topic_candidates()
