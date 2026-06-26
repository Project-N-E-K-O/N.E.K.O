from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.plugins.neko_roast.core.contracts import InteractionResult, PipelineStep, ViewerEvent, ViewerIdentity
from plugin.plugins.neko_roast.core.runtime import RoastRuntime


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
async def test_clear_viewer_profiles_resets_profiles_without_clearing_results(runtime: RoastRuntime) -> None:
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
    calls = 0

    def clear_marker() -> None:
        nonlocal calls
        calls += 1

    runtime.pipeline.clear_dry_run_session_state = clear_marker

    await runtime.clear_viewer_profiles()

    assert calls == 1


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
    first_event = ViewerEvent(uid="42", nickname="viewer", danmaku_text="绗竴娆℃潵", source="live_danmaku")
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
        "avatar_roast / live_danmaku from viewer: 绗竴娆℃潵",
    ]

def test_recent_interaction_context_summarizes_active_engagement_topic(runtime: RoastRuntime) -> None:
    event = ViewerEvent(
        uid="__neko_active__",
        nickname="NEKO",
        source="active_engagement",
        raw={
            "topic_material": {
                "source": "bili_trending",
                "shape": "either_or",
                "title": "鐚尗浠婂ぉ鎬庝箞杩欎箞瀹夐潤",
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

    assert context == ["active_engagement / active_engagement: bili_trending either_or - 鐚尗浠婂ぉ鎬庝箞杩欎箞瀹夐潤"]


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
                        "recent_topic_skip_reason": "single_viewer_flood",
                    }
                },
            ),
            steps=[PipelineStep("active_engagement", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )

    event = runtime.recent_results[-1]["event"]
    assert event["topic_source"] == "bili_trending"
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
        danmaku_text="璋㈣阿鐚尗",
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
    assert state["idle_hosting_status"]["cooldown_remaining"] == 70.0
    assert state["idle_hosting_status"]["min_interval_seconds"] == 120.0


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
    assert active_state["idle_hosting_status"]["min_interval_seconds"] == 60.0
    assert active_state["idle_hosting_status"]["cooldown_remaining"] == 10.0


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
    assert all(beat["shape"] for beat in beats)
    assert all(beat["hint"] for beat in beats)


def test_idle_hosting_result_exposes_host_beat_for_review(runtime: RoastRuntime) -> None:
    event = runtime._idle_hosting_event({"state": "idle"})

    public = event.to_dict()

    assert public["host_beat_key"]
    assert public["host_beat_shape"]
    assert public["host_beat_title"]


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
    assert event.raw["host_beat"]["title"] in context[0]


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
    assert state["active_engagement_status"]["eligible"] is False
    assert state["active_engagement_status"]["reason"] == "recent_danmaku_output"
    assert state["active_engagement_status"]["minimum_interval_remaining"] == 0.0
    assert 0.0 < state["active_engagement_status"]["recent_danmaku_cooldown_remaining"] <= 15.0
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
async def test_active_engagement_topic_material_rotates_shapes_and_titles(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "閲嶅妫€鏌ョ敤璇濋 A", "bvid": "BV_A"},
                {"title": "閲嶅妫€鏌ョ敤璇濋 B", "bvid": "BV_B"},
                {"title": "閲嶅妫€鏌ョ敤璇濋 C", "bvid": "BV_C"},
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
                {"title": "neutral topic after skipped danmaku", "bvid": "BV_AFTER_SKIPPED"},
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
                {"title": "neutral topic after filtered danmaku", "bvid": "BV_AFTER_FILTERED"},
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
                {"title": "neutral topic after reaction", "bvid": "BV_AFTER_REACTION"},
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
                {"title": "neutral topic after runtime feedback", "bvid": "BV_AFTER_RUNTIME_FEEDBACK"},
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
                {"title": "neutral topic after skipped active beat", "bvid": "BV_AFTER_ACTIVE_SKIP"},
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
                {"title": "useful trending fallback", "bvid": "BV_USEFUL"},
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
    assert topic["title"] == "useful trending fallback"


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
                {"title": "useful concrete trending fallback", "bvid": "BV_USEFUL"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics

    topic = await runtime._select_active_engagement_topic()

    assert topic["source"] == "bili_trending"
    assert topic["key"] == "bili:BV_USEFUL"
    assert topic["title"] == "useful concrete trending fallback"


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
    assert topic["title"].endswith("…")


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
                {"title": "闁插秴顦插Λ鈧弻銉ф暏閸楁洑绔撮悜顓熸偝", "bvid": "BV_ONLY"},
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics

    first = await runtime._select_active_engagement_topic()
    second = await runtime._select_active_engagement_topic()

    assert first["source"] == "bili_trending"
    assert second["source"] == "fallback"
    assert second["key"] != first["key"]


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
                {"title": "second tiny desk choice", "bvid": "BV_SECOND_TOPIC"},
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


def test_active_engagement_fallback_topics_do_not_use_room_silence_as_material(runtime: RoastRuntime) -> None:
    blocked_fragments = ("\u5f39\u5e55\u5c11", "\u6ca1\u5f39\u5e55", "\u6ca1\u4eba\u8bf4\u8bdd", "\u51b7\u573a", "\u5b89\u9759")

    titles = [topic["title"] for topic in runtime._active_engagement_fallback_topic_candidates()]

    assert titles
    assert not any(fragment in title for title in titles for fragment in blocked_fragments)


@pytest.mark.asyncio
async def test_active_engagement_fallback_topics_use_their_natural_shapes(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {"success": True, "videos": []}

    runtime._active_engagement_topic_fetcher = fetch_topics

    first = await runtime._select_active_engagement_topic()
    second = await runtime._select_active_engagement_topic()

    assert first["key"] == "fallback:snack-choice"
    assert first["shape"] == "either_or"
    assert second["key"] == "fallback:keyboard-busy"
    assert second["shape"] == "tiny_tease"
    assert "tiny playful tease" in second["hook"]


@pytest.mark.asyncio
async def test_active_engagement_topic_shapes_keep_rotating_after_full_cycle(runtime: RoastRuntime) -> None:
    async def fetch_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": f"杞崲妫€鏌ョ敤璇濋 {index}", "bvid": f"BV_ROTATE_{index}"}
                for index in range(8)
            ],
        }

    runtime._active_engagement_topic_fetcher = fetch_topics

    shapes = [(await runtime._select_active_engagement_topic())["shape"] for _ in range(6)]

    assert shapes == [
        "either_or",
        "light_stance",
        "tiny_tease",
        "small_challenge",
        "either_or",
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
    assert state["active_engagement_status"]["minimum_interval_remaining"] == 70.0
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
    assert standard_state["active_engagement_status"]["minimum_interval_seconds"] == 120.0
    assert standard_state["active_engagement_status"]["minimum_interval_remaining"] == 70.0
    assert active_state["active_engagement_status"]["minimum_interval_seconds"] == 90.0
    assert active_state["active_engagement_status"]["minimum_interval_remaining"] == 40.0


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
    assert len(runtime.recent_results) == 1


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
