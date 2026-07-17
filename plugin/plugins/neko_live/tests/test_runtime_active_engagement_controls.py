from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from plugin.plugins.neko_live.core.contracts import (
    InteractionResult,
    PipelineStep,
    ViewerEvent,
)
from plugin.plugins.neko_live.core.runtime import RoastRuntime


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

def test_recent_interaction_context_summarizes_active_engagement_topic(runtime: RoastRuntime) -> None:
    event = ViewerEvent(
        uid="__neko_active__",
        nickname="NEKO",
        source="active_engagement",
        raw={
            "topic_material": {
                "source": "bili_trending",
                "shape": "either_or",
                "title": "猫猫今天怎么这么安静",
                "key": "bili:BV1",
            }
        },
    )
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=event,
            steps=[PipelineStep("active_engagement", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    context = runtime.recent_interaction_context(limit=1)

    assert context == ["active_engagement / active_engagement: bili_trending either_or - 猫猫今天怎么这么安静"]

def test_recent_interaction_context_includes_active_engagement_family_and_axis(runtime: RoastRuntime) -> None:
    event = ViewerEvent(
        uid="__neko_active__",
        nickname="NEKO",
        source="active_engagement",
        raw={
            "topic_material": {
                "source": "fallback",
                "shape": "either_or",
                "title": "Pick one desk charm",
                "key": "fallback:desk",
                "family": "choice_vote",
                "fun_axis": "choice",
            }
        },
    )
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=event,
            steps=[PipelineStep("active_engagement", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    context = runtime.recent_interaction_context(limit=1)

    assert context == [
        "active_engagement / active_engagement: fallback either_or choice_vote choice - Pick one desk charm"
    ]

def test_recent_interaction_context_includes_active_engagement_reply_intent(runtime: RoastRuntime) -> None:
    event = ViewerEvent(
        uid="__neko_active__",
        nickname="NEKO",
        source="active_engagement",
        raw={
            "topic_material": {
                "source": "fallback",
                "shape": "small_challenge",
                "title": "Pick one tiny room goal",
                "key": "fallback:test",
                "intent": "tiny_answer",
                "family": "micro_challenge",
                "fun_axis": "micro_challenge",
                "reply_affordance": "viewer can answer in a few words",
            }
        },
    )
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=event,
            steps=[PipelineStep("active_engagement", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    context = runtime.recent_interaction_context(limit=1)

    assert context == [
        "active_engagement / active_engagement: fallback small_challenge tiny_answer micro_challenge micro_challenge - Pick one tiny room goal / reply: viewer can answer in a few words"
    ]

@pytest.mark.asyncio
async def test_live_state_marks_active_engagement_candidate_for_solo_quiet(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=90)

    state = await runtime.dashboard_state()

    assert state["live_state"]["state"] == "quiet"
    assert state["active_engagement_status"]["candidate"] is True
    assert state["active_engagement_status"]["eligible"] is True
    assert state["active_engagement_status"]["reason"] == "eligible"

@pytest.mark.asyncio
async def test_active_engagement_waits_longer_after_recent_danmaku_output(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    runtime.config.activity_level = "quiet"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(
        runtime,
        age_seconds=100,
        steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
    )

    state = await runtime.dashboard_state()

    assert state["live_state"]["state"] == "quiet"
    assert state["active_engagement_status"]["candidate"] is True
    assert state["active_engagement_status"]["eligible"] is False
    assert state["active_engagement_status"]["reason"] == "recent_danmaku_output"
    assert state["active_engagement_status"]["minimum_interval_remaining"] == 0.0
    assert 0.0 < state["active_engagement_status"]["recent_danmaku_cooldown_remaining"] <= 120.0
    assert state["live_director_status"]["next_auto_action"] == "active_engagement"
    assert state["live_director_status"]["eligible"] is False
    assert state["live_director_status"]["reason"] == "recent_danmaku_output"

@pytest.mark.asyncio
async def test_active_engagement_active_pacing_allows_shorter_post_danmaku_wait(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    runtime.config.activity_level = "active"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(
        runtime,
        age_seconds=70,
        steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
    )

    state = await runtime.dashboard_state()

    assert state["live_state"]["state"] == "quiet"
    assert state["active_engagement_status"]["candidate"] is True
    assert state["active_engagement_status"]["eligible"] is True
    assert state["active_engagement_status"]["reason"] == "eligible"
    assert state["active_engagement_status"]["recent_danmaku_cooldown_remaining"] == 0.0

@pytest.mark.asyncio
async def test_active_engagement_yields_when_idle_hosting_is_imminent(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=115)

    state = await runtime.dashboard_state()

    assert state["live_state"]["state"] == "quiet"
    assert state["active_engagement_status"]["candidate"] is True
    assert state["active_engagement_status"]["eligible"] is False
    assert state["active_engagement_status"]["reason"] == "approaching_idle_hosting"
    assert 0.0 < state["active_engagement_status"]["idle_hosting_wait_remaining"] <= 5.0
    assert state["live_director_status"]["next_auto_action"] == "idle_hosting"
    assert state["live_director_status"]["eligible"] is False
    assert state["live_director_status"]["reason"] == "approaching_idle_hosting"


@pytest.mark.asyncio
async def test_active_engagement_waits_after_recent_hosting_output(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=90)
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="__neko_idle__",
                nickname="NEKO",
                source="idle_hosting",
                live_mode="solo_stream",
            ),
            steps=[PipelineStep("idle_hosting", "ok"), PipelineStep("neko_dispatcher", "ok")],
            created_at=_created_at_age(6),
        )
    )

    state = await runtime.dashboard_state()
    result = await runtime.maybe_trigger_active_engagement()

    assert result is None
    assert state["active_engagement_status"]["candidate"] is True
    assert state["active_engagement_status"]["eligible"] is False
    assert state["active_engagement_status"]["reason"] == "recent_host_output"
    assert 0.0 < state["active_engagement_status"]["host_output_cooldown_remaining"] <= 30.0


@pytest.mark.asyncio
async def test_idle_hosting_waits_after_recent_hosting_output(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=150)
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="__neko_active__",
                nickname="NEKO",
                source="active_engagement",
                live_mode="solo_stream",
            ),
            steps=[PipelineStep("active_engagement", "ok"), PipelineStep("neko_dispatcher", "ok")],
            created_at=_created_at_age(8),
        )
    )

    state = await runtime.dashboard_state()
    result = await runtime.maybe_trigger_idle_hosting()

    assert result is None
    assert state["live_state"]["state"] == "idle"
    assert state["idle_hosting_status"]["candidate"] is True
    assert state["idle_hosting_status"]["eligible"] is False
    assert state["idle_hosting_status"]["reason"] == "recent_host_output"

@pytest.mark.asyncio
async def test_active_engagement_yields_early_enough_to_observe_idle_hosting(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=95)

    state = await runtime.dashboard_state()

    assert state["live_state"]["state"] == "quiet"
    assert state["active_engagement_status"]["reason"] == "approaching_idle_hosting"
    assert 20.0 <= state["active_engagement_status"]["idle_hosting_wait_remaining"] <= 30.0
    assert state["live_director_status"]["next_auto_action"] == "idle_hosting"

@pytest.mark.asyncio
async def test_trigger_active_engagement_runs_pipeline_for_solo_quiet(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=90)

    result = await runtime.trigger_active_engagement()

    assert result.status == "dry_run"
    assert result.event.source == "active_engagement"
    assert any(step.id == "active_engagement" and step.status == "ok" for step in result.steps)
    assert runtime.recent_results[-1]["event"]["source"] == "active_engagement"


@pytest.mark.asyncio
async def test_active_engagement_control_blocks_manual_and_automatic_triggers(
    runtime: RoastRuntime,
) -> None:
    runtime.config.active_engagement_enabled = False

    result = await runtime.trigger_active_engagement()

    assert result.status == "skipped"
    assert result.reason == "active_engagement.disabled"
    assert await runtime.maybe_trigger_active_engagement() is None

@pytest.mark.asyncio
async def test_active_engagement_valid_recent_danmaku_clears_prior_skip_reason(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "bili trending should still wait", "bvid": "BV_WAIT_SKIP"},
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
        )
    )
    runtime.record_result(
        InteractionResult(
            accepted=False,
            status="skipped",
            reason="safety.cooldown",
            event=ViewerEvent(
                uid="77",
                nickname="viewer2",
                danmaku_text="this skipped line should not poison the next topic",
                source="live_danmaku",
            ),
            steps=[PipelineStep("danmaku_response", "skipped", "safety.cooldown")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "recent_danmaku"
    assert topic["title"] == "keyboard sounds sleepy tonight"
    assert "recent_topic_skip_reason" not in topic

@pytest.mark.asyncio
async def test_active_engagement_does_not_label_non_danmaku_skips_as_danmaku_topic_skip(
    runtime: RoastRuntime,
) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "room mood after skipped active beat", "bvid": "BV_AFTER_ACTIVE_SKIP"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics
    runtime.record_result(
        InteractionResult(
            accepted=False,
            status="skipped",
            reason="active_engagement.minimum_interval",
            event=ViewerEvent(uid="__neko_active__", nickname="NEKO", source="active_engagement"),
            steps=[PipelineStep("active_engagement_gate", "skipped", "minimum_interval")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_AFTER_ACTIVE_SKIP"
    assert "recent_topic_skip_reason" not in topic

@pytest.mark.asyncio
async def test_active_engagement_avoids_repeating_recent_intent_shape(runtime: RoastRuntime) -> None:
    runtime._active_engagement_recent_shapes.extend(["either_or", "either_or"])
    runtime._active_engagement_recent_intents.extend(["quick_vote", "quick_vote"])

    async def fetch_topics(limit: int = 6) -> dict:
        return {"success": True, "videos": []}

    runtime._active_engagement_topic_fetcher = fetch_topics

    topic = await runtime._select_active_engagement_topic()

    assert topic["shape"] != "either_or"
    assert topic["intent"] != "quick_vote"
    assert topic["shape_guard_reason"] == "recent_shape_streak"
    assert "A/B" not in topic["hint"]
    assert "choice" not in topic["hint"].lower()

@pytest.mark.asyncio
async def test_trigger_active_engagement_skips_outside_solo_quiet(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "co_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=90)

    result = await runtime.trigger_active_engagement()

    assert result.status == "skipped"
    assert result.reason == "active_engagement.not_solo_stream"

@pytest.mark.asyncio
async def test_auto_active_engagement_triggers_when_solo_stream_is_quiet(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=90)

    result = await runtime.maybe_trigger_active_engagement()

    assert result is not None
    assert result.status == "dry_run"
    assert result.event.source == "active_engagement"
    assert runtime.recent_results[-1]["event"]["source"] == "active_engagement"

@pytest.mark.asyncio
async def test_auto_active_engagement_respects_minimum_interval(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    runtime._active_engagement_last_attempt_at = 100.0
    runtime._active_engagement_now = lambda: 150.0
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=90)

    result = await runtime.maybe_trigger_active_engagement()

    assert result is None
    state = await runtime.dashboard_state()
    assert state["active_engagement_status"]["reason"] == "minimum_interval"
    assert state["active_engagement_status"]["minimum_interval_remaining"] == 10.0
    assert state["active_engagement_status"]["recent_danmaku_cooldown_remaining"] == 0.0
    assert runtime.recent_results[-1]["event"]["source"] != "active_engagement"

@pytest.mark.asyncio
async def test_activity_level_controls_active_engagement_minimum_interval(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    runtime._active_engagement_last_attempt_at = 100.0
    runtime._active_engagement_now = lambda: 150.0
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=90)

    runtime.config.activity_level = "quiet"
    quiet_state = await runtime.dashboard_state()

    runtime.config.activity_level = "standard"
    standard_state = await runtime.dashboard_state()

    runtime.config.activity_level = "active"
    active_state = await runtime.dashboard_state()

    assert quiet_state["active_engagement_status"]["minimum_interval_seconds"] == 300.0
    assert quiet_state["active_engagement_status"]["minimum_interval_remaining"] == 250.0
    assert standard_state["active_engagement_status"]["minimum_interval_seconds"] == 60.0
    assert standard_state["active_engagement_status"]["minimum_interval_remaining"] == 10.0
    assert active_state["active_engagement_status"]["minimum_interval_seconds"] == 45.0
    assert active_state["active_engagement_status"]["minimum_interval_remaining"] == 0.0

@pytest.mark.asyncio
async def test_auto_active_engagement_does_not_record_skip_when_not_candidate(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=30)

    result = await runtime.maybe_trigger_active_engagement()

    assert result is None

@pytest.mark.asyncio
async def test_auto_active_engagement_takes_over_after_repeated_idle_hosting_without_viewer_response(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=300)
    for age in (180, 120, 60):
        runtime.record_result(
            InteractionResult(
                accepted=True,
                status="pushed",
                event=ViewerEvent(uid="__neko_idle__", nickname="NEKO", source="idle_hosting", live_mode="solo_stream"),
                steps=[PipelineStep("idle_hosting", "ok"), PipelineStep("neko_dispatcher", "ok")],
                created_at=_created_at_age(age),
            )
        )

    before = await runtime.dashboard_state()
    assert before["live_director_status"]["next_auto_action"] == "active_engagement"
    assert before["live_director_status"]["reason"] == "idle_hosting_streak"

    result = await runtime.maybe_trigger_active_engagement()

    assert result is not None
    assert result.status == "dry_run"
    assert result.event.source == "active_engagement"

@pytest.mark.asyncio
async def test_auto_active_engagement_ignores_dry_run_idle_beats(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime._live_listener_started_at = runtime._live_state_now() - 120.0
    runtime.safety_guard.set_connected(True)
    for age in (120, 60):
        runtime.record_result(
            InteractionResult(
                accepted=True,
                status="dry_run",
                event=ViewerEvent(uid="__neko_idle__", nickname="NEKO", source="idle_hosting", live_mode="solo_stream"),
                steps=[PipelineStep("idle_hosting", "ok"), PipelineStep("neko_dispatcher", "dry_run")],
                created_at=_created_at_age(age),
            )
        )

    state = await runtime.dashboard_state()

    assert state["live_state"]["state"] == "idle"
    assert state["live_director_status"]["next_auto_action"] == "idle_hosting"
    assert state["live_director_status"]["reason"] == "solo_idle"

@pytest.mark.asyncio
async def test_live_director_status_picks_active_engagement_for_solo_quiet(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=90)

    state = await runtime.dashboard_state()

    director = state["live_director_status"]
    assert director["next_auto_action"] == "active_engagement"
    assert director["eligible"] is True
    assert director["reason"] == "solo_quiet"
