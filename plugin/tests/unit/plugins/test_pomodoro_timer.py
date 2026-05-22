from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from plugin.plugins.study_companion.pomodoro_timer import PomodoroConfig, PomodoroTimer
from plugin.plugins.study_companion.study_habit_store import StudyHabitStore
from plugin.plugins.study_companion.store import StudyStore


class _Logger:
    def warning(self, *args, **kwargs):
        return None


@dataclass
class _Clock:
    now: float = 1_000.0

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FailingHabitStore:
    def __init__(self, *, fail_on: str) -> None:
        self.fail_on = fail_on
        self.focus_session = {
            "id": "focus-1",
            "date": "1970-01-01",
            "status": "active",
        }

    def create_focus_session(self, **_: Any) -> dict[str, Any]:
        if self.fail_on == "create_focus_session":
            raise RuntimeError("create failed")
        return dict(self.focus_session)

    def finish_focus_session(self, *_: Any, **__: Any) -> dict[str, Any]:
        if self.fail_on == "finish_focus_session":
            raise RuntimeError("finish failed")
        self.focus_session = {**self.focus_session, "status": "completed"}
        return dict(self.focus_session)

    def record_checkin(self, **_: Any) -> dict[str, Any]:
        if self.fail_on == "record_checkin":
            raise RuntimeError("checkin failed")
        return {"id": "checkin-1"}

    def get_goal(self, goal_id: str) -> dict[str, Any] | None:
        return None


class _CheckinFailingHabitStore(_FailingHabitStore):
    def __init__(self, *, failures_remaining: int | None = None) -> None:
        super().__init__(fail_on="record_checkin")
        self.failures_remaining = failures_remaining
        self.progress_delta = 0.0

    def get_goal(self, goal_id: str) -> dict[str, Any] | None:
        return {"id": goal_id, "unit": "pomodoro"}

    def update_goal(self, goal_id: str, *, progress_delta: float = 0.0) -> dict[str, Any]:
        del goal_id
        self.progress_delta += float(progress_delta or 0.0)
        return {"progress_amount": self.progress_delta}

    def record_checkin(self, **_: Any) -> dict[str, Any]:
        if self.failures_remaining is None:
            raise RuntimeError("checkin failed")
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise RuntimeError("checkin failed")
        return {"id": "checkin-1"}


def _habit_store(tmp_path: Path) -> tuple[StudyStore, StudyHabitStore]:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    return store, StudyHabitStore(store)


def test_pomodoro_timer_completes_focus_then_short_break_without_counting_break_minutes(
    tmp_path: Path,
) -> None:
    store, habits = _habit_store(tmp_path)
    try:
        clock = _Clock()
        timer = PomodoroTimer(
            habits,
            config=PomodoroConfig(
                focus_minutes=1,
                short_break_minutes=1,
                long_break_minutes=2,
                long_break_interval=2,
            ),
            clock=clock.time,
        )
        goal = habits.create_goal(
            date="1970-01-01",
            target_type="subject",
            subject="math",
            target_amount=1,
            unit="pomodoro",
        )

        started = timer.start(goal_id=goal["id"], focus_minutes=1)
        assert started["state"] == "focusing"
        assert started["remaining_seconds"] == 60

        timer.pause()
        clock.advance(30)
        paused = timer.status()
        assert paused["state"] == "paused"
        assert paused["remaining_seconds"] == 60
        timer.resume()

        clock.advance(60)
        transitioned = timer.tick()

        assert transitioned["state"] == "short_break"
        assert transitioned["session_count"] == 1
        assert transitioned["current_focus_session"]["actual_minutes"] == 1

        clock.advance(60)
        completed = timer.tick()

        assert completed["state"] == "completed"
        assert completed["remaining_seconds"] == 0
        assert habits.focus_minutes_for_date(started["date"]) == 1
        updated_goal = habits.get_goal(goal["id"])
        assert updated_goal is not None
        assert updated_goal["progress_amount"] == 1
        assert updated_goal["status"] == "completed"
        assert (
            habits.list_checkins(date=started["date"])[0]["source"] == "session_derived"
        )
    finally:
        store.close()


def test_pomodoro_timer_uses_long_break_interval_and_supports_cancel(
    tmp_path: Path,
) -> None:
    store, habits = _habit_store(tmp_path)
    try:
        clock = _Clock()
        timer = PomodoroTimer(
            habits,
            config=PomodoroConfig(
                focus_minutes=1,
                short_break_minutes=1,
                long_break_minutes=3,
                long_break_interval=2,
            ),
            clock=clock.time,
        )

        timer.start(focus_minutes=1)
        clock.advance(60)
        assert timer.tick()["state"] == "short_break"
        assert timer.skip_break()["state"] == "completed"

        timer.start(focus_minutes=1)
        clock.advance(60)
        long_break = timer.tick()
        assert long_break["state"] == "long_break"
        assert long_break["remaining_seconds"] == 180

        cancelled = timer.stop()

        assert cancelled["state"] == "cancelled"
        assert cancelled["current_focus_session"]["status"] == "completed"
    finally:
        store.close()


def test_pomodoro_stop_completes_expired_focus_before_cancelling(
    tmp_path: Path,
) -> None:
    store, habits = _habit_store(tmp_path)
    try:
        clock = _Clock()
        timer = PomodoroTimer(
            habits,
            config=PomodoroConfig(focus_minutes=1, short_break_minutes=1),
            clock=clock.time,
        )
        goal = habits.create_goal(
            date="1970-01-01",
            target_type="subject",
            subject="math",
            target_amount=1,
            unit="pomodoro",
        )

        started = timer.start(goal_id=goal["id"], focus_minutes=1)
        clock.advance(60)
        stopped = timer.stop()

        assert stopped["state"] == "short_break"
        assert stopped["session_count"] == 1
        assert stopped["current_focus_session"]["status"] == "completed"
        assert habits.get_goal(goal["id"])["progress_amount"] == 1
        assert habits.list_checkins(date=started["date"])[0]["source"] == "session_derived"
    finally:
        store.close()


def test_pomodoro_stop_is_noop_when_timer_is_not_active(tmp_path: Path) -> None:
    store, habits = _habit_store(tmp_path)
    try:
        clock = _Clock()
        timer = PomodoroTimer(
            habits,
            config=PomodoroConfig(focus_minutes=1, short_break_minutes=1),
            clock=clock.time,
        )

        assert timer.stop()["state"] == "idle"

        timer.start(focus_minutes=1)
        clock.advance(60)
        assert timer.tick()["state"] == "short_break"
        assert timer.skip_break()["state"] == "completed"

        stopped = timer.stop()

        assert stopped["state"] == "completed"
        assert stopped["current_focus_session"]["status"] == "completed"
    finally:
        store.close()


def test_pomodoro_timer_respects_disabled_session_derived_checkins(
    tmp_path: Path,
) -> None:
    store, habits = _habit_store(tmp_path)
    try:
        clock = _Clock()
        timer = PomodoroTimer(
            habits,
            config=PomodoroConfig(focus_minutes=1, short_break_minutes=1),
            clock=clock.time,
            auto_derive_from_session=False,
        )

        started = timer.start(focus_minutes=1)
        clock.advance(60)
        assert timer.tick()["state"] == "short_break"

        assert habits.focus_minutes_for_date(started["date"]) == 1
        assert habits.list_checkins(date=started["date"]) == []
    finally:
        store.close()


def test_pomodoro_timer_uses_configured_timezone_for_session_derived_checkins(
    tmp_path: Path,
) -> None:
    store, habits = _habit_store(tmp_path)
    try:
        timestamp = datetime(2024, 1, 1, 0, 30, tzinfo=timezone.utc).timestamp()
        clock = _Clock(timestamp)
        timer = PomodoroTimer(
            habits,
            config=PomodoroConfig(focus_minutes=1, short_break_minutes=1),
            clock=clock.time,
            checkin_timezone="America/Los_Angeles",
        )

        started = timer.start(focus_minutes=1)
        clock.advance(60)
        timer.tick()

        assert started["date"] == "2023-12-31"
        assert habits.list_checkins(date="2023-12-31")
        assert habits.list_checkins(date="2024-01-01") == []
    finally:
        store.close()


def test_pomodoro_start_does_not_mutate_state_when_initial_persistence_fails() -> None:
    timer = PomodoroTimer(
        _FailingHabitStore(fail_on="create_focus_session"),  # type: ignore[arg-type]
        config=PomodoroConfig(focus_minutes=1),
        clock=lambda: 1_000.0,
    )

    try:
        timer.start(focus_minutes=1)
    except RuntimeError as exc:
        assert str(exc) == "create failed"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("start should propagate persistence failures")

    status = timer.status()
    assert status["state"] == "idle"
    assert status["remaining_seconds"] == 0
    assert status["current_focus_session"] == {}


def test_pomodoro_completion_does_not_duplicate_progress_when_checkin_retries() -> None:
    habits = _CheckinFailingHabitStore(failures_remaining=1)
    clock = _Clock()
    timer = PomodoroTimer(
        habits,  # type: ignore[arg-type]
        config=PomodoroConfig(focus_minutes=1, short_break_minutes=1),
        clock=clock.time,
    )
    timer.start(goal_id="goal-1", focus_minutes=1)
    clock.advance(60)

    try:
        timer.tick()
    except RuntimeError as exc:
        assert str(exc) == "checkin failed"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("tick should propagate checkin persistence failures")

    assert timer.status()["state"] == "focusing"
    assert timer.status()["session_count"] == 0
    assert habits.progress_delta == 1.0

    timer.tick()

    assert timer.status()["state"] == "short_break"
    assert timer.status()["session_count"] == 1
    assert habits.progress_delta == 1.0


def test_pomodoro_completion_stays_retryable_when_persistence_fails() -> None:
    clock = _Clock()
    timer = PomodoroTimer(
        _FailingHabitStore(fail_on="finish_focus_session"),  # type: ignore[arg-type]
        config=PomodoroConfig(focus_minutes=1, short_break_minutes=1),
        clock=clock.time,
    )
    timer.start(focus_minutes=1)
    clock.advance(60)

    try:
        timer.tick()
    except RuntimeError as exc:
        assert str(exc) == "finish failed"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("tick should propagate persistence failures")

    status = timer.status()
    assert status["state"] == "focusing"
    assert status["session_count"] == 0
    assert status["remaining_seconds"] == 0
