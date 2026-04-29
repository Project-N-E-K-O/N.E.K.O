from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
STS2_PACKAGE_DIR = Path(__file__).resolve().parents[1]


def load_autoplay_loop_mixin():
    for name, path in {
        "plugin": PROJECT_ROOT / "plugin",
        "plugin.plugins": PROJECT_ROOT / "plugin" / "plugins",
        "plugin.plugins.sts2_autoplay": STS2_PACKAGE_DIR,
    }.items():
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(path)]
            sys.modules[name] = module
    return importlib.import_module("plugin.plugins.sts2_autoplay.autoplay_loop").AutoplayLoopMixin


AutoplayLoopMixin = load_autoplay_loop_mixin()


class LoopService(AutoplayLoopMixin):
    def __init__(self) -> None:
        self._autoplay_task = None
        self._semi_auto_task: dict[str, Any] | None = None
        self._paused = False
        self._autoplay_state = "idle"
        self._snapshot: dict[str, Any] = {}
        self._step_count = 0
        self._cfg: dict[str, Any] = {}
        self.started: list[dict[str, Any]] = []

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    def _normalized_screen_name(self, snapshot: dict[str, Any]) -> str:
        return str(snapshot.get("screen") or snapshot.get("normalized_screen") or "unknown")

    async def start_autoplay(self, objective: str | None = None, stop_condition: str = "current_floor") -> dict[str, Any]:
        self.started.append({"objective": objective, "stop_condition": stop_condition})
        return {"status": "running", "executed": True}

    def _emit_status(self) -> None:
        pass


def run(coro):
    return asyncio.run(coro)


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
