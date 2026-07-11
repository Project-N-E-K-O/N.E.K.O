"""Bilibili trending source for active engagement topics."""

from __future__ import annotations

import asyncio
import time
from typing import Any


async def bili_trending_topic_candidates(selector: Any) -> list[dict[str, Any]]:
    now = time.monotonic()
    if (
        selector._active_engagement_topic_cache
        and now - selector._active_engagement_topic_cache_at < 600.0
    ):
        return list(selector._active_engagement_topic_cache)
    fetcher = selector._active_engagement_topic_fetcher
    if fetcher is None:
        try:
            from utils.web_scraper import fetch_bilibili_trending

            fetcher = fetch_bilibili_trending
        except Exception:
            fetcher = None
    if not callable(fetcher):
        return []
    try:
        try:
            payload = await asyncio.wait_for(fetcher(limit=6), timeout=2.0)
        except TypeError:
            payload = await asyncio.wait_for(fetcher(), timeout=2.0)
    except Exception:
        return []
    videos = []
    if isinstance(payload, dict):
        videos = (
            payload.get("videos") or payload.get("video") or payload.get("items") or []
        )
    if not isinstance(videos, list):
        return []
    candidates: list[dict[str, Any]] = []
    for item in videos:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        if not selector.is_meaningful_topic_text(title):
            continue
        key = str(item.get("bvid") or item.get("id") or title).strip()
        compact_title = selector._runtime._compact_context_text(title, limit=40)
        profile = selector.material_profile(compact_title)
        if not profile:
            if not selector._active_engagement_recent_topic_skip_reason:
                selector._active_engagement_recent_topic_skip_reason = (
                    "low_confidence_topic"
                )
            continue
        candidate = {
            "source": "bili_trending",
            "key": f"bili:{key}",
            "title": compact_title,
            "hint": "Use this Bilibili topic only as a small safe hook; anchor the topic first, then ask one easy reply.",
        }
        candidate.update(profile)
        candidates.append(candidate)
        if len(candidates) >= 6:
            break
    if candidates:
        selector._active_engagement_recent_topic_skip_reason = ""
    selector._active_engagement_topic_cache = candidates
    selector._active_engagement_topic_cache_at = now
    return list(candidates)
