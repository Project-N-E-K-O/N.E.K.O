from __future__ import annotations

from pathlib import Path

from plugin.plugins.study_companion.checkin_manager import CheckinManager
from plugin.plugins.study_companion.study_habit_store import StudyHabitStore
from plugin.plugins.study_companion.store import StudyStore


class _Logger:
    def warning(self, *args, **kwargs):
        return None


def _study_store(tmp_path: Path) -> StudyStore:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    return store


def test_habit_store_creates_goals_and_cascades_focus_sessions(tmp_path: Path) -> None:
    store = _study_store(tmp_path)
    try:
        habits = StudyHabitStore(store)
        goal = habits.create_goal(
            date="2026-05-22",
            target_type="subject",
            subject="math",
            target_amount=2,
            unit="pomodoro",
        )
        focus = habits.create_focus_session(
            goal_id=goal["id"],
            mode="focus",
            planned_minutes=25,
            started_at="2026-05-22T23:50:00+08:00",
        )
        habits.finish_focus_session(
            focus["id"],
            ended_at="2026-05-23T00:15:00+08:00",
            actual_minutes=25,
            status="completed",
        )

        assert habits.list_goals(date="2026-05-22")[0]["progress_amount"] == 0
        assert habits.list_focus_sessions(date="2026-05-22")[0]["actual_minutes"] == 25
        assert habits.list_focus_sessions(date="2026-05-23") == []

        habits.delete_goal(goal["id"])

        assert habits.list_goals(date="2026-05-22") == []
        assert habits.list_focus_sessions(date="2026-05-22") == []
        assert habits.delete_goal("missing-goal") is False
    finally:
        store.close()


def test_checkin_manager_tracks_streaks_makeups_and_session_derived_progress(
    tmp_path: Path,
) -> None:
    store = _study_store(tmp_path)
    try:
        habits = StudyHabitStore(store)
        manager = CheckinManager(habits, makeup_window_days=3)
        goal = manager.create_goal(
            date="2026-05-20",
            target_type="subject",
            subject="math",
            target_amount=30,
            unit="minute",
        )

        manager.apply_session_progress(
            date="2026-05-20",
            duration_minutes=20,
            question_count=3,
            subject="math",
        )
        manager.manual_checkin(date="2026-05-21", today="2026-05-22", note="复习错题")
        manager.manual_checkin(date="2026-05-22", today="2026-05-22")
        manager.apply_session_progress(
            date="2026-05-20",
            duration_minutes=10,
            question_count=0,
            subject="math",
        )

        updated = habits.get_goal(goal["id"])
        status = manager.checkin_status(date="2026-05-22", today="2026-05-22")
        summary = manager.daily_summary(date="2026-05-20")

        assert updated is not None
        assert updated["progress_amount"] == 30
        assert updated["status"] == "completed"
        assert status["checked_in"] is True
        assert status["streak_days"] == 3
        assert status["makeup_window_days"] == 3
        assert summary["total_focus_minutes"] == 30
        assert summary["completed_goal_count"] == 1
        assert summary["weak_points"] == []
    finally:
        store.close()


def test_habit_data_stays_out_of_public_knowledge_export(tmp_path: Path) -> None:
    store = _study_store(tmp_path)
    try:
        habits = StudyHabitStore(store)
        habits.create_goal(
            date="2026-05-22",
            target_type="custom",
            subject="private",
            target_amount=1,
            unit="task",
            target_id="personal-plan",
        )

        exported = store.export_json()

        assert "daily_goals" not in exported
        assert "checkins" not in exported
        assert "focus_sessions" not in exported
        assert "personal-plan" not in str(exported)
    finally:
        store.close()
