from __future__ import annotations

import time
from typing import Any, Callable

from .models import SupervisionConfig


class SupervisionController:
    def __init__(
        self,
        config: SupervisionConfig | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config or SupervisionConfig()
        self._clock = clock or time.time
        self._enabled = bool(self.config.enabled)
        self._focus_active = False
        self._last_reminder_at = 0.0
        self._last_activity_at = 0.0
        self._last_ocr_text = ""
        self._sensor_available = False
        self._reminder_level = "idle"

    def on_focus_start(
        self,
        *,
        goal: dict[str, Any] | None,
        planned_minutes: float,
        now: float | None = None,
    ) -> dict[str, Any]:
        current = self._clock() if now is None else float(now)
        self._focus_active = True
        self._last_reminder_at = current
        self._last_activity_at = current
        self._reminder_level = "start"
        return {
            "enabled": self._enabled,
            "reminder_level": self._reminder_level,
            "message": "focus_started",
            "goal": dict(goal or {}),
            "planned_minutes": planned_minutes,
        }

    def on_focus_end(self, *, now: float | None = None) -> dict[str, Any]:
        self._focus_active = False
        self._reminder_level = "end"
        return self.status(now=now)

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        self._enabled = bool(enabled)
        if not self._enabled:
            self._reminder_level = "disabled"
        return self.status()

    def due_reminder(self, *, now: float | None = None) -> dict[str, Any]:
        current = self._clock() if now is None else float(now)
        due = (
            self._enabled
            and self._focus_active
            and current - self._last_reminder_at
            >= self.config.remind_interval_minutes * 60
        )
        if due:
            self._last_reminder_at = current
            self._reminder_level = "low_frequency"
        return {"due": bool(due), **self.status(now=current)}

    def observe_activity(
        self,
        *,
        ocr_text: str,
        sensor_available: bool,
        now: float | None = None,
    ) -> dict[str, Any]:
        current = self._clock() if now is None else float(now)
        self._sensor_available = bool(sensor_available)
        if not self._sensor_available:
            return {
                **self.status(now=current),
                "inactivity_detected": False,
                "suggested_action": "",
            }
        text = str(ocr_text or "")
        if text != self._last_ocr_text:
            self._last_ocr_text = text
            self._last_activity_at = current
            return {
                **self.status(now=current),
                "inactivity_detected": False,
                "suggested_action": "",
            }
        inactive = (
            self._enabled
            and self._focus_active
            and current - self._last_activity_at
            >= self.config.inactivity_timeout_minutes * 60
        )
        if inactive:
            self._reminder_level = "inactivity"
        return {
            **self.status(now=current),
            "inactivity_detected": bool(inactive),
            "suggested_action": "pause_or_switch" if inactive else "",
        }

    def status(self, *, now: float | None = None) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "focus_active": self._focus_active,
            "sensor_available": self._sensor_available,
            "reminder_level": self._reminder_level,
            "last_activity_at": self._last_activity_at,
            "last_reminder_at": self._last_reminder_at,
            "config": self.config.to_dict(),
        }
