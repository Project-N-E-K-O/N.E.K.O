"""Candidate source aggregation for active engagement topics."""

from __future__ import annotations

from typing import Any

from .active_topic_live_thread_source import live_thread_topic_candidates
from .active_topic_recent_source import recent_danmaku_topic_candidates
from .active_topic_trending_source import bili_trending_topic_candidates


async def topic_candidates(selector: Any) -> list[dict[str, Any]]:
    live_thread = live_thread_topic_candidates(selector)
    recent = recent_danmaku_topic_candidates(selector)
    recent_skip_reason = str(
        selector._active_engagement_recent_topic_skip_reason or ""
    ).strip()
    trending = await bili_trending_topic_candidates(selector)
    trending_skip_reason = str(
        selector._active_engagement_recent_topic_skip_reason or ""
    ).strip()
    if recent:
        selector._active_engagement_recent_topic_skip_reason = ""
    else:
        selector._active_engagement_recent_topic_skip_reason = (
            recent_skip_reason or trending_skip_reason
        )
    return [*live_thread, *recent, *trending]


__all__ = [
    "bili_trending_topic_candidates",
    "live_thread_topic_candidates",
    "recent_danmaku_topic_candidates",
    "topic_candidates",
]
