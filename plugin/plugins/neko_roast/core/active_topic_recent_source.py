"""Recent danmaku source for active engagement topics."""

from __future__ import annotations

from typing import Any


def recent_danmaku_topic_candidates(selector: Any) -> list[dict[str, Any]]:
    selector._active_engagement_recent_topic_skip_reason = ""
    if selector.has_streak(
        selector._active_engagement_recent_topic_sources, "recent_danmaku", 2
    ):
        selector._active_engagement_recent_topic_skip_reason = (
            "recent_danmaku_source_streak"
        )
        return []
    recent_items: list[tuple[str, str]] = []
    for result in reversed(selector.recent_results):
        if not isinstance(result, dict):
            continue
        event = result.get("event") if isinstance(result.get("event"), dict) else {}
        if str(event.get("source") or "") != "live_danmaku":
            continue
        if str(result.get("status") or "") not in {"pushed", "dry_run"}:
            if not selector._active_engagement_recent_topic_skip_reason:
                selector._active_engagement_recent_topic_skip_reason = (
                    "non_output_danmaku"
                )
            continue
        route = selector._runtime._route_from_result(result)
        if route == "avatar_roast":
            if not selector._active_engagement_recent_topic_skip_reason:
                selector._active_engagement_recent_topic_skip_reason = (
                    "avatar_roast_context"
                )
            continue
        age = selector._runtime._iso_age_sec(result.get("created_at"))
        if (
            age is not None
            and age > selector._ACTIVE_ENGAGEMENT_RECENT_DANMAKU_TOPIC_MAX_AGE_SECONDS
        ):
            if not selector._active_engagement_recent_topic_skip_reason:
                selector._active_engagement_recent_topic_skip_reason = (
                    "stale_recent_danmaku"
                )
            continue
        text = str(event.get("danmaku_text") or "").strip()
        if not text:
            continue
        if selector.is_viewer_to_viewer_mention_text(text):
            if not selector._active_engagement_recent_topic_skip_reason:
                selector._active_engagement_recent_topic_skip_reason = (
                    "viewer_to_viewer_mention"
                )
            continue
        if not selector.is_meaningful_topic_text(text):
            if not selector._active_engagement_recent_topic_skip_reason:
                selector._active_engagement_recent_topic_skip_reason = (
                    selector.topic_filter_reason(text) or "filtered_recent_danmaku"
                )
            continue
        compact = selector._runtime._compact_context_text(text, limit=40)
        uid = str(event.get("uid") or "").strip()
        recent_items.append((uid, compact))
        if len(recent_items) >= 6:
            break
    speaker_ids = {uid or "<anonymous>" for uid, _ in recent_items}
    if len(recent_items) >= 3 and len(speaker_ids) == 1:
        selector._active_engagement_recent_topic_skip_reason = "single_viewer_flood"
        return []
    candidates: list[dict[str, Any]] = []
    for _uid, compact in recent_items[:3]:
        profile = selector.material_profile(compact)
        if not profile:
            if not selector._active_engagement_recent_topic_skip_reason:
                selector._active_engagement_recent_topic_skip_reason = (
                    "low_confidence_topic"
                )
            continue
        candidate = {
            "source": "recent_danmaku",
            "privacy_classification": "viewer_derived",
            "key": f"danmaku:{compact}",
            "title": compact,
            "hint": "Anchor this recent danmaku first, then make one small reply hook without pretending a new viewer spoke.",
        }
        candidate.update(profile)
        candidates.append(candidate)
        if len(candidates) >= 3:
            break
    if candidates:
        selector._active_engagement_recent_topic_skip_reason = ""
    return candidates
