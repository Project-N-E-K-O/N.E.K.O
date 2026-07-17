"""Backward-compatible prompt helper facade."""

from __future__ import annotations

from ._prompt_context_blocks import (
    RECENT_CONTEXT_DEFAULT_LIMIT,
    RECENT_CONTEXT_LINE_LIMIT,
    ROOM_CONTEXT_DEFAULT_LIMIT,
    ROOM_CONTEXT_LINE_LIMIT,
    VIEWER_CONTEXT_LINE_LIMIT,
    live_events_context_block,
    meme_knowledge_context_block,
    recent_context_block,
    room_danmaku_context_block,
    viewer_preference_context_block,
    viewer_session_context_block,
)
from ._prompt_context_compaction import (
    NEKO_ALREADY_SAID_MARKER,
    REPLY_PATH_MARKER,
    SPENT_OUTPUT_FAMILY_MARKER,
    compact_context_line as _compact_context_line,
    compact_plain as _compact_plain,
    compact_preserving_reply_path as _compact_preserving_reply_path,
    compact_preserving_spent_output_family as _compact_preserving_spent_output_family,
)
from ._prompt_rules import (
    HOST_REPLY_CONTRACT,
    SHORT_REPLY_CONTRACT,
    anti_repeat_rules,
    live_output_quality_rules,
    short_reply_rules,
    sustained_charm_rules,
)


__all__ = [
    "HOST_REPLY_CONTRACT",
    "NEKO_ALREADY_SAID_MARKER",
    "RECENT_CONTEXT_DEFAULT_LIMIT",
    "RECENT_CONTEXT_LINE_LIMIT",
    "REPLY_PATH_MARKER",
    "ROOM_CONTEXT_DEFAULT_LIMIT",
    "ROOM_CONTEXT_LINE_LIMIT",
    "SHORT_REPLY_CONTRACT",
    "SPENT_OUTPUT_FAMILY_MARKER",
    "VIEWER_CONTEXT_LINE_LIMIT",
    "_compact_context_line",
    "_compact_plain",
    "_compact_preserving_reply_path",
    "_compact_preserving_spent_output_family",
    "anti_repeat_rules",
    "live_events_context_block",
    "live_output_quality_rules",
    "meme_knowledge_context_block",
    "recent_context_block",
    "room_danmaku_context_block",
    "short_reply_rules",
    "sustained_charm_rules",
    "viewer_preference_context_block",
    "viewer_session_context_block",
]
