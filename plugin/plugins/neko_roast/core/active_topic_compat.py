"""Backward-compatible active-topic helper facade for the runtime API."""

from __future__ import annotations

from collections import deque
from typing import Any

from . import (
    active_topic_pack,
    active_topic_rules,
    active_topic_selection,
    active_topic_shapes,
    active_topic_sources,
)
from .live_content import active_engagement_fallback_topic_candidates


class ActiveTopicCompatibilityMixin:
    def choose_candidate(
        self,
        candidates: list[dict[str, Any]],
        *,
        avoid_recent_fun_axis: bool,
        avoid_recent_family: bool,
        allow_similar_title: bool = False,
    ) -> dict[str, Any] | None:
        return active_topic_selection.choose_candidate(
            self,
            candidates,
            avoid_recent_fun_axis=avoid_recent_fun_axis,
            avoid_recent_family=avoid_recent_family,
            allow_similar_title=allow_similar_title,
        )

    @staticmethod
    def fallback_topic_candidates() -> list[dict[str, Any]]:
        return active_engagement_fallback_topic_candidates()

    def runtime_fallback_topic_candidates(self) -> list[dict[str, Any]]:
        provider = getattr(
            self._runtime,
            "_active_engagement_fallback_topic_candidates",
            None,
        )
        if callable(provider):
            return provider()
        return self.fallback_topic_candidates()

    @staticmethod
    def topic_pack(material: dict[str, Any] | None) -> str:
        return active_topic_pack.active_topic_pack(material)

    async def topic_candidates(self) -> list[dict[str, Any]]:
        return await active_topic_sources.topic_candidates(self)

    async def bili_trending_topic_candidates(self) -> list[dict[str, Any]]:
        return await active_topic_sources.bili_trending_topic_candidates(self)

    def recent_danmaku_topic_candidates(self) -> list[dict[str, Any]]:
        return active_topic_sources.recent_danmaku_topic_candidates(self)

    @staticmethod
    def is_meaningful_topic_text(text: str) -> bool:
        return active_topic_rules._is_meaningful_active_topic_text(text)

    @staticmethod
    def topic_filter_reason(text: str) -> str:
        return active_topic_rules._active_topic_filter_reason(text)

    @staticmethod
    def is_direct_neko_request_or_ack(dense_lowered: str) -> bool:
        return active_topic_rules._is_direct_neko_request_or_ack(dense_lowered)

    @staticmethod
    def is_untargeted_request_or_reaction(dense_lowered: str) -> bool:
        return active_topic_rules._is_untargeted_request_or_reaction(dense_lowered)

    @staticmethod
    def is_untargeted_request(dense_lowered: str) -> bool:
        return active_topic_rules._is_untargeted_request(dense_lowered)

    @staticmethod
    def is_reaction_only(dense_lowered: str) -> bool:
        return active_topic_rules._is_reaction_only(dense_lowered)

    @staticmethod
    def is_live_test_or_runtime_feedback(dense_lowered: str) -> bool:
        return active_topic_rules._is_live_test_or_runtime_feedback(dense_lowered)

    def next_shape(self) -> str:
        shape, next_index = active_topic_shapes.next_active_topic_shape(
            self._active_engagement_shape_index
        )
        self._active_engagement_shape_index = next_index
        return shape

    def guarded_shape(self, shape: str) -> str:
        shape, reason = active_topic_shapes.guarded_active_topic_shape(
            shape,
            self._active_engagement_recent_shapes,
        )
        self._active_engagement_shape_guard_reason = reason
        return shape

    @staticmethod
    def has_streak(values: deque[str], value: str, count: int) -> bool:
        return active_topic_rules._has_active_engagement_streak(values, value, count)

    @staticmethod
    def is_similar_title(title: str, recent_titles: deque[str]) -> bool:
        return active_topic_rules._is_similar_active_topic_title(title, recent_titles)

    @staticmethod
    def host_material_family(material: dict[str, Any] | None) -> str:
        return active_topic_rules._host_material_family(material)

    @staticmethod
    def material_profile(title: str) -> dict[str, str]:
        return active_topic_rules._active_topic_material_profile(title)

    @staticmethod
    def is_viewer_to_viewer_mention_text(text: str) -> bool:
        return active_topic_rules._is_viewer_to_viewer_mention_text(text)

    @staticmethod
    def is_neko_mention_target(name: str, lowered_aliases: set[str]) -> bool:
        return active_topic_rules._is_neko_mention_target(name, lowered_aliases)

    @staticmethod
    def hook_text(shape: str, title: str) -> str:
        return active_topic_rules._active_engagement_hook_text(shape, title)

    @staticmethod
    def pattern_text(shape: str) -> str:
        return active_topic_rules._active_engagement_pattern_text(shape)

    @staticmethod
    def hint_text(shape: str) -> str:
        return active_topic_rules._active_engagement_hint_text(shape)

    @staticmethod
    def intent_text(shape: str) -> str:
        return active_topic_rules._active_engagement_intent_text(shape)

    @staticmethod
    def fun_axis_text(shape: str) -> str:
        return active_topic_rules._active_engagement_fun_axis_text(shape)

    @staticmethod
    def reply_affordance_text(shape: str) -> str:
        return active_topic_rules._active_engagement_reply_affordance_text(shape)
