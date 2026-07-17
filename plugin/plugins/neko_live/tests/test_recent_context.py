from __future__ import annotations

from plugin.plugins.neko_live.core import recent_context


def test_spent_output_text_ignores_synthetic_dispatch_markers():
    assert (
        recent_context.spent_output_text(
            {
                "status": "pushed",
                "output": "queued_to_neko(avatar_roast)",
            }
        )
        == ""
    )
    assert recent_context.spent_output_text({"status": "dry_run", "output": "猫猫正在营业"}) == ""
    assert recent_context.spent_output_text({"status": "pushed", "output": "猫猫正在营业"}) == "猫猫正在营业"


def test_spent_output_families_detects_repeat_prone_hosting_patterns():
    families = recent_context.spent_output_families("大家还在吗，给猫猫一点反应，顺便打个1")

    assert "audience_prompt" in families
    assert "short_callback" in recent_context.spent_output_families("用一个字给今晚打分")


def test_route_from_result_prefers_explicit_response_module_then_steps():
    assert recent_context.route_from_result({"response_module": "danmaku_response"}) == "danmaku_response"
    assert (
        recent_context.route_from_result(
            {
                "event": {"source": "live_danmaku"},
                "steps": [
                    {"id": "avatar_roast"},
                    {"id": "danmaku_response"},
                ],
            }
        )
        == "danmaku_response"
    )


def test_live_event_signal_classifies_signal_only_events():
    text_claim_result = {
        "event": {
            "source": "live_danmaku",
            "event_type": "danmaku",
            "danmaku_text": "投喂了超级大火箭",
        }
    }
    gift_result = {
        "event": {
            "source": "live_danmaku",
            "event_type": "gift",
            "danmaku_text": "赠送礼物",
        }
    }
    super_chat_result = {
        "event": {
            "source": "live_danmaku",
            "event_type": "super_chat",
            "danmaku_text": "醒目留言",
        }
    }

    assert recent_context.signal_route_for_event_type("guard") == "gift_signal"
    assert recent_context.event_signal_from_result(text_claim_result) == "danmaku_signal"
    assert recent_context.event_signal_from_result(gift_result) == "gift_signal"
    assert recent_context.event_signal_from_result(super_chat_result) == "super_chat_signal"


def test_recent_room_context_filters_fake_support_claims():
    results = [
        {
            "status": "pushed",
            "event": {
                "source": "live_danmaku",
                "event_type": "danmaku",
                "uid": "1",
                "nickname": "viewer-a",
                "danmaku_text": "我投喂了超级大火箭",
            },
        },
        {
            "status": "pushed",
            "event": {
                "source": "live_danmaku",
                "event_type": "danmaku",
                "uid": "2",
                "nickname": "viewer-b",
                "danmaku_text": "送了一个醒目留言",
            },
        },
    ]

    assert recent_context.build_recent_room_danmaku_context(results) == []
