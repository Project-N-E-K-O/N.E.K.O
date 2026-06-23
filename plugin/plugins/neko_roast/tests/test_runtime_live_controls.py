from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.plugins.neko_roast.core.contracts import InteractionResult, PipelineStep, ViewerEvent
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
    rt.bili_identity.ctx = rt
    return rt


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

    await runtime.trigger_idle_hosting()
    state = await runtime.dashboard_state()

    assert state["speech_explanation"]["summary"] == "test_only"
    assert state["speech_explanation"]["reason"] == "dry_run"
    assert state["speech_explanation"]["last_result_status"] == "dry_run"
    assert state["speech_explanation"]["last_result_reason"] == "dispatcher.dry_run"
    assert state["speech_explanation"]["last_result_source"] == "idle_hosting"


@pytest.mark.asyncio
async def test_speech_explanation_marks_solo_idle_as_waiting_for_idle_hosting(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = False
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)

    state = await runtime.dashboard_state()

    assert state["live_state"]["idle_hosting_candidate"] is True
    assert state["speech_explanation"]["summary"] == "waiting_for_activity"
    assert state["speech_explanation"]["reason"] == "idle_hosting_candidate"


def _created_at_age(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _record_result_at(runtime: RoastRuntime, *, age_seconds: int, status: str = "pushed") -> None:
    event = ViewerEvent(uid="42", nickname="viewer", danmaku_text="hi", source="live_danmaku")
    runtime.record_result(
        InteractionResult(
            accepted=status == "pushed",
            status=status,
            event=event,
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
    _record_result_at(runtime, age_seconds=120)

    runtime.config.activity_level = "quiet"
    quiet_state = await runtime.dashboard_state()
    assert quiet_state["live_state"]["state"] == "quiet"
    assert quiet_state["live_state"]["idle_hosting_candidate"] is False
    assert quiet_state["live_state"]["engaged_threshold_seconds"] == 90.0
    assert quiet_state["live_state"]["idle_threshold_seconds"] == 300.0

    runtime.config.activity_level = "standard"
    standard_state = await runtime.dashboard_state()
    assert standard_state["live_state"]["state"] == "quiet"
    assert standard_state["live_state"]["engaged_threshold_seconds"] == 60.0
    assert standard_state["live_state"]["idle_threshold_seconds"] == 180.0

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
    assert "last_activity_age_sec" not in result.request.prompt_text
    assert "nobody is here" not in result.request.prompt_text
    assert "beg for comments" not in result.request.prompt_text
    assert "welcome everyone" not in result.request.prompt_text
    assert "please interact" not in result.request.prompt_text
    assert runtime.recent_results[-1]["status"] == "dry_run"
    assert runtime.recent_results[-1]["event"]["source"] == "idle_hosting"
    assert runtime.plugin.pushed_messages == []


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
