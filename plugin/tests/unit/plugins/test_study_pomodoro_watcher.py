from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace

import pytest

from plugin.plugins.study_companion import entry_communication_pomodoro_events
from plugin.plugins.study_companion import StudyCompanionPlugin
from plugin.sdk.plugin import Ok


class _Logger:
    def warning(self, *_args, **_kwargs) -> None:
        pass


class _Supervision:
    def __init__(self) -> None:
        self.focus_end_count = 0

    def on_focus_end(self) -> None:
        self.focus_end_count += 1


class _EventBus:
    def __init__(self) -> None:
        self.events = []

    async def emit(self, event) -> None:
        self.events.append(event)


class _DeadlineTimer:
    def __init__(self) -> None:
        self.state = "focusing"
        self.tick_count = 0
        self.pause_count = 0

    def status(self) -> dict[str, object]:
        return {
            "state": self.state,
            "remaining_seconds": 0,
            "current_focus_session": {"id": "focus-1"},
        }

    def tick(self) -> dict[str, object]:
        self.tick_count += 1
        if self.state == "focusing":
            self.state = "short_break"
        elif self.state == "short_break":
            self.state = "completed"
        return self.status()

    def pause(self) -> dict[str, object]:
        self.pause_count += 1
        self.state = "paused"
        return self.status()


def _plugin(timer, *, event_bus=None) -> StudyCompanionPlugin:
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    plugin.logger = _Logger()
    plugin.ctx = SimpleNamespace(_current_lanlan="fallback")
    plugin._habit_store = object()
    plugin._checkin_manager = object()
    plugin._pomodoro_timer = timer
    plugin._supervision = _Supervision()
    plugin._event_bus = event_bus
    plugin._pomodoro_lock = asyncio.Lock()
    plugin._pomodoro_wakeup = asyncio.Event()
    plugin._pomodoro_watcher_task = None
    plugin._pomodoro_session_id = "focus-1"
    plugin._pomodoro_target_lanlan = "yui-at-start"
    return plugin


def test_command_loop_hook_recreates_loop_bound_pomodoro_primitives() -> None:
    plugin = _plugin(_DeadlineTimer(), event_bus=_EventBus())
    plugin._pomodoro_timer.state = "paused"
    startup_wakeup = plugin._pomodoro_wakeup
    startup_lock = plugin._pomodoro_lock

    async def bind_startup_wakeup() -> None:
        waiter = asyncio.create_task(startup_wakeup.wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with suppress(asyncio.CancelledError):
            await waiter

    asyncio.run(bind_startup_wakeup())

    async def start_command_loop() -> None:
        await plugin._on_command_loop_start()
        await asyncio.sleep(0)
        assert plugin._pomodoro_wakeup is not startup_wakeup
        assert plugin._pomodoro_lock is not startup_lock
        assert plugin._pomodoro_watcher_task is not None
        assert not plugin._pomodoro_watcher_task.done()
        await plugin._cancel_pomodoro_watcher()

    asyncio.run(start_command_loop())


@pytest.mark.asyncio
async def test_watcher_emits_focus_and_break_deadlines_once_without_status_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        entry_communication_pomodoro_events,
        "_POMODORO_DEADLINE_EPSILON_SECONDS",
        0.01,
    )
    timer = _DeadlineTimer()
    bus = _EventBus()
    plugin = _plugin(timer, event_bus=bus)

    plugin._start_pomodoro_watcher()
    await asyncio.sleep(0.05)
    await plugin._cancel_pomodoro_watcher()

    assert timer.tick_count == 2
    assert [event.name for event in bus.events] == [
        "pomodoro_focus_completed",
        "pomodoro_break_completed",
    ]
    assert bus.events[0].payload == {
        "session_id": "focus-1",
        "break_type": "short_break",
        "target_lanlan": "yui-at-start",
    }
    assert bus.events[1].payload == {
        "session_id": "focus-1",
        "break_type": "short_break",
        "target_lanlan": "yui-at-start",
    }
    assert plugin._pomodoro_watcher_task is None
    assert plugin._supervision.focus_end_count == 1


@pytest.mark.asyncio
async def test_paused_timer_is_not_ticked_by_watcher() -> None:
    timer = _DeadlineTimer()
    timer.state = "paused"
    plugin = _plugin(timer, event_bus=_EventBus())

    plugin._start_pomodoro_watcher()
    await asyncio.sleep(0)
    await plugin._cancel_pomodoro_watcher()

    assert timer.tick_count == 0
    assert plugin._event_bus.events == []
    assert plugin._pomodoro_watcher_task is None


@pytest.mark.asyncio
async def test_wakeup_restarts_watcher_after_failed_tick() -> None:
    timer = _DeadlineTimer()
    timer.state = "paused"
    plugin = _plugin(timer, event_bus=_EventBus())

    async def _failed_tick() -> None:
        raise RuntimeError("transient tick failure")

    failed_task = asyncio.create_task(_failed_tick())
    plugin._pomodoro_watcher_task = failed_task
    failed_task.add_done_callback(plugin._on_pomodoro_watcher_done)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert plugin._pomodoro_watcher_task is None
    plugin._wake_pomodoro_watcher()
    restarted_task = plugin._pomodoro_watcher_task

    assert restarted_task is not None
    assert not restarted_task.done()
    await plugin._cancel_pomodoro_watcher()


@pytest.mark.asyncio
async def test_deadline_transition_runs_when_communication_is_disabled() -> None:
    timer = _DeadlineTimer()
    plugin = _plugin(timer, event_bus=None)

    async with plugin._pomodoro_runtime_lock():
        status, transition = await plugin._tick_pomodoro_timer_locked()
    delivered = await plugin._emit_pomodoro_transition(transition)

    assert status["state"] == "short_break"
    assert timer.tick_count == 1
    assert delivered is False


@pytest.mark.asyncio
async def test_pause_completes_expired_focus_before_pausing() -> None:
    timer = _DeadlineTimer()
    bus = _EventBus()
    plugin = _plugin(timer, event_bus=bus)

    result = await plugin.study_pomodoro_pause()

    assert isinstance(result, Ok)
    assert result.value["state"] == "short_break"
    assert timer.tick_count == 1
    assert timer.pause_count == 0
    assert [event.name for event in bus.events] == ["pomodoro_focus_completed"]
    assert plugin._supervision.focus_end_count == 1


class _ManualTimer:
    def __init__(self, state: str) -> None:
        self.state = state

    def status(self) -> dict[str, object]:
        return {"state": self.state}

    def stop(self) -> dict[str, object]:
        self.state = "cancelled"
        return {"state": self.state, "current_focus_session": {"id": "focus-1"}}

    def skip_break(self) -> dict[str, object]:
        self.state = "completed"
        return {"state": self.state, "remaining_seconds": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["stop", "skip_break"])
async def test_manual_completion_does_not_emit_deadline_event(operation: str) -> None:
    timer = _ManualTimer("focusing" if operation == "stop" else "short_break")
    bus = _EventBus()
    plugin = _plugin(timer, event_bus=bus)

    if operation == "stop":
        result = await plugin.study_pomodoro_stop()
    else:
        result = await plugin.study_pomodoro_skip_break()

    assert isinstance(result, Ok)
    assert bus.events == []
    assert plugin._pomodoro_session_id == ""
    assert plugin._pomodoro_target_lanlan is None


def test_target_role_prefers_entry_context_then_current_role() -> None:
    plugin = _plugin(_DeadlineTimer())

    assert (
        plugin._resolve_pomodoro_target_lanlan({"_ctx": {"lanlan_name": "entry-yui"}})
        == "entry-yui"
    )
    assert plugin._resolve_pomodoro_target_lanlan({"_ctx": {}}) is None
    assert plugin._resolve_pomodoro_target_lanlan({}) == "fallback"
    plugin.ctx._current_lanlan = ""
    assert plugin._resolve_pomodoro_target_lanlan({}) is None
