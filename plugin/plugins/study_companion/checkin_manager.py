from __future__ import annotations

from datetime import date as date_type, datetime, timedelta
from typing import Any

from .study_habit_store import StudyHabitStore


def _parse_date(value: str) -> date_type:
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _date_text(value: date_type) -> str:
    return value.isoformat()


class CheckinManager:
    def __init__(self, habits: StudyHabitStore, *, makeup_window_days: int = 3) -> None:
        self._habits = habits
        self.makeup_window_days = max(0, min(7, int(makeup_window_days)))

    def create_goal(
        self,
        *,
        date: str,
        target_type: str,
        subject: str,
        target_amount: float,
        unit: str,
        target_id: str = "",
    ) -> dict[str, Any]:
        return self._habits.create_goal(
            date=date,
            target_type=target_type,
            subject=subject,
            target_amount=target_amount,
            unit=unit,
            target_id=target_id,
        )

    def update_goal(self, goal_id: str, **updates: Any) -> dict[str, Any]:
        return self._habits.update_goal(goal_id, **updates)

    def delete_goal(self, goal_id: str) -> None:
        self._habits.delete_goal(goal_id)

    def manual_checkin(
        self, *, date: str, today: str, note: str = ""
    ) -> dict[str, Any]:
        checkin_date = _parse_date(date)
        today_date = _parse_date(today)
        delta_days = (today_date - checkin_date).days
        if delta_days < 0:
            raise ValueError("cannot check in for a future date")
        if delta_days > self.makeup_window_days:
            raise ValueError("checkin date is outside makeup window")
        status = "checked_in" if delta_days == 0 else "makeup"
        return self._habits.record_checkin(
            date=date, status=status, source="manual", note=note
        )

    def apply_session_progress(
        self,
        *,
        date: str,
        duration_minutes: float,
        question_count: int,
        subject: str = "",
    ) -> dict[str, Any]:
        duration = max(0.0, float(duration_minutes or 0.0))
        questions = max(0, int(question_count or 0))
        matched_goals = []
        for goal in self._habits.list_goals(date=date):
            if subject and goal.get("subject") and str(goal["subject"]) != str(subject):
                continue
            unit = str(goal.get("unit") or "")
            delta = 0.0
            if unit in {"minute", "minutes"}:
                delta = duration
            elif unit in {"question", "questions"}:
                delta = float(questions)
            elif unit in {"task", "pomodoro"} and duration > 0:
                delta = 1.0
            if delta <= 0:
                continue
            matched_goals.append(
                self._habits.update_goal(str(goal["id"]), progress_delta=delta)
            )
        if duration > 0:
            focus = self._habits.create_focus_session(
                goal_id=str(matched_goals[0]["id"]) if matched_goals else "",
                mode="focus",
                planned_minutes=duration,
                started_at=f"{date[:10]}T00:00:00",
            )
            self._habits.finish_focus_session(
                str(focus["id"]),
                ended_at=f"{date[:10]}T00:00:00",
                actual_minutes=duration,
                status="completed",
            )
        checkin = self._habits.record_checkin(
            date=date, status="checked_in", source="session_derived"
        )
        return {"goals": matched_goals, "checkin": checkin}

    def checkin_status(self, *, date: str, today: str | None = None) -> dict[str, Any]:
        target = _parse_date(date)
        today_date = _parse_date(today or date)
        checked_dates = self._habits.checked_dates(through_date=_date_text(target))
        streak = 0
        cursor = target
        while _date_text(cursor) in checked_dates:
            streak += 1
            cursor -= timedelta(days=1)
        checkins = self._habits.list_checkins(date=date)
        return {
            "date": date[:10],
            "checked_in": any(
                item["status"] in {"checked_in", "makeup"} for item in checkins
            ),
            "checkins": checkins,
            "streak_days": streak,
            "makeup_window_days": self.makeup_window_days,
            "makeup_available_dates": [
                _date_text(today_date - timedelta(days=offset))
                for offset in range(1, self.makeup_window_days + 1)
            ],
        }

    def daily_summary(self, *, date: str) -> dict[str, Any]:
        goals = self._habits.list_goals(date=date)
        completed = [goal for goal in goals if goal.get("status") == "completed"]
        incomplete = [goal for goal in goals if goal.get("status") == "active"]
        total_focus = self._habits.focus_minutes_for_date(date)
        return {
            "date": date[:10],
            "total_focus_minutes": total_focus,
            "goals": goals,
            "completed_goals": completed,
            "incomplete_goals": incomplete,
            "completed_goal_count": len(completed),
            "incomplete_goal_count": len(incomplete),
            "weak_points": [],
            "phase7_memory": {"enabled": False, "items": []},
        }
