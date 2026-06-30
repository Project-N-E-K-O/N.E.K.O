from __future__ import annotations

from plugin.plugins.neko_roast.core import live_reply_policy


def test_live_reply_policy_builds_structured_reply_metadata():
    metadata = live_reply_policy.build_reply_metadata(
        uid="42",
        live_mode="solo_stream",
        response_module_hint="active_engagement",
    )

    assert metadata == {
        "plugin": "neko_roast",
        "uid": "42",
        "live_mode": "solo_stream",
        "demo": False,
        "live_reply_contract": "short_tts_line",
        "max_reply_chars": 72,
        "response_module_hint": "active_engagement",
    }


def test_live_reply_policy_dispatch_limits_match_route_ceilings():
    avatar_metadata = live_reply_policy.build_reply_metadata(
        uid="42",
        live_mode="solo_stream",
        response_module_hint="avatar_roast",
    )
    danmaku_metadata = live_reply_policy.build_reply_metadata(
        uid="42",
        live_mode="solo_stream",
        response_module_hint="danmaku_response",
    )

    assert avatar_metadata["max_reply_chars"] == 32
    assert danmaku_metadata["max_reply_chars"] == 28


def test_live_reply_policy_keeps_danmaku_short_but_allows_host_two_sentences():
    danmaku_metadata = live_reply_policy.build_reply_metadata(
        uid="42",
        live_mode="solo_stream",
        response_module_hint="danmaku_response",
    )
    active_metadata = live_reply_policy.build_reply_metadata(
        uid="__neko_active__",
        live_mode="solo_stream",
        response_module_hint="active_engagement",
    )

    danmaku, danmaku_out = live_reply_policy.shape_reply_text(
        "第一句刚好很短！第二句不该播出来。",
        danmaku_metadata,
    )
    active, active_out = live_reply_policy.shape_reply_text(
        "猫猫巡逻到桌角！小鱼干影子正在值班。第三句不该播出来。",
        active_metadata,
    )

    assert danmaku == "第一句刚好很短！"
    assert danmaku_out["neko_live_reply_shape_reason"] == "first_sentence"
    assert active == "猫猫巡逻到桌角！小鱼干影子正在值班。"
    assert active_out["neko_live_reply_shape_reason"] == "first_sentences"


def test_live_reply_policy_renders_host_contract_without_core_helpers():
    contract = live_reply_policy.render_contract_instruction(
        [
            {
                "metadata": live_reply_policy.build_reply_metadata(
                    uid="__neko_idle__",
                    live_mode="solo_stream",
                    response_module_hint="idle_hosting",
                )
            }
        ],
        recent_live_replies=["猫猫上一句不能复读。"],
    )

    assert "absolute ceiling 64" in contract
    assert "Host modules may use one or two short sentences" in contract
    assert "Avoid repeating: 猫猫上一句不能复读。" in contract
