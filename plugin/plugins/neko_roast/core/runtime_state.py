"""Mutable runtime state initialization for NEKO Live."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections import deque
from typing import Any


def initialize_runtime_state(runtime: Any) -> None:
    """Initialize runtime-local mutable state and scheduler caches."""
    runtime.recent_results = deque(maxlen=runtime.config.recent_limit)
    runtime.recent_sandbox_results = deque(maxlen=runtime.config.recent_limit)
    runtime.runtime_timeline = deque(maxlen=200)
    runtime.live_connection_state = "disconnected"
    runtime.live_connection_auth_mode = "unknown"
    runtime.live_room_context = {}
    runtime.instructions_injected = False
    runtime.instructions_signature = ""
    runtime.developer_instructions_injected = False
    runtime._last_live_danmaku_seen_at = 0.0
    runtime._last_live_danmaku_seen_type = ""
    runtime._config_last_persist_at = 0.0
    runtime._config_last_error = ""
    runtime._config_lock = None
    runtime._config_revision = 0
    runtime._stopping = False
    runtime._accepting_live_events = False
    runtime._timeline_salt = secrets.token_bytes(32)

    runtime._idle_hosting_task: asyncio.Task[Any] | None = None
    runtime._idle_hosting_last_attempt_at = 0.0
    runtime._idle_hosting_consecutive_failures = 0
    runtime._idle_hosting_sleep = asyncio.sleep
    runtime._idle_hosting_now = time.monotonic
    runtime._live_state_now = time.monotonic
    runtime._live_listener_started_at = 0.0
    runtime._idle_hosting_recent_beat_keys = deque(maxlen=10)
    runtime._idle_hosting_recent_beat_axes = deque(maxlen=5)
    runtime._idle_hosting_recent_beat_titles = deque(maxlen=10)
    runtime._idle_hosting_recent_reply_affordances = deque(maxlen=5)
    runtime._idle_hosting_beat_index = 0
    runtime._recent_host_material_families = deque(maxlen=12)

    runtime._active_engagement_last_attempt_at = 0.0
    runtime._active_engagement_now = time.monotonic
    runtime._active_engagement_topic_fetcher = None
    runtime._active_engagement_topic_cache = []
    runtime._active_engagement_topic_cache_at = 0.0
    runtime._active_engagement_recent_topic_keys = deque(maxlen=12)
    runtime._active_engagement_recent_topic_titles = deque(maxlen=8)
    runtime._active_engagement_recent_topic_sources = deque(maxlen=6)
    runtime._active_engagement_recent_fun_axes = deque(maxlen=6)
    runtime._active_engagement_recent_shapes = deque(maxlen=6)
    runtime._active_engagement_recent_intents = deque(maxlen=6)
    runtime._active_engagement_recent_reply_affordances = deque(maxlen=6)
    runtime._active_engagement_recent_topic_skip_reason = ""
    runtime._active_engagement_shape_guard_reason = ""
    runtime._active_engagement_shape_index = 0
