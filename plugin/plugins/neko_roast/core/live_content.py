"""Compatibility facade for static NEKO Live content materials."""

from __future__ import annotations

from .live_active_content import active_engagement_fallback_topic_candidates
from .live_idle_content import idle_hosting_beat_candidates

__all__ = [
    "active_engagement_fallback_topic_candidates",
    "idle_hosting_beat_candidates",
]
