"""Compatibility facade for NEKO Live output policy."""

from __future__ import annotations

from .live_reply_contracts import (
    DEFAULT_DISPATCH_REPLY_CHARS,
    DISPATCH_REPLY_CHAR_LIMITS,
    HOST_MODULES,
    REPLY_TARGET_CHARS,
    ROUTE_CEILINGS,
    ROUTE_NOTES,
    build_reply_metadata,
    coerce_live_reply_limit,
    is_live_reply_metadata,
    max_reply_chars_for_module,
    reply_limit_from_metadata,
    response_module,
)
from .live_reply_fallbacks import (
    ACTIVE_FALLBACK_REPLIES,
    BLAND_DANMAKU_REPLY_TERMS,
    BLAND_FALLBACK_REPLIES,
    DANGLING_CHOICE_RE,
    DEFAULT_FALLBACK_REPLIES,
    FORBIDDEN_OUTPUT_TERMS,
    HOST_AUDIENCE_PROMPT_TOKENS,
    HOST_FALLBACK_REPLIES,
    LOW_CONFIDENCE_HOST_TERMS,
    OPAQUE_QUESTION_MARKERS,
    OPAQUE_TOPIC_DRIFT_TERMS,
    RECENT_REPLY_AVOIDANCE_SIZE,
    VOICE_ECHO_NORMALIZE_RE,
    choose_fallback_reply,
    host_prompt_signal_count,
    looks_like_bland_danmaku_reply,
    looks_like_opaque_topic_drift,
    needs_quality_fallback,
    normalize_text,
    safe_fallback_reply,
    sentence_budget,
    trim_dangling_choice,
)
from .live_reply_instructions import (
    coerce_recent_reply_values,
    merge_metadata_from_callbacks,
    render_contract_instruction,
    render_recent_reply_avoidance,
)
from .live_reply_shape import first_sentences, shape_reply_text

__all__ = [name for name in globals() if not name.startswith("__")]
