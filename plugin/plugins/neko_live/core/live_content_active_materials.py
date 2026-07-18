"""Active-engagement fallback topic material accessors for NEKO Live."""

from __future__ import annotations

from typing import Any

from .live_content_active_catalog import ACTIVE_ENGAGEMENT_FALLBACK_TOPIC_CANDIDATES


def active_engagement_fallback_topic_candidates() -> list[dict[str, Any]]:
    return [
        dict(candidate) for candidate in ACTIVE_ENGAGEMENT_FALLBACK_TOPIC_CANDIDATES
    ]
