"""Stateful active-engagement topic selection for solo stream hosting."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from . import active_topic_rules
from .live_active_content import active_engagement_fallback_topic_candidates


class ActiveTopicSelector:
    """Selects and rotates active-engagement material for the runtime.

    The selector owns selection behavior while the runtime still owns the
    mutable deques/caches for backward-compatible tests and dashboard state.
    """

    def __init__(self, runtime: Any) -> None:
        object.__setattr__(self, "_runtime", runtime)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runtime, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_runtime":
            object.__setattr__(self, name, value)
            return
        setattr(self._runtime, name, value)

    async def select_topic(self) -> dict[str, Any]:
        candidates = await self.topic_candidates()
        fallback_candidates = self._runtime._active_engagement_fallback_topic_candidates()
        fallback = fallback_candidates[0]
        shape = self.next_shape()
        chosen = fallback
        exhausted_cached_topics = bool(candidates)
        candidate = self.choose_candidate(candidates, avoid_recent_fun_axis=True, avoid_recent_family=True)
        if candidate is None:
            candidate = self.choose_candidate(candidates, avoid_recent_fun_axis=False, avoid_recent_family=True)
        if candidate is None:
            candidate = self.choose_candidate(candidates, avoid_recent_fun_axis=True, avoid_recent_family=False)
        if candidate is not None:
            chosen = candidate
            exhausted_cached_topics = False
        if exhausted_cached_topics:
            self._active_engagement_topic_cache = []
            self._active_engagement_topic_cache_at = 0.0
            refreshed_candidates = await self.topic_candidates()
            candidate = self.choose_candidate(refreshed_candidates, avoid_recent_fun_axis=True, avoid_recent_family=True)
            if candidate is None:
                candidate = self.choose_candidate(refreshed_candidates, avoid_recent_fun_axis=False, avoid_recent_family=True)
            if candidate is None:
                candidate = self.choose_candidate(refreshed_candidates, avoid_recent_fun_axis=True, avoid_recent_family=False)
            if candidate is not None:
                chosen = candidate
                exhausted_cached_topics = False
        if exhausted_cached_topics:
            chosen = (
                self.choose_candidate(fallback_candidates, avoid_recent_fun_axis=True, avoid_recent_family=True)
                or self.choose_candidate(fallback_candidates, avoid_recent_fun_axis=False, avoid_recent_family=True)
                or self.choose_candidate(fallback_candidates, avoid_recent_fun_axis=True, avoid_recent_family=False)
                or self.choose_candidate(
                    fallback_candidates,
                    avoid_recent_fun_axis=False,
                    avoid_recent_family=False,
                    allow_similar_title=True,
                )
                or fallback
            )
        elif chosen is fallback:
            chosen = (
                self.choose_candidate(fallback_candidates, avoid_recent_fun_axis=True, avoid_recent_family=True)
                or self.choose_candidate(fallback_candidates, avoid_recent_fun_axis=False, avoid_recent_family=True)
                or self.choose_candidate(fallback_candidates, avoid_recent_fun_axis=True, avoid_recent_family=False)
                or self.choose_candidate(
                    fallback_candidates,
                    avoid_recent_fun_axis=False,
                    avoid_recent_family=False,
                    allow_similar_title=True,
                )
                or fallback
            )
        preferred_shape = str(chosen.get("preferred_shape") or shape).strip() or shape
        shape = self.guarded_shape(preferred_shape)
        key = str(chosen.get("key") or chosen.get("title") or fallback["key"]).strip()
        self._active_engagement_recent_topic_keys.append(key)
        title = str(chosen.get("title") or fallback["title"]).strip()
        if title:
            self._active_engagement_recent_topic_titles.append(title)
        intent = self.intent_text(shape)
        hint = str(chosen.get("hint") or fallback["hint"]).strip()
        if shape != preferred_shape:
            hint = self.hint_text(shape)
        topic = {
            "source": str(chosen.get("source") or "fallback"),
            "shape": shape,
            "key": key,
            "title": title,
            "family": self.host_material_family(chosen),
            "fun_axis": str(chosen.get("fun_axis") or "").strip() or self.fun_axis_text(shape),
            "hook": self.hook_text(shape, title),
            "pattern": self.pattern_text(shape),
            "intent": intent,
            "live_column": str(chosen.get("live_column") or "").strip(),
            "topic_pack": self.topic_pack(chosen),
            "reply_affordance": str(chosen.get("reply_affordance") or "").strip()
            or self.reply_affordance_text(shape),
            "hint": hint,
        }
        self._active_engagement_recent_topic_sources.append(str(topic["source"]))
        if topic["fun_axis"]:
            self._active_engagement_recent_fun_axes.append(str(topic["fun_axis"]))
        if topic["family"]:
            self._recent_host_material_families.append(str(topic["family"]))
        self._active_engagement_recent_shapes.append(shape)
        self._active_engagement_recent_intents.append(intent)
        if topic["reply_affordance"]:
            self._active_engagement_recent_reply_affordances.append(str(topic["reply_affordance"]))
        skip_reason = str(self._active_engagement_recent_topic_skip_reason or "").strip()
        if skip_reason:
            topic["recent_topic_skip_reason"] = skip_reason
        shape_guard_reason = str(self._active_engagement_shape_guard_reason or "").strip()
        if shape_guard_reason:
            topic["shape_guard_reason"] = shape_guard_reason
        return topic

    def choose_candidate(
        self,
        candidates: list[dict[str, Any]],
        *,
        avoid_recent_fun_axis: bool,
        avoid_recent_family: bool,
        allow_similar_title: bool = False,
    ) -> dict[str, Any] | None:
        recent_spent_families = self._runtime._recent_spent_output_families() if avoid_recent_family else set()
        for candidate in candidates:
            if not active_topic_rules._is_clean_live_material(candidate):
                if not self._active_engagement_recent_topic_skip_reason:
                    self._active_engagement_recent_topic_skip_reason = "unclean_topic_material"
                continue
            key = str(candidate.get("key") or candidate.get("title") or "").strip()
            if not key or key in self._active_engagement_recent_topic_keys:
                continue
            title = str(candidate.get("title") or "").strip()
            if (
                not allow_similar_title
                and title
                and self.is_similar_title(title, self._active_engagement_recent_topic_titles)
            ):
                if not self._active_engagement_recent_topic_skip_reason:
                    self._active_engagement_recent_topic_skip_reason = "similar_topic_title"
                continue
            axis = str(candidate.get("fun_axis") or "").strip()
            if avoid_recent_fun_axis and axis and axis in self._active_engagement_recent_fun_axes:
                continue
            reply_affordance = str(candidate.get("reply_affordance") or "").strip()
            if (
                avoid_recent_fun_axis
                and reply_affordance
                and reply_affordance in self._active_engagement_recent_reply_affordances
            ):
                continue
            family = self.host_material_family(candidate)
            if avoid_recent_family and family:
                if family in self._recent_host_material_families:
                    if not self._active_engagement_recent_topic_skip_reason:
                        self._active_engagement_recent_topic_skip_reason = "recent_host_family"
                    continue
                if family in recent_spent_families:
                    if not self._active_engagement_recent_topic_skip_reason:
                        self._active_engagement_recent_topic_skip_reason = "recent_spent_output_family"
                    continue
            return candidate
        return None

    @staticmethod
    def fallback_topic_candidates() -> list[dict[str, Any]]:
        return active_engagement_fallback_topic_candidates()

    @staticmethod
    def topic_pack(material: dict[str, Any] | None) -> str:
        if not isinstance(material, dict):
            return ""
        explicit = str(material.get("topic_pack") or "").strip()
        if explicit:
            return explicit
        live_column = str(material.get("live_column") or "").lower()
        family = active_topic_rules._host_material_family(material)
        combined = " ".join(
            str(material.get(field) or "").lower()
            for field in ("key", "title", "fun_axis", "preferred_shape", "shape", "reply_affordance")
        )
        if family in {"tease", "host_self_test"} or any(marker in live_column for marker in ("verdict", "court", "award", "score")):
            return "neko_verdict"
        if family == "short_callback" or any(marker in live_column for marker in ("callback", "password", "command")):
            return "viewer_callback"
        if family == "choice_vote" or any(marker in live_column for marker in ("poll", "vote", "choice", "button")):
            return "micro_poll"
        if family == "micro_challenge" or any(marker in live_column for marker in ("challenge", "mission")):
            return "micro_challenge"
        if family == "object_scene" or any(marker in live_column for marker in ("observation", "patrol", "detective")):
            return "room_observation"
        if family == "room_mood" or any(marker in live_column for marker in ("radio", "weather", "thermometer", "filter", "mood")):
            return "room_mood"
        if "stance" in combined:
            return "neko_stance"
        return family or "general"

    async def topic_candidates(self) -> list[dict[str, Any]]:
        recent = self.recent_danmaku_topic_candidates()
        recent_skip_reason = str(self._active_engagement_recent_topic_skip_reason or "").strip()
        trending = await self.bili_trending_topic_candidates()
        trending_skip_reason = str(self._active_engagement_recent_topic_skip_reason or "").strip()
        if recent:
            self._active_engagement_recent_topic_skip_reason = ""
        else:
            self._active_engagement_recent_topic_skip_reason = recent_skip_reason or trending_skip_reason
        return [*recent, *trending]

    async def bili_trending_topic_candidates(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._active_engagement_topic_cache and now - self._active_engagement_topic_cache_at < 600.0:
            return list(self._active_engagement_topic_cache)
        fetcher = self._active_engagement_topic_fetcher
        if fetcher is None:
            try:
                from utils.web_scraper import fetch_bilibili_trending

                fetcher = fetch_bilibili_trending
            except Exception:
                fetcher = None
        if not callable(fetcher):
            return []
        try:
            try:
                payload = await asyncio.wait_for(fetcher(limit=6), timeout=2.0)
            except TypeError:
                payload = await asyncio.wait_for(fetcher(), timeout=2.0)
        except Exception:
            return []
        videos = []
        if isinstance(payload, dict):
            videos = payload.get("videos") or payload.get("video") or payload.get("items") or []
        if not isinstance(videos, list):
            return []
        candidates: list[dict[str, Any]] = []
        for item in videos:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            if not self.is_meaningful_topic_text(title):
                continue
            key = str(item.get("bvid") or item.get("id") or title).strip()
            compact_title = self._runtime._compact_context_text(title, limit=40)
            profile = self.material_profile(compact_title)
            if not profile:
                self._active_engagement_recent_topic_skip_reason = "low_confidence_topic"
                continue
            candidate = {
                "source": "bili_trending",
                "key": f"bili:{key}",
                "title": compact_title,
                "hint": "Use this Bilibili topic only as a small safe hook; anchor the topic first, then ask one easy reply.",
            }
            candidate.update(profile)
            candidates.append(candidate)
            if len(candidates) >= 6:
                break
        self._active_engagement_topic_cache = candidates
        self._active_engagement_topic_cache_at = now
        return list(candidates)

    def recent_danmaku_topic_candidates(self) -> list[dict[str, Any]]:
        self._active_engagement_recent_topic_skip_reason = ""
        if self.has_streak(self._active_engagement_recent_topic_sources, "recent_danmaku", 2):
            self._active_engagement_recent_topic_skip_reason = "recent_danmaku_source_streak"
            return []
        recent_items: list[tuple[str, str]] = []
        for result in reversed(self.recent_results):
            if not isinstance(result, dict):
                continue
            event = result.get("event") if isinstance(result.get("event"), dict) else {}
            if str(event.get("source") or "") != "live_danmaku":
                continue
            if str(result.get("status") or "") not in {"pushed", "dry_run"}:
                if not self._active_engagement_recent_topic_skip_reason:
                    self._active_engagement_recent_topic_skip_reason = "non_output_danmaku"
                continue
            route = self._runtime._route_from_result(result)
            if route == "avatar_roast":
                if not self._active_engagement_recent_topic_skip_reason:
                    self._active_engagement_recent_topic_skip_reason = "avatar_roast_context"
                continue
            age = self._runtime._iso_age_sec(result.get("created_at"))
            if age is not None and age > self._ACTIVE_ENGAGEMENT_RECENT_DANMAKU_TOPIC_MAX_AGE_SECONDS:
                if not self._active_engagement_recent_topic_skip_reason:
                    self._active_engagement_recent_topic_skip_reason = "stale_recent_danmaku"
                continue
            text = str(event.get("danmaku_text") or "").strip()
            if not text:
                continue
            if self.is_viewer_to_viewer_mention_text(text):
                if not self._active_engagement_recent_topic_skip_reason:
                    self._active_engagement_recent_topic_skip_reason = "viewer_to_viewer_mention"
                continue
            if not self.is_meaningful_topic_text(text):
                if not self._active_engagement_recent_topic_skip_reason:
                    self._active_engagement_recent_topic_skip_reason = (
                        self.topic_filter_reason(text) or "filtered_recent_danmaku"
                    )
                continue
            compact = self._runtime._compact_context_text(text, limit=40)
            uid = str(event.get("uid") or "").strip()
            recent_items.append((uid, compact))
            if len(recent_items) >= 6:
                break
        speaker_ids = {uid for uid, _ in recent_items if uid}
        if len(recent_items) >= 3 and len(speaker_ids) == 1:
            self._active_engagement_recent_topic_skip_reason = "single_viewer_flood"
            return []
        candidates: list[dict[str, Any]] = []
        for _uid, compact in recent_items[:3]:
            profile = self.material_profile(compact)
            if not profile:
                if not self._active_engagement_recent_topic_skip_reason:
                    self._active_engagement_recent_topic_skip_reason = "low_confidence_topic"
                continue
            candidate = {
                "source": "recent_danmaku",
                "key": f"danmaku:{compact}",
                "title": compact,
                "hint": "Anchor this recent danmaku first, then make one small reply hook without pretending a new viewer spoke.",
            }
            candidate.update(profile)
            candidates.append(candidate)
            if len(candidates) >= 3:
                break
        if candidates:
            self._active_engagement_recent_topic_skip_reason = ""
        return candidates

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
        shapes = ["either_or", "light_stance", "tiny_tease", "small_challenge"]
        shape = shapes[self._active_engagement_shape_index % len(shapes)]
        self._active_engagement_shape_index += 1
        return shape

    def guarded_shape(self, shape: str) -> str:
        self._active_engagement_shape_guard_reason = ""
        shapes = ["either_or", "light_stance", "tiny_tease", "small_challenge"]
        normalized = shape if shape in shapes else shapes[0]
        if not self.has_streak(self._active_engagement_recent_shapes, normalized, 2):
            return normalized
        self._active_engagement_shape_guard_reason = "recent_shape_streak"
        for candidate in shapes:
            if candidate != normalized and not self.has_streak(
                self._active_engagement_recent_shapes, candidate, 1
            ):
                return candidate
        for candidate in shapes:
            if candidate != normalized:
                return candidate
        return normalized

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
