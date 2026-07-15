from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from plugin.plugins.neko_roast.core.contracts import (
    InteractionRequest,
    InteractionResult,
    LiveRoomStatus,
    PipelineStep,
    RoastConfig,
    ViewerEvent,
    ViewerIdentity,
    ViewerProfile,
)
from plugin.plugins.neko_roast.core.runtime_config_activation import activate_config
from plugin.plugins.neko_roast.core.runtime import RoastRuntime
from plugin.plugins.neko_roast.core.runtime_live_listener import stop_live_listener
from plugin.plugins.neko_roast.core.runtime_live_input import (
    _public_lookup_room_ref,
    _signal_event_type,
    remember_live_danmaku_seen,
)
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
        self.lookup_status = LiveRoomStatus(
            room_id=123,
            ok=True,
            title="战雷陆战练车：今晚只打轻松局",
            anchor_name="水水",
            live_status="live",
            message="live room is streaming",
        )

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

    async def lookup_room_status(self, room_id: int) -> LiveRoomStatus:
        return LiveRoomStatus(
            room_id=room_id,
            ok=self.lookup_status.ok,
            title=self.lookup_status.title,
            anchor_name=self.lookup_status.anchor_name,
            live_status=self.lookup_status.live_status,
            message=self.lookup_status.message,
        )


class FakeLiveProvider:
    platform = "douyin"

    def __init__(self, room_ref: str) -> None:
        self.room_ref = room_ref
        self.started: list[str] = []
        self.stopped = 0

    def is_listening(self) -> bool:
        return True

    def configured_room_ref(self) -> str:
        return self.room_ref

    def configured_room_id(self) -> int:
        return 0

    def normalize_room_ref(self, room_ref: object) -> dict:
        if not isinstance(room_ref, str):
            return {"ok": False, "platform": "douyin", "room_ref": "", "room_id": 0, "message": "invalid room"}
        text = room_ref.strip()
        parsed = urlparse(text)
        if parsed.scheme and parsed.netloc:
            text = parsed.path.strip("/").split("/", 1)[0]
        text = text.strip()
        if not text:
            return {"ok": False, "platform": "douyin", "room_ref": "", "room_id": 0, "message": "invalid room"}
        return {"ok": True, "platform": "douyin", "room_ref": text, "room_id": 0, "message": ""}

    def listener_state(self) -> dict:
        return {"state": "connected" if self.started else "disconnected", "room_ref": self.room_ref, "room_id": 0}

    async def start_listening(self, room_ref: str) -> bool:
        self.started.append(room_ref)
        self.room_ref = room_ref
        return True

    async def stop_listening(self) -> None:
        self.stopped += 1

@pytest.fixture
def runtime(tmp_path: Path) -> RoastRuntime:
    rt = RoastRuntime(Plugin(tmp_path))
    rt.bili_live_ingest = FakeIngest()
    rt.avatar_roast.ctx = rt
    rt.danmaku_response.ctx = rt
    rt.active_engagement.ctx = rt
    rt.warmup_hosting.ctx = rt
    rt.live_support_events.ctx = rt
    rt.bili_identity.ctx = rt
    return rt


def test_dashboard_actions_include_manual_hosting_actions(runtime: RoastRuntime) -> None:
    action_ids = {action["id"] for action in runtime.dashboard_actions()}

    assert "trigger_idle_hosting" in action_ids
    assert "trigger_warmup_hosting" in action_ids
    assert "trigger_active_engagement" in action_ids


def test_dashboard_actions_do_not_include_destructive_viewer_profile_controls(runtime: RoastRuntime) -> None:
    action_ids = {action["id"] for action in runtime.dashboard_actions()}

    assert "clear_viewer_profiles" not in action_ids
    assert "delete_viewer_profile" not in action_ids
    assert "reset_viewer_impression" not in action_ids


@pytest.mark.asyncio
async def test_lookup_live_room_caches_public_room_title(runtime: RoastRuntime) -> None:
    result = await runtime.lookup_live_room("123")

    assert result["title"] == "战雷陆战练车：今晚只打轻松局"
    assert runtime.live_room_context["title"] == "战雷陆战练车：今晚只打轻松局"
    assert runtime.live_room_context["anchor_name"] == "水水"
    assert runtime.live_room_context["live_status"] == "live"

    snapshot = runtime.live_connection_snapshot()
    assert snapshot["title"] == "战雷陆战练车：今晚只打轻松局"
    assert snapshot["anchor_name"] == "水水"
    assert snapshot["live_status"] == "live"


@pytest.mark.asyncio
async def test_connect_live_room_refreshes_room_title_context(runtime: RoastRuntime) -> None:
    await runtime.set_live_room("123")
    runtime.config.live_mode = "solo_stream"

    snapshot = await runtime.connect_live_room()

    assert snapshot["title"] == "战雷陆战练车：今晚只打轻松局"
    assert runtime.live_room_context["title"] == "战雷陆战练车：今晚只打轻松局"

    request = runtime.danmaku_response.build_request(
        ViewerEvent(
            uid="42",
            nickname="viewer",
            danmaku_text="今天玩什么",
            source="live_danmaku",
            live_mode="solo_stream",
        ),
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer", roast_count=1),
    )
    assert "live_room_title_theme: 战雷陆战练车：今晚只打轻松局" in request.prompt_text


@pytest.mark.asyncio
async def test_offline_live_room_blocks_solo_auto_warmup(runtime: RoastRuntime) -> None:
    runtime.bili_live_ingest.lookup_status = LiveRoomStatus(
        room_id=123,
        ok=True,
        title="offline test room",
        anchor_name="anchor",
        live_status="offline",
        message="live room is offline",
    )
    runtime.config.live_room_id = 123
    runtime.config.live_mode = "solo_stream"
    runtime.config.dry_run = False

    snapshot = await runtime.connect_live_room()
    warmup = await runtime.maybe_trigger_warmup_hosting()

    assert snapshot["connected"] is True
    assert snapshot["live_status"] == "offline"
    assert warmup is None
    state = await runtime.dashboard_state()
    assert state["live_status"]["summary"] == "cannot_stream"
    assert state["live_status"]["reason"] == "live_room_offline"
    assert state["live_state"]["state"] == "blocked"
    assert state["live_state"]["warmup_hosting_candidate"] is False
    assert len(runtime.recent_results) == 0


@pytest.mark.asyncio
async def test_sync_live_instructions_does_not_push_when_live_disabled(runtime: RoastRuntime) -> None:
    runtime.config.live_enabled = False

    result = await runtime.sync_live_instructions()

    assert result == "not_injected"
    assert runtime.instructions_injected is False
    assert runtime.plugin.pushed_messages == []


@pytest.mark.asyncio
async def test_sync_live_instructions_can_force_restore_stale_live_context(
    runtime: RoastRuntime,
) -> None:
    runtime.config.live_enabled = False

    result = await runtime.sync_live_instructions(force=True)

    assert result.startswith("instructions_restored")
    assert runtime.instructions_injected is False
    assert len(runtime.plugin.pushed_messages) == 1
    assert (
        runtime.plugin.pushed_messages[0]["metadata"]["description"]
        == "Neko Roast behavior restore"
    )


@pytest.mark.asyncio
async def test_sync_live_instructions_force_cleans_legacy_context_while_live_enabled(
    runtime: RoastRuntime,
) -> None:
    runtime.config.live_enabled = True

    result = await runtime.sync_live_instructions(force=True)

    assert result.startswith("live_scene_not_ready(room_not_configured); instructions_restored")
    assert runtime.instructions_injected is False
    assert len(runtime.plugin.pushed_messages) == 1
    assert (
        runtime.plugin.pushed_messages[0]["metadata"]["description"]
        == "Neko Roast behavior restore"
    )


@pytest.mark.asyncio
async def test_connect_live_room_injects_live_context_when_dry_run_defaults_off(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123

    snapshot = await runtime.connect_live_room()

    assert snapshot["connected"] is True
    assert snapshot["platform"] == "bilibili"
    assert snapshot["room_ref"] == "123"
    assert runtime.instructions_injected is True
    assert len(runtime.plugin.pushed_messages) == 1
    assert runtime.plugin.pushed_messages[0]["metadata"]["description"] == "Neko Roast behavior instructions"


@pytest.mark.asyncio
async def test_sync_live_instructions_injects_light_live_scene_for_real_output(
    runtime: RoastRuntime,
) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = False
    runtime.config.live_mode = "solo_stream"
    runtime.config.stream_theme = "late night tiny desk chat"
    runtime.live_room_context = {
        "platform": "bilibili",
        "room_ref": "123",
        "live_status": "live",
        "title": "fallback room title",
    }
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)

    result = await runtime.sync_live_instructions()

    assert result.startswith("instructions_queued")
    assert runtime.instructions_injected is True
    assert runtime.instructions_signature
    assert len(runtime.plugin.pushed_messages) == 1
    message = runtime.plugin.pushed_messages[0]
    assert message["ai_behavior"] == "read"
    assert message["metadata"]["description"] == "Neko Roast behavior instructions"
    text = message["parts"][0]["text"]
    assert "NEKO Live scene is active" in text
    assert "solo_stream" in text
    assert "late night tiny desk chat" in text
    assert "not a private chat with {MASTER_NAME}" in text


@pytest.mark.asyncio
async def test_sync_live_instructions_reinjects_when_stream_theme_changes(
    runtime: RoastRuntime,
) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = False
    runtime.config.live_mode = "solo_stream"
    runtime.config.stream_theme = "first theme"
    runtime.live_room_context = {"room_ref": "123", "live_status": "live"}
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)

    first = await runtime.sync_live_instructions()
    runtime.config.stream_theme = "second theme"
    second = await runtime.sync_live_instructions()

    assert first.startswith("instructions_queued")
    assert "instructions_restored" in second
    assert "instructions_queued(target=" in second
    assert len(runtime.plugin.pushed_messages) == 3
    assert runtime.plugin.pushed_messages[1]["metadata"]["description"] == "Neko Roast behavior restore"
    assert runtime.plugin.pushed_messages[2]["metadata"]["description"] == "Neko Roast behavior instructions"
    assert "second theme" in runtime.plugin.pushed_messages[2]["parts"][0]["text"]


@pytest.mark.asyncio
async def test_sync_live_instructions_does_not_inject_for_offline_room(
    runtime: RoastRuntime,
) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = False
    runtime.config.live_mode = "solo_stream"
    runtime.config.stream_theme = "offline theme"
    runtime.live_room_context = {"room_ref": "123", "live_status": "offline"}
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)

    result = await runtime.sync_live_instructions()

    assert result == "live_scene_not_ready(live_room_offline)"
    assert runtime.instructions_injected is False
    assert runtime.plugin.pushed_messages == []


def test_live_connection_snapshot_does_not_stringify_listener_state_objects(runtime: RoastRuntime) -> None:
    class _LooksLikePublicValue:
        def __str__(self) -> str:
            return "connected-secret"

        def __int__(self) -> int:
            return 999

    class _UnsafeLiveProvider:
        platform = "douyin"

        def configured_room_ref(self) -> str:
            return "room-42"

        def configured_room_id(self) -> int:
            return 0

        def listener_state(self) -> dict:
            value = _LooksLikePublicValue()
            return {
                "state": value,
                "viewer_count": value,
                "last_error": value,
                "connection_plan": value,
                "reconnect": value,
            }

    runtime.live_provider = _UnsafeLiveProvider()
    runtime.live_connection_state = "disconnected"
    runtime.config.live_enabled = True

    snapshot = runtime.live_connection_snapshot()

    assert snapshot == {
        "platform": "douyin",
        "room_ref": "room-42",
        "room_id": 0,
        "state": "disconnected",
        "connected": False,
        "listening": False,
        "viewer_count": 0,
    }
    assert "connected-secret" not in str(snapshot)


def test_live_connection_snapshot_accepts_only_public_listener_state_scalars(runtime: RoastRuntime) -> None:
    class _LiveProvider:
        platform = "bilibili"

        def configured_room_ref(self) -> str:
            return "123"

        def configured_room_id(self) -> int:
            return 123

        def listener_state(self) -> dict:
            return {
                "state": " receiving ",
                "viewer_count": "42",
                "last_error": " temporary disconnect ",
                "connection_plan": {"ready": False},
                "reconnect": {"retry_count": 1},
            }

    runtime.live_provider = _LiveProvider()
    runtime.config.live_enabled = True

    snapshot = runtime.live_connection_snapshot()

    assert snapshot["state"] == "receiving"
    assert snapshot["connected"] is True
    assert snapshot["listening"] is True
    assert snapshot["viewer_count"] == 42
    assert snapshot["last_error"] == "temporary disconnect"
    assert snapshot["connection_plan"] == {"ready": False}
    assert snapshot["reconnect"] == {"retry_count": 1}


@pytest.mark.asyncio
async def test_set_live_room_syncs_room_ref_for_provider_router(runtime: RoastRuntime) -> None:
    config = await runtime.set_live_room("https://live.bilibili.com/456")

    assert config.live_room_id == 456
    assert config.live_room_ref == "456"
    assert runtime.live_provider.configured_room_ref() == "456"


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
async def test_handle_manual_event_uses_selected_live_provider_identity(runtime: RoastRuntime) -> None:
    runtime.config.developer_tools_enabled = True
    runtime.config.live_enabled = True
    runtime.config.live_platform = "douyin"
    runtime.config.live_room_ref = "room-42"
    runtime.config.live_room_id = 0
    runtime.safety_guard.set_connected(True)
    runtime.viewer_profile.ctx = runtime

    result = await runtime.handle_manual_event(uid="42", nickname="viewer", danmaku_text="hello")

    assert result.identity is not None
    assert result.identity.uid == "douyin:42"
    assert result.profile is not None
    assert result.profile.uid == "douyin:42"
    assert [step.id for step in result.steps][:3] == [
        "permission_gate",
        "safety_guard.before_event",
        "douyin_identity",
    ]
    assert [profile["uid"] for profile in await runtime.viewer_store.recent_profiles()] == ["douyin:42"]


@pytest.mark.asyncio
async def test_lookup_other_room_does_not_replace_configured_room_context(runtime: RoastRuntime) -> None:
    await runtime.set_live_room(123)
    configured_context = dict(runtime.live_room_context)
    runtime.bili_live_ingest.lookup_status.title = "preview room"

    result = await runtime.lookup_live_room(999)

    assert result["room_ref"] == "999"
    assert result["title"] == "preview room"
    assert runtime.live_room_context == configured_context


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
async def test_update_config_stops_captured_old_provider_before_platform_switch(
    runtime: RoastRuntime,
) -> None:
    douyin = FakeLiveProvider("room-42")
    runtime.douyin_live_ingest = douyin
    runtime.config.live_platform = "bilibili"
    runtime.config.live_room_ref = "100"
    runtime.config.live_room_id = 100
    runtime.config.live_enabled = True
    await runtime.bili_live_ingest.start_listening(100)

    await runtime.update_config(
        {
            "live_platform": "douyin",
            "live_room_ref": "room-42",
            "live_enabled": True,
        }
    )

    assert runtime.bili_live_ingest.stopped == 1
    assert runtime.bili_live_ingest.room_id == 0
    assert douyin.stopped == 0
    assert douyin.started == ["room-42"]
    assert runtime.config.live_platform == "douyin"


@pytest.mark.asyncio
async def test_concurrent_developer_mode_updates_serialize_transition_side_effects(
    runtime: RoastRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[bool] = []

    async def sync_developer_mode(*, announce: bool = False, force: bool = False) -> str:
        calls.append(bool(runtime.config.developer_tools_enabled))
        if len(calls) == 1:
            entered.set()
            await release.wait()
        return "synced"

    monkeypatch.setattr(runtime, "sync_developer_mode", sync_developer_mode)
    first = asyncio.create_task(runtime.update_config({"developer_tools_enabled": True}))
    await entered.wait()
    second = asyncio.create_task(runtime.update_config({"developer_tools_enabled": False}))
    release.set()
    await asyncio.gather(first, second)

    assert calls == [True, False]
    assert runtime.config.developer_tools_enabled is False


@pytest.mark.asyncio
async def test_update_config_force_syncs_developer_mode_only_on_transition(
    runtime: RoastRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_calls: list[tuple[bool, bool]] = []

    async def sync_developer_mode(*, announce: bool = False, force: bool = False) -> str:
        sync_calls.append((announce, force))
        return "synced"

    monkeypatch.setattr(runtime, "sync_developer_mode", sync_developer_mode)
    runtime.config.developer_tools_enabled = True

    await runtime.update_config({"developer_tools_enabled": True, "dry_run": False})
    assert sync_calls == []

    await runtime.update_config({"developer_tools_enabled": False})
    assert sync_calls == [(False, True)]


@pytest.mark.asyncio
async def test_update_config_normalizes_platform_alias_before_reconnect_check(runtime: RoastRuntime) -> None:
    provider = FakeLiveProvider("room-42")
    runtime.live_provider = provider
    runtime.config.live_platform = "dy"
    runtime.config.live_room_ref = "room-42"
    runtime.config.live_room_id = 0
    runtime.config.live_enabled = True

    await runtime.update_config({"live_platform": "douyin"})

    assert runtime.config.live_platform == "douyin"
    assert provider.started == []
    assert provider.stopped == 0


@pytest.mark.asyncio
async def test_update_config_uses_provider_room_id_in_non_bilibili_reconnect_audit(runtime: RoastRuntime) -> None:
    provider = FakeLiveProvider("room-43")
    runtime.live_provider = provider
    runtime.config.live_platform = "dy"
    runtime.config.live_room_ref = "room-42"
    runtime.config.live_room_id = 12345
    runtime.config.live_enabled = True

    await runtime.update_config({"live_room_ref": "room-43"})

    record = next(item for item in runtime.audit.recent(10) if item["op"] == "live_reconnected")
    assert provider.started == ["room-43"]
    assert record["detail"]["platform"] == "douyin"
    assert record["detail"]["room_id"] == 0
    assert record["detail"]["previous_room_id"] == 0
    assert record["detail"]["previous_room_ref"] == "room-42"


@pytest.mark.asyncio
async def test_update_config_normalizes_douyin_room_ref_before_persist(runtime: RoastRuntime) -> None:
    runtime.config.live_platform = "douyin"
    runtime.config.live_room_ref = ""
    runtime.config.live_room_id = 12345
    runtime.config.live_enabled = False

    config = await runtime.update_config(
        {"live_room_ref": "https://live.douyin.com/room-42?cookie=must-not-leak"}
    )
    persisted = runtime.plugin.config.updates[-1]

    assert config.live_room_ref == "room-42"
    assert config.live_room_id == 0
    assert persisted["neko_roast"]["live_room_ref"] == "room-42"
    assert persisted["neko_roast"]["live_room_id"] == 0
    assert "must-not-leak" not in str(persisted)


@pytest.mark.asyncio
async def test_update_config_preserves_douyin_target_on_partial_rate_limit_update(
    runtime: RoastRuntime,
) -> None:
    runtime.config.live_platform = "douyin"
    runtime.config.live_room_ref = "room-42"
    runtime.config.live_room_id = 0
    runtime.config.rate_limit_seconds = 1
    runtime.config.live_enabled = False

    config = await runtime.update_config({"rate_limit_seconds": 10})
    persisted = runtime.plugin.config.updates[-1]

    assert config.live_platform == "douyin"
    assert config.live_room_ref == "room-42"
    assert config.live_room_id == 0
    assert config.rate_limit_seconds == 10
    assert persisted["neko_roast"] == {"rate_limit_seconds": 10}


@pytest.mark.asyncio
async def test_update_config_clears_room_target_when_switching_to_douyin(runtime: RoastRuntime) -> None:
    runtime.config.live_platform = "bilibili"
    runtime.config.live_room_ref = "12345"
    runtime.config.live_room_id = 12345
    runtime.config.live_enabled = False

    config = await runtime.update_config({"live_platform": "douyin"})
    persisted = runtime.plugin.config.updates[-1]

    assert config.live_platform == "douyin"
    assert config.live_room_ref == ""
    assert config.live_room_id == 0
    assert persisted["neko_roast"]["live_room_ref"] == ""
    assert persisted["neko_roast"]["live_room_id"] == 0


@pytest.mark.asyncio
async def test_update_config_does_not_derive_non_bilibili_previous_room_ref_from_legacy_room_id(
    runtime: RoastRuntime,
) -> None:
    provider = FakeLiveProvider("room-43")
    runtime.live_provider = provider
    runtime.config.live_platform = "douyin"
    runtime.config.live_room_ref = ""
    runtime.config.live_room_id = 12345
    runtime.config.live_enabled = True

    await runtime.update_config({"live_room_ref": "room-43"})

    record = next(item for item in runtime.audit.recent(10) if item["op"] == "live_reconnected")
    assert provider.started == ["room-43"]
    assert record["detail"]["previous_room_ref"] == ""
    assert record["detail"]["previous_room_id"] == 0


def test_activate_config_ignores_legacy_room_id_for_non_bilibili_target(runtime: RoastRuntime) -> None:
    runtime.live_connection_state = "connected"
    runtime.safety_guard.set_connected(True)

    activate_config(
        runtime,
        RoastConfig(
            live_platform="douyin",
            live_room_ref="",
            live_room_id=12345,
            live_enabled=True,
        ),
    )

    assert runtime.live_connection_state == "disconnected"
    assert runtime.safety_guard.connected is False


@pytest.mark.asyncio
async def test_update_config_stops_listener_when_live_is_disabled(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 100
    runtime.config.live_enabled = True
    await runtime.bili_live_ingest.start_listening(100)
    runtime.live_audience_session.start_session()

    await runtime.update_config({"live_enabled": False})

    assert runtime.bili_live_ingest.stopped == 1
    assert runtime.bili_live_ingest.room_id == 0
    assert runtime.config.live_enabled is False
    assert runtime.safety_guard.connected is False
    assert runtime.live_connection_snapshot()["connected"] is False
    assert runtime.live_audience_session.snapshot()["active"] is False


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
    persisted = runtime.plugin.config.updates[-1]["neko_roast"]
    assert persisted["live_room_ref"] == "200"
    assert persisted["live_room_id"] == 200


@pytest.mark.asyncio
async def test_connect_live_room_accepts_douyin_url_from_hosted_ui_without_leaking_query(
    runtime: RoastRuntime,
) -> None:
    provider = FakeLiveProvider("")
    runtime.live_provider = provider
    runtime.config.live_platform = "douyin"
    runtime.config.live_room_ref = ""
    room_url = "https://live.douyin.com/room-42?cookie=must-not-leak&signature=hidden"

    snapshot = await runtime.connect_live_room(room_url)
    dumped = json.dumps(
        {
            "snapshot": snapshot,
            "config": runtime.config.to_public_dict(),
            "audit": runtime.audit.recent(10),
            "updates": runtime.plugin.config.updates,
        },
        ensure_ascii=False,
    )

    assert snapshot["platform"] == "douyin"
    assert snapshot["connected"] is True
    assert snapshot["room_ref"] == "room-42"
    assert snapshot["room_id"] == 0
    assert runtime.config.live_room_ref == "room-42"
    assert runtime.config.live_room_id == 0
    assert provider.started == ["room-42"]
    assert provider.configured_room_ref() == "room-42"
    assert "must-not-leak" not in dumped
    assert "signature=hidden" not in dumped


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
async def test_douyin_config_update_keeps_live_room_ref(runtime: RoastRuntime) -> None:
    runtime.config.live_platform = "douyin"
    runtime.config.live_room_ref = "room-42"
    runtime.config.live_enabled = True

    await runtime.update_config({"live_room_ref": "room-43", "live_enabled": True})

    assert runtime.live_provider.platform == "douyin"
    assert runtime.live_provider.configured_room_ref() == "room-43"
    assert runtime.config.live_room_ref == "room-43"


@pytest.mark.asyncio
async def test_stop_live_listener_defaults_to_mark_disabled(runtime: RoastRuntime) -> None:
    runtime.config.live_enabled = True
    runtime.live_room_context = {"live_status": "live", "title": "room"}
    await runtime.bili_live_ingest.start_listening(100)
    runtime.live_audience_session.start_session()

    await stop_live_listener(runtime)

    assert runtime.config.live_enabled is False
    assert runtime.live_connection_snapshot()["connected"] is False
    assert runtime.live_room_context == {"live_status": "unknown"}
    assert runtime.live_audience_session.snapshot()["active"] is False
    assert runtime.live_audience_session.snapshot()["has_session"] is True


@pytest.mark.asyncio
async def test_live_listener_starts_session_and_dashboard_projects_it(runtime: RoastRuntime) -> None:
    await runtime._start_live_listener(123)

    state = await runtime.dashboard_state()

    assert state["live_session"]["active"] is True
    assert state["live_session"]["has_session"] is True
    assert state["live_session"]["interaction_viewer_count"] == 0


async def test_dashboard_state_uses_public_config_projection(runtime: RoastRuntime) -> None:
    class _SecretLike:
        def __str__(self) -> str:
            return "token=must-not-leak"

        def __bool__(self) -> bool:
            return True

    secret = _SecretLike()
    runtime.config.live_platform = secret  # type: ignore[assignment]
    runtime.config.live_room_ref = "https://live.douyin.com/123?signature=room-secret"
    runtime.config.live_enabled = secret  # type: ignore[assignment]
    runtime.config.viewer_store_dir = secret  # type: ignore[assignment]

    state = await runtime.dashboard_state()

    rendered = json.dumps(state["config"], ensure_ascii=False, sort_keys=True)
    assert state["config"]["live_platform"] == "bilibili"
    assert state["config"]["live_enabled"] is False
    assert state["config"]["viewer_store_dir"] == ""
    assert "[redacted]" in rendered
    assert "must-not-leak" not in rendered
    assert "room-secret" not in rendered


def test_runtime_health_rows_do_not_stringify_public_projection_objects(runtime: RoastRuntime) -> None:
    class _LooksLikePublicValue:
        def __str__(self) -> str:
            return "health-object-secret"

        def __int__(self) -> int:
            return 99

    value = _LooksLikePublicValue()
    runtime.live_provider = SimpleNamespace(
        status=lambda: {
            "last_event_at": "2026-06-20T10:00:00+00:00",
            "last_event_type": value,
            "last_published_event_type": value,
            "last_status_only_event_type": value,
        }
    )
    runtime.event_bus = SimpleNamespace(
        status=lambda: {
            "publish_count": value,
            "last_publish_at": "2026-06-20T10:00:00+00:00",
            "last_event_type": value,
        }
    )
    runtime.live_events = SimpleNamespace(
        status=lambda: {
            "last_decision_at": "2026-06-20T10:00:00+00:00",
            "last_candidate_count": value,
            "last_selected_type": value,
        }
    )
    runtime.recent_results.append(
        {
            "status": value,
            "reason": value,
            "response_latency_ms": value,
            "steps": [{"id": "neko_dispatcher"}],
        }
    )
    runtime.dispatcher.output_channel_status = lambda: {
        "ready": False,
        "reason": value,
        "detail": value,
    }
    runtime._config_last_error = value

    rows = {row["id"]: row for row in runtime.runtime_health_rows()}

    assert rows["live_ingest"]["last_outcome"] == ""
    assert rows["event_bus"]["status"] == "idle"
    assert rows["event_bus"]["count"] == 0
    assert rows["event_bus"]["last_outcome"] == ""
    assert rows["selection"]["status"] == "idle"
    assert rows["selection"]["count"] == 0
    assert rows["selection"]["last_outcome"] == ""
    assert rows["pipeline"]["last_outcome"] == ""
    assert rows["pipeline"]["last_skip_reason"] == ""
    assert rows["pipeline"]["last_latency_ms"] is None
    assert rows["dispatcher"]["last_skip_reason"] == "output_channel_unavailable"
    assert rows["dispatcher"]["output_channel_detail"] == ""
    assert rows["config_store"]["last_error"] == ""
    assert "health-object-secret" not in str(rows)


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
        {"id": "live_signal", "age_sec": 3.0, "last_outcome": "live_danmaku"},
    ]

    assert runtime._last_viewer_activity_age_sec(rows) == 3.0


def test_live_danmaku_signal_refreshes_viewer_activity_even_before_reply(
    runtime: RoastRuntime,
) -> None:
    runtime._hosting_without_viewer_count = 2
    remember_live_danmaku_seen(
        runtime,
        ViewerEvent(
            uid="fake_u01",
            nickname="viewer",
            danmaku_text="1",
            source="live_danmaku",
        ),
    )

    rows = runtime.runtime_health_rows()
    signal_row = next(row for row in rows if row["id"] == "live_signal")

    assert runtime._hosting_without_viewer_count == 0
    assert signal_row["status"] == "healthy"
    assert signal_row["last_outcome"] == "live_danmaku"
    assert runtime._last_viewer_activity_age_sec(rows) is not None
    assert runtime._last_viewer_activity_age_sec(rows) <= 1.0


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
        "danmaku_response / live_danmaku from viewer: one more line / spent_output_family=food_drink,reward / NEKO already said: old snack reward bit"
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

    assert "spent_output_family=food_drink,reward,audience_prompt" in context[0]
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


def test_record_result_does_not_stringify_object_request_metadata_for_monitoring(runtime: RoastRuntime) -> None:
    class _LooksLikeProfile:
        def __str__(self) -> str:
            return "emoji_or_reaction"

    event = ViewerEvent(uid="42", nickname="viewer", danmaku_text="哈哈哈", source="live_danmaku")
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
            "danmaku_profile": _LooksLikeProfile(),
            "danmaku_reply_target": b"current_reaction",
            "danmaku_reply_shape": {"shape": "mirror_mood_in_a_few_chars"},
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

    assert "danmaku_profile" not in latest
    assert "danmaku_reply_target" not in latest
    assert "danmaku_reply_shape" not in latest


def test_signal_event_type_accepts_only_string_raw_event_type() -> None:
    class _LooksLikeGift:
        def __str__(self) -> str:
            return "gift"

    assert _signal_event_type(ViewerEvent(uid="42", source="live_danmaku", raw={"event_type": " gift "})) == "gift"
    assert _signal_event_type(ViewerEvent(uid="42", source="live_danmaku", raw={"event_type": _LooksLikeGift()})) == ""
    assert _signal_event_type(ViewerEvent(uid="42", source="live_danmaku", raw={"event_type": b"gift"})) == ""
    assert _signal_event_type(ViewerEvent(uid="42", source="live_danmaku", raw={})) == ""


def test_public_lookup_room_ref_does_not_stringify_objects() -> None:
    class _LooksLikeRoomRef:
        def __str__(self) -> str:
            return "room-42"

    assert _public_lookup_room_ref(" room-42 ") == "room-42"
    assert _public_lookup_room_ref(42) == "42"
    assert _public_lookup_room_ref(True) == ""
    assert _public_lookup_room_ref(_LooksLikeRoomRef()) == ""


def test_danmaku_mention_parser_distinguishes_neko_cjk_address_from_viewer_nickname() -> None:
    from plugin.plugins.neko_roast.core import danmaku_text_rules

    assert danmaku_text_rules.is_viewer_to_viewer_mention_text("@neko\u505a\u8fd9\u4e2a") is False
    assert danmaku_text_rules.is_viewer_to_viewer_mention_text("@\u732b\u732b\u5199\u9996\u8bd7") is False
    assert danmaku_text_rules.is_viewer_to_viewer_mention_text("@\u732b\u732b\u559c\u6b22\u5976\u8336\u5417") is False
    assert danmaku_text_rules.is_viewer_to_viewer_mention_text("@\u732b\u5a18\u63a8\u8350\u4e00\u4e2a") is False
    assert danmaku_text_rules.is_viewer_to_viewer_mention_text("@\u732b\u732b\u8bf4\u53e5\u8bdd") is False
    assert danmaku_text_rules.is_viewer_to_viewer_mention_text("@\u732b\u5a18\u6559\u6211\u4e00\u4e0b") is False
    assert danmaku_text_rules.is_viewer_to_viewer_mention_text("@\u732b\u732b\u6559\u4f60\u4e00\u62db") is False
    assert danmaku_text_rules.is_viewer_to_viewer_mention_text("@\u732b\u732b\u6559\u5b66\u8fd9\u4e2a") is False
    assert danmaku_text_rules.is_viewer_to_viewer_mention_text("@\u5c0f\u5929\u5531\u6b4c") is False
    assert danmaku_text_rules.is_viewer_to_viewer_mention_text("@\u732b\u732b\u6c42\u63a8\u8350") is False
    assert danmaku_text_rules.is_viewer_to_viewer_mention_text("@\u732b\u5a18\u7ed9\u6211\u4e00\u4e2a\u63a8\u8350") is False
    assert danmaku_text_rules.is_viewer_to_viewer_mention_text("@\u732b\u732b\u597d\u53ef\u7231 \u4f60\u770b\u8fd9\u4e2a") is True
    assert danmaku_text_rules.is_viewer_to_viewer_mention_text("@\u732b\u5a18\u6559\u5e08 \u4f60\u770b\u8fd9\u4e2a") is True
    assert danmaku_text_rules.is_viewer_to_viewer_mention_text("@\u732b\u732b\u6559\u7ec3 \u4f60\u770b\u8fd9\u4e2a") is True
    assert danmaku_text_rules.is_viewer_to_viewer_mention_text("@\u5c0f\u5929\u5531\u7247 \u4f60\u770b\u8fd9\u4e2a") is True
    assert danmaku_text_rules.is_viewer_to_viewer_mention_text("@Alice @neko what do you think") is False
    assert danmaku_text_rules.is_viewer_to_viewer_mention_text("@Alice @\u732b\u732b\uff0c\u4eca\u5929\u64ad\u4ec0\u4e48") is False


@pytest.mark.asyncio
async def test_handle_live_payload_routes_gift_to_support_events_without_avatar_roast(runtime: RoastRuntime) -> None:
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    runtime.config.live_enabled = True
    runtime.bili_live_ingest = BiliLiveIngestModule()
    runtime.bili_live_ingest.ctx = runtime
    calls: list[ViewerEvent] = []

    async def record_call(event: ViewerEvent) -> InteractionResult:
        calls.append(event)
        return InteractionResult(
            accepted=True,
            status="dry_run",
            event=event,
            steps=[PipelineStep("live_support_events", "dry_run")],
        )

    runtime.pipeline.handle_event = record_call

    result = await runtime.handle_live_payload(
        {
            "uid": "42",
            "nickname": "viewer",
            "text": "sent a small gift",
            "event_type": "gift",
        }
    )

    assert len(calls) == 1
    assert result.status == "dry_run"
    assert calls[0].raw["event_type"] == "gift"
    assert result.steps[0].id == "live_support_events"
    assert all(step.id != "avatar_roast" for step in result.steps)


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
    runtime._live_state_now = lambda: 1000.0
    await runtime.bili_live_ingest.start_listening(123)
    runtime._live_listener_started_at = 760.0
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
async def test_live_state_moves_from_warmup_after_any_hosting_output(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=ViewerEvent(uid="__neko_active__", nickname="NEKO", source="active_engagement", live_mode="solo_stream"),
            steps=[PipelineStep("active_engagement", "ok"), PipelineStep("neko_dispatcher", "ok")],
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
    assert "non-numeric danmaku cue" in result.request.prompt_text
    assert "Do not announce that nobody is talking" in result.request.prompt_text
    assert "last_activity_age_sec" not in result.request.prompt_text
    assert "nobody is here" not in result.request.prompt_text
    assert "beg for comments" not in result.request.prompt_text
    assert "welcome everyone" not in result.request.prompt_text
    assert "please interact" not in result.request.prompt_text
    assert runtime.recent_results[-1]["status"] == "dry_run"
    assert runtime.recent_results[-1]["event"]["source"] == "idle_hosting"
    assert runtime.plugin.pushed_messages == []


@pytest.mark.asyncio
async def test_idle_and_warmup_hosting_controls_block_manual_and_automatic_triggers(
    runtime: RoastRuntime,
) -> None:
    runtime.config.idle_hosting_enabled = False
    runtime.config.warmup_hosting_enabled = False

    idle = await runtime.trigger_idle_hosting()
    warmup = await runtime.trigger_warmup_hosting()

    assert idle.status == "skipped"
    assert idle.reason == "idle_hosting.disabled"
    assert warmup.status == "skipped"
    assert warmup.reason == "warmup_hosting.disabled"
    assert await runtime.maybe_trigger_idle_hosting() is None
    assert await runtime.maybe_trigger_warmup_hosting() is None


@pytest.mark.asyncio
async def test_auto_idle_hosting_skips_when_previous_idle_has_no_viewer_reply(
    runtime: RoastRuntime,
) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    _record_result_at(runtime, age_seconds=360)
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
            created_at=_created_at_age(120),
        )
    )

    result = await runtime.maybe_trigger_idle_hosting()

    assert result is not None
    assert result.status == "skipped"
    assert result.reason == "idle_hosting.no_fresh_material"
    assert runtime.recent_results[-1]["status"] == "skipped"
    assert runtime.recent_results[-1]["reason"] == "idle_hosting.no_fresh_material"


@pytest.mark.asyncio
async def test_auto_idle_hosting_runs_after_viewer_replies_to_previous_idle(
    runtime: RoastRuntime,
) -> None:
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
            event=ViewerEvent(
                uid="__neko_idle__",
                nickname="NEKO",
                source="idle_hosting",
                live_mode="solo_stream",
            ),
            steps=[PipelineStep("idle_hosting", "ok"), PipelineStep("neko_dispatcher", "dry_run")],
            created_at=_created_at_age(360),
        )
    )
    _record_result_at(runtime, age_seconds=180)

    result = await runtime.maybe_trigger_idle_hosting()

    assert result is not None
    assert result.status == "dry_run"
    assert result.event.source == "idle_hosting"
    assert runtime.recent_results[-1]["event"]["source"] == "idle_hosting"


@pytest.mark.asyncio
async def test_auto_idle_hosting_skips_after_two_hosting_outputs_without_viewer_reply(
    runtime: RoastRuntime,
) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_enabled = True
    runtime.config.dry_run = True
    runtime.config.live_mode = "solo_stream"
    await runtime.bili_live_ingest.start_listening(123)
    runtime.safety_guard.set_connected(True)
    for index, source in enumerate(("warmup_hosting", "active_engagement"), start=1):
        runtime.record_result(
            InteractionResult(
                accepted=True,
                status="pushed",
                event=ViewerEvent(
                    uid=f"__neko_host_{index}__",
                    nickname="NEKO",
                    source=source,
                    live_mode="solo_stream",
                ),
                steps=[PipelineStep(source, "ok"), PipelineStep("neko_dispatcher", "ok")],
                created_at=_created_at_age(240 - index),
            )
        )

    result = await runtime.maybe_trigger_idle_hosting()

    assert result is not None
    assert result.status == "skipped"
    assert result.reason == "idle_hosting.no_viewer_response"


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
            status="pushed",
            event=first,
            steps=[PipelineStep("idle_hosting", "ok"), PipelineStep("neko_dispatcher", "ok")],
        )
    )
    second = runtime._idle_hosting_event({"state": "idle"})
    runtime.record_result(
        InteractionResult(
            accepted=True,
            status="pushed",
            event=second,
            steps=[PipelineStep("idle_hosting", "ok"), PipelineStep("neko_dispatcher", "ok")],
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
            status="pushed",
            event=ViewerEvent(uid="__neko_warmup__", nickname="NEKO", source="warmup_hosting", live_mode="solo_stream"),
            steps=[PipelineStep("warmup_hosting", "ok"), PipelineStep("neko_dispatcher", "ok")],
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
async def test_cancelled_stop_can_be_retried(runtime: RoastRuntime, monkeypatch: pytest.MonkeyPatch) -> None:
    async def cancel_stop() -> None:
        raise asyncio.CancelledError

    async def complete_stop() -> None:
        return None

    monkeypatch.setattr(runtime, "_stop_idle_hosting_loop", cancel_stop)
    with pytest.raises(asyncio.CancelledError):
        await runtime.stop()

    assert runtime._stopping is False

    monkeypatch.setattr(runtime, "_stop_idle_hosting_loop", complete_stop)
    await runtime.stop()

    assert runtime._stopping is True


@pytest.mark.asyncio
async def test_config_store_health_row_tracks_successful_persist(runtime: RoastRuntime) -> None:
    runtime.plugin.ctx = SimpleNamespace(update_own_config=None)

    await runtime.update_config({"dry_run": True})
    state = await runtime.dashboard_state()

    rows = {row["id"]: row for row in state["health_rows"]}
    assert rows["config_store"]["status"] == "healthy"
    assert rows["config_store"]["age_sec"] is not None
    assert rows["config_store"]["last_error"] == ""


@pytest.mark.asyncio
async def test_disconnect_live_room_clears_stale_connected_room_status(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123

    connected = await runtime.connect_live_room()
    disconnected = await runtime.disconnect_live_room()

    assert connected["connected"] is True
    assert connected["live_status"] == "live"
    assert disconnected["connected"] is False
    assert disconnected["listening"] is False
    assert disconnected["live_status"] == "unknown"
    assert runtime.config.live_enabled is False
    assert runtime.live_room_context == {"live_status": "unknown"}


@pytest.mark.asyncio
async def test_disconnect_live_room_exits_cached_live_prompt_context(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123

    await runtime.connect_live_room()
    await runtime.disconnect_live_room()

    assert runtime.instructions_injected is False
    assert runtime.plugin.pushed_messages[0]["metadata"]["description"] == "Neko Roast behavior instructions"
    assert (
        runtime.plugin.pushed_messages[-1]["metadata"]["description"]
        == "Neko Roast behavior restore"
    )

    request = runtime.danmaku_response.build_request(
        ViewerEvent(
            uid="42",
            nickname="viewer",
            danmaku_text="今天还播吗",
            source="live_danmaku",
            live_mode="solo_stream",
        ),
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer", roast_count=1),
    )

    assert "战雷陆战练车" not in request.prompt_text
    assert "live_room_title_theme" not in request.prompt_text
    assert "live_room_anchor_name" not in request.prompt_text
    assert "live_room_status" not in request.prompt_text


def test_safety_guard_blocks_live_like_output_when_disconnected(
    runtime: RoastRuntime,
) -> None:
    runtime.safety_guard.set_connected(False)

    for source in (
        "live_danmaku",
        "manual_live_simulation",
        "idle_hosting",
        "active_engagement",
        "warmup_hosting",
    ):
        decision = runtime.safety_guard.before_output(
            ViewerEvent(uid="42", nickname="viewer", source=source)
        )
        assert decision.allowed is False
        assert decision.status == "disconnected"

    sandbox = runtime.safety_guard.before_output(
        ViewerEvent(uid="42", nickname="viewer", source="developer_sandbox")
    )
    assert sandbox.allowed is True
