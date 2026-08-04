from plugin.plugins.neko_live.core.contracts import ViewerEvent
from plugin.plugins.neko_live.core.pipeline_routing import (
    entrance_pacing_interval_seconds,
    is_explicit_self_avatar_roast_request,
    is_live_danmaku_with_text,
    route_for_event,
)


def _event(
    source: str, text: str = "hello", live_mode: str = "solo_stream"
) -> ViewerEvent:
    return ViewerEvent(
        uid="u1",
        nickname="viewer",
        danmaku_text=text,
        source=source,
        live_mode=live_mode,
    )


def test_pipeline_routing_sends_hosting_sources_to_their_modules():
    assert (
        route_for_event(
            _event("warmup_hosting", ""),
            is_transient_event_result=True,
            has_uid_lock=False,
            already_roasted=False,
            entrance_pacing_active=False,
        ).response_module_id
        == "warmup_hosting"
    )
    assert (
        route_for_event(
            _event("active_engagement", ""),
            is_transient_event_result=True,
            has_uid_lock=False,
            already_roasted=False,
            entrance_pacing_active=False,
        ).response_module_id
        == "active_engagement"
    )
    assert (
        route_for_event(
            _event("idle_hosting", ""),
            is_transient_event_result=True,
            has_uid_lock=False,
            already_roasted=False,
            entrance_pacing_active=False,
        ).response_module_id
        == "idle_hosting"
    )


def test_pipeline_routing_repeat_live_danmaku_goes_to_danmaku_response():
    route = route_for_event(
        _event("live_danmaku"),
        is_transient_event_result=False,
        has_uid_lock=True,
        already_roasted=True,
        entrance_pacing_active=False,
    )

    assert route.response_module_id == "danmaku_response"
    assert route.viewer_gate_reason == "repeat_danmaku"
    assert not route.should_mark_roasted


def test_pipeline_routing_explicit_self_avatar_request_bypasses_repeat_gate():
    route = route_for_event(
        _event("live_danmaku", "猫猫再锐评一下我的头像", "co_stream"),
        is_transient_event_result=False,
        has_uid_lock=True,
        already_roasted=True,
        entrance_pacing_active=False,
    )

    assert route.response_module_id == "avatar_roast"
    assert route.viewer_gate_reason == "explicit_self_avatar_roast"
    assert route.should_mark_roasted is False


def test_pipeline_routing_first_explicit_self_avatar_request_keeps_automatic_mark():
    route = route_for_event(
        _event("live_danmaku", "评价一下我头像"),
        is_transient_event_result=False,
        has_uid_lock=True,
        already_roasted=False,
        entrance_pacing_active=True,
    )

    assert route.response_module_id == "avatar_roast"
    assert route.viewer_gate_reason == "explicit_self_avatar_roast"
    assert route.should_mark_roasted is True


def test_pipeline_routing_explicit_self_avatar_request_respects_existing_gates():
    disabled = route_for_event(
        _event("live_danmaku", "吐槽一下我的头像"),
        is_transient_event_result=False,
        has_uid_lock=True,
        already_roasted=True,
        entrance_pacing_active=False,
        avatar_roast_enabled=False,
    )
    pressured = route_for_event(
        _event("live_danmaku", "rate my avatar", "co_stream"),
        is_transient_event_result=False,
        has_uid_lock=True,
        already_roasted=True,
        entrance_pacing_active=False,
        avatar_roast_allowed=False,
    )

    assert disabled.response_module_id == "danmaku_response"
    assert disabled.viewer_gate_reason == "avatar_roast_disabled"
    assert pressured.response_module_id == "danmaku_response"
    assert pressured.viewer_gate_reason == "avatar_roast_pressure"


def test_explicit_self_avatar_request_classifier_avoids_generic_or_other_targets():
    assert is_explicit_self_avatar_roast_request(
        _event("live_danmaku", "锐评我的头像")
    )
    assert is_explicit_self_avatar_roast_request(
        _event("live_danmaku", "review my profile picture")
    )
    assert not is_explicit_self_avatar_roast_request(
        _event("live_danmaku", "我喜欢我的头像")
    )
    assert not is_explicit_self_avatar_roast_request(
        _event("live_danmaku", "锐评小明的头像")
    )


def test_pipeline_routing_entrance_paced_new_viewer_claims_session_without_avatar_roast():
    route = route_for_event(
        _event("live_danmaku"),
        is_transient_event_result=False,
        has_uid_lock=True,
        already_roasted=False,
        entrance_pacing_active=True,
    )

    assert route.response_module_id == "danmaku_response"
    assert route.viewer_gate_reason == "entrance_pacing"
    assert route.should_mark_roasted


def test_pipeline_routing_falls_back_to_avatar_roast_for_first_live_danmaku():
    route = route_for_event(
        _event("live_danmaku"),
        is_transient_event_result=False,
        has_uid_lock=True,
        already_roasted=False,
        entrance_pacing_active=False,
    )

    assert route.response_module_id == "avatar_roast"
    assert route.should_mark_roasted


def test_pipeline_routing_uses_text_reply_when_first_roast_is_disabled():
    route = route_for_event(
        _event("live_danmaku"),
        is_transient_event_result=False,
        has_uid_lock=True,
        already_roasted=False,
        entrance_pacing_active=False,
        avatar_roast_enabled=False,
    )

    assert route.response_module_id == "danmaku_response"
    assert route.viewer_gate_reason == "avatar_roast_disabled"
    assert route.should_mark_roasted is False


def test_pipeline_routing_live_danmaku_and_activity_pacing_helpers():
    assert is_live_danmaku_with_text(_event("live_danmaku", "hi"))
    assert is_live_danmaku_with_text(_event("manual_live_simulation", "hi"))
    assert not is_live_danmaku_with_text(_event("live_danmaku", ""))
    assert entrance_pacing_interval_seconds("quiet") == 75.0
    assert entrance_pacing_interval_seconds("active") == 30.0
    assert entrance_pacing_interval_seconds("unknown") == 45.0
