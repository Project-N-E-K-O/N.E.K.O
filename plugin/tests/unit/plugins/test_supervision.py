from __future__ import annotations

from plugin.plugins.study_companion.supervision import (
    SupervisionConfig,
    SupervisionController,
)


def test_supervision_reminders_are_low_frequency_and_can_be_disabled() -> None:
    controller = SupervisionController(
        SupervisionConfig(
            enabled=True, remind_interval_minutes=10, inactivity_timeout_minutes=5
        ),
        clock=lambda: 0.0,
    )

    start = controller.on_focus_start(
        goal={"subject": "math"}, planned_minutes=25, now=0.0
    )
    assert start["reminder_level"] == "start"
    assert start["enabled"] is True

    assert controller.due_reminder(now=9 * 60)["due"] is False
    assert controller.due_reminder(now=10 * 60)["due"] is True
    assert controller.due_reminder(now=11 * 60)["due"] is False

    disabled = controller.set_enabled(False)

    assert disabled["enabled"] is False
    assert controller.due_reminder(now=30 * 60)["due"] is False

    ended = controller.on_focus_end(now=31 * 60)

    assert ended["focus_active"] is False
    assert ended["reminder_level"] == "end"


def test_supervision_inactivity_degrades_when_sensor_unavailable() -> None:
    controller = SupervisionController(
        SupervisionConfig(
            enabled=True, remind_interval_minutes=10, inactivity_timeout_minutes=5
        ),
        clock=lambda: 0.0,
    )
    controller.on_focus_start(goal={}, planned_minutes=25, now=0.0)

    unavailable = controller.observe_activity(
        ocr_text="", sensor_available=False, now=60.0
    )
    first = controller.observe_activity(
        ocr_text="same text", sensor_available=True, now=120.0
    )
    inactive = controller.observe_activity(
        ocr_text="same text", sensor_available=True, now=421.0
    )
    changed = controller.observe_activity(
        ocr_text="new text", sensor_available=True, now=430.0
    )

    assert unavailable["sensor_available"] is False
    assert unavailable["inactivity_detected"] is False
    assert first["inactivity_detected"] is False
    assert inactive["inactivity_detected"] is True
    assert inactive["suggested_action"] == "pause_or_switch"
    assert changed["inactivity_detected"] is False
    assert changed["reminder_level"] != "inactivity"


def test_supervision_uses_idle_signal_as_activity_when_screen_is_static() -> None:
    controller = SupervisionController(
        SupervisionConfig(
            enabled=True,
            remind_interval_minutes=10,
            inactivity_timeout_minutes=5,
        ),
        clock=lambda: 0.0,
    )
    controller.on_focus_start(goal={}, planned_minutes=25, now=0.0)

    controller.observe_activity(
        ocr_text="same text",
        sensor_available=True,
        idle_seconds=1.0,
        now=60.0,
    )
    active = controller.observe_activity(
        ocr_text="same text",
        sensor_available=True,
        idle_seconds=2.0,
        now=421.0,
    )

    assert active["inactivity_detected"] is False
    assert active["suggested_action"] == ""
    assert active["reminder_level"] != "inactivity"
    assert active["idle_seconds"] == 2.0


def test_supervision_idle_away_detected() -> None:
    controller = SupervisionController(
        SupervisionConfig(
            enabled=True,
            remind_interval_minutes=10,
            inactivity_timeout_minutes=5,
            idle_away_seconds=120,
        ),
        clock=lambda: 0.0,
    )
    controller.on_focus_start(goal={}, planned_minutes=25, now=0.0)

    away = controller.observe_activity(
        ocr_text="same text",
        sensor_available=True,
        idle_seconds=121.0,
        now=121.0,
    )

    assert away["inactivity_detected"] is True
    assert away["suggested_action"] == "pause_or_switch"
    assert away["reminder_level"] == "away"
    assert away["idle_seconds"] == 121.0


def test_supervision_flags_foreground_distraction_during_focus() -> None:
    controller = SupervisionController(
        SupervisionConfig(
            enabled=True,
            remind_interval_minutes=10,
            inactivity_timeout_minutes=5,
        ),
        clock=lambda: 0.0,
    )
    controller.on_focus_start(goal={}, planned_minutes=25, now=0.0)

    result = controller.observe_activity(
        ocr_text="same text",
        sensor_available=True,
        idle_seconds=1.0,
        foreground_category="gaming",
        now=60.0,
    )

    assert result["inactivity_detected"] is False
    assert result["distraction_detected"] is True
    assert result["foreground_category"] == "gaming"
    assert result["suggested_action"] == "return_to_focus"
    assert result["reminder_level"] == "distraction"


def test_supervision_idle_away_takes_priority_over_distraction() -> None:
    controller = SupervisionController(
        SupervisionConfig(
            enabled=True,
            remind_interval_minutes=10,
            inactivity_timeout_minutes=5,
            idle_away_seconds=120,
        ),
        clock=lambda: 0.0,
    )
    controller.on_focus_start(goal={}, planned_minutes=25, now=0.0)

    result = controller.observe_activity(
        ocr_text="same text",
        sensor_available=True,
        idle_seconds=121.0,
        foreground_category="gaming",
        now=121.0,
    )

    assert result["inactivity_detected"] is True
    assert result["suggested_action"] == "pause_or_switch"
    assert result["reminder_level"] == "away"
    assert result["foreground_category"] == "gaming"
    assert result["distraction_detected"] is False


def test_supervision_clears_distraction_level_after_foreground_changes() -> None:
    controller = SupervisionController(
        SupervisionConfig(
            enabled=True,
            remind_interval_minutes=10,
            inactivity_timeout_minutes=5,
        ),
        clock=lambda: 0.0,
    )
    controller.on_focus_start(goal={}, planned_minutes=25, now=0.0)

    distracted = controller.observe_activity(
        ocr_text="same text",
        sensor_available=True,
        idle_seconds=1.0,
        foreground_category="gaming",
        now=60.0,
    )
    recovered = controller.observe_activity(
        ocr_text="same text",
        sensor_available=True,
        now=61.0,
    )

    assert distracted["reminder_level"] == "distraction"
    assert recovered["inactivity_detected"] is False
    assert recovered["suggested_action"] == ""
    assert recovered["reminder_level"] == "active"
    assert "distraction_detected" not in recovered
