"""Readonly dashboard explanation projection for NEKO Live."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .runtime_timeline import timeline_for_trace
from .viewer_preferences import safe_int, safe_preference_counts, safe_text


def live_explanation(
    runtime: Any,
    *,
    profiles: list[dict[str, Any]],
    health_rows: list[dict[str, Any]],
    live_status: dict[str, Any],
    live_state: dict[str, Any],
    live_director_status: dict[str, Any],
    speech_explanation: dict[str, Any],
) -> dict[str, Any]:
    latest = runtime.recent_results[-1] if runtime.recent_results else {}
    selection = _module_status(runtime.live_events)
    trace_id = safe_text(latest.get("trace_id"), max_len=120) if isinstance(latest, dict) else ""
    return {
        "summary": safe_text(speech_explanation.get("summary"), max_len=80) or "waiting",
        "reason": safe_text(speech_explanation.get("reason"), max_len=120),
        "trace_id": trace_id,
        "timeline": timeline_for_trace(runtime, trace_id),
        "chain": _chain_rows(health_rows),
        "selection": _selection_summary(selection),
        "viewer_memory": _viewer_memory_summary(profiles),
        "latest_result": _latest_result_summary(latest),
        "live_status": {
            "summary": safe_text(live_status.get("summary"), max_len=80),
            "reason": safe_text(live_status.get("reason"), max_len=120),
            "can_output": bool(live_status.get("can_output")),
        },
        "live_state": {
            "state": safe_text(live_state.get("state"), max_len=80),
            "reason": safe_text(live_state.get("reason"), max_len=120),
        },
        "director": {
            "next_auto_action": safe_text(live_director_status.get("next_auto_action"), max_len=80),
            "reason": safe_text(live_director_status.get("reason"), max_len=120),
        },
    }


def _chain_rows(health_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in health_rows:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "id": safe_text(item.get("id"), max_len=80),
                "stage": safe_text(item.get("stage"), max_len=80),
                "status": safe_text(item.get("status"), max_len=80),
                "last_outcome": safe_text(item.get("last_outcome"), max_len=120),
                "last_skip_reason": safe_text(item.get("last_skip_reason"), max_len=160),
                "age_sec": item.get("age_sec") if isinstance(item.get("age_sec"), (int, float)) else None,
                "last_latency_ms": item.get("last_latency_ms") if isinstance(item.get("last_latency_ms"), (int, float)) else None,
            }
        )
    return rows


def _selection_summary(selection: dict[str, Any]) -> dict[str, Any]:
    theme_keys = selection.get("last_theme_keys")
    return {
        "window_open": bool(selection.get("window_open")),
        "buffered": safe_int(selection.get("buffered")),
        "last_selected_type": safe_text(selection.get("last_selected_type"), max_len=80),
        "last_candidate_count": safe_int(selection.get("last_candidate_count")),
        "last_skip_reason": safe_text(selection.get("last_skip_reason"), max_len=160),
        "reply_selection_policy": safe_text(selection.get("reply_selection_policy"), max_len=40),
        "recent_danmaku_candidates": safe_int(selection.get("recent_danmaku_candidates")),
        "viewer_memory_count": safe_int(selection.get("viewer_memory_count")),
        "theme_keys": [
            safe_text(item, max_len=80)
            for item in (theme_keys if isinstance(theme_keys, list) else [])
            if safe_text(item, max_len=80)
        ][:6],
    }


def _viewer_memory_summary(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    tag_counts: Counter[str] = Counter()
    topic_counts: Counter[str] = Counter()
    joke_counts: Counter[str] = Counter()
    profiles_with_preferences = 0
    profiles_with_impressions = 0
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        counts = safe_preference_counts(profile.get("preference_tags"))
        if counts:
            profiles_with_preferences += 1
            tag_counts.update(counts)
        topics = safe_preference_counts(profile.get("favorite_topics"))
        jokes = safe_preference_counts(profile.get("running_jokes"))
        if topics or jokes or safe_text(profile.get("impression_summary"), max_len=180):
            profiles_with_impressions += 1
        topic_counts.update(topics)
        joke_counts.update(jokes)
    return {
        "profile_count": len([profile for profile in profiles if isinstance(profile, dict)]),
        "profiles_with_preferences": profiles_with_preferences,
        "profiles_with_impressions": profiles_with_impressions,
        "top_preference_tags": [
            {"tag": tag, "count": count}
            for tag, count in tag_counts.most_common(6)
        ],
        "top_favorite_topics": [
            {"tag": tag, "count": count}
            for tag, count in topic_counts.most_common(6)
        ],
        "top_running_jokes": [
            {"tag": tag, "count": count}
            for tag, count in joke_counts.most_common(6)
        ],
    }


def _latest_result_summary(latest: Any) -> dict[str, Any]:
    if not isinstance(latest, dict):
        return {}
    return {
        "status": safe_text(latest.get("status"), max_len=80),
        "route": safe_text(latest.get("response_module"), max_len=80),
        "event_signal": safe_text(latest.get("event_signal"), max_len=80),
        "reason": safe_text(latest.get("reason"), max_len=160),
        "latency_ms": latest.get("response_latency_ms") if isinstance(latest.get("response_latency_ms"), (int, float)) else None,
        "created_at": safe_text(latest.get("created_at"), max_len=120),
        "danmaku_profile": safe_text(latest.get("danmaku_profile"), max_len=80),
        "danmaku_reply_target": safe_text(latest.get("danmaku_reply_target"), max_len=80),
        "danmaku_reply_shape": safe_text(latest.get("danmaku_reply_shape"), max_len=80),
        "danmaku_anchor_hint": safe_text(latest.get("danmaku_anchor_hint"), max_len=40),
        "reply_length_mode": safe_text(latest.get("reply_length_mode"), max_len=40),
        "room_theme": safe_text(latest.get("room_theme"), max_len=80),
    }


def _module_status(module: Any) -> dict[str, Any]:
    status = getattr(module, "status", None)
    if not callable(status):
        return {}
    try:
        data = status()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
