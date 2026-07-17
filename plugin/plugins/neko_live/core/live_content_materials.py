"""Compatibility facade for NEKO Live static material pools."""

from __future__ import annotations

from typing import Any

from .live_content_active_materials import active_engagement_fallback_topic_candidates


def idle_hosting_beat_candidates() -> list[dict[str, Any]]:
    try:
        from .live_content_host_materials import (
            idle_hosting_beat_candidates as host_candidates,
        )
    except ModuleNotFoundError as exc:
        expected = f"{__package__}.live_content_host_materials"
        if exc.name != expected:
            raise
        return []
    return host_candidates()


__all__ = [
    "active_engagement_fallback_topic_candidates",
    "idle_hosting_beat_candidates",
]
