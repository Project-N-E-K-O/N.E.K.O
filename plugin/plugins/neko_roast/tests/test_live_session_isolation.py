from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from plugin.plugins.neko_roast.core.contracts import (
    InteractionResult,
    LiveEvent,
    LiveRoomStatus,
    ViewerEvent,
    ViewerIdentity,
)
from plugin.plugins.neko_roast.core.runtime import RoastRuntime
from plugin.plugins.neko_roast.core.runtime_live_listener import (
    start_live_listener,
    stop_live_listener,
)
from plugin.plugins.neko_roast.core.runtime_live_session import invalidate_live_session
from plugin.plugins.neko_roast.modules.bili_live_ingest import BiliLiveIngestModule


@pytest.mark.asyncio
async def test_start_live_listener_starts_fresh_session_state(runtime: RoastRuntime) -> None:
    runtime.recent_results.append({"status": "pushed", "response_module": "warmup_hosting"})
    runtime.live_events._last_dispatch_at = 91.0
    runtime.live_events._last_decision_at = 92.0
    runtime.live_events._room_topic.remember_live_event(
        ViewerEvent(uid="old-viewer", nickname="old", danmaku_text="old room topic"),
        score=1.0,
    )
    runtime._idle_hosting_last_attempt_at = 93.0
    runtime._idle_hosting_recent_beat_keys.append("old-beat")
    runtime._active_engagement_last_attempt_at = 94.0
    runtime._active_engagement_recent_topic_keys.append("old-topic")
    runtime._recent_host_material_families.append("old-family")
    runtime.runtime_timeline.append({"trace_id": "old-trace"})
    runtime._last_live_danmaku_seen_at = 95.0
    runtime._last_live_danmaku_seen_type = "live_danmaku"

    started = await start_live_listener(runtime, 123)

    assert started is True
    assert getattr(runtime, "_live_session_generation", 0) > 0
    assert list(runtime.recent_results) == []
    assert runtime.live_events._last_dispatch_at == 0.0
    assert runtime.live_events._last_decision_at == 0.0
    assert runtime.live_events.status()["recent_danmaku_candidates"] == 0
    assert runtime._idle_hosting_last_attempt_at == 0.0
    assert list(runtime._idle_hosting_recent_beat_keys) == []
    assert runtime._active_engagement_last_attempt_at == 0.0
    assert list(runtime._active_engagement_recent_topic_keys) == []
    assert list(runtime._recent_host_material_families) == []
    assert list(runtime.runtime_timeline) == []
    assert runtime._last_live_danmaku_seen_at == 0.0
    assert runtime._last_live_danmaku_seen_type == ""


@pytest.mark.asyncio
async def test_late_result_from_previous_session_is_discarded(runtime: RoastRuntime) -> None:
    assert await start_live_listener(runtime, 123) is True
    old_generation = runtime._live_session_generation
    old_event = ViewerEvent(
        uid="old-viewer",
        nickname="old",
        danmaku_text="old room message",
        source="live_danmaku",
        raw={"_live_session_generation": old_generation},
    )
    old_result = InteractionResult(
        accepted=True,
        status="pushed",
        event=old_event,
        output="late old-room output",
    )

    assert await start_live_listener(runtime, 456) is True
    runtime.record_result(old_result)

    assert runtime._live_session_generation != old_generation
    assert list(runtime.recent_results) == []
    assert runtime.live_audience_session.snapshot()["neko_output_count"] == 0


@pytest.mark.asyncio
async def test_pipeline_binds_live_event_to_current_session(runtime: RoastRuntime) -> None:
    assert await start_live_listener(runtime, 123) is True
    event = ViewerEvent(uid="", source="live_danmaku")

    await runtime.pipeline.handle_event(event)

    assert event.raw["_live_session_generation"] == runtime._live_session_generation


@pytest.mark.asyncio
async def test_stop_live_listener_invalidates_current_session(runtime: RoastRuntime) -> None:
    assert await start_live_listener(runtime, 123) is True
    active_generation = runtime._live_session_generation

    await stop_live_listener(runtime)

    assert runtime._live_session_generation != active_generation


@pytest.mark.asyncio
async def test_room_switch_blocks_old_event_before_dispatch(runtime: RoastRuntime) -> None:
    identity_entered = asyncio.Event()
    resume_identity = asyncio.Event()

    async def resolve_identity(_event: ViewerEvent) -> ViewerIdentity:
        identity_entered.set()
        await resume_identity.wait()
        return ViewerIdentity(uid="9", nickname="old viewer")

    class _Dispatcher:
        def __init__(self) -> None:
            self.calls = 0

        async def push_roast(self, _request) -> str:
            self.calls += 1
            return "pushed"

    runtime.bili_identity.resolve = resolve_identity
    original_upsert = runtime.viewer_profile.upsert
    upsert_calls = 0

    async def track_upsert(identity: ViewerIdentity):
        nonlocal upsert_calls
        upsert_calls += 1
        return await original_upsert(identity)

    runtime.viewer_profile.upsert = track_upsert  # type: ignore[method-assign]
    dispatcher = _Dispatcher()
    runtime.dispatcher = dispatcher
    runtime.live_room_context = {"live_status": "live"}
    assert await start_live_listener(runtime, 123) is True
    event = ViewerEvent(
        uid="9",
        nickname="old viewer",
        source="live_danmaku",
        raw={
            "event_type": "gift",
            "gift_name": "old-room gift",
            "support_verified": True,
        },
    )

    pending = asyncio.create_task(runtime.pipeline.handle_event(event))
    await identity_entered.wait()
    assert await start_live_listener(runtime, 456) is True
    resume_identity.set()
    result = await pending

    assert result.status == "skipped"
    assert result.reason == "live_session.stale"
    assert dispatcher.calls == 0
    assert upsert_calls == 0


@pytest.mark.asyncio
async def test_room_switch_refreshes_context_before_syncing_instructions(
    runtime: RoastRuntime,
) -> None:
    runtime.config.live_room_id = 123
    runtime.config.live_room_ref = "123"
    runtime.config.live_enabled = True
    runtime.live_room_context = {
        "platform": "bilibili",
        "room_ref": "123",
        "room_id": 123,
        "title": "old room",
        "anchor_name": "old anchor",
        "live_status": "live",
    }
    assert await start_live_listener(runtime, 123) is True

    async def lookup_room_status(room_id: int) -> LiveRoomStatus:
        assert room_id == 456
        return LiveRoomStatus(
            room_id=456,
            ok=True,
            title="new room",
            anchor_name="new anchor",
            live_status="live",
        )

    synced_contexts: list[tuple[dict, bool]] = []

    async def sync_live_instructions(*, force: bool = False) -> str:
        synced_contexts.append((dict(runtime.live_room_context), force))
        return "instructions_injected"

    runtime.bili_live_ingest.lookup_room_status = lookup_room_status
    runtime.sync_live_instructions = sync_live_instructions  # type: ignore[method-assign]

    await runtime.update_config(
        {"live_room_id": 456, "live_room_ref": "456", "live_enabled": True}
    )

    assert runtime.live_room_context["room_id"] == 456
    assert runtime.live_room_context["title"] == "new room"
    assert runtime.live_room_context["anchor_name"] == "new anchor"
    assert synced_contexts == [(runtime.live_room_context, True)]


@pytest.mark.asyncio
async def test_bili_listener_stop_clears_support_event_dedupe_window() -> None:
    module = BiliLiveIngestModule()
    module._recent_support_event_keys["old-room-gift"] = 123.0
    module._last_event_at = 124.0
    module._last_event_type = "gift"

    await module.stop_listening()

    assert module._recent_support_event_keys == {}
    assert module._last_event_at == 0.0
    assert module._last_event_type == ""


def test_bili_event_captures_session_before_event_bus_handoff() -> None:
    published: list[LiveEvent] = []
    module = BiliLiveIngestModule()
    module.ctx = SimpleNamespace(
        _stopping=False,
        _live_session_generation=17,
        event_bus=SimpleNamespace(
            publish=lambda _event_type, event: published.append(event)
        ),
    )
    module._room_id = 123

    module._on_live_event(
        "DANMU_MSG",
        {"uid": 9, "nickname": "viewer", "text": "queued before room switch"},
    )

    assert published[0].session_generation == 17


def test_live_event_generation_survives_payload_projection(
    runtime: RoastRuntime,
) -> None:
    event = LiveEvent(
        type="danmaku",
        uid="9",
        payload={"uid": "9", "text": "queued"},
        session_generation=23,
    )
    runtime.live_events.ctx = runtime

    payload = runtime.live_events._payload_for_event(event, "danmaku")

    assert payload["_live_session_generation"] == 23

    support_event = LiveEvent(
        type="gift",
        uid="9",
        payload={"uid": "9", "gift_name": "gift"},
        raw={"uid": "9", "gift_name": "gift", "event_type": "gift"},
        session_generation=23,
    )
    runtime.live_support_events.ctx = runtime
    support_payload = runtime.live_support_events._payload_for_event(
        support_event.raw,
        event_type_hint="gift",
        fallback_event=support_event,
    )
    assert support_payload["_live_session_generation"] == 23


def test_douyin_normalize_preserves_internal_session_generation(
    runtime: RoastRuntime,
) -> None:
    runtime.config.live_platform = "douyin"

    event = runtime.live_provider.normalize(
        {
            "uid": "viewer-9",
            "text": "queued",
            "_live_session_generation": 31,
        }
    )

    assert event.raw["_live_session_generation"] == 31


@pytest.mark.asyncio
async def test_live_event_is_blocked_while_listener_is_not_accepting(
    runtime: RoastRuntime,
) -> None:
    class _Dispatcher:
        def __init__(self) -> None:
            self.calls = 0

        async def push_roast(self, _request) -> str:
            self.calls += 1
            return "pushed"

    assert await start_live_listener(runtime, 123) is True
    runtime._accepting_live_events = False
    dispatcher = _Dispatcher()
    runtime.dispatcher = dispatcher
    event = ViewerEvent(
        uid="9",
        nickname="viewer",
        source="live_danmaku",
        raw={
            "event_type": "gift",
            "gift_name": "startup gift",
            "support_verified": True,
            "_live_session_generation": runtime._live_session_generation,
        },
    )

    result = await runtime.pipeline.handle_event(event)

    assert result.status == "skipped"
    assert result.reason == "live_session.stale"
    assert dispatcher.calls == 0


@pytest.mark.asyncio
async def test_session_reset_cancels_pending_event_tasks(runtime: RoastRuntime) -> None:
    wait_forever = asyncio.Event()
    live_task = asyncio.create_task(wait_forever.wait())
    support_task = asyncio.create_task(wait_forever.wait())
    runtime.live_events._tasks.add(live_task)
    runtime.live_support_events._tasks.add(support_task)

    runtime.live_events.reset()
    runtime.live_support_events.reset()
    await asyncio.sleep(0)

    assert live_task.cancelled()
    assert support_task.cancelled()


@pytest.mark.asyncio
async def test_invalidating_live_session_cancels_open_support_combo(runtime: RoastRuntime) -> None:
    await runtime.live_support_events.setup(runtime)
    scheduler = runtime.live_support_events._scheduler
    assert scheduler is not None
    scheduler._combo_idle_seconds = 0.01
    dispatched: list[dict] = []

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload)

    scheduler._dispatch = dispatch
    scheduler.submit(
        {
            "event_type": "gift",
            "uid": "viewer-9",
            "room_ref": "42",
            "gift_name": "Heart",
            "gift_count": 3,
            "combo_count": 3,
            "combo_id": "combo-1",
            "combo_end": False,
            "provider_event_type": "COMBO_SEND",
            "provider_event_id": "evt-1",
            "support_verified": True,
            "support_evidence": "manual_live_simulation",
        }
    )
    assert scheduler.status()["active_combo_count"] == 1

    invalidate_live_session(runtime)
    await asyncio.sleep(0.02)

    assert dispatched == []
    assert scheduler.status()["active_combo_count"] == 0
    assert scheduler.status()["pending_count"] == 0
    await runtime.live_support_events.teardown()
