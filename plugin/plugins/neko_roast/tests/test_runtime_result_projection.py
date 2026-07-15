from __future__ import annotations

import pytest

from plugin.plugins.neko_roast.core.contracts import (
    InteractionRequest,
    InteractionResult,
    PipelineStep,
    ViewerEvent,
    ViewerIdentity,
    ViewerProfile,
)
from plugin.plugins.neko_roast.core.runtime import RoastRuntime
from plugin.plugins.neko_roast.modules.bili_live_ingest import BiliLiveIngestModule


def test_record_result_keeps_fake_support_claim_as_danmaku_signal(runtime: RoastRuntime) -> None:
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="赠送 1 个 粉丝团灯牌",
        source="live_danmaku",
    )

    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=event,
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    latest = runtime.recent_results[-1]
    assert latest["response_module"] == "danmaku_response"
    assert latest["event_signal"] == "danmaku_signal"


def test_record_result_exposes_real_gift_event_signal(runtime: RoastRuntime) -> None:
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="赠送 1 个 粉丝团灯牌",
        source="live_danmaku",
        raw={"event_type": "gift"},
    )

    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=event,
            steps=[PipelineStep("gift_signal", "skipped"), PipelineStep("neko_dispatcher", "skipped")],
        )
    )

    latest = runtime.recent_results[-1]
    assert latest["event_signal"] == "gift_signal"


def test_record_result_exposes_danmaku_response_profile_for_monitoring(runtime: RoastRuntime) -> None:
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="哈哈哈",
        source="live_danmaku",
    )
    identity = ViewerIdentity(uid="42", nickname="viewer")
    profile = ViewerProfile(uid="42", nickname="viewer", roast_count=1)
    request = InteractionRequest(
        event=event,
        identity=identity,
        profile=profile,
        prompt_text="prompt",
        live_mode="solo_stream",
        strength="normal",
        metadata={
            "danmaku_profile": "emoji_or_reaction",
            "danmaku_reply_target": "current_reaction",
            "danmaku_reply_shape": "mirror_mood_in_a_few_chars",
            "danmaku_anchor_hint": "鍝堝搱",
            "reply_length_mode": "expanded",
            "room_theme": "small talk",
        },
    )

    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=event,
            identity=identity,
            profile=profile,
            request=request,
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    latest = runtime.recent_results[-1]

    assert latest["response_module"] == "danmaku_response"
    assert latest["danmaku_profile"] == "emoji_or_reaction"
    assert latest["danmaku_reply_target"] == "current_reaction"
    assert latest["danmaku_reply_shape"] == "mirror_mood_in_a_few_chars"
    assert latest["danmaku_anchor_hint"] == "鍝堝搱"
    assert latest["reply_length_mode"] == "expanded"
    assert latest["room_theme"] == "small talk"


def test_record_result_rejects_object_reply_review_metadata(runtime: RoastRuntime) -> None:
    class SpoofText:
        def __str__(self):
            return "room_bridge"

    event = ViewerEvent(uid="42", nickname="viewer", danmaku_text="hello", source="live_danmaku")
    identity = ViewerIdentity(uid="42", nickname="viewer")
    profile = ViewerProfile(uid="42", nickname="viewer", roast_count=1)
    request = InteractionRequest(
        event=event,
        identity=identity,
        profile=profile,
        prompt_text="prompt",
        live_mode="solo_stream",
        strength="normal",
        metadata={
            "reply_length_mode": SpoofText(),
            "room_theme": SpoofText(),
            "danmaku_anchor_hint": SpoofText(),
        },
    )

    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=event,
            identity=identity,
            profile=profile,
            request=request,
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    latest = runtime.recent_results[-1]

    assert "reply_length_mode" not in latest
    assert "room_theme" not in latest
    assert "danmaku_anchor_hint" not in latest


def test_recent_room_danmaku_context_groups_room_theme(runtime: RoastRuntime) -> None:
    for uid, nickname, text in (
        ("1", "alice", "夜里选小甜食还是热饮？"),
        ("2", "bob", "1"),
        ("3", "carol", "我选热饮"),
    ):
        runtime.record_result(
            InteractionResult(
                accepted=True,
                status="pushed",
                event=ViewerEvent(
                    uid=uid,
                    nickname=nickname,
                    danmaku_text=text,
                    source="live_danmaku",
                ),
                steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
            )
        )

    lines = runtime.recent_room_danmaku_context(
        ViewerEvent(uid="4", nickname="dora", danmaku_text="热饮吧", source="live_danmaku")
    )
    rendered = "\n".join(lines)

    assert "room_theme=choice / preference prompt" in rendered
    assert "filtered_low_value_danmaku=1" in rendered
    assert "alice: 夜里选小甜食还是热饮？" in rendered
    assert "carol: 我选热饮" in rendered
    assert "answer the current viewer first" in rendered
    assert "do not re-ask the same choice" in rendered


def test_record_result_exposes_spent_output_family_for_monitoring(runtime: RoastRuntime) -> None:
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="ok",
                source="live_danmaku",
            ),
            output="小鱼干奖励先记账，等弹幕接一句",
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    latest = runtime.recent_results[-1]

    assert latest["spent_output_family"] == "food_drink,reward,audience_prompt"


def test_record_result_does_not_expose_spent_output_family_for_dispatcher_placeholder(runtime: RoastRuntime) -> None:
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="one more line",
                source="live_danmaku",
            ),
            output="queued_to_neko(target=Lanlan, ai_behavior=respond, visibility=none, text=gift chat plan)",
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    latest = runtime.recent_results[-1]

    assert "spent_output_family" not in latest


def test_record_result_does_not_expose_spent_output_family_for_dry_run_text(runtime: RoastRuntime) -> None:
    runtime.record_result(
        InteractionResult(
            accepted=False,
            status="dry_run",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="one more line",
                source="live_danmaku",
            ),
            output="小鱼干奖励先记账，等弹幕接一句。",
            reason="dispatcher.dry_run",
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "dry_run")],
        )
    )

    latest = runtime.recent_results[-1]

    assert "spent_output_family" not in latest
    assert runtime._recent_spent_output_families() == set()
    assert runtime.recent_interaction_context(limit=1) == ["danmaku_response / live_danmaku from viewer: one more line"]


def test_record_result_exposes_active_topic_recent_skip_reason(runtime: RoastRuntime) -> None:
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="dry_run",
            event=ViewerEvent(
                uid="__neko_active__",
                nickname="NEKO",
                source="active_engagement",
                raw={
                    "topic_material": {
                        "source": "bili_trending",
                        "key": "bili:BV_ROOM_NEUTRAL",
                        "title": "room neutral tiny desk vote",
                        "live_column": "NEKO micro poll",
                        "recent_topic_skip_reason": "single_viewer_flood",
                    }
                },
            ),
            steps=[PipelineStep("active_engagement", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    event = runtime.recent_results[-1]["event"]
    assert event["topic_source"] == "bili_trending"
    assert event["topic_live_column"] == "NEKO micro poll"
    assert event["topic_recent_skip_reason"] == "single_viewer_flood"


@pytest.mark.parametrize(
    ("event_type", "expected_signal"),
    [
        ("gift", "gift_signal"),
        ("guard", "gift_signal"),
        ("super_chat", "super_chat_signal"),
    ],
)
def test_record_result_uses_live_event_type_for_signal_observation(
    runtime: RoastRuntime,
    event_type: str,
    expected_signal: str,
) -> None:
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="谢谢猫猫",
        source="live_danmaku",
        raw={"event_type": event_type},
    )

    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=event,
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    latest = runtime.recent_results[-1]
    assert latest["event"]["event_type"] == event_type
    assert latest["event_signal"] == expected_signal


@pytest.mark.asyncio
async def test_handle_live_payload_routes_gift_to_support_events(runtime: RoastRuntime) -> None:
    runtime.config.dry_run = False
    runtime.config.live_mode = "solo_stream"
    runtime.config.live_enabled = True
    runtime.safety_guard.set_connected(True)
    runtime.bili_live_ingest = BiliLiveIngestModule()
    runtime.bili_live_ingest.ctx = runtime

    result = await runtime.handle_live_payload(
        {
            "uid": "42",
            "nickname": "viewer",
            "text": "sent a small gift",
            "event_type": "gift",
            "gift_name": "small gift",
            "gift_num": 1,
            "gift_total_coin": 100,
        }
    )

    assert result.status == "pushed"
    assert result.reason == ""
    latest = runtime.recent_results[-1]
    assert latest["event_signal"] == "gift_signal"
    assert latest["response_module"] == "live_support_events"
    assert latest["support_event_type"] == "gift"
    assert latest["support_event_tier"] == "light"
    assert latest["support_event_label"] == "small gift"
    assert latest["event"]["event_type"] == "gift"
    assert latest["event"]["support_gift_name"] == "small gift"
    assert latest["event"]["support_gift_num"] == 1
    assert latest["trace_id"].startswith("tr_")
    assert latest["event"]["trace_id"] == latest["trace_id"]
    stages = [row["stage"] for row in latest["timeline"]]
    assert "pipeline.route" in stages
    assert "request.build" in stages
    assert "dispatcher.push" in stages
    assert "result.record" in stages
    assert any(row["route"] == "live_support_events" for row in latest["timeline"])
    state = await runtime.dashboard_state()
    assert state["live_explain"]["trace_id"] == latest["trace_id"]
    assert all(step["id"] != "avatar_roast" for step in latest["steps"])
    assert any(step["id"] == "live_support_events" for step in latest["steps"])


@pytest.mark.asyncio
async def test_handle_live_payload_accepts_signal_gift_count_and_value(runtime: RoastRuntime) -> None:
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    runtime.config.live_enabled = True
    runtime.safety_guard.set_connected(True)
    runtime.bili_live_ingest = BiliLiveIngestModule()
    runtime.bili_live_ingest.ctx = runtime

    result = await runtime.handle_live_payload(
        {
            "uid": "42",
            "nickname": "viewer",
            "event_type": "gift",
            "gift_name": "big gift",
            "gift_count": 2,
            "gift_value": 12000,
        }
    )

    assert result.status == "dry_run"
    latest = runtime.recent_results[-1]
    assert latest["response_module"] == "live_support_events"
    assert latest["support_event_type"] == "gift"
    assert latest["support_event_tier"] == "high"
    assert latest["event"]["gift_count"] == 2
    assert latest["event"]["gift_value"] == 12000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "expected_support_type", "expected_signal"),
    [
        ("gift", "gift", "gift_signal"),
        ("guard", "guard", "gift_signal"),
        ("super_chat", "super_chat", "super_chat_signal"),
        ("sc", "super_chat", "super_chat_signal"),
    ],
)
async def test_handle_live_payload_routes_support_events_through_pipeline(
    runtime: RoastRuntime,
    event_type: str,
    expected_support_type: str,
    expected_signal: str,
) -> None:
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    runtime.config.live_enabled = True
    runtime.safety_guard.set_connected(True)
    runtime.bili_live_ingest = BiliLiveIngestModule()
    runtime.bili_live_ingest.ctx = runtime

    result = await runtime.handle_live_payload(
        {
            "uid": "42",
            "nickname": "viewer",
            "text": "support event text",
            "event_type": event_type,
            "gift_name": "support",
            "gift_num": 1,
            "gift_total_coin": 1000,
            "guard_level": 3,
        }
    )

    assert result.status == "dry_run"
    latest = runtime.recent_results[-1]
    assert latest["response_module"] == "live_support_events"
    assert latest["event_signal"] == expected_signal
    assert latest["support_event_type"] == expected_support_type
    assert any(step["id"] == "live_support_events" for step in latest["steps"])
