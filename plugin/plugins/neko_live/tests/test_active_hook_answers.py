from __future__ import annotations

from plugin.plugins.neko_live.core.active_hook_answers import is_active_hook_answer_event
from plugin.plugins.neko_live.core.contracts import LiveConfig, ViewerEvent
from plugin.plugins.neko_live.core.pipeline import LivePipeline
from plugin.plugins.neko_live.core.pipeline_routing import route_for_event
from types import SimpleNamespace


def test_short_option_answer_matches_recent_active_engagement_hook():
    recent_results = [
        {
            "status": "pushed",
            "event": {
                "source": "active_engagement",
                "topic_shape": "either_or",
                "topic_reply_affordance": "viewer can answer with one side",
            },
        }
    ]
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="C",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    assert is_active_hook_answer_event(recent_results, event) is True


def test_short_option_answer_routes_to_danmaku_response():
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="A",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    route = route_for_event(
        event,
        is_transient_event_result=False,
        has_uid_lock=True,
        already_roasted=False,
        entrance_pacing_active=False,
        active_hook_answer=True,
    )

    assert route.response_module_id == "danmaku_response"
    assert route.viewer_gate_reason == "active_hook_answer"
    assert route.should_mark_roasted is False


def test_short_option_answer_without_recent_hook_is_not_special():
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="A",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    assert is_active_hook_answer_event([], event) is False


def test_pipeline_marks_short_option_answer_with_active_hook_hint():
    runtime = SimpleNamespace(
        config=LiveConfig(live_mode="solo_stream"),
        recent_results=[
            {
                "status": "pushed",
                "event": {
                    "source": "active_engagement",
                    "topic_shape": "either_or",
                    "topic_reply_affordance": "viewer can answer with one side",
                },
            }
        ],
    )
    pipeline = LivePipeline(runtime)
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="A",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    route = pipeline._route_for_event(
        event,
        is_transient_event=False,
        has_uid_lock=True,
        already_roasted=False,
    )

    assert route.response_module_id == "danmaku_response"
    assert route.viewer_gate_reason == "active_hook_answer"
    assert event.raw["danmaku_context_hint"] == "active_hook_answer"
