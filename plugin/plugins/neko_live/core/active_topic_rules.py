"""Compatibility facade for active-engagement topic rules.

The implementation is split by concern so reviewers can inspect filtering,
safety, mention parsing, rotation, material profiling, and copy separately.
This module keeps the older private helper names stable for existing tests and
runtime compatibility delegates.
"""

from __future__ import annotations

import importlib
from typing import Any

from . import (
    active_engagement_copy,
    active_topic_core_fallbacks,
    active_topic_filters,
    active_topic_meaning,
    active_topic_mentions,
    active_topic_safety,
)


def _optional_split_module(name: str) -> Any | None:
    qualified_name = f"{__package__}.{name}"
    try:
        return importlib.import_module(qualified_name)
    except ModuleNotFoundError as exc:
        if exc.name != qualified_name:
            raise
        return None


_active_topic_materials = _optional_split_module("active_topic_materials")
_active_topic_rotation = _optional_split_module("active_topic_rotation")


_is_meaningful_active_topic_text = active_topic_meaning.is_meaningful_active_topic_text
_active_topic_filter_reason = active_topic_meaning.active_topic_filter_reason

_is_low_confidence_active_topic_text = (
    active_topic_safety.is_low_confidence_active_topic_text
)
_is_clean_live_material_text = active_topic_safety.is_clean_live_material_text
_is_clean_live_material = active_topic_safety.is_clean_live_material

_is_direct_neko_request_or_ack = active_topic_filters.is_direct_neko_request_or_ack
_is_untargeted_request_or_reaction = (
    active_topic_filters.is_untargeted_request_or_reaction
)
_is_untargeted_request = active_topic_filters.is_untargeted_request
_is_reaction_only = active_topic_filters.is_reaction_only
_is_live_test_or_runtime_feedback = (
    active_topic_filters.is_live_test_or_runtime_feedback
)

_is_viewer_to_viewer_mention_text = (
    active_topic_mentions.is_viewer_to_viewer_mention_text
)
_is_neko_mention_target = active_topic_mentions.is_neko_mention_target

_has_active_engagement_streak = (
    _active_topic_rotation.has_active_engagement_streak
    if _active_topic_rotation is not None
    else active_topic_core_fallbacks.has_active_engagement_streak
)
_normalize_active_topic_title = (
    _active_topic_rotation.normalize_active_topic_title
    if _active_topic_rotation is not None
    else active_topic_core_fallbacks.normalize_active_topic_title
)
_is_similar_active_topic_title = (
    _active_topic_rotation.is_similar_active_topic_title
    if _active_topic_rotation is not None
    else active_topic_core_fallbacks.is_similar_active_topic_title
)

_host_material_family = (
    _active_topic_materials.host_material_family
    if _active_topic_materials is not None
    else active_topic_core_fallbacks.host_material_family
)
_active_topic_material_profile = (
    _active_topic_materials.active_topic_material_profile
    if _active_topic_materials is not None
    else active_topic_core_fallbacks.active_topic_material_profile
)

_active_engagement_hook_text = active_engagement_copy.active_engagement_hook_text
_active_engagement_pattern_text = active_engagement_copy.active_engagement_pattern_text
_active_engagement_hint_text = active_engagement_copy.active_engagement_hint_text
_active_engagement_intent_text = active_engagement_copy.active_engagement_intent_text
_active_engagement_fun_axis_text = (
    active_engagement_copy.active_engagement_fun_axis_text
)
_active_engagement_reply_affordance_text = (
    active_engagement_copy.active_engagement_reply_affordance_text
)
