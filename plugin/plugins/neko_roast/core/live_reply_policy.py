"""Compatibility facade for plugin-owned NEKO Live output policy.

Keep this import path for existing plugin-side callers and tests. The
implementation is local to ``plugin.plugins.neko_roast`` so the plugin no
longer depends on host/core NEKO Live special cases.
"""

from __future__ import annotations

from .live_output_policy import (  # noqa: F401
    coerce_recent_reply_values,
    first_sentences,
    merge_metadata_from_callbacks,
    needs_quality_fallback,
    normalize_text,
    render_contract_instruction,
    render_recent_reply_avoidance,
    shape_reply_text,
    trim_dangling_choice,
)
from .live_reply_contract import (  # noqa: F401
    DEFAULT_DISPATCH_REPLY_CHARS,
    DANMAKU_ROOM_BRIDGE_REPLY_CHARS,
    DISPATCH_REPLY_CHAR_LIMITS,
    HOST_MODULES,
    LiveReplyContract,
    REPLY_CONTRACT_NAME,
    REPLY_TARGET_CHARS,
    ROUTE_CEILINGS,
    ROOM_BRIDGE_REPLY_MODE,
    build_live_reply_contract,
    build_reply_metadata,
    coerce_live_reply_limit,
    is_longer_danmaku_reply,
    is_live_reply_metadata,
    is_room_bridge_danmaku_reply,
    max_reply_chars_for_module,
    reply_limit_from_metadata,
    response_module,
)

__all__ = (
    "coerce_recent_reply_values",
    "first_sentences",
    "merge_metadata_from_callbacks",
    "needs_quality_fallback",
    "normalize_text",
    "render_contract_instruction",
    "render_recent_reply_avoidance",
    "shape_reply_text",
    "trim_dangling_choice",
    "DEFAULT_DISPATCH_REPLY_CHARS",
    "DANMAKU_ROOM_BRIDGE_REPLY_CHARS",
    "DISPATCH_REPLY_CHAR_LIMITS",
    "HOST_MODULES",
    "LiveReplyContract",
    "REPLY_CONTRACT_NAME",
    "REPLY_TARGET_CHARS",
    "ROUTE_CEILINGS",
    "ROOM_BRIDGE_REPLY_MODE",
    "build_live_reply_contract",
    "build_reply_metadata",
    "coerce_live_reply_limit",
    "is_longer_danmaku_reply",
    "is_live_reply_metadata",
    "is_room_bridge_danmaku_reply",
    "max_reply_chars_for_module",
    "reply_limit_from_metadata",
    "response_module",
)
