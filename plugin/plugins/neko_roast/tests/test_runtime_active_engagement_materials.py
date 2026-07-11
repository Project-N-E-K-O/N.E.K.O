from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from plugin.plugins.neko_roast.core.contracts import (
    InteractionResult,
    PipelineStep,
    ViewerEvent,
)
from plugin.plugins.neko_roast.core.runtime import RoastRuntime
from plugin.plugins.neko_roast.modules.active_engagement import ActiveEngagementModule


def _created_at_age(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _record_result_at(
    runtime: RoastRuntime,
    *,
    age_seconds: int,
    source: str = "live_danmaku",
    steps: list[PipelineStep] | None = None,
) -> None:
    event = ViewerEvent(uid="42", nickname="viewer", danmaku_text="hi", source=source)  # type: ignore[arg-type]
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=event,
            steps=steps or [PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
            created_at=_created_at_age(age_seconds),
        )
    )


@pytest.mark.asyncio
async def test_trigger_active_engagement_attaches_topic_material(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "测试热榜：猫猫为什么突然安静", "bvid": "BV1"},
                {"title": "测试热榜：今天直播间适合选哪边", "bvid": "BV2"},
            ],
        }

    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    runtime._active_engagement_topic_fetcher = fetch_topics
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=90)

    result = await runtime.trigger_active_engagement()

    topic = result.event.raw["topic_material"]
    assert topic["source"] == "bili_trending"
    assert topic["title"] == "测试热榜：今天直播间适合选哪边"
    assert topic["shape"] in {"either_or", "light_stance", "tiny_tease", "small_challenge"}
    assert topic["hook"]
    assert topic["pattern"]
    assert "Topic material" in result.request.prompt_text
    assert runtime.recent_results[-1]["event"]["topic_source"] == "bili_trending"
    assert runtime.recent_results[-1]["event"]["topic_shape"] == topic["shape"]
    assert runtime.recent_results[-1]["event"]["topic_hook"] == topic["hook"]
    assert runtime.recent_results[-1]["event"]["topic_pattern"] == topic["pattern"]

@pytest.mark.asyncio
async def test_bili_trending_topic_material_gets_replyable_shape_profile(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "desk snack or hot drink choice", "bvid": "BV_CHOICE"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_CHOICE"
    assert topic["shape"] == "either_or"
    assert topic["fun_axis"] == "choice"
    assert topic["family"] == "choice_vote"
    assert topic["live_column"] == "NEKO micro poll"
    assert topic["reply_affordance"] == "viewer can answer in danmaku with one concrete side"
    assert "A/B word choice" in topic["hint"]

def test_recent_danmaku_topic_material_gets_replyable_shape_profile(runtime: RoastRuntime) -> None:
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="keyboard sounds sleepy tonight",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topics = runtime._recent_danmaku_topic_candidates()

    assert topics[0]["source"] == "recent_danmaku"
    assert topics[0]["preferred_shape"] == "tiny_tease"
    assert topics[0]["fun_axis"] == "tease"
    assert topics[0]["live_column"] == "NEKO tiny verdict"
    assert topics[0]["reply_affordance"] == "viewer can tease NEKO or the topic back"


@pytest.mark.asyncio
async def test_active_engagement_prefers_recent_live_thread_over_single_topic(
    runtime: RoastRuntime,
) -> None:
    for uid, text in (
        ("42", "这杯奶茶不好喝"),
        ("43", "这个奶茶真的不好喝"),
    ):
        runtime.record_result(
            InteractionResult(
                accepted=True,
                status="pushed",
                event=ViewerEvent(
                    uid=uid,
                    nickname=f"viewer-{uid}",
                    danmaku_text=text,
                    source="live_danmaku",
                ),
                steps=[
                    PipelineStep("danmaku_response", "ok"),
                    PipelineStep("neko_dispatcher", "ok"),
                ],
            )
        )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "live_thread"
    assert topic["shape"] == "light_stance"
    assert topic["fun_axis"] == "viewer_callback"
    assert topic["live_column"] == "NEKO thread pickup"
    assert "不好喝" in topic["title"]
    assert topic["reply_affordance"] == "viewer can add one small stance or example"


def test_active_engagement_prompt_includes_recent_thread_evidence() -> None:
    prompt = ActiveEngagementModule._build_prompt(
        "normal",
        topic_material={
            "source": "live_thread",
            "shape": "light_stance",
            "title": "多人在聊「不好喝」：这杯奶茶不好喝",
            "interest": "多人在聊「不好喝」：这杯奶茶不好喝",
            "relevance": 89,
            "risk": 20,
            "evidence": ["这杯奶茶不好喝", "这个奶茶真的不好喝"],
            "reply_affordance": "viewer can add one small stance or example",
        },
    )

    assert "- source: live_thread" in prompt
    assert "- interest: 多人在聊「不好喝」：这杯奶茶不好喝" in prompt
    assert "- relevance: 89" in prompt
    assert "- risk: 20" in prompt
    assert "- recent thread evidence:" in prompt
    assert "这个奶茶真的不好喝" in prompt


@pytest.mark.asyncio
async def test_active_engagement_topic_material_rotates_shapes_and_titles(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "普通直播话题 A", "bvid": "BV_A"},
                {"title": "普通直播话题 B", "bvid": "BV_B"},
                {"title": "普通直播话题 C", "bvid": "BV_C"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics

    first = await runtime._select_active_engagement_topic()
    second = await runtime._select_active_engagement_topic()

    assert first["key"] != second["key"]
    assert first["shape"] != second["shape"]

@pytest.mark.asyncio
async def test_active_engagement_prefers_meaningful_recent_danmaku_over_trending(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "bili trending should wait", "bvid": "BV_WAIT"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(uid="42", nickname="viewer", danmaku_text="keyboard sounds sleepy tonight", source="live_danmaku"),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "recent_danmaku"
    assert topic["title"] == "keyboard sounds sleepy tonight"
    assert topic["key"] == "danmaku:keyboard sounds sleepy tonight"

@pytest.mark.asyncio
async def test_active_engagement_compacts_long_trending_titles(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {
                    "title": "late night tiny desk setup choice with many extra details that would make NEKO ramble",
                    "bvid": "BV_LONG_TOPIC",
                },
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_LONG_TOPIC"
    assert len(topic["title"]) <= 40
    assert topic["title"].endswith("...")

@pytest.mark.asyncio
async def test_active_engagement_uses_fallback_instead_of_repeating_recent_single_topic(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "late night room mood choice", "bvid": "BV_ONLY"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics

    first = await runtime._select_active_engagement_topic()
    second = await runtime._select_active_engagement_topic()

    assert first["source"] == "bili_trending"
    assert second["source"] == "fallback"
    assert second["key"] != first["key"]

@pytest.mark.asyncio
async def test_active_engagement_skips_similar_topic_titles_even_with_different_keys(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "今晚猫猫小电台怎么开场", "bvid": "BV_CAT_RADIO_A"},
                {"title": "今晚猫猫小电台开场方式", "bvid": "BV_CAT_RADIO_B"},
                {"title": "桌面零食二选一", "bvid": "BV_SNACK_CHOICE"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics

    first = await runtime._select_active_engagement_topic()
    second = await runtime._select_active_engagement_topic()

    assert first["key"] == "bili:BV_CAT_RADIO_A"
    assert second["key"] == "bili:BV_SNACK_CHOICE"
    assert second["title"] == "桌面零食二选一"
    assert second["recent_topic_skip_reason"] == "similar_topic_title"

@pytest.mark.asyncio
async def test_active_engagement_falls_back_when_all_external_titles_are_similar(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "今晚猫猫小电台怎么开场", "bvid": "BV_CAT_RADIO_A"},
                {"title": "今晚猫猫小电台开场方式", "bvid": "BV_CAT_RADIO_B"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics

    first = await runtime._select_active_engagement_topic()
    second = await runtime._select_active_engagement_topic()

    assert first["key"] == "bili:BV_CAT_RADIO_A"
    assert second["source"] == "fallback"
    assert second["key"] != first["key"]
    assert second["recent_topic_skip_reason"] == "similar_topic_title"

@pytest.mark.asyncio
async def test_active_engagement_refreshes_trending_when_cached_topics_are_exhausted(
    runtime: RoastRuntime,
) -> None:
    calls = 0

    async def fetch_topics(limit: int = 6) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "success": True,
                "videos": [
                    {"title": "first tiny desk choice", "bvid": "BV_FIRST_TOPIC"},
                ],
            }
        return {
            "success": True,
            "videos": [
                {"title": "second tiny cat challenge", "bvid": "BV_SECOND_TOPIC"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics

    first = await runtime._select_active_engagement_topic()
    second = await runtime._select_active_engagement_topic()

    assert first["source"] == "bili_trending"
    assert first["key"] == "bili:BV_FIRST_TOPIC"
    assert second["source"] == "bili_trending"
    assert second["key"] == "bili:BV_SECOND_TOPIC"
    assert calls == 2

@pytest.mark.asyncio
async def test_active_engagement_has_enough_fallback_topics_for_low_danmaku_stream(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {"success": True, "videos": []}

    runtime._active_engagement_topic_fetcher = fetch_topics

    topics = [await runtime._select_active_engagement_topic() for _ in range(10)]

    assert all(topic["source"] == "fallback" for topic in topics)
    assert len({topic["key"] for topic in topics}) == 10
    assert all("fallback:" in topic["key"] for topic in topics)
    assert all(topic["key"] not in {"fallback:small-choice", "fallback:viewer-mini-vote"} for topic in topics)
    assert all(len(topic["title"]) >= 8 for topic in topics)

def test_active_engagement_relaxed_similarity_still_prefers_unused_key(runtime: RoastRuntime) -> None:
    runtime._active_engagement_recent_topic_keys.append("fallback:used")
    runtime._active_engagement_recent_topic_titles.append("same tiny room choice")

    candidate = runtime._choose_active_engagement_candidate(
        [
            {"key": "fallback:used", "title": "same tiny room choice", "fun_axis": "choice"},
            {"key": "fallback:unused", "title": "same tiny room choice again", "fun_axis": "choice"},
        ],
        avoid_recent_fun_axis=False,
        avoid_recent_family=False,
        allow_similar_title=True,
    )

    assert candidate is not None
    assert candidate["key"] == "fallback:unused"

@pytest.mark.asyncio
async def test_active_engagement_avoids_recent_idle_hosting_material_family(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {"success": True, "videos": []}

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime._recent_host_material_families.append("choice_vote")

    topic = await runtime._select_active_engagement_topic()

    assert topic["key"] == "fallback:keyboard-busy"
    assert topic["family"] != "choice_vote"
    assert topic["recent_topic_skip_reason"] == "recent_host_family"

def test_idle_hosting_avoids_recent_active_engagement_material_family(runtime: RoastRuntime) -> None:
    runtime._recent_host_material_families.append("room_mood")

    beat = runtime._next_idle_hosting_beat()

    assert beat["family"] != "room_mood"
    assert beat["idle_stage"] == "settle"

@pytest.mark.asyncio
async def test_active_engagement_avoids_recent_spent_output_family(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {"success": True, "videos": []}

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="__neko_active__",
                nickname="NEKO",
                source="active_engagement",
            ),
            output="键盘今天像在偷偷打盹。",
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert "object_scene" in runtime._recent_spent_output_families()
    assert topic["family"] != "object_scene"
    assert topic["recent_topic_skip_reason"] == "recent_spent_output_family"

def test_active_engagement_fallback_topics_do_not_use_room_silence_as_material(runtime: RoastRuntime) -> None:
    blocked_fragments = ("\u5f39\u5e55\u5c11", "\u6ca1\u5f39\u5e55", "\u6ca1\u4eba\u8bf4\u8bdd", "\u51b7\u573a", "\u5b89\u9759")

    titles = [topic["title"] for topic in runtime._active_engagement_fallback_topic_candidates()]

    assert titles
    assert not any(fragment in title for title in titles for fragment in blocked_fragments)

def test_active_engagement_fallback_topics_explain_fun_axis_and_reply_path(runtime: RoastRuntime) -> None:
    topics = runtime._active_engagement_fallback_topic_candidates()
    axes = {topic.get("fun_axis") for topic in topics}

    assert len(topics) >= 33
    assert {"choice", "tease", "mood", "micro_challenge", "viewer_callback"}.issubset(axes)
    assert len({topic.get("live_column") for topic in topics if topic.get("live_column")}) >= 14
    assert all(str(topic.get("reply_affordance") or "").strip() for topic in topics)
    assert not any("what should we talk about" in str(topic.get("hint") or "").lower() for topic in topics)
    keys = {topic["key"] for topic in topics}
    assert "fallback:tiny-court" in keys
    assert "fallback:two-char-password" in keys
    assert "fallback:lightstick-reflection" in keys

@pytest.mark.asyncio
async def test_active_engagement_fallback_topics_use_their_natural_shapes(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {"success": True, "videos": []}

    runtime._active_engagement_topic_fetcher = fetch_topics

    first = await runtime._select_active_engagement_topic()
    second = await runtime._select_active_engagement_topic()

    assert first["key"] == "fallback:keyboard-busy"
    assert first["shape"] == "tiny_tease"
    assert first["live_column"] == "NEKO tiny verdict"
    assert first["topic_pack"] == "neko_verdict"
    assert "tiny playful tease" in first["hook"]
    assert second["key"] == "fallback:snack-choice"
    assert second["shape"] == "either_or"
    assert second["live_column"] == "NEKO micro poll"
    assert second["topic_pack"] == "micro_poll"

@pytest.mark.asyncio
async def test_active_engagement_topic_exposes_viewer_reply_affordance(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {"success": True, "videos": []}

    runtime._active_engagement_topic_fetcher = fetch_topics

    topic = await runtime._select_active_engagement_topic()

    assert topic["intent"] == "tease_back"
    assert topic["topic_pack"] == "neko_verdict"
    assert topic["reply_affordance"] == "viewer can tease the keyboard or NEKO back"

@pytest.mark.asyncio
async def test_active_engagement_topic_preserves_fallback_fun_axis(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {"success": True, "videos": []}

    runtime._active_engagement_topic_fetcher = fetch_topics

    topic = await runtime._select_active_engagement_topic()

    assert topic["fun_axis"] == "tease"
    assert topic["reply_affordance"] == "viewer can tease the keyboard or NEKO back"

@pytest.mark.asyncio
async def test_active_engagement_topic_selection_prefers_fresh_fun_axis(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {"success": True, "videos": []}

    runtime._active_engagement_topic_fetcher = fetch_topics

    topics = [await runtime._select_active_engagement_topic() for _ in range(4)]
    axes = [topic["fun_axis"] for topic in topics]

    assert len(set(axes)) >= 4
    assert all(left != right for left, right in zip(axes, axes[1:]))

@pytest.mark.asyncio
async def test_active_engagement_topic_selection_prefers_fresh_reply_affordance(
    runtime: RoastRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [
        {
            "source": "fallback",
            "key": "topic:mood-one",
            "title": "quiet mood one",
            "hint": "first",
            "preferred_shape": "light_stance",
            "fun_axis": "mood",
            "reply_affordance": "viewer can answer with one mood word",
        },
        {
            "source": "fallback",
            "key": "topic:choice-same-reply",
            "title": "fresh choice",
            "hint": "same reply path",
            "preferred_shape": "either_or",
            "fun_axis": "choice",
            "reply_affordance": "viewer can answer with one mood word",
        },
        {
            "source": "fallback",
            "key": "topic:tease-new-reply",
            "title": "fresh tease",
            "hint": "fresh reply path",
            "preferred_shape": "tiny_tease",
            "fun_axis": "tease",
            "reply_affordance": "viewer can tease NEKO back",
        },
    ]
    monkeypatch.setattr(runtime, "_active_engagement_fallback_topic_candidates", lambda: list(candidates))

    async def fetch_topics(limit: int = 6) -> dict:
        return {"success": True, "videos": []}

    runtime._active_engagement_topic_fetcher = fetch_topics

    first = await runtime._select_active_engagement_topic()
    second = await runtime._select_active_engagement_topic()

    assert first["key"] == "topic:mood-one"
    assert second["key"] == "topic:tease-new-reply"

@pytest.mark.asyncio
async def test_active_engagement_topic_shapes_follow_material_profile(runtime: RoastRuntime) -> None:
    titles = [
        "桌面零食二选一",
        "键盘像在打盹",
        "猫猫假装正经三秒",
        "今晚小电台气氛",
        "水杯还是热饮",
        "屏幕也在盯回来",
        "夜猫子状态投票",
        "猫爪按钮选择",
    ]

    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": title, "bvid": f"BV_ROTATE_{index}"}
                for index, title in enumerate(titles)
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics

    shapes = [(await runtime._select_active_engagement_topic())["shape"] for _ in range(4)]

    assert shapes == [
        "either_or",
        "tiny_tease",
        "small_challenge",
        "light_stance",
    ]
