from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from plugin.core.ui_manifest import normalize_plugin_ui_manifest
from plugin.plugins.study_companion.models import build_config
from plugin.plugins.study_companion.ui_api import (
    build_habit_dashboard_payload,
    build_pomodoro_status_payload,
)
from plugin.server.application.plugins.ui_query_service import _build_surfaces_sync


def test_phase6_config_parses_habit_defaults_and_clamps_ranges() -> None:
    config = build_config(
        {
            "study": {
                "pomodoro": {
                    "focus_minutes": 500,
                    "short_break_minutes": -1,
                    "long_break_minutes": 99,
                    "long_break_interval": 50,
                    "allow_skip_break": False,
                },
                "supervision": {
                    "enabled": True,
                    "remind_interval_minutes": 0,
                    "inactivity_timeout_minutes": 99,
                    "allow_disable_by_chat": False,
                },
                "checkin": {
                    "streak_timezone": "Asia/Shanghai",
                    "makeup_window_days": 99,
                    "auto_derive_from_session": False,
                },
            }
        }
    )

    assert config.pomodoro.focus_minutes == 25
    assert config.pomodoro.short_break_minutes == 5
    assert config.pomodoro.long_break_minutes == 15
    assert config.pomodoro.long_break_interval == 4
    assert config.pomodoro.allow_skip_break is False
    assert config.supervision.remind_interval_minutes == 10
    assert config.supervision.inactivity_timeout_minutes == 5
    assert config.supervision.allow_disable_by_chat is False
    assert config.checkin.streak_timezone == "Asia/Shanghai"
    assert config.checkin.makeup_window_days == 3
    assert config.checkin.auto_derive_from_session is False


def test_phase6_ui_panels_are_registered() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    with (plugin_dir / "plugin.toml").open("rb") as handle:
        config = tomllib.load(handle)
    plugin_ui = normalize_plugin_ui_manifest(config, plugin_id="study_companion")
    assert plugin_ui is not None
    surfaces, warnings = _build_surfaces_sync(
        "study_companion",
        {
            "id": "study_companion",
            "config_path": str(plugin_dir / "plugin.toml"),
            "plugin_ui": plugin_ui,
            "i18n": config["plugin"]["i18n"],
        },
    )

    assert warnings == []
    surface_ids = {surface["id"] for surface in surfaces if surface["available"]}
    assert {
        "habit-dashboard",
        "pomodoro-panel",
        "daily-goal-editor",
        "session-summary",
    }.issubset(surface_ids)


def test_phase6_ui_payload_builders_shape_dashboard_and_pomodoro_status() -> None:
    pomodoro = build_pomodoro_status_payload(
        {"state": "focusing", "remaining_seconds": 1200, "session_count": 2},
    )
    dashboard = build_habit_dashboard_payload(
        goals=[
            {
                "id": "g1",
                "status": "completed",
                "target_amount": 1,
                "progress_amount": 1,
            }
        ],
        checkin={"checked_in": True, "streak_days": 5},
        pomodoro=pomodoro,
        summary={"total_focus_minutes": 50},
        supervision={"enabled": True, "sensor_available": False},
    )

    assert pomodoro["state"] == "focusing"
    assert pomodoro["remaining_seconds"] == 1200
    assert dashboard["summary"]["completed_goal_count"] == 1
    assert dashboard["checkin"]["streak_days"] == 5
    assert dashboard["supervision"]["sensor_available"] is False
