"""Compatibility facade for NEKO Live live-status calculations."""

from __future__ import annotations

from . import (
    live_status_active,
    live_status_core,
    live_status_director,
    live_status_idle,
    live_status_readiness,
    live_status_timing,
)


active_engagement_min_interval_seconds = (
    live_status_timing.active_engagement_min_interval_seconds
)
active_engagement_after_danmaku_interval_seconds = (
    live_status_timing.active_engagement_after_danmaku_interval_seconds
)
active_engagement_idle_grace_seconds = (
    live_status_timing.active_engagement_idle_grace_seconds
)
idle_hosting_min_interval_seconds = live_status_timing.idle_hosting_min_interval_seconds
solo_warmup_timeout_seconds = live_status_timing.solo_warmup_timeout_seconds
live_state_threshold_seconds = live_status_timing.live_state_threshold_seconds
age_sec = live_status_timing.age_sec
iso_age_sec = live_status_timing.iso_age_sec
IsoAgeFn = live_status_timing.IsoAgeFn
recent_live_danmaku_output_age_sec = (
    live_status_timing.recent_live_danmaku_output_age_sec
)
recent_live_danmaku_event_age_sec = live_status_timing.recent_live_danmaku_event_age_sec
recent_hosting_output_age_sec = live_status_timing.recent_hosting_output_age_sec
last_viewer_activity_age_sec = live_status_timing.last_viewer_activity_age_sec
last_output_age_sec = live_status_timing.last_output_age_sec


live_status_summary = live_status_core.live_status_summary
live_state_summary = live_status_core.live_state_summary
idle_hosting_status = live_status_idle.idle_hosting_status
idle_hosting_wait_remaining_for_quiet_state = (
    live_status_idle.idle_hosting_wait_remaining_for_quiet_state
)
active_engagement_status = live_status_active.active_engagement_status
live_director_status = live_status_director.live_director_status
solo_test_readiness = live_status_readiness.solo_test_readiness
speech_explanation = live_status_readiness.speech_explanation
