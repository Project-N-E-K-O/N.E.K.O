from __future__ import annotations

import json

import pytest

from plugin.plugins.neko_live.core.contracts import (
    InteractionRequest,
    InteractionResult,
    PipelineStep,
    ViewerEvent,
    ViewerIdentity,
    ViewerProfile,
)
from plugin.plugins.neko_live.core.runtime import LiveRuntime
from plugin.plugins.neko_live.core.runtime_timeline import record_timeline, timeline_for_trace


@pytest.mark.asyncio
async def test_dashboard_state_exposes_runtime_health_rows(runtime: LiveRuntime) -> None:
    event = ViewerEvent(uid="42", nickname="dry", danmaku_text="hi", source="live_danmaku")
    runtime.record_result(
        InteractionResult(
            accepted=False,
            status="dry_run",
            event=event,
            reason="dispatcher.dry_run",
            steps=[PipelineStep("neko_dispatcher", "dry_run", "dry_run(target=none)")],
        )
    )

    state = await runtime.dashboard_state()

    rows = {row["id"]: row for row in state["health_rows"]}
    assert {
        "live_ingest",
        "event_bus",
        "selection",
        "pipeline",
        "safety_guard",
        "dispatcher",
        "config_store",
    }.issubset(rows)
    assert rows["pipeline"]["last_outcome"] == "dry_run"
    assert rows["dispatcher"]["last_outcome"] == "dry_run"
    assert rows["dispatcher"]["last_skip_reason"] == "dispatcher.dry_run"
    assert rows["safety_guard"]["current_state"] == runtime.safety_guard.status()


@pytest.mark.asyncio
async def test_dashboard_state_exposes_latest_response_latency(runtime: LiveRuntime) -> None:
    event = ViewerEvent(
        uid="42",
        nickname="latency",
        danmaku_text="hi",
        source="live_danmaku",
        seen_at="2026-06-20T10:00:00+00:00",
    )
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=event,
            steps=[PipelineStep("neko_dispatcher", "ok", "queued_to_neko(target=default)")],
            created_at="2026-06-20T10:00:03+00:00",
        )
    )

    state = await runtime.dashboard_state()

    assert state["speech_explanation"]["last_result_latency_ms"] == 3000
    rows = {row["id"]: row for row in state["health_rows"]}
    assert rows["pipeline"]["last_latency_ms"] == 3000
    assert rows["dispatcher"]["last_latency_ms"] == 3000


@pytest.mark.asyncio
async def test_dashboard_state_exposes_privacy_safe_live_explanation(runtime: LiveRuntime) -> None:
    identity = ViewerIdentity(uid="1001", nickname="tech")
    raw_danmaku = "这个 AI 插件怎么配置？token=must-not-leak"
    await runtime.viewer_store.record_live_danmaku(identity, raw_danmaku)
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="1001",
                nickname="tech",
                danmaku_text=raw_danmaku,
                source="live_danmaku",
            ),
            steps=[
                PipelineStep("danmaku_response", "ok"),
                PipelineStep("neko_dispatcher", "ok"),
            ],
        )
    )

    state = await runtime.dashboard_state()
    explain = state["live_explain"]

    assert explain["latest_result"]["status"] == "pushed"
    assert explain["latest_result"]["route"] == "danmaku_response"
    assert explain["viewer_memory"]["profile_count"] == 1
    assert explain["viewer_memory"]["profiles_with_preferences"] == 1
    tags = {item["tag"]: item["count"] for item in explain["viewer_memory"]["top_preference_tags"]}
    assert tags["questions"] == 1
    assert tags["tech_ai"] == 1
    assert {row["id"] for row in explain["chain"]} >= {"pipeline", "dispatcher", "selection"}
    assert explain["trace_id"].startswith("tr_")
    assert {row["trace_id"] for row in explain["timeline"]} == {explain["trace_id"]}
    assert [row["stage"] for row in explain["timeline"]] == ["result.record"]

    serialized = json.dumps(explain, ensure_ascii=False)
    assert "这个 AI 插件怎么配置" not in serialized
    assert "must-not-leak" not in serialized


@pytest.mark.asyncio
async def test_dashboard_live_explain_exposes_reply_review_fields(runtime: LiveRuntime) -> None:
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
            "danmaku_profile": "normal_line",
            "danmaku_reply_target": "current_danmaku_meaning",
            "danmaku_reply_shape": "one_compact_reply",
            "danmaku_anchor_hint": "hello",
            "reply_length_mode": "room_bridge",
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

    state = await runtime.dashboard_state()
    latest = state["live_explain"]["latest_result"]

    assert latest["danmaku_profile"] == "normal_line"
    assert latest["danmaku_reply_target"] == "current_danmaku_meaning"
    assert latest["danmaku_reply_shape"] == "one_compact_reply"
    assert latest["danmaku_anchor_hint"] == "hello"
    assert latest["reply_length_mode"] == "room_bridge"
    assert latest["room_theme"] == "small talk"


def test_runtime_timeline_redacts_sensitive_summary_fields(runtime: LiveRuntime) -> None:
    event = ViewerEvent(
        uid="1001",
        nickname="tech",
        danmaku_text="hello",
        source="live_danmaku",
        trace_id="tr_sensitive",
    )

    record_timeline(
        runtime,
        event,
        stage="pipeline.route",
        status="skipped",
        reason="token=must-not-leak",
        route="cookie=must-not-leak",
    )

    timeline = timeline_for_trace(runtime, "tr_sensitive")
    assert timeline == [
        {
            "trace_id": "tr_sensitive",
            "at": timeline[0]["at"],
            "stage": "pipeline.route",
            "status": "skipped",
            "reason": "",
            "route": "",
            "uid": timeline[0]["uid"],
            "source": "live_danmaku",
        }
    ]
    serialized = json.dumps(timeline, ensure_ascii=False)
    assert timeline[0]["uid"].startswith("viewer_")
    assert timeline[0]["uid"] != "1001"
    assert "must-not-leak" not in serialized
    assert "token=" not in serialized
    assert "cookie=" not in serialized


@pytest.mark.asyncio
async def test_dashboard_state_says_ready_when_connected_and_output_enabled(runtime: LiveRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = False
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)

    state = await runtime.dashboard_state()

    assert state["live_status"]["summary"] == "ready_to_stream"
    assert state["live_status"]["reason"] == "ready"
    assert state["live_status"]["can_output"] is True


@pytest.mark.asyncio
async def test_dashboard_state_blocks_output_when_output_channel_unavailable(runtime: LiveRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = False
    runtime.plugin.output_channel_ready = False
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)

    state = await runtime.dashboard_state()

    assert state["live_status"]["summary"] == "cannot_stream"
    assert state["live_status"]["reason"] == "output_channel_unavailable"
    assert state["live_status"]["can_output"] is False
    assert state["speech_explanation"]["summary"] == "cannot_stream"
    assert state["speech_explanation"]["reason"] == "output_channel_unavailable"

    dispatcher_row = next(row for row in state["health_rows"] if row["id"] == "dispatcher")
    assert dispatcher_row["status"] == "blocked"
    assert dispatcher_row["last_skip_reason"] == "output_channel_unavailable"
    assert dispatcher_row["output_channel_ready"] is False


@pytest.mark.asyncio
async def test_dashboard_state_says_test_only_when_dry_run_is_enabled(runtime: LiveRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)

    state = await runtime.dashboard_state()

    assert state["live_status"]["summary"] == "test_only"
    assert state["live_status"]["reason"] == "dry_run"
    assert state["live_status"]["can_output"] is False


@pytest.mark.asyncio
async def test_dashboard_state_explains_manual_pause_as_temporarily_not_speaking(runtime: LiveRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = False
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    runtime.pause()

    state = await runtime.dashboard_state()

    assert state["live_status"]["summary"] == "temporarily_not_speaking"
    assert state["live_status"]["reason"] == "manual_paused"
    assert state["live_status"]["can_output"] is False


@pytest.mark.asyncio
async def test_dashboard_state_says_cannot_stream_without_room(runtime: LiveRuntime) -> None:
    runtime.config.live_room_id = 0

    state = await runtime.dashboard_state()

    assert state["live_status"]["summary"] == "cannot_stream"
    assert state["live_status"]["reason"] == "room_not_configured"
    assert state["live_status"]["can_output"] is False
    assert state["speech_explanation"]["summary"] == "cannot_stream"
    assert state["speech_explanation"]["reason"] == "room_not_configured"


@pytest.mark.asyncio
async def test_speech_explanation_keeps_dry_run_result_visible(runtime: LiveRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)

    await runtime.trigger_warmup_hosting()
    state = await runtime.dashboard_state()

    assert state["speech_explanation"]["summary"] == "test_only"
    assert state["speech_explanation"]["reason"] == "dry_run"
    assert state["speech_explanation"]["last_result_status"] == "dry_run"
    assert state["speech_explanation"]["last_result_reason"] == "dispatcher.dry_run"
    assert state["speech_explanation"]["last_result_source"] == "warmup_hosting"


@pytest.mark.asyncio
async def test_speech_explanation_marks_solo_idle_as_waiting_for_idle_hosting(runtime: LiveRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = False
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)

    state = await runtime.dashboard_state()

    assert state["live_state"]["warmup_hosting_candidate"] is True
    assert state["speech_explanation"]["summary"] == "waiting_for_activity"
    assert state["speech_explanation"]["reason"] == "solo_stream_warmup"
