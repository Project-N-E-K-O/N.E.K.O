from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from plugin.plugins.neko_roast.core.contracts import (
    InteractionResult,
    PipelineStep,
    ViewerEvent,
)
from plugin.plugins.neko_roast.core.runtime import RoastRuntime


def _created_at_age(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")

@pytest.mark.asyncio
async def test_active_engagement_ignores_single_viewer_danmaku_flood_as_topic_material(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "room neutral tiny desk vote", "bvid": "BV_ROOM_NEUTRAL"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    for text in [
        "keyboard sounds sleepy tonight",
        "the chair is judging me",
        "this mug looks dramatic",
    ]:
        runtime.record_result(
            InteractionResult(
                accepted=True,
                status="pushed",
                event=ViewerEvent(uid="42", nickname="viewer", danmaku_text=text, source="live_danmaku"),
                steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
            )
        )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_ROOM_NEUTRAL"
    assert topic["title"] == "room neutral tiny desk vote"
    assert topic["recent_topic_skip_reason"] == "single_viewer_flood"

@pytest.mark.asyncio
async def test_active_engagement_ignores_stale_recent_danmaku_as_topic_material(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "fresh neutral desk vote", "bvid": "BV_FRESH_NEUTRAL"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
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
            created_at=_created_at_age(361),
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_FRESH_NEUTRAL"
    assert topic["recent_topic_skip_reason"] == "stale_recent_danmaku"

@pytest.mark.asyncio
async def test_active_engagement_ignores_avatar_roast_danmaku_as_topic_material(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "neutral room choice after first roast", "bvid": "BV_AFTER_FIRST_ROAST"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
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
            steps=[PipelineStep("avatar_roast", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_AFTER_FIRST_ROAST"
    assert topic["recent_topic_skip_reason"] == "avatar_roast_context"

@pytest.mark.asyncio
async def test_active_engagement_ignores_non_output_danmaku_as_topic_material(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "room mood after skipped danmaku", "bvid": "BV_AFTER_SKIPPED"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=False,
            status="skipped",
            reason="safety.cooldown",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="keyboard sounds sleepy tonight",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "skipped", "safety.cooldown")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_AFTER_SKIPPED"
    assert topic["recent_topic_skip_reason"] == "non_output_danmaku"

@pytest.mark.asyncio
async def test_active_engagement_labels_filtered_recent_danmaku_skip_reason(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "room mood after filtered danmaku", "bvid": "BV_AFTER_FILTERED"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="\u732b\u732b\u80fd\u4e0d\u80fd\u9009\u4e00\u676f\u996e\u6599",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_AFTER_FILTERED"
    assert topic["recent_topic_skip_reason"] == "filtered_direct_request"

@pytest.mark.asyncio
async def test_active_engagement_labels_reaction_topic_skip_reason(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "room mood after reaction", "bvid": "BV_AFTER_REACTION"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="\u54c8\u54c8\u54c8\u54c8\u7b11\u6b7b\u4e86",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_AFTER_REACTION"
    assert topic["recent_topic_skip_reason"] == "filtered_reaction"

@pytest.mark.asyncio
async def test_active_engagement_labels_runtime_feedback_topic_skip_reason(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "room mood after runtime feedback", "bvid": "BV_AFTER_RUNTIME_FEEDBACK"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="\u56de\u590d\u6709\u70b9\u957f\uff0c\u5ef6\u8fdf\u4e5f\u6709\u70b9\u5927",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_AFTER_RUNTIME_FEEDBACK"
    assert topic["recent_topic_skip_reason"] == "filtered_runtime_feedback"

@pytest.mark.asyncio
async def test_active_engagement_ignores_tiny_recent_danmaku_as_topic_material(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "useful desk snack choice", "bvid": "BV_USEFUL"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(uid="42", nickname="viewer", danmaku_text="6", source="live_danmaku"),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["title"] == "useful desk snack choice"

@pytest.mark.asyncio
async def test_active_engagement_ignores_direct_questions_to_neko_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "tiny desk setup choices for late night", "bvid": "BV_DIRECT_QUESTION_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="\u732b\u732b\u4f60\u559c\u6b22\u5976\u8336\u5417",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_DIRECT_QUESTION_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_direct_opinion_questions_to_neko_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "tiny keyboard sound choice", "bvid": "BV_DIRECT_OPINION_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="\u732b\u732b\u4f60\u89c9\u5f97\u952e\u76d8\u5435\u4e0d\u5435",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_DIRECT_OPINION_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_direct_requests_to_neko_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "tiny snack ranking choice", "bvid": "BV_DIRECT_REQUEST_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="\u732b\u732b\u8bb2\u8bb2\u4eca\u5929\u7684\u5c0f\u96f6\u98df",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_DIRECT_REQUEST_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_direct_review_requests_to_neko_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "tiny drink ranking choice", "bvid": "BV_DIRECT_REVIEW_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="\u732b\u732b\u9510\u8bc4\u4e00\u4e0b\u6211\u7684\u952e\u76d8",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_DIRECT_REVIEW_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_direct_help_requests_to_neko_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "tiny late night drink choice", "bvid": "BV_DIRECT_HELP_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="\u732b\u732b\u5e2e\u6211\u9009\u4e00\u4e0b\u996e\u6599",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_DIRECT_HELP_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_direct_assignment_requests_to_neko_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "tiny nickname voting choice", "bvid": "BV_DIRECT_ASSIGNMENT_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="\u732b\u732b\u7ed9\u6211\u8d77\u4e2a\u5916\u53f7",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_DIRECT_ASSIGNMENT_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_english_direct_requests_to_neko_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "tiny desk drink choice", "bvid": "BV_EN_DIRECT_REQUEST_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="NEKO help me choose a drink",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_EN_DIRECT_REQUEST_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_english_tell_me_requests_to_neko_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "tiny desk snack choice", "bvid": "BV_EN_TELL_ME_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="NEKO tell me a tiny joke",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_EN_TELL_ME_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_english_can_you_requests_to_neko_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "tiny late desk choice", "bvid": "BV_EN_CAN_YOU_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="NEKO can you choose a drink",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_EN_CAN_YOU_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_english_could_you_requests_to_neko_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "tiny late desk snack", "bvid": "BV_EN_COULD_YOU_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="NEKO could you pick a snack",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_EN_COULD_YOU_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_english_please_requests_to_neko_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "tiny late desk game", "bvid": "BV_EN_PLEASE_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="NEKO please pick a snack",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_EN_PLEASE_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_english_pls_requests_to_neko_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "tiny late desk puzzle", "bvid": "BV_EN_PLS_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="NEKO pls pick a snack",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_EN_PLS_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_english_thanks_to_neko_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "tiny late desk poll", "bvid": "BV_EN_THANKS_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="NEKO thank you",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_EN_THANKS_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_chinese_thanks_to_neko_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "\u6df1\u591c\u684c\u9762\u5c0f\u6295\u7968", "bvid": "BV_ZH_THANKS_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="\u8c22\u8c22\u732b\u732b",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_ZH_THANKS_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_chinese_can_you_requests_to_neko_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "\u6df1\u591c\u996e\u6599\u4e8c\u9009\u4e00", "bvid": "BV_ZH_CAN_YOU_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="\u732b\u732b\u80fd\u4e0d\u80fd\u9009\u4e00\u676f\u996e\u6599",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_ZH_CAN_YOU_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_chinese_should_you_requests_to_neko_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "\u6df1\u591c\u5c0f\u96f6\u98df\u4e8c\u9009\u4e00", "bvid": "BV_ZH_SHOULD_YOU_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="\u732b\u732b\u8981\u4e0d\u8981\u9009\u4e00\u676f\u996e\u6599",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_ZH_SHOULD_YOU_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_untargeted_direct_requests_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "\u6df1\u591c\u684c\u9762\u5c0f\u7269\u6295\u7968", "bvid": "BV_UNTARGETED_REQUEST_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="\u8bb2\u8bb2\u4eca\u5929\u7684\u5c0f\u96f6\u98df",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_UNTARGETED_REQUEST_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_reaction_only_danmaku_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "\u6df1\u591c\u996e\u6599\u4e8c\u9009\u4e00", "bvid": "BV_REACTION_ONLY_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="\u54c8\u54c8\u54c8\u54c8\u7b11\u6b7b\u4e86",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_REACTION_ONLY_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_english_untargeted_requests_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "tiny desk light choice", "bvid": "BV_EN_UNTARGETED_REQUEST_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="tell me a tiny joke",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_EN_UNTARGETED_REQUEST_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_english_reaction_only_danmaku_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "tiny snack vote", "bvid": "BV_EN_REACTION_ONLY_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="lololol",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_EN_REACTION_ONLY_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_status_control_danmaku_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "\u6df1\u591c\u7535\u53f0\u5c0f\u6295\u7968", "bvid": "BV_STATUS_CONTROL_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="\u4e0b\u4e00\u6b65\u770b\u4e00\u4e0b\u72b6\u6001",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_STATUS_CONTROL_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_latency_and_length_feedback_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "\u6df1\u591c\u684c\u9762\u5c0f\u7269\u4e8c\u9009\u4e00", "bvid": "BV_LATENCY_FEEDBACK_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="\u56de\u590d\u6709\u70b9\u957f\uff0c\u5ef6\u8fdf\u4e5f\u6709\u70b9\u5927",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_LATENCY_FEEDBACK_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_viewer_to_viewer_mentions_as_topic_material(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "\u6df1\u591c\u732b\u7a9d\u5c0f\u6295\u7968", "bvid": "BV_VIEWER_MENTION_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="@\u8def\u8fc7\u7684\u8230\u957f \u4f60\u770b\u5230\u521a\u521a\u90a3\u53e5\u4e86\u5417",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_VIEWER_MENTION_FILTER"
    assert topic["recent_topic_skip_reason"] == "viewer_to_viewer_mention"

def test_active_engagement_mention_parser_keeps_neko_directed_mentions() -> None:
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@路过的舰长 你看这个") is True
    assert RoastRuntime._is_viewer_to_viewer_mention_text("＠路过的舰长 你看这个") is True
    assert RoastRuntime._is_viewer_to_viewer_mention_text("\uff20路过的舰长 你看这个") is True
    assert RoastRuntime._is_viewer_to_viewer_mention_text("\uff20路过的舰长\uff1a你看这个") is True
    assert RoastRuntime._is_viewer_to_viewer_mention_text("\uff20路过的舰长\uff0c你看这个") is True
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@猫猫 今天像小电台") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@猫猫今天像小电台") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("\uff20猫猫\uff1a今天像小电台") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@猫猫虫 你看这个") is True
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@小天使 晚上好") is True
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@NEKO pick one") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@neko\u505a\u8fd9\u4e2a") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@neko\u5199\u9996\u8bd7") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@neko\u9009\u4e00\u4e0b") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@\u732b\u732b\u559c\u6b22\u5976\u8336\u5417") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@\u732b\u5a18\u63a8\u8350\u4e00\u4e2a") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@\u732b\u732b\u8bf4\u53e5\u8bdd") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@\u732b\u5a18\u6559\u6211\u4e00\u4e0b") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@\u732b\u732b\u6559\u4f60\u4e00\u62db") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@\u732b\u732b\u6559\u5b66\u8fd9\u4e2a") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@\u5c0f\u5929\u5531\u6b4c") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@\u732b\u732b\u6c42\u63a8\u8350") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@\u732b\u5a18\u7ed9\u6211\u4e00\u4e2a\u63a8\u8350") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@\u732b\u732b\u770b\u770b\u8fd9\u4e2a") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@\u5c0f\u5929\u770b\u4e00\u4e0b") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@\u732b\u732b\u597d\u53ef\u7231 \u4f60\u770b\u8fd9\u4e2a") is True
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@\u732b\u5a18\u6559\u5e08 \u4f60\u770b\u8fd9\u4e2a") is True
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@\u732b\u732b\u6559\u7ec3 \u4f60\u770b\u8fd9\u4e2a") is True
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@\u5c0f\u5929\u5531\u7247 \u4f60\u770b\u8fd9\u4e2a") is True
    assert RoastRuntime._is_viewer_to_viewer_mention_text("＠neko今天播什么") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("\uff20neko今天播什么") is False
    assert RoastRuntime._is_viewer_to_viewer_mention_text("@neko123 你看这个") is True
    assert RoastRuntime._is_viewer_to_viewer_mention_text("没有提到谁") is False

@pytest.mark.asyncio
async def test_active_engagement_limits_recent_danmaku_source_streak(runtime: RoastRuntime) -> None:
    runtime._active_engagement_recent_topic_sources.extend(["recent_danmaku", "recent_danmaku"])

    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "\u6df1\u591c\u684c\u9762\u5c0f\u6295\u7968", "bvid": "BV_SOURCE_STREAK"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="\u4eca\u5929\u7684\u732b\u7a9d\u50cf\u5c0f\u7535\u53f0",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_SOURCE_STREAK"
    assert topic["recent_topic_skip_reason"] == "recent_danmaku_source_streak"

@pytest.mark.asyncio
async def test_active_engagement_ignores_room_silence_as_topic_material(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "\u76f4\u64ad\u95f4\u600e\u4e48\u8fd9\u4e48\u5b89\u9759", "bvid": "BV_SILENCE"},
                {"title": "\u6df1\u591c\u684c\u9762\u5c0f\u7269\u4e8c\u9009\u4e00", "bvid": "BV_USEFUL_SILENCE_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="\u6ca1\u4eba\u8bf4\u8bdd\u4e86",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_USEFUL_SILENCE_FILTER"
    assert topic["title"] == "\u6df1\u591c\u684c\u9762\u5c0f\u7269\u4e8c\u9009\u4e00"

@pytest.mark.asyncio
async def test_active_engagement_ignores_short_chinese_quiet_room_material(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "猫猫为什么突然安静", "bvid": "BV_SHORT_QUIET_CN"},
                {"title": "深夜饮料二选一", "bvid": "BV_USEFUL_SHORT_QUIET_CN"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="猫猫突然安静了",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_USEFUL_SHORT_QUIET_CN"
    assert topic["title"] == "深夜饮料二选一"

@pytest.mark.asyncio
async def test_active_engagement_ignores_english_room_silence_as_topic_material(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "why is the cat suddenly quiet", "bvid": "BV_SILENCE_EN"},
                {"title": "late night drink choice", "bvid": "BV_USEFUL_SILENCE_EN_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="cat is suddenly quiet",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_USEFUL_SILENCE_EN_FILTER"
    assert topic["title"] == "late night drink choice"

@pytest.mark.asyncio
async def test_active_engagement_ignores_tiny_trending_titles_as_topic_material(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "ok", "bvid": "BV_TINY"},
                {"title": "useful desk snack choice", "bvid": "BV_USEFUL"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_USEFUL"
    assert topic["title"] == "useful desk snack choice"

@pytest.mark.asyncio
async def test_active_engagement_ignores_generic_host_prompt_topics(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "what should we talk about today", "bvid": "BV_GENERIC"},
                {"title": "tiny desk setup choices for late night", "bvid": "BV_USEFUL"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="everyone interact with NEKO",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_USEFUL"
    assert topic["title"] == "tiny desk setup choices for late night"

@pytest.mark.asyncio
async def test_active_engagement_ignores_english_chat_bait_topic_material(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "let's get the chat moving tonight", "bvid": "BV_CHAT_BAIT"},
                {"title": "tiny keyboard sound choice", "bvid": "BV_USEFUL_CHAT_BAIT_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="keep the chat alive",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_USEFUL_CHAT_BAIT_FILTER"
    assert topic["title"] == "tiny keyboard sound choice"

@pytest.mark.asyncio
async def test_active_engagement_ignores_recommendation_request_topics(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "any recommendations for tonight", "bvid": "BV_RECOMMEND_EN"},
                {"title": "夜里桌面小物二选一", "bvid": "BV_USEFUL_RECOMMEND_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="有什么推荐吗",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_USEFUL_RECOMMEND_FILTER"
    assert topic["title"] == "夜里桌面小物二选一"

@pytest.mark.asyncio
async def test_active_engagement_ignores_promo_or_giveaway_topic_material(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "关注转发抽奖限时福利", "bvid": "BV_PROMO_CN"},
                {"title": "sponsored giveaway subscribe and win", "bvid": "BV_PROMO_EN"},
                {"title": "猫猫今晚认真三秒挑战", "bvid": "BV_USEFUL_PROMO_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_USEFUL_PROMO_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_heavy_or_controversial_topic_material(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "突发事故致多人伤亡", "bvid": "BV_HEAVY_CN"},
                {"title": "celebrity scandal controversy death toll", "bvid": "BV_HEAVY_EN"},
                {"title": "猫猫今晚认真三秒挑战", "bvid": "BV_USEFUL_HEAVY_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_USEFUL_HEAVY_FILTER"

@pytest.mark.asyncio
async def test_active_engagement_ignores_open_ended_topic_survey_material(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "what are we doing tonight", "bvid": "BV_OPEN_SURVEY"},
                {"title": "late night drink choices", "bvid": "BV_USEFUL_OPEN_SURVEY_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="今晚做什么",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_USEFUL_OPEN_SURVEY_FILTER"
    assert topic["title"] == "late night drink choices"

@pytest.mark.asyncio
async def test_active_engagement_ignores_punctuated_english_generic_host_prompt_topics(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "what! should! we! talk! about! today", "bvid": "BV_GENERIC_EN_PUNCT"},
                {"title": "late night tiny desk choices", "bvid": "BV_USEFUL_EN_PUNCT"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="everyone!!! interact!!! with!!! NEKO",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_USEFUL_EN_PUNCT"
    assert topic["title"] == "late night tiny desk choices"

@pytest.mark.asyncio
async def test_active_engagement_ignores_chinese_generic_host_prompt_topics(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "想看什么就发弹幕", "bvid": "BV_GENERIC_CN"},
                {"title": "夜里桌面小物二选一", "bvid": "BV_USEFUL_CN"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="来点弹幕扣1",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_USEFUL_CN"
    assert topic["title"] == "夜里桌面小物二选一"

@pytest.mark.asyncio
async def test_active_engagement_ignores_presence_check_host_bait_topics(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "还在吗吱一声给点反应", "bvid": "BV_PRESENCE_CHECK_CN"},
                {"title": "猫猫深夜桌面物件投票", "bvid": "BV_USEFUL_PRESENCE_FILTER"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="在不在冒个泡接一句",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_USEFUL_PRESENCE_FILTER"
    assert topic["title"] == "猫猫深夜桌面物件投票"

@pytest.mark.asyncio
async def test_active_engagement_ignores_spaced_chinese_generic_host_prompt_topics(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "想 看 什 么 就 发 弹 幕", "bvid": "BV_GENERIC_SPACED_CN"},
                {"title": "猫猫深夜桌面物件投票", "bvid": "BV_USEFUL_SPACED_CN"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="来 点 弹 幕 扣 1",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_USEFUL_SPACED_CN"
    assert topic["title"] == "猫猫深夜桌面物件投票"

@pytest.mark.asyncio
async def test_active_engagement_ignores_punctuated_chinese_generic_host_prompt_topics(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "想！看！什！么！就！发！弹！幕！", "bvid": "BV_GENERIC_PUNCT_CN"},
                {"title": "猫猫深夜饮料二选一", "bvid": "BV_USEFUL_PUNCT_CN"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="来！点！弹！幕！扣！1！",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_USEFUL_PUNCT_CN"
    assert topic["title"] == "猫猫深夜饮料二选一"

def test_proactive_material_avoids_generic_host_bait(runtime: RoastRuntime) -> None:
    blocked_fragments = (
        "everyone interact",
        "say something",
        "send danmaku",
        "start sending",
        "what should we talk about",
        "tell me what you want",
        "get the chat moving",
        "keep the chat alive",
        "\u5927\u5bb6\u5feb\u6765\u4e92\u52a8",
        "\u5f39\u5e55\u5237\u8d77\u6765",
        "\u60f3\u804a\u4ec0\u4e48",
        "\u6ca1\u4eba\u8bf4\u8bdd",
        "\u51b7\u573a",
        "\u61c2\u5f88\u591a",
        "\u4e13\u5bb6",
        "\u653b\u7565",
        "\u6559\u7a0b",
        "expert",
        "guide",
        "tutorial",
    )
    materials = [*runtime._active_engagement_fallback_topic_candidates(), *runtime._idle_hosting_beat_candidates()]

    for material in materials:
        combined = " ".join(
            str(material.get(field) or "")
            for field in ("title", "hint", "reply_affordance", "fun_axis", "shape")
        ).lower()
        assert not any(fragment.lower() in combined for fragment in blocked_fragments), material
