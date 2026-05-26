from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import PomodoroConfig, _range_or_default
from .study_habit_store import StudyHabitStore


def _datetime_from_timestamp(value: float, timezone_name: str = "local") -> datetime:
    zone_name = str(timezone_name or "local").strip()
    if zone_name and zone_name.lower() != "local":
        try:
            return datetime.fromtimestamp(float(value), ZoneInfo(zone_name))
        except ZoneInfoNotFoundError:
            pass
    return datetime.fromtimestamp(float(value), timezone.utc).astimezone()


def _iso_from_timestamp(value: float, timezone_name: str = "local") -> str:
    return _datetime_from_timestamp(value, timezone_name).isoformat()


def _date_from_timestamp(value: float, timezone_name: str = "local") -> str:
    return _datetime_from_timestamp(value, timezone_name).date().isoformat()


class PomodoroTimer:
    def __init__(
        self,
        habits: StudyHabitStore,
        *,
        config: PomodoroConfig | None = None,
        clock: Callable[[], float] | None = None,
        auto_derive_from_session: bool = True,
        checkin_timezone: str = "local",
    ) -> None:
        self._habits = habits
        self.config = config or PomodoroConfig()
        self._clock = clock or time.time
        self.auto_derive_from_session = bool(auto_derive_from_session)
        self.checkin_timezone = str(checkin_timezone or "local").strip() or "local"
        self._state = "idle"
        self._mode = "focus"
        self._deadline = 0.0
        self._remaining_on_pause = 0.0
        self._started_at = 0.0
        self._active_started_at = 0.0
        self._active_elapsed_seconds = 0.0
        self._planned_minutes = 0.0
        self._goal_id = ""
        self._focus_session: dict[str, Any] | None = None
        self._session_count = 0
        self._pause_count = 0
        self._interrupt_count = 0
        self._progress_applied_session_id = ""

    def start(
        self, *, goal_id: str = "", focus_minutes: int | None = None
    ) -> dict[str, Any]:
        if self._state in {"focusing", "paused", "short_break", "long_break"}:
            return self.status()
        minutes = self.config.focus_minutes
        if self.config.allow_custom_duration and focus_minutes is not None:
            minutes = _range_or_default(
                focus_minutes, 1, 120, self.config.focus_minutes
            )
        now = self._clock()
        goal_key = str(goal_id or "")
        focus_session = self._habits.create_focus_session(
            goal_id=goal_key,
            mode="focus",
            planned_minutes=minutes,
            started_at=_iso_from_timestamp(now, self.checkin_timezone),
        )
        self._state = "focusing"
        self._mode = "focus"
        self._goal_id = goal_key
        self._started_at = now
        self._active_started_at = now
        self._active_elapsed_seconds = 0.0
        self._planned_minutes = float(minutes)
        self._deadline = now + minutes * 60
        self._remaining_on_pause = 0.0
        self._pause_count = 0
        self._interrupt_count = 0
        self._focus_session = focus_session
        self._progress_applied_session_id = ""
        return self.status()

    def pause(self) -> dict[str, Any]:
        if self._state != "focusing":
            return self.status()
        now = self._clock()
        self._active_elapsed_seconds += max(0.0, now - self._active_started_at)
        self._remaining_on_pause = max(0.0, self._deadline - now)
        self._state = "paused"
        self._pause_count += 1
        return self.status()

    def resume(self) -> dict[str, Any]:
        if self._state != "paused":
            return self.status()
        now = self._clock()
        self._active_started_at = now
        self._deadline = now + max(0.0, self._remaining_on_pause)
        self._state = "focusing"
        return self.status()

    def stop(self) -> dict[str, Any]:
        if self._state not in {"focusing", "paused", "short_break", "long_break"}:
            return self.status()
        if self._state == "focusing" and self._clock() >= self._deadline:
            return self._complete_focus()
        if self._state in {"focusing", "paused"} and self._focus_session is not None:
            now = self._clock()
            elapsed_seconds = self._active_elapsed_seconds
            if self._state == "focusing":
                elapsed_seconds += max(0.0, now - self._active_started_at)
            elapsed = max(
                0.0,
                min(self._planned_minutes, elapsed_seconds / 60.0),
            )
            self._focus_session = self._habits.finish_focus_session(
                str(self._focus_session["id"]),
                ended_at=_iso_from_timestamp(now, self.checkin_timezone),
                actual_minutes=elapsed,
                status="cancelled",
                pause_count=self._pause_count,
                interrupt_count=self._interrupt_count,
            )
        self._state = "cancelled"
        self._deadline = self._clock()
        return self.status()

    def skip_break(self) -> dict[str, Any]:
        if (
            self._state not in {"short_break", "long_break"}
            or not self.config.allow_skip_break
        ):
            return self.status()
        self._state = "completed"
        self._deadline = self._clock()
        return self.status()

    def tick(self) -> dict[str, Any]:
        if self._state == "focusing" and self._clock() >= self._deadline:
            return self._complete_focus()
        if (
            self._state in {"short_break", "long_break"}
            and self._clock() >= self._deadline
        ):
            self._state = "completed"
            self._deadline = self._clock()
        return self.status()

    def _complete_focus(self) -> dict[str, Any]:
        now = self._clock()
        completed_focus: dict[str, Any] | None = None
        if self._focus_session is not None:
            completed_focus = self._habits.finish_focus_session(
                str(self._focus_session["id"]),
                ended_at=_iso_from_timestamp(now, self.checkin_timezone),
                actual_minutes=self._planned_minutes,
                status="completed",
                pause_count=self._pause_count,
                interrupt_count=self._interrupt_count,
            )
            self._apply_completed_focus_progress(completed_focus)
            self._focus_session = completed_focus
        self._session_count += 1
        if self._session_count % self.config.long_break_interval == 0:
            self._state = "long_break"
            self._mode = "long_break"
            self._deadline = now + self.config.long_break_minutes * 60
        else:
            self._state = "short_break"
            self._mode = "short_break"
            self._deadline = now + self.config.short_break_minutes * 60
        return self.status()

    def _apply_completed_focus_progress(self, focus: dict[str, Any]) -> None:
        focus_date = str(
            focus.get("date") or _date_from_timestamp(self._started_at, self.checkin_timezone)
        )[:10]
        session_id = str(focus.get("id") or "")
        if self._goal_id:
            goal = self._habits.get_goal(self._goal_id)
            if goal is not None:
                unit = str(goal.get("unit") or "").strip().lower()
                delta = 0.0
                if unit in {"minute", "minutes"}:
                    delta = self._planned_minutes
                elif unit in {"pomodoro", "task"}:
                    delta = 1.0
                if delta > 0 and (
                    not session_id or session_id != self._progress_applied_session_id
                ):
                    self._habits.update_goal(self._goal_id, progress_delta=delta)
                    self._progress_applied_session_id = session_id
        if self.auto_derive_from_session:
            self._habits.record_checkin(
                date=focus_date, status="checked_in", source="session_derived"
            )

    def status(self) -> dict[str, Any]:
        now = self._clock()
        if self._state == "paused":
            remaining = self._remaining_on_pause
        elif self._state in {"focusing", "short_break", "long_break"}:
            remaining = max(0.0, self._deadline - now)
        else:
            remaining = 0.0
        return {
            "state": self._state,
            "mode": self._mode,
            "remaining_seconds": int(round(remaining)),
            "session_count": self._session_count,
            "goal_id": self._goal_id,
            "date": _date_from_timestamp(self._started_at or now, self.checkin_timezone),
            "pause_count": self._pause_count,
            "interrupt_count": self._interrupt_count,
            "current_focus_session": dict(self._focus_session or {}),
            "config": self.config.to_dict(),
        }
