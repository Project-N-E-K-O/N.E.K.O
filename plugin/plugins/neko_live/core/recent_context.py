"""Compatibility facade for compact live-context memory helpers."""

from __future__ import annotations

from .recent_context_builders import (
    build_recent_interaction_context,
    build_recent_room_danmaku_context,
    build_viewer_session_context,
)
from .recent_context_lines import (
    active_engagement_context_line as _active_engagement_context_line,
    idle_hosting_context_line as _idle_hosting_context_line,
    viewer_event_context_line as _viewer_event_context_line,
)
from .recent_context_routes import (
    event_signal_from_result,
    route_from_result,
    signal_route_for_event_type,
)
from .recent_context_text import compact_context_text
from .recent_output_families import (
    recent_spent_output_families,
    spent_output_families,
    spent_output_text,
)


__all__ = [
    "_active_engagement_context_line",
    "_idle_hosting_context_line",
    "_viewer_event_context_line",
    "build_recent_interaction_context",
    "build_recent_room_danmaku_context",
    "build_viewer_session_context",
    "compact_context_text",
    "event_signal_from_result",
    "recent_spent_output_families",
    "route_from_result",
    "signal_route_for_event_type",
    "spent_output_families",
    "spent_output_text",
]
