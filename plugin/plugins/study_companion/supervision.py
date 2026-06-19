from __future__ import annotations

import time
from typing import Any, Callable

from .models import SupervisionConfig


_DISTRACTION_FOREGROUND_CATEGORIES = frozenset({"gaming", "entertainment"})


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
        idle_seconds: float | None = None,
        foreground_category: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        current = self._clock() if now is None else float(now)
        self._sensor_available = bool(sensor_available)
        try:
            idle_value = None if idle_seconds is None else max(0.0, float(idle_seconds))
        except (TypeError, ValueError, OverflowError):
            idle_value = None
        foreground = str(foreground_category or "").strip().lower()

        def _result(
            *,
            inactivity_detected: bool,
            suggested_action: str,
            distraction_detected: bool = False,
        ) -> dict[str, Any]:
            result = {
                **self.status(now=current),
                "inactivity_detected": bool(inactivity_detected),
                "suggested_action": suggested_action,
            }
            if idle_value is not None:
                result["idle_seconds"] = idle_value
            if foreground:
                result["foreground_category"] = foreground
                result["distraction_detected"] = bool(distraction_detected)
            return result

        if not self._sensor_available:
            return _result(inactivity_detected=False, suggested_action="")
        timeout_seconds = self.config.inactivity_timeout_minutes * 60
        away_seconds = self.config.idle_away_seconds
        active_from_idle = idle_value is not None and idle_value < timeout_seconds
        distraction_detected = (
            self._enabled
            and self._focus_active
            and foreground in _DISTRACTION_FOREGROUND_CATEGORIES
        )
        away_detected = (
            self._enabled
            and self._focus_active
            and idle_value is not None
            and idle_value >= away_seconds
        )
        text = str(ocr_text or "")
        if text != self._last_ocr_text or active_from_idle:
            self._last_ocr_text = text
            self._last_activity_at = current
            if self._reminder_level != "active":
                self._reminder_level = "active"
        if away_detected:
            self._reminder_level = "away"
            return _result(
                inactivity_detected=True,
                suggested_action="pause_or_switch",
            )
        if distraction_detected:
            self._reminder_level = "distraction"
            return _result(
                inactivity_detected=False,
                suggested_action="return_to_focus",
                distraction_detected=True,
            )
        inactive = (
            self._enabled
            and self._focus_active
            and current - self._last_activity_at >= timeout_seconds
        )
        if inactive:
            self._reminder_level = "inactivity"
        elif self._reminder_level == "distraction":
            self._reminder_level = "active"
        return _result(
            inactivity_detected=bool(inactive),
            suggested_action="pause_or_switch" if inactive else "",
        )

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
