"""Compatibility facade for plugin-local NEKO Live output policy helpers.

The implementation is split by concern: quality fallback, final text shaping,
recent-output memory, and prompt-contract rendering. This module keeps the
older import path stable for prompts, dry-run summaries, monitors, and tests.
"""

from __future__ import annotations

from .live_output_contract_prompt import ROUTE_NOTES, merge_metadata_from_callbacks, render_contract_instruction
from .live_output_memory import (
    RECENT_REPLY_AVOIDANCE_SIZE,
    coerce_recent_reply_values,
    render_recent_reply_avoidance,
)
from .live_output_quality import (
    ACTIVE_FALLBACK_REPLIES,
    BLAND_DANMAKU_REPLY_TERMS,
    BLAND_FALLBACK_REPLIES,
    DEFAULT_FALLBACK_REPLIES,
    FORBIDDEN_OUTPUT_TERMS,
    HOST_AUDIENCE_PROMPT_TOKENS,
    HOST_FALLBACK_REPLIES,
    LOW_CONFIDENCE_HOST_TERMS,
    OPAQUE_QUESTION_MARKERS,
    OPAQUE_TOPIC_DRIFT_TERMS,
    choose_fallback_reply,
    host_prompt_signal_count,
    looks_like_bland_danmaku_reply,
    looks_like_opaque_topic_drift,
    needs_quality_fallback,
    normalize_text,
    safe_fallback_reply,
)
from .live_output_shape import (
    DANGLING_CHOICE_RE,
    first_sentences,
    sentence_budget,
    shape_reply_text,
    trim_dangling_choice,
)

__all__ = (
    "ROUTE_NOTES",
    "merge_metadata_from_callbacks",
    "render_contract_instruction",
    "RECENT_REPLY_AVOIDANCE_SIZE",
    "coerce_recent_reply_values",
    "render_recent_reply_avoidance",
    "ACTIVE_FALLBACK_REPLIES",
    "BLAND_DANMAKU_REPLY_TERMS",
    "BLAND_FALLBACK_REPLIES",
    "DEFAULT_FALLBACK_REPLIES",
    "FORBIDDEN_OUTPUT_TERMS",
    "HOST_AUDIENCE_PROMPT_TOKENS",
    "HOST_FALLBACK_REPLIES",
    "LOW_CONFIDENCE_HOST_TERMS",
    "OPAQUE_QUESTION_MARKERS",
    "OPAQUE_TOPIC_DRIFT_TERMS",
    "choose_fallback_reply",
    "host_prompt_signal_count",
    "looks_like_bland_danmaku_reply",
    "looks_like_opaque_topic_drift",
    "needs_quality_fallback",
    "normalize_text",
    "safe_fallback_reply",
    "DANGLING_CHOICE_RE",
    "first_sentences",
    "sentence_budget",
    "shape_reply_text",
    "trim_dangling_choice",
)
