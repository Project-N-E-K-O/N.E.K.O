from __future__ import annotations

from types import SimpleNamespace

from plugin.plugins.neko_live.modules.live_events.scene_state import (
    SCENE_STATE_PROMPT_MAX_CHARS,
    SceneState,
)


def _result(
    *,
    trace_id: str,
    source: str,
    status: str = "pushed",
    live_mode: str = "solo_stream",
    **event_fields: str,
) -> dict[str, object]:
    event = {
        "source": source,
        "live_mode": live_mode,
        "trace_id": trace_id,
        **event_fields,
    }
    return {
        "status": status,
        "trace_id": trace_id,
        "created_at": f"created:{trace_id}",
        "event": event,
    }


def _event(
    *,
    trace_id: str = "viewer-1",
    live_mode: str = "solo_stream",
    hint: str = "",
    event_type: str = "danmaku",
) -> SimpleNamespace:
    raw = {"event_type": event_type}
    if hint:
        raw["danmaku_context_hint"] = hint
    return SimpleNamespace(
        source="live_danmaku",
        live_mode=live_mode,
        trace_id=trace_id,
        raw=raw,
    )


def test_scene_state_starts_from_successful_solo_host_beat():
    state = SceneState(now=lambda: 100.0)
    state.observe_result(
        _result(
            trace_id="host-1",
            source="active_engagement",
            topic_shape="either_or",
        )
    )

    prompt = state.prompt_for_event(_event())
    status = state.status(live_mode="solo_stream")

    assert prompt.reason == "rendered"
    assert prompt.characters <= SCENE_STATE_PROMPT_MAX_CHARS
    assert "phase=viewer_choice" in prompt.text
    assert "thread=either_or" in prompt.text
    assert "ask no second question" in prompt.text
    assert status["scene_state_active"] is True
    assert status["scene_state_phase"] == "viewer_choice"
    assert status["scene_state_thread_key"] == "either_or"


def test_scene_state_active_hook_answer_gets_one_callback_then_closes():
    state = SceneState(now=lambda: 100.0)
    state.observe_result(
        _result(
            trace_id="host-1",
            source="active_engagement",
            topic_shape="either_or",
        )
    )
    event = _event(trace_id="answer-1", hint="active_hook_answer")

    first = state.prompt_for_event(event)
    duplicate = state.prompt_for_event(event)
    callback_status = state.status(live_mode="solo_stream")
    pushed = _result(trace_id="answer-1", source="live_danmaku")
    pushed["danmaku_profile"] = "active_hook_answer"
    state.observe_result(pushed)
    closed = state.prompt_for_event(_event(trace_id="viewer-2"))
    closed_status = state.status(live_mode="solo_stream")

    assert first.reason == "rendered"
    assert "phase=callback" in first.text
    assert "pay off once" in first.text
    assert duplicate.text == first.text
    assert callback_status["scene_state_phase"] == "viewer_choice"
    assert callback_status["scene_state_viewer_response_count"] == 0
    assert "phase=close" in closed.text
    assert "do not force the old hook" in closed.text
    assert closed_status["scene_state_viewer_response_count"] == 1


def test_scene_state_failed_hook_answer_does_not_advance_the_scene():
    state = SceneState(now=lambda: 100.0)
    state.observe_result(
        _result(
            trace_id="host-1",
            source="active_engagement",
            topic_shape="either_or",
        )
    )
    event = _event(trace_id="answer-1", hint="active_hook_answer")

    callback = state.prompt_for_event(event)
    failed = _result(
        trace_id="answer-1",
        source="live_danmaku",
        status="output_failed",
    )
    failed["danmaku_profile"] = "active_hook_answer"
    state.observe_result(failed)
    after_failure = state.prompt_for_event(_event(trace_id="viewer-2"))
    status = state.status(live_mode="solo_stream")

    assert "phase=callback" in callback.text
    assert "phase=viewer_choice" in after_failure.text
    assert status["scene_state_phase"] == "viewer_choice"
    assert status["scene_state_viewer_response_count"] == 0


def test_scene_state_bounds_viewer_turns_and_transitions():
    state = SceneState(now=lambda: 100.0)
    state.observe_result(
        _result(
            trace_id="idle-1",
            source="idle_hosting",
            host_beat_shape="soft_observation",
        )
    )

    for index in range(3):
        state.observe_result(
            _result(trace_id=f"viewer-{index}", source="live_danmaku")
        )

    status = state.status(live_mode="solo_stream")
    assert status["scene_state_phase"] == "close"
    assert status["scene_state_viewer_turn_count"] == 3

    state.observe_result(_result(trace_id="viewer-4", source="live_danmaku"))
    transitioned = state.status(live_mode="solo_stream")
    assert transitioned["scene_state_active"] is False
    assert transitioned["scene_state_phase"] == "idle"


def test_scene_state_ignores_non_pushed_co_stream_and_support_results():
    state = SceneState(now=lambda: 100.0)
    for result in (
        _result(
            trace_id="dry",
            source="active_engagement",
            status="dry_run",
            topic_shape="either_or",
        ),
        _result(
            trace_id="co",
            source="active_engagement",
            live_mode="co_stream",
            topic_shape="either_or",
        ),
        _result(
            trace_id="gift",
            source="live_danmaku",
            event_type="gift",
        ),
    ):
        state.observe_result(result)

    status = state.status(live_mode="solo_stream")
    assert status["scene_state_active"] is False
    assert state.prompt_for_event(
        _event(live_mode="co_stream")
    ).reason == "inactive_mode"


def test_scene_state_expires_lazily_and_resets_without_transcript():
    now = [100.0]
    state = SceneState(now=lambda: now[0])
    secret = "token=must-not-leak"
    state.observe_result(
        _result(
            trace_id="host-secret",
            source="active_engagement",
            topic_shape=secret,
            topic_title=secret,
        )
    )

    before = state.status(live_mode="solo_stream")
    assert before["scene_state_active"] is True
    assert before["scene_state_thread_key"] == ""
    assert secret not in str(before)

    now[0] = 221.0
    expired = state.status(live_mode="solo_stream")
    assert expired["scene_state_active"] is False
    assert expired["scene_state_expired_count"] == 1

    state.reset()
    reset = state.status(live_mode="solo_stream")
    assert reset["scene_state_transition_count"] == 0
    assert reset["scene_state_expired_count"] == 0
    assert reset["scene_state_prompt_uses"] == 0


def test_scene_state_support_event_never_receives_scene_prompt():
    state = SceneState(now=lambda: 100.0)
    state.observe_result(
        _result(
            trace_id="host-1",
            source="active_engagement",
            topic_shape="tiny_tease",
        )
    )

    prompt = state.prompt_for_event(
        _event(trace_id="support", event_type="super_chat")
    )

    assert prompt.text == ""
    assert prompt.reason == "unsupported_event"
