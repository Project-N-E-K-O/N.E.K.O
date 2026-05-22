from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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


def _habit_store(tmp_path: Path) -> StudyHabitStore:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    return StudyHabitStore(store)


def test_pomodoro_timer_completes_focus_then_short_break_without_counting_break_minutes(tmp_path: Path) -> None:
    habits = _habit_store(tmp_path)
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

    started = timer.start(goal_id="", focus_minutes=1)
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


def test_pomodoro_timer_uses_long_break_interval_and_supports_cancel(tmp_path: Path) -> None:
    habits = _habit_store(tmp_path)
    clock = _Clock()
    timer = PomodoroTimer(
        habits,
        config=PomodoroConfig(focus_minutes=1, short_break_minutes=1, long_break_minutes=3, long_break_interval=2),
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
