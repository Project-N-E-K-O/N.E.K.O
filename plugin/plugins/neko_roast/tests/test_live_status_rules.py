from types import SimpleNamespace

from plugin.plugins.neko_roast.core import runtime_dashboard
from plugin.plugins.neko_roast.core.live_status_active import active_engagement_status
from plugin.plugins.neko_roast.core.live_status_director import live_director_status
from plugin.plugins.neko_roast.core.live_status_timing import (
    recent_hosting_output_age_sec,
    recent_live_danmaku_event_age_sec,
)
from plugin.plugins.neko_roast.core.recent_output_families import spent_output_families
from plugin.plugins.neko_roast.core.runtime_recent_context_api import (
    RuntimeRecentContextApiMixin,
)
from plugin.plugins.neko_roast.core.runtime_dashboard_api import (
    RuntimeDashboardApiMixin,
)


class _RecentContextRuntime(RuntimeRecentContextApiMixin):
    def __init__(self, recent_results):
        self.recent_results = recent_results


def test_recent_hosting_output_age_ignores_dry_run_results():
    results = [
        {
            "status": "dry_run",
            "response_module": "idle_hosting",
            "created_at": "2026-07-09T23:00:00Z",
        }
    ]

    assert recent_hosting_output_age_sec(results, lambda value: 1.0) is None


def test_recent_danmaku_event_age_ignores_dry_run_results():
    results = [
        {
            "status": "dry_run",
            "event": {"source": "live_danmaku"},
            "created_at": "2026-07-10T18:00:00Z",
        }
    ]

    assert recent_live_danmaku_event_age_sec(results, lambda value: 1.0) is None


def test_dry_run_danmaku_does_not_reset_actual_route_streak():
    runtime = _RecentContextRuntime(
        [
            {"status": "pushed", "response_module": "idle_hosting"},
            {"status": "pushed", "response_module": "idle_hosting"},
            {"status": "dry_run", "event": {"source": "live_danmaku"}},
        ]
    )

    assert runtime._recent_actual_route_streak_since_viewer_activity("idle_hosting") == 2


def test_spent_output_families_returns_choice_vote_once():
    families = spent_output_families("choice either_or \u4e8c\u9009\u4e00")

    assert families.count("choice_vote") == 1


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
        recent_hosting_output_age=None,
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


def test_idle_takeover_streak_preserves_action_during_host_output_cooldown():
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
        idle_hosting_status={"eligible": False, "reason": "recent_host_output"},
        active_engagement_status=active,
    )

    assert active["reason"] == "idle_hosting_streak"
    assert active["eligible"] is False
    assert active["cooldown_remaining"] == 80.0
    assert director["next_auto_action"] == "active_engagement"
    assert director["eligible"] is False
    assert director["reason"] == "idle_hosting_streak"


def test_idle_takeover_streak_preserves_action_during_minimum_interval():
    active = active_engagement_status(
        config=SimpleNamespace(live_mode="solo_stream", activity_level="standard"),
        live_status={"summary": "ready_to_stream", "cooldown_remaining": 0.0},
        live_state={"state": "idle", "mode": "solo_stream"},
        now=120.0,
        last_attempt_at=100.0,
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
    assert active["eligible"] is False
    assert active["cooldown_remaining"] == 40.0
    assert director["next_auto_action"] == "active_engagement"
    assert director["eligible"] is False
    assert director["reason"] == "idle_hosting_streak"


def test_idle_takeover_streak_preserves_action_during_danmaku_cooldown():
    active = active_engagement_status(
        config=SimpleNamespace(live_mode="solo_stream", activity_level="standard"),
        live_status={"summary": "ready_to_stream", "cooldown_remaining": 0.0},
        live_state={"state": "idle", "mode": "solo_stream"},
        now=120.0,
        last_attempt_at=0.0,
        min_interval_seconds=60.0,
        recent_danmaku_output_age=10.0,
        recent_danmaku_wait_seconds=45.0,
        idle_hosting_wait_remaining=None,
        idle_grace_seconds=30.0,
        idle_takeover_streak=3,
    )

    director = live_director_status(
        config=SimpleNamespace(live_mode="solo_stream"),
        live_status={"summary": "ready_to_stream"},
        live_state={"state": "idle", "mode": "solo_stream"},
        idle_hosting_status={"eligible": False, "reason": "recent_danmaku_output"},
        active_engagement_status=active,
    )

    assert active["reason"] == "idle_hosting_streak"
    assert active["eligible"] is False
    assert active["cooldown_remaining"] == 35.0
    assert director["next_auto_action"] == "active_engagement"
    assert director["eligible"] is False
    assert director["reason"] == "idle_hosting_streak"


def test_runtime_dashboard_api_keeps_module_level_compatibility_exports(monkeypatch):
    health_rows = [{"id": "pipeline", "status": "ok"}]
    actions = [{"id": "refresh", "label": "Refresh"}]
    monkeypatch.setattr(runtime_dashboard, "runtime_health_rows", lambda runtime: health_rows)
    monkeypatch.setattr(runtime_dashboard, "dashboard_actions", lambda: actions)

    runtime = RuntimeDashboardApiMixin()

    assert runtime.runtime_health_rows() == health_rows
    assert runtime.dashboard_actions() == actions
