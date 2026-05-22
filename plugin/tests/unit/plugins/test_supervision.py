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
