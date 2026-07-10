from types import SimpleNamespace

from plugin.plugins.neko_roast.core.live_status_active import active_engagement_status
from plugin.plugins.neko_roast.core.live_status_director import live_director_status
from plugin.plugins.neko_roast.core.live_status_timing import recent_hosting_output_age_sec


def test_recent_hosting_output_age_ignores_dry_run_results():
    results = [
        {
            "status": "dry_run",
            "response_module": "idle_hosting",
            "created_at": "2026-07-09T23:00:00Z",
        }
    ]

    assert recent_hosting_output_age_sec(results, lambda value: 1.0) is None


def test_idle_takeover_streak_reaches_director_active_engagement_branch():
    active = active_engagement_status(
        config=SimpleNamespace(live_mode="solo_stream", activity_level="standard"),
        live_status={"summary": "ready_to_stream", "cooldown_remaining": 0.0},
        live_state={"state": "idle", "mode": "solo_stream"},
        now=120.0,
        last_attempt_at=0.0,
        min_interval_seconds=60.0,
        recent_danmaku_output_age=None,
        recent_danmaku_wait_seconds=45.0,
        idle_hosting_wait_remaining=None,
        idle_grace_seconds=30.0,
        idle_takeover_streak=3,
        recent_hosting_output_age=10.0,
        host_output_cooldown_seconds=90.0,
    )

    director = live_director_status(
        config=SimpleNamespace(live_mode="solo_stream"),
        live_status={"summary": "ready_to_stream"},
        live_state={"state": "idle", "mode": "solo_stream"},
        idle_hosting_status={"eligible": False, "reason": "minimum_interval"},
        active_engagement_status=active,
    )

    assert active["reason"] == "idle_hosting_streak"
    assert active["eligible"] is True
    assert director["next_auto_action"] == "active_engagement"
    assert director["reason"] == "idle_hosting_streak"
