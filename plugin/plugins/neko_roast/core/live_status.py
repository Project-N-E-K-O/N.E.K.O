"""Compatibility facade for NEKO Live status calculations."""

from __future__ import annotations

from .live_director_state import (
    active_engagement_status,
    idle_hosting_status,
    idle_hosting_wait_remaining_for_quiet_state,
    live_director_status,
)
from .live_state import live_state_summary, live_status_summary
from .live_timing import (
    IsoAgeFn,
    _activity_level,
    active_engagement_after_danmaku_interval_seconds,
    active_engagement_idle_grace_seconds,
    active_engagement_min_interval_seconds,
    age_sec,
    idle_hosting_min_interval_seconds,
    iso_age_sec,
    last_output_age_sec,
    last_viewer_activity_age_sec,
    live_state_threshold_seconds,
    recent_live_danmaku_event_age_sec,
    recent_live_danmaku_output_age_sec,
    solo_warmup_timeout_seconds,
)
from .solo_readiness import solo_test_readiness, speech_explanation

__all__ = [
    "IsoAgeFn",
    "_activity_level",
    "active_engagement_after_danmaku_interval_seconds",
    "active_engagement_idle_grace_seconds",
    "active_engagement_min_interval_seconds",
    "active_engagement_status",
    "age_sec",
    "idle_hosting_min_interval_seconds",
    "idle_hosting_status",
    "idle_hosting_wait_remaining_for_quiet_state",
    "iso_age_sec",
    "last_output_age_sec",
    "last_viewer_activity_age_sec",
    "live_director_status",
    "live_state_summary",
    "live_state_threshold_seconds",
    "live_status_summary",
    "recent_live_danmaku_event_age_sec",
    "recent_live_danmaku_output_age_sec",
    "solo_test_readiness",
    "solo_warmup_timeout_seconds",
    "speech_explanation",
]
