from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest


from plugin.plugins.sts2_autoplay.tests._isolated_loader import load_isolated_sts2_module


PROJECT_ROOT = Path(__file__).resolve().parents[4]


AutoplayLoopMixin = load_isolated_sts2_module("sts2_autoplay_loop_test_pkg", "autoplay_loop").AutoplayLoopMixin


class DummyLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.exceptions: list[str] = []

    def warning(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self.warnings.append(str(message))

    def exception(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self.exceptions.append(str(message))


class LoopService(AutoplayLoopMixin):
    start_autoplay = AutoplayLoopMixin.start_autoplay
    _is_semi_auto_task_complete = AutoplayLoopMixin._is_semi_auto_task_complete

    def __init__(self) -> None:
        self._autoplay_task = None
        self._semi_auto_task: dict[str, Any] | None = None
        self._paused = False
        self._autoplay_state = "idle"
        self._snapshot: dict[str, Any] = {}
        self._step_count = 0
        self._cfg: dict[str, Any] = {}
        self._shutdown = False
        self._last_error = ""
        self._last_task_report_step = 0
        self.reports: list[dict[str, Any]] = []
        self.completed = False
        self.autonomous_calls = 0
        self.status_emits = 0
        self.step_results: list[dict[str, Any]] = []
        self.started: list[dict[str, Any]] = []
        self.error_events: list[tuple[str, dict[str, Any] | None, str | None]] = []
        self.frontend_messages: list[dict[str, Any]] = []
        self.logger = DummyLogger()

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    def _normalized_screen_name(self, snapshot: dict[str, Any]) -> str:
        return str(snapshot.get("screen") or snapshot.get("normalized_screen") or "unknown")

    async def refresh_state(self) -> dict[str, Any]:
        self._snapshot = {"screen": "combat", "floor": 1, "act": 1, "in_combat": True}
        return {"status": "ok", "snapshot": self._snapshot}

    def _emit_status(self) -> None:
        self.status_emits += 1

    async def step_once(self) -> dict[str, Any]:
        if self.step_results:
            result = self.step_results.pop(0)
            if not self.step_results:
                self._shutdown = True
            return result
        self._shutdown = True
        return {"status": "idle"}

    async def _push_neko_report(self, result: dict[str, Any]) -> None:
        self.reports.append(result)

    async def _complete_semi_auto_task(self) -> None:
        self.completed = True

    async def _maybe_emit_frontend_message(self, **kwargs: Any) -> None:
        self.frontend_messages.append(kwargs)

    async def _notify_neko_task_event(self, event: str, task: dict[str, Any] | None = None, reason: str | None = None) -> None:
        self.error_events.append((event, task, reason))

    def _assess_neko_autonomous_action(self, prev_screen: str | None) -> dict[str, Any] | None:
        self.autonomous_calls += 1
        return None


def run(coro):
    return asyncio.run(coro)


@pytest.mark.unit
def test_start_autoplay_clears_stale_error_and_schedules_task() -> None:
    async def scenario() -> None:
        service = LoopService()
        service._last_error = "old failure"
        result = await service.start_autoplay(objective="再试试", stop_condition="current_floor")

        assert result["status"] == "running"
        assert result["task_started"] is True
        assert service._last_error == ""
        assert service._autoplay_state == "running"
        assert service._autoplay_task is not None
        assert not service._autoplay_task.done()
        service._shutdown = True
        await service.stop_autoplay()

    run(scenario())


@pytest.mark.unit
def test_start_autoplay_ignores_done_task_when_retrying() -> None:
    async def scenario() -> None:
        service = LoopService()
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        service._autoplay_task = done_task
        service._last_error = "old failure"

        result = await service.start_autoplay(objective="再试试", stop_condition="current_floor")

        assert result["task_started"] is True
        assert service._last_error == ""
        assert service._autoplay_task is not None
        assert service._autoplay_task is not done_task
        service._shutdown = True
        await service.stop_autoplay()

    run(scenario())


@pytest.mark.unit
def test_resume_autoplay_without_background_task_returns_idle_without_restarting() -> None:
    service = LoopService()
    service._semi_auto_task = {
        "objective": "打完这场战斗",
        "stop_condition": "current_combat",
    }

    result = run(service.resume_autoplay())

    assert result["status"] == "idle"
    assert result["executed"] is False
    assert service._autoplay_state == "idle"
    assert service.started == []


@pytest.mark.unit
def test_current_floor_task_started_in_combat_does_not_complete_on_reward_screen() -> None:
    service = LoopService()
    service._semi_auto_task = {
        "stop_condition": "current_floor",
        "start_screen": "combat",
        "start_floor": 3,
    }
    service._snapshot = {"screen": "reward", "floor": 3, "in_combat": False}

    assert service._is_semi_auto_task_complete() is False


@pytest.mark.unit
def test_current_combat_task_started_in_combat_completes_on_reward_screen() -> None:
    service = LoopService()
    service._semi_auto_task = {
        "stop_condition": "current_combat",
        "start_screen": "combat",
        "start_floor": 3,
        "has_entered_combat": True,
    }
    service._snapshot = {"screen": "reward", "floor": 3, "in_combat": False}

    assert service._is_semi_auto_task_complete() is True


@pytest.mark.unit
def test_autoplay_loop_exception_sets_error_and_clears_task_for_retry() -> None:
    async def scenario() -> None:
        service = LoopService()
        service._autoplay_state = "running"
        service._semi_auto_task = {"stop_condition": "manual", "start_floor": 1}
        service._snapshot = {"screen": "combat", "floor": 1}

        async def broken_step_once() -> dict[str, Any]:
            raise RuntimeError("orb helper missing")

        service.step_once = broken_step_once
        task = asyncio.create_task(service._autoplay_loop())
        service._autoplay_task = task
        await task

        assert service._autoplay_state == "error"
        assert service._last_error == "orb helper missing"
        assert service._autoplay_task is None
        assert service.status_emits >= 1
        assert service.logger.exceptions == ["尖塔半自动循环异常停止"]
        assert service.error_events == [("error", {"stop_condition": "manual", "start_floor": 1}, "orb helper missing")]
        assert service.frontend_messages[0]["event_type"] == "error"

    run(scenario())


@pytest.mark.unit
def test_autoplay_loop_error_step_then_idle_emits_report_and_assesses_autonomous() -> None:
    service = LoopService()
    service._autoplay_state = "running"
    service._cfg = {"neko_reporting_enabled": True, "neko_report_interval_steps": 1}
    service._semi_auto_task = {"stop_condition": "manual", "start_floor": 1}
    service._snapshot = {"screen": "combat", "floor": 1}
    service.step_results = [
        {"status": "error", "error": "bad action"},
        {"status": "idle"},
    ]

    run(service._autoplay_loop())

    assert service._last_error == "bad action"
    assert service._step_count == 1
    assert service.reports == [{"status": "idle"}]
    assert service.autonomous_calls == 1
    assert service.status_emits >= 1
