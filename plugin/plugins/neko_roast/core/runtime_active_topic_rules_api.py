"""Runtime compatibility API for active topic rule helpers."""

from __future__ import annotations

from typing import Any

from . import active_topic_rules


class RuntimeActiveTopicRulesApiMixin:
    @staticmethod
    def _is_meaningful_active_topic_text(text: str) -> bool:
        return active_topic_rules._is_meaningful_active_topic_text(text)

    @staticmethod
    def _active_topic_filter_reason(text: str) -> str:
        return active_topic_rules._active_topic_filter_reason(text)

    @staticmethod
    def _is_direct_neko_request_or_ack(dense_lowered: str) -> bool:
        return active_topic_rules._is_direct_neko_request_or_ack(dense_lowered)

    @staticmethod
    def _is_untargeted_request_or_reaction(dense_lowered: str) -> bool:
        return active_topic_rules._is_untargeted_request_or_reaction(dense_lowered)

    @staticmethod
    def _is_untargeted_request(dense_lowered: str) -> bool:
        return active_topic_rules._is_untargeted_request(dense_lowered)

    @staticmethod
    def _is_reaction_only(dense_lowered: str) -> bool:
        return active_topic_rules._is_reaction_only(dense_lowered)

    @staticmethod
    def _is_live_test_or_runtime_feedback(dense_lowered: str) -> bool:
        return active_topic_rules._is_live_test_or_runtime_feedback(dense_lowered)

    @staticmethod
    def _host_material_family(material: dict[str, Any] | None) -> str:
        return active_topic_rules._host_material_family(material)

    @staticmethod
    def _active_topic_material_profile(title: str) -> dict[str, str]:
        return active_topic_rules._active_topic_material_profile(title)

    @staticmethod
    def _is_viewer_to_viewer_mention_text(text: str) -> bool:
        return active_topic_rules._is_viewer_to_viewer_mention_text(text)

    @staticmethod
    def _is_neko_mention_target(name: str, lowered_aliases: set[str]) -> bool:
        return active_topic_rules._is_neko_mention_target(name, lowered_aliases)

    @staticmethod
    def _active_engagement_hook_text(shape: str, title: str) -> str:
        return active_topic_rules._active_engagement_hook_text(shape, title)

    @staticmethod
    def _active_engagement_pattern_text(shape: str) -> str:
        return active_topic_rules._active_engagement_pattern_text(shape)

    @staticmethod
    def _active_engagement_hint_text(shape: str) -> str:
        return active_topic_rules._active_engagement_hint_text(shape)

    @staticmethod
    def _active_engagement_intent_text(shape: str) -> str:
        return active_topic_rules._active_engagement_intent_text(shape)

    @staticmethod
    def _active_engagement_fun_axis_text(shape: str) -> str:
        return active_topic_rules._active_engagement_fun_axis_text(shape)

    @staticmethod
    def _active_engagement_reply_affordance_text(shape: str) -> str:
        return active_topic_rules._active_engagement_reply_affordance_text(shape)
