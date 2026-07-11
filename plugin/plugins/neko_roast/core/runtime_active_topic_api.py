"""Runtime compatibility API for active topic selection helpers."""

from __future__ import annotations

from collections import deque
from typing import Any

from .active_topic_selector import ActiveTopicSelector
from .live_content import active_engagement_fallback_topic_candidates


class RuntimeActiveTopicApiMixin:
    async def _select_active_engagement_topic(self) -> dict[str, Any]:
        return await self.active_topic_selector.select_topic()

    def _choose_active_engagement_candidate(
        self,
        candidates: list[dict[str, Any]],
        *,
        avoid_recent_fun_axis: bool,
        avoid_recent_family: bool,
        allow_similar_title: bool = False,
    ) -> dict[str, Any] | None:
        return self.active_topic_selector.choose_candidate(
            candidates,
            avoid_recent_fun_axis=avoid_recent_fun_axis,
            avoid_recent_family=avoid_recent_family,
            allow_similar_title=allow_similar_title,
        )

    @staticmethod
    def _active_engagement_fallback_topic_candidates() -> list[dict[str, Any]]:
        return active_engagement_fallback_topic_candidates()

    @staticmethod
    def _active_engagement_topic_pack(material: dict[str, Any] | None) -> str:
        return ActiveTopicSelector.topic_pack(material)

    async def _active_engagement_topic_candidates(self) -> list[dict[str, Any]]:
        return await self.active_topic_selector.topic_candidates()

    async def _bili_trending_topic_candidates(self) -> list[dict[str, Any]]:
        return await self.active_topic_selector.bili_trending_topic_candidates()

    def _recent_danmaku_topic_candidates(self) -> list[dict[str, Any]]:
        return self.active_topic_selector.recent_danmaku_topic_candidates()

    def _next_active_engagement_shape(self) -> str:
        return self.active_topic_selector.next_shape()

    def _active_engagement_guarded_shape(self, shape: str) -> str:
        return self.active_topic_selector.guarded_shape(shape)

    @staticmethod
    def _has_active_engagement_streak(values: deque[str], value: str, count: int) -> bool:
        return ActiveTopicSelector.has_streak(values, value, count)

    @staticmethod
    def _is_similar_active_topic_title(title: str, recent_titles: deque[str]) -> bool:
        return ActiveTopicSelector.is_similar_title(title, recent_titles)
