"""Session-bound state helpers for explicit live-listener runs."""

from __future__ import annotations

import secrets
from typing import Any


_LIVE_SESSION_GENERATION_KEY = "_live_session_generation"
_LIVE_SESSION_SOURCES = {
    "live_danmaku",
    "manual_live_simulation",
    "warmup_hosting",
    "idle_hosting",
    "active_engagement",
}


def bind_event_to_live_session(runtime: Any, event: Any) -> int | None:
    if str(getattr(event, "source", "") or "") not in _LIVE_SESSION_SOURCES:
        return None
    raw = getattr(event, "raw", None)
    if not isinstance(raw, dict):
        raw = {}
        event.raw = raw
    if event_live_session_generation(event) is None:
        raw[_LIVE_SESSION_GENERATION_KEY] = int(
            getattr(runtime, "_live_session_generation", 0) or 0
        )
    return event_live_session_generation(event)


def event_live_session_generation(event: Any) -> int | None:
    raw = getattr(event, "raw", None)
    if not isinstance(raw, dict) or _LIVE_SESSION_GENERATION_KEY not in raw:
        return None
    value = raw.get(_LIVE_SESSION_GENERATION_KEY)
    if isinstance(value, bool):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def is_current_live_session_event(runtime: Any, event: Any) -> bool:
    generation = event_live_session_generation(event)
    if generation is None:
        return True
    current = int(getattr(runtime, "_live_session_generation", 0) or 0)
    if generation == 0 and current == 0:
        return True
    return bool(getattr(runtime, "_accepting_live_events", False)) and generation == current


def begin_live_session(runtime: Any) -> int:
    """Start a fresh live session without carrying room-local state forward."""

    runtime._live_session_generation = int(runtime._live_session_generation or 0) + 1
    runtime.recent_results.clear()
    runtime.runtime_timeline.clear()
    runtime._timeline_salt = secrets.token_bytes(32)
    runtime._last_live_danmaku_seen_at = 0.0
    runtime._last_live_danmaku_seen_type = ""

    runtime.live_events.reset()
    runtime.live_support_events.reset()
    runtime.pipeline.clear_dry_run_session_state()

    runtime._idle_hosting_last_attempt_at = 0.0
    runtime._idle_hosting_consecutive_failures = 0
    runtime._idle_hosting_beat_index = 0
    runtime._idle_hosting_recent_beat_keys.clear()
    runtime._idle_hosting_recent_beat_axes.clear()
    runtime._idle_hosting_recent_beat_titles.clear()
    runtime._idle_hosting_recent_reply_affordances.clear()
    runtime._recent_host_material_families.clear()

    runtime._active_engagement_last_attempt_at = 0.0
    runtime._active_engagement_recent_topic_keys.clear()
    runtime._active_engagement_recent_topic_titles.clear()
    runtime._active_engagement_recent_topic_sources.clear()
    runtime._active_engagement_recent_fun_axes.clear()
    runtime._active_engagement_recent_shapes.clear()
    runtime._active_engagement_recent_intents.clear()
    runtime._active_engagement_recent_reply_affordances.clear()
    runtime._active_engagement_recent_topic_skip_reason = ""
    runtime._active_engagement_shape_guard_reason = ""
    runtime._active_engagement_shape_index = 0

    runtime.live_audience_session.start_session()
    return runtime._live_session_generation


def invalidate_live_session(runtime: Any) -> int:
    """Make all work bound to the current live session immediately stale."""

    runtime._live_session_generation = int(
        getattr(runtime, "_live_session_generation", 0) or 0
    ) + 1
    runtime.live_audience_session.finish_session()
    runtime.live_events.reset()
    runtime.live_support_events.reset()
    return runtime._live_session_generation
