from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from typing import Any

import pytest

from plugin.plugins.neko_live.core import active_topic_builder
from plugin.plugins.neko_live.core.active_topic_candidate_picker import choose_fallback_candidate
from plugin.plugins.neko_live.core.active_topic_selector import ActiveTopicSelector
from plugin.plugins.neko_live.core.recent_output_families import spent_output_families


class FakeRuntime:
    _ACTIVE_ENGAGEMENT_RECENT_DANMAKU_TOPIC_MAX_AGE_SECONDS = 360.0

    def __init__(self) -> None:
        self.recent_results: deque[dict[str, Any]] = deque()
        self._recent_host_material_families: deque[str] = deque(maxlen=12)
        self._active_engagement_topic_fetcher = self._empty_topic_fetcher
        self._active_engagement_topic_cache: list[dict[str, Any]] = []
        self._active_engagement_topic_cache_at = 0.0
        self._active_engagement_recent_topic_keys: deque[str] = deque(maxlen=12)
        self._active_engagement_recent_topic_titles: deque[str] = deque(maxlen=8)
        self._active_engagement_recent_topic_sources: deque[str] = deque(maxlen=6)
        self._active_engagement_recent_fun_axes: deque[str] = deque(maxlen=6)
        self._active_engagement_recent_shapes: deque[str] = deque(maxlen=6)
        self._active_engagement_recent_intents: deque[str] = deque(maxlen=6)
        self._active_engagement_recent_reply_affordances: deque[str] = deque(maxlen=6)
        self._active_engagement_recent_topic_skip_reason = ""
        self._active_engagement_shape_guard_reason = ""
        self._active_engagement_shape_index = 0

    def _recent_spent_output_families(self) -> set[str]:
        return set()

    @staticmethod
    def _compact_context_text(value: str, *, limit: int = 80) -> str:
        text = " ".join(str(value).split())
        return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."

    @staticmethod
    def _route_from_result(result: dict[str, Any]) -> str:
        return str(result.get("response_module") or "")

    @staticmethod
    def _iso_age_sec(_value: Any) -> float | None:
        return 0.0

    @staticmethod
    def _active_engagement_fallback_topic_candidates() -> list[dict[str, Any]]:
        return [
            {
                "source": "fallback",
                "key": "custom:desk-choice",
                "title": "desk choice",
                "preferred_shape": "either_or",
                "fun_axis": "choice",
                "live_column": "NEKO micro poll",
                "reply_affordance": "viewer can pick one concrete side",
                "hint": "Make one tiny A/B choice.",
            }
        ]

    @staticmethod
    async def _empty_topic_fetcher(limit: int = 6) -> dict[str, Any]:
        return {"success": True, "videos": []}


@pytest.mark.asyncio
async def test_active_topic_selector_uses_runtime_fallback_and_records_rotation_state():
    runtime = FakeRuntime()
    selector = ActiveTopicSelector(runtime)

    topic = await selector.select_topic()

    assert topic["key"] == "custom:desk-choice"
    assert topic["shape"] == "either_or"
    assert topic["reply_affordance"] == "viewer can pick one concrete side"
    assert list(runtime._active_engagement_recent_topic_keys) == ["custom:desk-choice"]
    assert list(runtime._active_engagement_recent_fun_axes) == ["choice"]


def test_active_topic_selector_choose_candidate_avoids_recent_family():
    runtime = FakeRuntime()
    runtime._recent_host_material_families.append("choice_vote")
    selector = ActiveTopicSelector(runtime)

    candidate = selector.choose_candidate(
        [
            {
                "key": "used-choice",
                "title": "cat choice A or B",
                "fun_axis": "choice",
                "reply_affordance": "viewer can pick one concrete side",
            },
            {
                "key": "fresh-object",
                "title": "keyboard patrol",
                "fun_axis": "object_scene",
                "reply_affordance": "viewer can answer with one small object",
            },
        ],
        avoid_recent_fun_axis=False,
        avoid_recent_family=True,
    )

    assert candidate is not None
    assert candidate["key"] == "fresh-object"
    assert runtime._active_engagement_recent_topic_skip_reason == "recent_host_family"


def test_active_topic_selector_fallback_relaxes_similarity_before_recent_family():
    runtime = FakeRuntime()
    runtime._recent_host_material_families.append("choice_vote")
    runtime._active_engagement_recent_topic_titles.append("same tiny room choice")

    candidate = choose_fallback_candidate(
        ActiveTopicSelector(runtime),
        [
            {
                "key": "stale-choice",
                "title": "same tiny room choice",
                "fun_axis": "choice",
                "reply_affordance": "viewer can pick one concrete side",
            },
            {
                "key": "fresh-mood",
                "title": "same tiny room mood again",
                "fun_axis": "mood",
                "reply_affordance": "viewer can answer with one mood word",
            },
        ],
        {"key": "fallback", "title": "fallback"},
    )

    assert candidate["key"] == "fresh-mood"


def test_fallback_records_when_recent_family_was_avoided():
    runtime = FakeRuntime()
    runtime._recent_host_material_families.append("choice_vote")

    candidate = choose_fallback_candidate(
        ActiveTopicSelector(runtime),
        [{"key": "fresh-mood", "title": "room mood", "fun_axis": "mood"}],
        {"key": "fallback", "title": "fallback"},
    )

    assert candidate["key"] == "fresh-mood"
    assert runtime._active_engagement_recent_topic_skip_reason == "recent_host_family"


def test_spent_output_families_classifies_real_choice_food_drink_text():
    families = spent_output_families(
        "\u591c\u91cc\u9009\u5c0f\u751c\u98df\u8fd8\u662f\u70ed\u996e\uff1f\u5feb\u9009\u4e00\u4e2a\u544a\u8bc9\u6211\u5440\u55b5\uff01"
    )

    assert "choice_vote" in families
    assert "food_drink" in families


def test_active_topic_selector_topic_pack_classifies_reply_path():
    assert ActiveTopicSelector.topic_pack({"live_column": "NEKO micro poll", "title": "cup or keyboard"}) == "micro_poll"
    assert ActiveTopicSelector.topic_pack({"live_column": "room observation", "title": "keyboard patrol"}) == "room_observation"


def test_shape_guard_keeps_ambient_danmaku_factual_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeRuntime()
    used: list[int] = []
    runtime.live_events = SimpleNamespace(
        mark_ambient_chat_used=lambda seq: used.append(seq)
    )
    selector = ActiveTopicSelector(runtime)
    monkeypatch.setattr(
        ActiveTopicSelector,
        "guarded_shape",
        lambda _selector, _shape: "tiny_tease",
    )

    topic = active_topic_builder.build_topic(
        selector,
        {
            "source": "recent_danmaku",
            "key": "ambient_danmaku:7",
            "title": "cats would win this challenge",
            "preferred_shape": "light_stance",
            "hint": "Treat this as a recent room remark without inventing who said it.",
            "preserve_factual_hint": True,
        },
        runtime._active_engagement_fallback_topic_candidates()[0],
        "light_stance",
    )

    assert topic["shape"] == "tiny_tease"
    assert "recent room remark" in topic["hint"]
    assert "playful tease" in topic["hint"]
    assert used == [7]
