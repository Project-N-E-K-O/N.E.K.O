from __future__ import annotations

from plugin.plugins.neko_roast.core import live_status
from plugin.plugins.neko_roast.core.contracts import RoastConfig


def test_live_status_summary_blocks_until_stream_is_connected_and_output_ready():
    config = RoastConfig(live_room_id=42, live_enabled=True, live_mode="solo_stream", dry_run=False)

    disconnected = live_status.live_status_summary(
        config=config,
        live_connection={"connected": False, "room_id": 42},
        safety_status="running",
        cooldown_remaining=0.0,
        output_channel={"ready": True},
    )
    output_blocked = live_status.live_status_summary(
        config=config,
        live_connection={"connected": True, "room_id": 42},
        safety_status="running",
        cooldown_remaining=0.0,
        output_channel={"ready": False, "reason": "output_channel_unavailable"},
    )

    assert disconnected["summary"] == "cannot_stream"
    assert disconnected["reason"] == "live_ingest_disconnected"
    assert output_blocked["summary"] == "cannot_stream"
    assert output_blocked["reason"] == "output_channel_unavailable"


def test_live_status_summary_normalizes_platform_alias_from_connection_snapshot():
    config = RoastConfig(
        live_platform="dy",
        live_room_ref="https://live.douyin.com/example?cookie=must-not-leak",
        live_room_id=12345,
        live_enabled=True,
    )

    state = live_status.live_status_summary(
        config=config,
        live_connection={"connected": False, "platform": "dy", "room_ref": "example", "room_id": 67890},
        safety_status="running",
        cooldown_remaining=0.0,
        output_channel={"ready": True},
    )

    assert state["platform"] == "douyin"
    assert state["room_ref"] == "example"
    assert state["room_id"] == 0
    assert "must-not-leak" not in str(state)


def test_live_status_summary_prefers_sanitized_connection_room_target():
    config = RoastConfig(
        live_platform="bilibili",
        live_room_ref="https://live.bilibili.com/111",
        live_room_id=111,
        live_enabled=True,
        dry_run=False,
    )

    state = live_status.live_status_summary(
        config=config,
        live_connection={
            "connected": True,
            "platform": "bilibili",
            "room_ref": "https://live.bilibili.com/222",
            "room_id": 222,
        },
        safety_status="running",
        cooldown_remaining=0.0,
        output_channel={"ready": True},
    )

    assert state["room_ref"] == "https://live.bilibili.com/222"
    assert state["room_id"] == 222
    assert state["summary"] == "ready_to_stream"


def test_live_status_summary_does_not_stringify_room_target_objects():
    class _LooksLikeRoomRef:
        def __str__(self) -> str:
            return "room-object-secret"

    class _LooksLikeRoomId:
        def __int__(self) -> int:
            return 12345

    config = RoastConfig(live_platform="bilibili", live_enabled=True, dry_run=False)
    config.live_room_ref = _LooksLikeRoomRef()  # type: ignore[assignment]
    config.live_room_id = _LooksLikeRoomId()  # type: ignore[assignment]

    state = live_status.live_status_summary(
        config=config,
        live_connection={
            "connected": True,
            "platform": "bilibili",
            "room_ref": _LooksLikeRoomRef(),
            "room_id": _LooksLikeRoomId(),
        },
        safety_status="running",
        cooldown_remaining=0.0,
        output_channel={"ready": True},
    )

    assert state["room_ref"] == ""
    assert state["room_id"] == 0
    assert state["summary"] == "cannot_stream"
    assert state["reason"] == "room_not_configured"
    assert "room-object-secret" not in str(state)


def test_live_state_summary_marks_solo_warmup_before_first_viewer_activity():
    config = RoastConfig(live_room_id=42, live_enabled=True, live_mode="solo_stream", dry_run=True)

    state = live_status.live_state_summary(
        config=config,
        live_status={"summary": "test_only", "safety_status": "running", "cooldown_remaining": 0.0},
        health_rows=[],
        recent_results=[],
        warmup_observed=False,
        warmup_elapsed=10.0,
        engaged_threshold=60.0,
        idle_threshold=120.0,
        warmup_timeout_seconds=45.0,
    )

    assert state["state"] == "warmup"
    assert state["warmup_hosting_candidate"] is True
    assert state["idle_hosting_candidate"] is False


def test_active_engagement_status_defers_when_idle_hosting_is_approaching():
    config = RoastConfig(live_room_id=42, live_enabled=True, live_mode="solo_stream", dry_run=False)

    status = live_status.active_engagement_status(
        config=config,
        live_status={"summary": "ready_to_stream", "cooldown_remaining": 0.0},
        live_state={"state": "quiet"},
        now=100.0,
        last_attempt_at=0.0,
        min_interval_seconds=60.0,
        recent_danmaku_output_age=None,
        recent_danmaku_wait_seconds=45.0,
        idle_hosting_wait_remaining=5.0,
        idle_grace_seconds=15.0,
        idle_takeover_streak=0,
    )

    assert status["candidate"] is True
    assert status["eligible"] is False
    assert status["reason"] == "approaching_idle_hosting"
    assert status["cooldown_remaining"] == 5.0


def test_live_director_status_routes_idle_takeover_to_active_engagement():
    config = RoastConfig(live_room_id=42, live_enabled=True, live_mode="solo_stream", dry_run=False)

    director = live_status.live_director_status(
        config=config,
        live_status={"summary": "ready_to_stream"},
        live_state={"state": "idle", "mode": "solo_stream"},
        idle_hosting_status={"eligible": True, "cooldown_remaining": 0.0, "min_interval_seconds": 90.0},
        active_engagement_status={
            "eligible": True,
            "reason": "idle_hosting_streak",
            "cooldown_remaining": 0.0,
            "min_interval_seconds": 60.0,
        },
    )

    assert director["next_auto_action"] == "active_engagement"
    assert director["eligible"] is True
    assert director["reason"] == "idle_hosting_streak"
