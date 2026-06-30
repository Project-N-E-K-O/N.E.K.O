"""Compatibility facade for active-engagement pure rules.

The implementation is split by responsibility so the scheduler can depend on a
stable import path while reviewers can inspect text filters, material handling,
and prompt-shape wording independently.
"""

from __future__ import annotations

from .active_engagement_shapes import (
    _active_engagement_fun_axis_text,
    _active_engagement_hint_text,
    _active_engagement_hook_text,
    _active_engagement_intent_text,
    _active_engagement_pattern_text,
    _active_engagement_reply_affordance_text,
)
from .active_topic_filters import (
    _active_topic_filter_reason,
    _is_clean_live_material,
    _is_clean_live_material_text,
    _is_direct_neko_request_or_ack,
    _is_live_test_or_runtime_feedback,
    _is_low_confidence_active_topic_text,
    _is_meaningful_active_topic_text,
    _is_neko_mention_target,
    _is_reaction_only,
    _is_untargeted_request,
    _is_untargeted_request_or_reaction,
    _is_viewer_to_viewer_mention_text,
)
from .active_topic_materials import (
    _active_topic_material_profile,
    _has_active_engagement_streak,
    _host_material_family,
    _is_similar_active_topic_title,
    _normalize_active_topic_title,
)

__all__ = [
    "_active_engagement_fun_axis_text",
    "_active_engagement_hint_text",
    "_active_engagement_hook_text",
    "_active_engagement_intent_text",
    "_active_engagement_pattern_text",
    "_active_engagement_reply_affordance_text",
    "_active_topic_filter_reason",
    "_active_topic_material_profile",
    "_has_active_engagement_streak",
    "_host_material_family",
    "_is_clean_live_material",
    "_is_clean_live_material_text",
    "_is_direct_neko_request_or_ack",
    "_is_live_test_or_runtime_feedback",
    "_is_low_confidence_active_topic_text",
    "_is_meaningful_active_topic_text",
    "_is_neko_mention_target",
    "_is_reaction_only",
    "_is_similar_active_topic_title",
    "_is_untargeted_request",
    "_is_untargeted_request_or_reaction",
    "_is_viewer_to_viewer_mention_text",
    "_normalize_active_topic_title",
]
