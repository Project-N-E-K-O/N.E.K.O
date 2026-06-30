from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

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


class ConfigApi:
    def __init__(self) -> None:
        self.updates: list[dict] = []
        self.ensure_payloads: list[dict] = []
        self.update_entered: asyncio.Event | None = None
        self.resume_update: asyncio.Event | None = None

    async def dump(self, timeout: float = 0) -> dict:
        return {"neko_roast": {}}

    async def update(self, payload: dict) -> None:
        if self.update_entered is not None:
            self.update_entered.set()
        if self.resume_update is not None:
            await self.resume_update.wait()
        self.updates.append(payload)

    async def profile_ensure_active(self, _profile: str, payload: dict, timeout: float = 0) -> None:
        self.ensure_payloads.append(payload)


class Plugin:
    def __init__(self, tmp_path: Path) -> None:
        self.config = ConfigApi()
        self.ctx = None
        self.logger = None
        self._data_path = tmp_path
        self.pushed_messages: list[dict] = []
        self.output_channel_ready = True

    def data_path(self) -> Path:
        return self._data_path

    def push_message(self, **kwargs):
        self.pushed_messages.append(kwargs)
        return None


class FakeIngest:
    def __init__(self) -> None:
        self.started: list[int] = []
        self.stopped = 0
        self.room_id = 0
        self.start_result = True

    def is_listening(self) -> bool:
        return self.room_id > 0

    def listener_state(self) -> dict:
        if not self.is_listening():
            return {"state": "disconnected", "room_id": self.room_id, "viewer_count": 0}
        return {"state": "connected", "room_id": self.room_id, "viewer_count": 0}

    async def start_listening(self, room_id: int) -> bool:
        await self.stop_listening()
        self.started.append(room_id)
        if not self.start_result:
            self.room_id = 0
            return False
        self.room_id = room_id
        return True

    async def stop_listening(self) -> None:
        if self.room_id > 0:
            self.stopped += 1
        self.room_id = 0


@pytest.fixture
def runtime(tmp_path: Path) -> RoastRuntime:
    rt = RoastRuntime(Plugin(tmp_path))
    rt.bili_live_ingest = FakeIngest()
    rt.avatar_roast.ctx = rt
    rt.active_engagement.ctx = rt
    rt.warmup_hosting.ctx = rt
    rt.bili_identity.ctx = rt
    return rt


def test_dashboard_actions_include_manual_hosting_actions(runtime: RoastRuntime) -> None:
    action_ids = {action["id"] for action in runtime.dashboard_actions()}

    assert "trigger_idle_hosting" in action_ids
    assert "trigger_warmup_hosting" in action_ids
    assert "trigger_active_engagement" in action_ids


def test_dashboard_actions_include_clear_viewer_profiles(runtime: RoastRuntime) -> None:
    action_ids = {action["id"] for action in runtime.dashboard_actions()}

    assert "clear_viewer_profiles" in action_ids


@pytest.mark.asyncio
async def test_sync_live_instructions_does_not_push_when_live_disabled(runtime: RoastRuntime) -> None:
    runtime.config.live_enabled = False

    result = await runtime.sync_live_instructions()

    assert result == "not_injected"
    assert runtime.instructions_injected is False
    assert runtime.plugin.pushed_messages == []


@pytest.mark.asyncio
async def test_connect_live_room_injects_live_instructions_after_listener_starts(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123

    snapshot = await runtime.connect_live_room()

    assert snapshot["connected"] is True
    assert runtime.instructions_injected is True
    assert len(runtime.plugin.pushed_messages) == 1
    assert runtime.plugin.pushed_messages[0]["metadata"]["description"] == "Neko Roast behavior instructions"


@pytest.mark.asyncio
async def test_connect_live_room_resets_idle_hosting_failure_counter(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime._idle_hosting_consecutive_failures = runtime._IDLE_HOSTING_FAILURE_LIMIT

    snapshot = await runtime.connect_live_room()

    assert snapshot["connected"] is True
    assert runtime._idle_hosting_consecutive_failures == 0


@pytest.mark.asyncio
async def test_clear_viewer_profiles_resets_profiles_without_clearing_results(runtime: RoastRuntime) -> None:
    runtime.config.developer_tools_enabled = True
    await runtime.viewer_store.upsert_identity(ViewerIdentity(uid="1001", nickname="viewer"))
    await runtime.viewer_store.mark_roasted("1001", "first roast")
    runtime.recent_results.appendleft(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(uid="1001", nickname="viewer", danmaku_text="hello", source="live"),
            output="keep result",
        )
    )

    result = await runtime.clear_viewer_profiles()

    assert result["cleared"] == 1
    assert await runtime.viewer_store.recent_profiles() == []
    assert len(runtime.recent_results) == 1
    assert runtime.recent_results[0].output == "keep result"
    assert runtime.audit.recent(1)[0]["op"] == "viewer_profiles_clear"


@pytest.mark.asyncio
async def test_clear_viewer_profiles_resets_pipeline_session_state(runtime: RoastRuntime) -> None:
    runtime.config.developer_tools_enabled = True
    calls = 0

    def clear_marker() -> None:
        nonlocal calls
        calls += 1

    runtime.pipeline.clear_dry_run_session_state = clear_marker

    await runtime.clear_viewer_profiles()

    assert calls == 1


@pytest.mark.asyncio
async def test_clear_viewer_profiles_requires_developer_mode(runtime: RoastRuntime) -> None:
    runtime.config.developer_tools_enabled = False
    await runtime.viewer_store.upsert_identity(ViewerIdentity(uid="1001", nickname="viewer"))

    with pytest.raises(PermissionError):
        await runtime.clear_viewer_profiles()

    assert [profile["uid"] for profile in await runtime.viewer_store.recent_profiles()] == ["1001"]


@pytest.mark.asyncio
async def test_handle_manual_event_requires_developer_mode(runtime: RoastRuntime) -> None:
    runtime.config.developer_tools_enabled = False
    runtime.config.live_enabled = True

    with pytest.raises(PermissionError):
        await runtime.handle_manual_event(uid="1001", nickname="viewer", danmaku_text="hello")


@pytest.mark.asyncio
async def test_update_config_restarts_listener_when_room_changes(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 100
    runtime.config.live_enabled = True
    await runtime.bili_live_ingest.start_listening(100)

    await runtime.update_config({"live_room_id": 200, "live_enabled": True})

    assert runtime.bili_live_ingest.started == [100, 200]
    assert runtime.bili_live_ingest.stopped == 1
    assert runtime.bili_live_ingest.room_id == 200
    assert runtime.config.live_enabled is True
    assert runtime.live_connection_snapshot()["connected"] is True


@pytest.mark.asyncio
async def test_set_live_room_stops_listener_when_room_switch_fails(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 100
    runtime.config.live_enabled = True
    await runtime.bili_live_ingest.start_listening(100)
    runtime.bili_live_ingest.start_result = False

    await runtime.set_live_room(200)

    assert runtime.bili_live_ingest.stopped == 1
    assert runtime.bili_live_ingest.room_id == 0
    assert runtime.config.live_room_id == 200
    assert runtime.config.live_enabled is False
    assert runtime.live_connection_snapshot()["connected"] is False


@pytest.mark.asyncio
async def test_connect_live_room_rolls_back_live_enabled_when_start_fails(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.bili_live_ingest.start_result = False

    snapshot = await runtime.connect_live_room()

    assert snapshot["connected"] is False
    assert runtime.config.live_enabled is False
    assert runtime.safety_guard.connected is False
    assert runtime.instructions_injected is False
    assert runtime.plugin.pushed_messages == []


@pytest.mark.asyncio
async def test_connect_live_room_switches_active_room_without_double_start(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 100
    runtime.config.live_enabled = True
    await runtime.bili_live_ingest.start_listening(100)

    snapshot = await runtime.connect_live_room(200)

    assert snapshot["connected"] is True
    assert runtime.config.live_room_id == 200
    assert runtime.bili_live_ingest.started == [100, 200]
    assert runtime.bili_live_ingest.stopped == 1
    assert runtime.bili_live_ingest.room_id == 200


@pytest.mark.asyncio
async def test_connect_live_room_resets_dry_run_session_marker(runtime: RoastRuntime) -> None:
    calls = 0

    def clear_marker() -> None:
        nonlocal calls
        calls += 1

    runtime.config.live_room_id = 123
    runtime.pipeline.clear_dry_run_session_state = clear_marker

    snapshot = await runtime.connect_live_room()

    assert snapshot["connected"] is True
    assert calls == 1


@pytest.mark.asyncio
async def test_disconnect_during_room_update_is_not_undone_by_stale_listener_snapshot(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 100
    runtime.config.live_enabled = True
    await runtime.bili_live_ingest.start_listening(100)
    runtime.plugin.config.update_entered = asyncio.Event()
    runtime.plugin.config.resume_update = asyncio.Event()

    update_task = asyncio.create_task(runtime.update_config({"live_room_id": 200}))
    await runtime.plugin.config.update_entered.wait()
    await runtime.disconnect_live_room()
    runtime.plugin.config.resume_update.set()
    _ = await update_task

    assert runtime.config.live_room_id == 200
    assert runtime.config.live_enabled is False
    assert runtime.bili_live_ingest.room_id == 0
    assert runtime.bili_live_ingest.started == [100]
    assert runtime.live_connection_snapshot()["connected"] is False


@pytest.mark.asyncio
async def test_config_fallback_does_not_persist_ephemeral_live_enabled(runtime: RoastRuntime) -> None:
    runtime.plugin.ctx = SimpleNamespace(update_own_config=None)
    runtime.config.live_enabled = True

    await runtime.update_config({"dry_run": False})

    assert runtime.plugin.config.ensure_payloads == [{"neko_roast": {"dry_run": False}}]
    assert runtime.plugin.config.updates == [{"neko_roast": {"dry_run": False}}]


@pytest.mark.asyncio
async def test_dashboard_state_exposes_runtime_health_rows(runtime: RoastRuntime) -> None:
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
async def test_dashboard_state_exposes_latest_response_latency(runtime: RoastRuntime) -> None:
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
async def test_dashboard_state_says_ready_when_connected_and_output_enabled(runtime: RoastRuntime) -> None:
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
async def test_dashboard_state_blocks_output_when_output_channel_unavailable(runtime: RoastRuntime) -> None:
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
async def test_dashboard_state_says_test_only_when_dry_run_is_enabled(runtime: RoastRuntime) -> None:
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
async def test_dashboard_state_explains_manual_pause_as_temporarily_not_speaking(runtime: RoastRuntime) -> None:
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
async def test_dashboard_state_says_cannot_stream_without_room(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 0

    state = await runtime.dashboard_state()

    assert state["live_status"]["summary"] == "cannot_stream"
    assert state["live_status"]["reason"] == "room_not_configured"
    assert state["live_status"]["can_output"] is False
    assert state["speech_explanation"]["summary"] == "cannot_stream"
    assert state["speech_explanation"]["reason"] == "room_not_configured"


@pytest.mark.asyncio
async def test_speech_explanation_keeps_dry_run_result_visible(runtime: RoastRuntime) -> None:
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
async def test_speech_explanation_marks_solo_idle_as_waiting_for_idle_hosting(runtime: RoastRuntime) -> None:
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


def _created_at_age(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _record_result_at(
    runtime: RoastRuntime,
    *,
    age_seconds: int,
    status: str = "pushed",
    source: str = "live_danmaku",
    steps: list[PipelineStep] | None = None,
) -> None:
    event = ViewerEvent(uid="42", nickname="viewer", danmaku_text="hi", source=source)  # type: ignore[arg-type]
    runtime.record_result(
        InteractionResult(
            accepted=status == "pushed",
            status=status,
            event=event,
            steps=steps or [],
            created_at=_created_at_age(age_seconds),
        )
    )


def test_recent_interaction_context_summarizes_routes_and_viewer_text(runtime: RoastRuntime) -> None:
    first_event = ViewerEvent(uid="42", nickname="viewer", danmaku_text="第一次来", source="live_danmaku")
    second_event = ViewerEvent(uid="__neko_idle__", nickname="NEKO", source="idle_hosting")
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=first_event,
            steps=[PipelineStep("avatar_roast", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )
    runtime.record_result(
        InteractionResult(
            accepted=False,
            status="dry_run",
            event=second_event,
            reason="dispatcher.dry_run",
            steps=[PipelineStep("avatar_roast", "ok"), PipelineStep("neko_dispatcher", "dry_run")],
        )
    )

    context = runtime.recent_interaction_context(limit=2)

    assert context == [
        "idle_hosting / idle_hosting: solo quiet-room host beat",
        "avatar_roast / live_danmaku from viewer: 第一次来",
    ]

def test_viewer_session_context_keeps_same_uid_recent_danmaku(runtime: RoastRuntime) -> None:
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(uid="42", nickname="viewer", danmaku_text="第一次来", source="live_danmaku"),
            identity=ViewerIdentity(uid="42", nickname="viewer"),
            steps=[PipelineStep("avatar_roast", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(uid="77", nickname="other", danmaku_text="别人的弹幕", source="live_danmaku"),
            identity=ViewerIdentity(uid="77", nickname="other"),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="dry_run",
            event=ViewerEvent(uid="42", nickname="viewer", danmaku_text="那你继续说", source="live_danmaku"),
            identity=ViewerIdentity(uid="42", nickname="viewer"),
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "dry_run")],
        )
    )

    context = runtime.viewer_session_context("42")

    assert context == [
        "danmaku_response: 那你继续说",
        "avatar_roast: 第一次来",
    ]


def test_live_state_viewer_activity_ignores_non_danmaku_health_rows(runtime: RoastRuntime) -> None:
    rows = [
        {"id": "live_ingest", "age_sec": 1.0, "last_outcome": "entry"},
        {"id": "event_bus", "age_sec": 2.0, "last_outcome": "gift"},
        {"id": "selection", "age_sec": 3.0, "last_outcome": "super_chat"},
    ]

    assert runtime._last_viewer_activity_age_sec(rows) is None


def test_live_state_viewer_activity_keeps_danmaku_health_rows(runtime: RoastRuntime) -> None:
    rows = [
        {"id": "live_ingest", "age_sec": 1.0, "last_outcome": "entry"},
        {"id": "event_bus", "age_sec": 8.0, "last_outcome": "danmaku"},
        {"id": "selection", "age_sec": 12.0, "last_outcome": "live_danmaku"},
    ]

    assert runtime._last_viewer_activity_age_sec(rows) == 8.0


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


def test_recent_interaction_context_includes_spent_neko_output(runtime: RoastRuntime) -> None:
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
            output="old snack reward bit",
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    context = runtime.recent_interaction_context(limit=1)

    assert context == [
        "danmaku_response / live_danmaku from viewer: one more line / spent_output_family=reward / NEKO already said: old snack reward bit"
    ]


def test_recent_interaction_context_ignores_dispatcher_placeholder_output(runtime: RoastRuntime) -> None:
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
            output="queued_to_neko(target=Lanlan, ai_behavior=respond, visibility=none)",
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    context = runtime.recent_interaction_context(limit=1)

    assert context == ["danmaku_response / live_danmaku from viewer: one more line"]
    assert "NEKO already said" not in context[0]


def test_viewer_session_context_includes_spent_neko_output(runtime: RoastRuntime) -> None:
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="same viewer line",
                source="live_danmaku",
            ),
            output="old avatar joke",
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    context = runtime.viewer_session_context("42", limit=1)

    assert context == ["danmaku_response: same viewer line / NEKO already said: old avatar joke"]


def test_recent_interaction_context_marks_spent_output_families(runtime: RoastRuntime) -> None:
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="say something",
                source="live_danmaku",
            ),
            output="小鱼干奖励先记账，等弹幕接一句",
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    context = runtime.recent_interaction_context(limit=1)

    assert "spent_output_family=reward,audience_prompt" in context[0]
    assert "NEKO already said" in context[0]


def test_spent_output_family_does_not_treat_common_or_as_choice_vote(runtime: RoastRuntime) -> None:
    assert "choice_vote" not in runtime._spent_output_families("short reaction for viewer")
    assert "choice_vote" in runtime._spent_output_families("either_or room choice")


def test_spent_output_family_matches_english_tokens_as_words(runtime: RoastRuntime) -> None:
    families = runtime._spent_output_families("I can explain this catch without a presentation.")

    assert "program_plan" not in families
    assert "audience_prompt" not in families
    assert "reward" not in families
    assert "program_plan" in runtime._spent_output_families("tiny plan for the room")
    assert "audience_prompt" in runtime._spent_output_families("chat can answer this")
    assert "reward" in runtime._spent_output_families("gift for the first answer")


def test_spent_output_family_marks_live_audience_prompt_variants(runtime: RoastRuntime) -> None:
    for output in (
        "大家想听猫猫聊点什么",
        "你们想看猫猫做什么，发弹幕说一句",
        "来一句短弹幕给猫猫接话",
        "给猫猫打个分或者打个标签",
        "还在的观众扣个1，猫猫看看有没有人",
        "给猫猫一点反应，吱一声也行",
        "drop a 1 if the chat is still alive",
        "直播间还有人吗，猫猫探头",
        "有人在吗，猫猫确认一下信号",
        "anyone here with a tiny signal",
    ):
        assert "audience_prompt" in runtime._spent_output_families(output)


def test_spent_output_family_does_not_mark_example_phrase_as_audience_prompt(runtime: RoastRuntime) -> None:
    assert "audience_prompt" not in runtime._spent_output_families("猫猫打个比方，这局像开盲盒")


def test_recent_spent_output_family_keeps_longer_live_window(runtime: RoastRuntime) -> None:
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(uid="viewer_reward", nickname="viewer", source="live_danmaku"),
            output="小鱼干奖励先收好。",
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )
    for index in range(8):
        runtime.record_result(
            InteractionResult(
                accepted=True,
                status="pushed",
                event=ViewerEvent(uid=f"viewer_{index}", nickname="viewer", source="live_danmaku"),
                output=f"普通回应 {index}",
                steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
            )
        )

    assert "reward" in runtime._recent_spent_output_families()


def test_viewer_session_context_ignores_dry_run_placeholder_output(runtime: RoastRuntime) -> None:
    runtime.record_result(
        InteractionResult(
            accepted=False,
            status="dry_run",
            event=ViewerEvent(
                uid="42",
                nickname="viewer",
                danmaku_text="same viewer line",
                source="live_danmaku",
            ),
            output="dry_run(target=none, ai_behavior=respond)",
            reason="dispatcher.dry_run",
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "dry_run")],
        )
    )

    context = runtime.viewer_session_context("42", limit=1)

    assert context == ["danmaku_response: same viewer line"]
    assert "NEKO already said" not in context[0]


def test_record_result_exposes_response_module_and_gift_signal(runtime: RoastRuntime) -> None:
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

    assert latest["spent_output_family"] == "reward,audience_prompt"


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
async def test_handle_live_payload_records_gift_as_signal_only_without_avatar_roast(runtime: RoastRuntime) -> None:
    runtime.config.dry_run = False
    runtime.config.live_mode = "solo_stream"
    runtime.config.live_enabled = True
    runtime.bili_live_ingest = BiliLiveIngestModule()
    runtime.bili_live_ingest.ctx = runtime

    result = await runtime.handle_live_payload(
        {
            "uid": "42",
            "nickname": "viewer",
            "text": "sent a small gift",
            "event_type": "gift",
        }
    )

    assert result.status == "skipped"
    assert result.reason == "live_event_signal.unsupported_gift"
    latest = runtime.recent_results[-1]
    assert latest["event_signal"] == "gift_signal"
    assert latest["response_module"] == "gift_signal"
    assert latest["event"]["event_type"] == "gift"
    assert all(step["id"] != "avatar_roast" for step in latest["steps"])


@pytest.mark.asyncio
async def test_live_state_marks_recent_activity_as_engaged(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = False
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=30)

    state = await runtime.dashboard_state()

    assert state["live_state"]["state"] == "engaged"
    assert state["live_state"]["reason"] == "recent_activity"
    assert state["live_state"]["idle_hosting_candidate"] is False


@pytest.mark.asyncio
async def test_live_state_marks_activity_gap_as_quiet(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = False
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=90)

    state = await runtime.dashboard_state()

    assert state["live_state"]["state"] == "quiet"
    assert state["live_state"]["reason"] == "quiet_activity_gap"
    assert state["live_state"]["idle_hosting_candidate"] is False


@pytest.mark.asyncio
async def test_live_state_uses_viewer_activity_not_neko_output_for_idle(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = False
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=240)
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(uid="__neko_active__", nickname="NEKO", source="active_engagement", live_mode="solo_stream"),
            steps=[PipelineStep("active_engagement", "ok"), PipelineStep("neko_dispatcher", "ok")],
            created_at=_created_at_age(10),
        )
    )

    state = await runtime.dashboard_state()

    assert state["live_state"]["state"] == "idle"
    assert state["live_state"]["reason"] == "no_recent_activity"
    assert state["live_state"]["last_viewer_activity_age_sec"] >= 200
    assert state["live_state"]["last_output_age_sec"] <= 20
    assert state["live_state"]["idle_hosting_candidate"] is True


@pytest.mark.asyncio
async def test_live_state_marks_solo_stream_without_activity_as_warmup(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)

    state = await runtime.dashboard_state()

    assert state["live_state"]["state"] == "warmup"
    assert state["live_state"]["reason"] == "solo_stream_warmup"
    assert state["live_state"]["warmup_hosting_candidate"] is True
    assert state["live_director_status"]["next_auto_action"] == "warmup_hosting"
    assert state["live_director_status"]["reason"] == "solo_warmup"


@pytest.mark.asyncio
async def test_live_state_times_out_warmup_to_idle_when_no_one_speaks(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    runtime._live_state_now = lambda: 100.0
    await runtime._start_live_listener(123)
    runtime._live_state_now = lambda: 160.0

    state = await runtime.dashboard_state()

    assert state["live_state"]["state"] == "idle"
    assert state["live_state"]["reason"] == "no_recent_activity"
    assert state["live_state"]["warmup_hosting_candidate"] is False
    assert state["live_state"]["idle_hosting_candidate"] is True
    assert state["live_director_status"]["next_auto_action"] == "idle_hosting"


@pytest.mark.asyncio
async def test_live_state_moves_from_warmup_to_idle_when_no_viewer_activity(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="dry_run",
            event=ViewerEvent(uid="__neko_warmup__", nickname="NEKO", source="warmup_hosting", live_mode="solo_stream"),
            steps=[PipelineStep("warmup_hosting", "ok"), PipelineStep("neko_dispatcher", "dry_run")],
            created_at=_created_at_age(240),
        )
    )

    state = await runtime.dashboard_state()

    assert state["live_state"]["state"] == "idle"
    assert state["live_state"]["reason"] == "no_recent_activity"
    assert state["live_state"]["warmup_hosting_candidate"] is False
    assert state["live_state"]["idle_hosting_candidate"] is True


@pytest.mark.asyncio
async def test_live_state_allows_idle_hosting_candidate_only_for_solo_stream(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = False
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=240)

    state = await runtime.dashboard_state()

    assert state["live_state"]["state"] == "idle"
    assert state["live_state"]["reason"] == "no_recent_activity"
    assert state["live_state"]["mode_role"] == "solo_host"
    assert state["live_state"]["idle_hosting_candidate"] is True
    assert state["idle_hosting_status"]["eligible"] is True
    assert state["idle_hosting_status"]["cooldown_remaining"] == 0.0


@pytest.mark.asyncio
async def test_idle_hosting_status_explains_minimum_interval(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    runtime._idle_hosting_last_attempt_at = 100.0
    runtime._idle_hosting_now = lambda: 150.0
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=240)

    state = await runtime.dashboard_state()

    assert state["live_state"]["idle_hosting_candidate"] is True
    assert state["idle_hosting_status"]["eligible"] is False
    assert state["idle_hosting_status"]["reason"] == "minimum_interval"
    assert state["idle_hosting_status"]["cooldown_remaining"] == 40.0
    assert state["idle_hosting_status"]["min_interval_seconds"] == 90.0


@pytest.mark.asyncio
async def test_activity_level_controls_idle_hosting_minimum_interval(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    runtime._idle_hosting_last_attempt_at = 100.0
    runtime._idle_hosting_now = lambda: 150.0
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)

    runtime.config.activity_level = "quiet"
    quiet_state = await runtime.dashboard_state()
    assert quiet_state["idle_hosting_status"]["min_interval_seconds"] == 180.0
    assert quiet_state["idle_hosting_status"]["cooldown_remaining"] == 130.0

    runtime.config.activity_level = "active"
    active_state = await runtime.dashboard_state()
    assert active_state["idle_hosting_status"]["min_interval_seconds"] == 45.0
    assert active_state["idle_hosting_status"]["cooldown_remaining"] == 0.0


@pytest.mark.asyncio
async def test_activity_level_controls_live_state_thresholds(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = False
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=121)

    runtime.config.activity_level = "quiet"
    quiet_state = await runtime.dashboard_state()
    assert quiet_state["live_state"]["state"] == "quiet"
    assert quiet_state["live_state"]["idle_hosting_candidate"] is False
    assert quiet_state["live_state"]["engaged_threshold_seconds"] == 90.0
    assert quiet_state["live_state"]["idle_threshold_seconds"] == 300.0

    runtime.config.activity_level = "standard"
    standard_state = await runtime.dashboard_state()
    assert standard_state["live_state"]["state"] == "idle"
    assert standard_state["live_state"]["idle_hosting_candidate"] is True
    assert standard_state["live_state"]["engaged_threshold_seconds"] == 60.0
    assert standard_state["live_state"]["idle_threshold_seconds"] == 120.0

    runtime.config.activity_level = "active"
    active_state = await runtime.dashboard_state()
    assert active_state["live_state"]["state"] == "idle"
    assert active_state["live_state"]["idle_hosting_candidate"] is True
    assert active_state["live_state"]["engaged_threshold_seconds"] == 30.0
    assert active_state["live_state"]["idle_threshold_seconds"] == 90.0


@pytest.mark.asyncio
async def test_live_state_allows_idle_hosting_candidate_in_dry_run(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=240)

    state = await runtime.dashboard_state()

    assert state["live_status"]["summary"] == "test_only"
    assert state["live_state"]["state"] == "idle"
    assert state["live_state"]["idle_hosting_candidate"] is True


@pytest.mark.asyncio
async def test_live_state_keeps_co_stream_idle_from_becoming_candidate(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = False
    runtime.config.live_mode = "co_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=240)

    state = await runtime.dashboard_state()

    assert state["live_state"]["state"] == "idle"
    assert state["live_state"]["mode_role"] == "companion"
    assert state["live_state"]["idle_hosting_candidate"] is False


@pytest.mark.asyncio
async def test_live_state_paused_and_blocked_take_priority(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = False
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=240)
    runtime.pause()

    paused_state = await runtime.dashboard_state()
    assert paused_state["live_state"]["state"] == "paused"
    assert paused_state["live_state"]["idle_hosting_candidate"] is False

    await runtime.disconnect_live_room()
    blocked_state = await runtime.dashboard_state()
    assert blocked_state["live_state"]["state"] == "blocked"
    assert blocked_state["live_state"]["idle_hosting_candidate"] is False


@pytest.mark.asyncio
async def test_trigger_idle_hosting_dry_run_records_pipeline_result(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=240)

    result = await runtime.trigger_idle_hosting()

    assert result.status == "dry_run"
    assert result.reason == "dispatcher.dry_run"
    assert result.event.source == "idle_hosting"
    assert result.event.live_mode == "solo_stream"
    assert result.request is not None
    assert "solo idle hosting" in result.request.prompt_text
    assert "one short live-host line" in result.request.prompt_text
    assert "tiny live-room topic" in result.request.prompt_text
    assert "sound like NEKO hosting" in result.request.prompt_text
    assert "low-pressure reply hook" in result.request.prompt_text
    assert "Do not announce that nobody is talking" in result.request.prompt_text
    assert "last_activity_age_sec" not in result.request.prompt_text
    assert "nobody is here" not in result.request.prompt_text
    assert "beg for comments" not in result.request.prompt_text
    assert "welcome everyone" not in result.request.prompt_text
    assert "please interact" not in result.request.prompt_text
    assert runtime.recent_results[-1]["status"] == "dry_run"
    assert runtime.recent_results[-1]["event"]["source"] == "idle_hosting"
    assert runtime.plugin.pushed_messages == []


def test_idle_hosting_event_rotates_host_beats(runtime: RoastRuntime) -> None:
    events = [runtime._idle_hosting_event({"state": "idle"}) for _ in range(4)]
    beats = [event.raw["host_beat"] for event in events]

    assert len({beat["key"] for beat in beats}) == 4
    assert len({beat["fun_axis"] for beat in beats}) >= 4
    assert all(left["fun_axis"] != right["fun_axis"] for left, right in zip(beats, beats[1:]))
    assert all(beat["shape"] for beat in beats)
    assert all(beat["fun_axis"] for beat in beats)
    assert all(beat["hint"] for beat in beats)
    assert all(beat["reply_affordance"] for beat in beats)


def test_idle_hosting_skips_similar_recent_beat_titles(runtime: RoastRuntime, monkeypatch: pytest.MonkeyPatch) -> None:
    candidates = [
        {
            "key": "idle:cat-radio-a",
            "shape": "soft_observation",
            "fun_axis": "mood",
            "title": "今晚猫猫小电台怎么开场",
            "hint": "first",
            "reply_affordance": "viewer can answer with one mood word",
        },
        {
            "key": "idle:cat-radio-b",
            "shape": "tiny_choice",
            "fun_axis": "choice",
            "title": "今晚猫猫小电台开场方式",
            "hint": "similar",
            "reply_affordance": "viewer can pick one side",
        },
        {
            "key": "idle:desk-snack",
            "shape": "tiny_choice",
            "fun_axis": "choice",
            "title": "桌面零食二选一",
            "hint": "fresh",
            "reply_affordance": "viewer can pick one snack",
        },
    ]
    monkeypatch.setattr(runtime, "_idle_hosting_beat_candidates", lambda: list(candidates))

    first = runtime._next_idle_hosting_beat()
    second = runtime._next_idle_hosting_beat()

    assert first["key"] == "idle:cat-radio-a"
    assert second["key"] == "idle:desk-snack"


def test_idle_hosting_prefers_fresh_reply_affordance(
    runtime: RoastRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [
        {
            "key": "idle:mood-one",
            "shape": "soft_observation",
            "fun_axis": "mood",
            "title": "quiet mood one",
            "hint": "first",
            "reply_affordance": "viewer can answer with one mood word",
        },
        {
            "key": "idle:choice-same-reply",
            "shape": "tiny_choice",
            "fun_axis": "choice",
            "title": "fresh choice",
            "hint": "same reply path",
            "reply_affordance": "viewer can answer with one mood word",
        },
        {
            "key": "idle:tease-new-reply",
            "shape": "tiny_tease",
            "fun_axis": "tease",
            "title": "fresh tease",
            "hint": "fresh reply path",
            "reply_affordance": "viewer can tease NEKO back",
        },
    ]
    monkeypatch.setattr(runtime, "_idle_hosting_beat_candidates", lambda: list(candidates))

    first = runtime._next_idle_hosting_beat()
    second = runtime._next_idle_hosting_beat()

    assert first["key"] == "idle:mood-one"
    assert second["key"] == "idle:tease-new-reply"


def test_idle_hosting_falls_back_when_all_beat_titles_are_similar(
    runtime: RoastRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [
        {
            "key": "idle:cat-radio-a",
            "shape": "soft_observation",
            "fun_axis": "mood",
            "title": "今晚猫猫小电台怎么开场",
            "hint": "first",
            "reply_affordance": "viewer can answer with one mood word",
        },
        {
            "key": "idle:cat-radio-b",
            "shape": "tiny_choice",
            "fun_axis": "choice",
            "title": "今晚猫猫小电台开场方式",
            "hint": "similar",
            "reply_affordance": "viewer can pick one side",
        },
    ]
    monkeypatch.setattr(runtime, "_idle_hosting_beat_candidates", lambda: list(candidates))

    first = runtime._next_idle_hosting_beat()
    second = runtime._next_idle_hosting_beat()

    assert first["key"] == "idle:cat-radio-a"
    assert second["key"] == "idle:cat-radio-b"


def test_idle_hosting_result_exposes_host_beat_for_review(runtime: RoastRuntime) -> None:
    event = runtime._idle_hosting_event({"state": "idle"})

    public = event.to_dict()

    assert public["host_beat_key"]
    assert public["host_beat_shape"]
    assert public["host_beat_fun_axis"]
    assert public["host_beat_title"]
    assert public["host_beat_live_column"]
    assert public["host_beat_idle_stage"]
    assert public["host_beat_reply_affordance"]


def test_recent_interaction_context_summarizes_idle_hosting_host_beat(runtime: RoastRuntime) -> None:
    event = runtime._idle_hosting_event({"state": "idle"})
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="dry_run",
            event=event,
            steps=[PipelineStep("idle_hosting", "ok"), PipelineStep("neko_dispatcher", "dry_run")],
        )
    )

    context = runtime.recent_interaction_context(limit=1)

    assert "idle_hosting / idle_hosting:" in context[0]
    assert event.raw["host_beat"]["shape"] in context[0]
    assert event.raw["host_beat"]["family"] in context[0]
    assert event.raw["host_beat"]["fun_axis"] in context[0]
    assert event.raw["host_beat"]["live_column"] in context[0]
    assert event.raw["host_beat"]["idle_stage"] in context[0]
    assert event.raw["host_beat"]["title"] in context[0]
    assert event.raw["host_beat"]["reply_affordance"] in context[0]


def test_idle_hosting_progresses_stage_after_repeated_idle_beats(runtime: RoastRuntime) -> None:
    first = runtime._idle_hosting_event({"state": "idle"})
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="dry_run",
            event=first,
            steps=[PipelineStep("idle_hosting", "ok"), PipelineStep("neko_dispatcher", "dry_run")],
        )
    )
    second = runtime._idle_hosting_event({"state": "idle"})
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="dry_run",
            event=second,
            steps=[PipelineStep("idle_hosting", "ok"), PipelineStep("neko_dispatcher", "dry_run")],
        )
    )
    third = runtime._idle_hosting_event({"state": "idle"})

    assert first.raw["host_beat"]["idle_stage"] == "settle"
    assert second.raw["host_beat"]["idle_stage"] == "column"
    assert third.raw["host_beat"]["idle_stage"] == "callback"


@pytest.mark.asyncio
async def test_trigger_idle_hosting_skips_co_stream(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "co_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)

    result = await runtime.trigger_idle_hosting()

    assert result.status == "skipped"
    assert result.reason == "idle_hosting.not_solo_stream"
    assert runtime.recent_results[-1]["reason"] == "idle_hosting.not_solo_stream"


@pytest.mark.asyncio
async def test_trigger_idle_hosting_skips_when_live_state_is_not_idle(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=30)

    result = await runtime.trigger_idle_hosting()

    assert result.status == "skipped"
    assert result.reason == "idle_hosting.not_idle"
    assert runtime.recent_results[-1]["reason"] == "idle_hosting.not_idle"


@pytest.mark.asyncio
async def test_auto_idle_hosting_triggers_when_solo_stream_is_idle(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=240)

    result = await runtime.maybe_trigger_idle_hosting()

    assert result is not None
    assert result.status == "dry_run"
    assert result.event.source == "idle_hosting"
    assert runtime.recent_results[-1]["event"]["source"] == "idle_hosting"


@pytest.mark.asyncio
async def test_auto_idle_hosting_does_not_record_skip_when_not_candidate(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "co_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)

    result = await runtime.maybe_trigger_idle_hosting()

    assert result is None
    assert list(runtime.recent_results) == []


@pytest.mark.asyncio
async def test_auto_idle_hosting_respects_minimum_interval(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    runtime._idle_hosting_last_attempt_at = 100.0
    runtime._idle_hosting_now = lambda: 150.0
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)

    result = await runtime.maybe_trigger_idle_hosting()

    assert result is None
    assert list(runtime.recent_results) == []


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
    assert topic["reply_affordance"] == "viewer can pick one concrete side"
    assert "A/B choice" in topic["hint"]


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


def test_idle_hosting_avoids_recent_spent_output_family(runtime: RoastRuntime) -> None:
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
            output="今晚猫窝小电台的气氛先记一笔。",
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    beat = runtime._next_idle_hosting_beat()

    assert "room_mood" in runtime._recent_spent_output_families()
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
            output="那就二选一，今晚先选热饮还是小甜食。",
            steps=[PipelineStep("danmaku_response", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    topic = await runtime._select_active_engagement_topic()

    assert "choice_vote" in runtime._recent_spent_output_families()
    assert topic["key"] == "fallback:keyboard-busy"
    assert topic["family"] != "choice_vote"
    assert topic["recent_topic_skip_reason"] == "recent_spent_output_family"


def test_active_engagement_fallback_topics_do_not_use_room_silence_as_material(runtime: RoastRuntime) -> None:
    blocked_fragments = ("\u5f39\u5e55\u5c11", "\u6ca1\u5f39\u5e55", "\u6ca1\u4eba\u8bf4\u8bdd", "\u51b7\u573a", "\u5b89\u9759")

    titles = [topic["title"] for topic in runtime._active_engagement_fallback_topic_candidates()]

    assert titles
    assert not any(fragment in title for title in titles for fragment in blocked_fragments)


def test_idle_hosting_beats_have_enough_live_feel_variety(runtime: RoastRuntime) -> None:
    beats = runtime._idle_hosting_beat_candidates()
    shapes = {beat["shape"] for beat in beats}
    axes = {beat["fun_axis"] for beat in beats}
    titles = [beat["title"] for beat in beats]

    assert len(beats) >= 24
    assert {
        "soft_observation",
        "tiny_choice",
        "light_tease",
        "small_mood",
        "one_word_call",
        "micro_challenge",
    }.issubset(shapes)
    assert {"choice", "tease", "mood", "micro_challenge", "viewer_callback"}.issubset(axes)
    assert len({beat.get("live_column") for beat in beats if beat.get("live_column")}) >= 12
    assert all(str(beat.get("reply_affordance") or "").strip() for beat in beats)
    assert any("\u4e00\u4e2a\u5b57" in title or "\u4e00\u4e2a\u8bcd" in title for title in titles)
    assert any("\u4e8c\u9009\u4e00" in title or "A/B" in title for title in titles)
    assert any("\u4e09\u5b57" in title for title in titles)
    assert any("\u5c4f\u5e55" in title for title in titles)
    assert any("\u5c3e\u5df4" in title for title in titles)
    assert any("\u4e00\u5b57" in title for title in titles)
    assert any("\u4e0d\u592a\u9760\u8c31\u5956" in title for title in titles)


def test_active_engagement_fallback_topics_explain_fun_axis_and_reply_path(runtime: RoastRuntime) -> None:
    topics = runtime._active_engagement_fallback_topic_candidates()
    axes = {topic.get("fun_axis") for topic in topics}

    assert len(topics) >= 36
    assert {"choice", "tease", "mood", "micro_challenge", "viewer_callback"}.issubset(axes)
    assert len({topic.get("live_column") for topic in topics if topic.get("live_column")}) >= 14
    assert all(str(topic.get("reply_affordance") or "").strip() for topic in topics)
    assert not any("what should we talk about" in str(topic.get("hint") or "").lower() for topic in topics)
    keys = {topic["key"] for topic in topics}
    assert "fallback:tiny-court" in keys
    assert "fallback:two-char-password" in keys
    assert "fallback:lightstick-reflection" in keys


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


@pytest.mark.asyncio
async def test_active_engagement_fallback_topics_use_their_natural_shapes(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {"success": True, "videos": []}

    runtime._active_engagement_topic_fetcher = fetch_topics

    first = await runtime._select_active_engagement_topic()
    second = await runtime._select_active_engagement_topic()

    assert first["key"] == "fallback:snack-choice"
    assert first["shape"] == "either_or"
    assert first["live_column"] == "NEKO micro poll"
    assert first["topic_pack"] == "micro_poll"
    assert second["key"] == "fallback:keyboard-busy"
    assert second["shape"] == "tiny_tease"
    assert second["live_column"] == "NEKO tiny verdict"
    assert second["topic_pack"] == "neko_verdict"
    assert "tiny playful tease" in second["hook"]


@pytest.mark.asyncio
async def test_active_engagement_topic_exposes_viewer_reply_affordance(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {"success": True, "videos": []}

    runtime._active_engagement_topic_fetcher = fetch_topics

    topic = await runtime._select_active_engagement_topic()

    assert topic["intent"] == "quick_vote"
    assert topic["topic_pack"] == "micro_poll"
    assert topic["reply_affordance"] == "viewer can pick one concrete side"


@pytest.mark.asyncio
async def test_active_engagement_topic_preserves_fallback_fun_axis(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {"success": True, "videos": []}

    runtime._active_engagement_topic_fetcher = fetch_topics

    topic = await runtime._select_active_engagement_topic()

    assert topic["fun_axis"] == "choice"
    assert topic["reply_affordance"] == "viewer can pick one concrete side"


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
async def test_auto_active_engagement_can_take_over_after_repeated_idle_hosting(runtime: RoastRuntime) -> None:
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
    assert runtime.recent_results[-1]["event"]["source"] == "active_engagement"


@pytest.mark.asyncio
async def test_auto_active_engagement_takes_over_after_two_idle_beats(runtime: RoastRuntime) -> None:
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
    assert state["live_director_status"]["next_auto_action"] == "active_engagement"
    assert state["live_director_status"]["reason"] == "idle_hosting_streak"


@pytest.mark.asyncio
async def test_auto_warmup_hosting_triggers_once_for_new_solo_stream(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)

    result = await runtime.maybe_trigger_warmup_hosting()

    assert result is not None
    assert result.status == "dry_run"
    assert result.event.source == "warmup_hosting"
    assert any(step.id == "warmup_hosting" and step.status == "ok" for step in result.steps)
    assert runtime.recent_results[-1]["event"]["source"] == "warmup_hosting"


@pytest.mark.asyncio
async def test_auto_warmup_hosting_does_not_repeat_after_recent_result(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=240)

    result = await runtime.maybe_trigger_warmup_hosting()

    assert result is None


@pytest.mark.asyncio
async def test_live_disabled_blocks_solo_auto_hosting_even_with_stale_connection(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = False
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)

    warmup = await runtime.maybe_trigger_warmup_hosting()

    assert warmup is None
    assert len(runtime.recent_results) == 0

    _record_result_at(runtime, age_seconds=90)
    active = await runtime.maybe_trigger_active_engagement()

    assert active is None
    assert len(runtime.recent_results) == 1

    runtime.recent_results.clear()
    _record_result_at(runtime, age_seconds=240)
    idle = await runtime.maybe_trigger_idle_hosting()

    assert idle is None
    assert len(runtime.recent_results) == 1

    state = await runtime.dashboard_state()
    assert state["live_status"]["summary"] == "cannot_stream"
    assert state["live_status"]["reason"] == "live_disabled"
    assert state["live_state"]["state"] == "blocked"
    assert state["live_state"]["warmup_hosting_candidate"] is False
    assert state["live_state"]["idle_hosting_candidate"] is False
    assert state["live_director_status"]["next_auto_action"] == "none"


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


@pytest.mark.asyncio
async def test_live_director_status_picks_idle_hosting_for_solo_idle(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=240)

    state = await runtime.dashboard_state()

    director = state["live_director_status"]
    assert director["next_auto_action"] == "idle_hosting"
    assert director["eligible"] is True
    assert director["reason"] == "solo_idle"


@pytest.mark.asyncio
async def test_live_director_status_does_not_auto_host_for_co_stream_quiet(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "co_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=120)

    state = await runtime.dashboard_state()

    director = state["live_director_status"]
    assert director["next_auto_action"] == "none"
    assert director["eligible"] is False
    assert director["reason"] == "companion_mode"


@pytest.mark.asyncio
async def test_solo_test_readiness_lists_independent_mode_capabilities(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)

    state = await runtime.dashboard_state()

    readiness = state["solo_test_readiness"]
    assert readiness["ready"] is True
    assert readiness["summary"] == "ready_for_test"
    assert readiness["mode"] == "solo_stream"
    items = {item["id"]: item for item in readiness["items"]}
    assert set(items) == {
        "preflight",
        "test_isolation",
        "warmup_hosting",
        "avatar_roast",
        "danmaku_response",
        "active_engagement",
        "idle_hosting",
        "pacing_control",
    }
    assert all(item["status"] == "ready" for item in items.values())


@pytest.mark.asyncio
async def test_solo_test_readiness_warns_when_viewer_profiles_are_present(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = False
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    await runtime.viewer_store.upsert_identity(ViewerIdentity(uid="1001", nickname="viewer"))

    state = await runtime.dashboard_state()

    readiness = state["solo_test_readiness"]
    items = {item["id"]: item for item in readiness["items"]}
    assert readiness["profile_count"] == 1
    assert items["test_isolation"]["status"] == "warning"
    assert items["test_isolation"]["reason"] == "viewer_profiles_present"


@pytest.mark.asyncio
async def test_solo_test_readiness_marks_test_isolation_ready_after_profile_clear(runtime: RoastRuntime) -> None:
    runtime.config.developer_tools_enabled = True
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = False
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    await runtime.viewer_store.upsert_identity(ViewerIdentity(uid="1001", nickname="viewer"))
    await runtime.clear_viewer_profiles()

    state = await runtime.dashboard_state()

    items = {item["id"]: item for item in state["solo_test_readiness"]["items"]}
    assert state["solo_test_readiness"]["profile_count"] == 0
    assert items["test_isolation"]["status"] == "ready"
    assert items["test_isolation"]["reason"] == "clean"


@pytest.mark.asyncio
async def test_solo_test_readiness_marks_warmup_hosting_observed_after_result(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="dry_run",
            event=ViewerEvent(uid="__neko_warmup__", nickname="NEKO", source="warmup_hosting", live_mode="solo_stream"),
            steps=[PipelineStep("warmup_hosting", "ok"), PipelineStep("neko_dispatcher", "dry_run")],
        )
    )

    state = await runtime.dashboard_state()

    items = {item["id"]: item for item in state["solo_test_readiness"]["items"]}
    assert items["warmup_hosting"]["status"] == "observed"
    assert items["warmup_hosting"]["reason"] == "observed"


@pytest.mark.asyncio
async def test_solo_test_readiness_blocks_companion_mode(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "co_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)

    state = await runtime.dashboard_state()

    readiness = state["solo_test_readiness"]
    assert readiness["ready"] is False
    assert readiness["summary"] == "not_solo_stream"
    assert readiness["mode"] == "co_stream"


@pytest.mark.asyncio
async def test_stop_cancels_idle_hosting_loop(runtime: RoastRuntime) -> None:
    runtime._start_idle_hosting_loop()
    task = runtime._idle_hosting_task
    assert task is not None

    await runtime.stop()

    assert task.done()


@pytest.mark.asyncio
async def test_config_store_health_row_tracks_successful_persist(runtime: RoastRuntime) -> None:
    runtime.plugin.ctx = SimpleNamespace(update_own_config=None)

    await runtime.update_config({"dry_run": True})
    state = await runtime.dashboard_state()

    rows = {row["id"]: row for row in state["health_rows"]}
    assert rows["config_store"]["status"] == "healthy"
    assert rows["config_store"]["age_sec"] is not None
    assert rows["config_store"]["last_error"] == ""
