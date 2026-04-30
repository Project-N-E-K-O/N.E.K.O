from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest


from plugin.plugins.sts2_autoplay.tests._isolated_loader import load_isolated_sts2_module


PROJECT_ROOT = Path(__file__).resolve().parents[4]


STS2AutoplayService = load_isolated_sts2_module("sts2_neko_command_test_pkg", "service").STS2AutoplayService


class DummyLogger:
    def warning(self, message: Any, *args: Any, **kwargs: Any) -> None:
        pass

    def error(self, message: Any, *args: Any, **kwargs: Any) -> None:
        pass

    def info(self, message: Any, *args: Any, **kwargs: Any) -> None:
        pass


class CommandService(STS2AutoplayService):
    def __init__(self) -> None:
        super().__init__(DummyLogger(), lambda payload: None)
        self.called: list[tuple[str, Any]] = []

    async def health_check(self) -> dict[str, Any]:
        self.called.append(("health_check", None))
        return {"status": "connected", "message": "已连接"}

    async def refresh_state(self) -> dict[str, Any]:
        self.called.append(("refresh_state", None))
        return {"status": "ok", "message": "已刷新"}

    async def get_snapshot(self) -> dict[str, Any]:
        self.called.append(("get_snapshot", None))
        return {"status": "ok", "message": "当前局面"}

    async def recommend_one_card_by_neko(self, objective: str | None = None) -> dict[str, Any]:
        self.called.append(("recommend_one_card_by_neko", objective))
        return {"status": "recommended", "summary": "建议打一张牌", "executed": False}

    async def play_one_card_by_neko(self, objective: str | None = None) -> dict[str, Any]:
        self.called.append(("play_one_card_by_neko", objective))
        return {"status": "ok", "summary": "已打出一张牌", "executed": True}

    async def step_once(self) -> dict[str, Any]:
        self.called.append(("step_once", None))
        return {"status": "ok", "summary": "已执行一步"}

    async def start_autoplay(self, objective: str | None = None, stop_condition: str = "current_floor") -> dict[str, Any]:
        self.called.append(("start_autoplay", {"objective": objective, "stop_condition": stop_condition}))
        if objective == "自动打一下但是已经在运行":
            return {"status": "running", "message": "尖塔半自动任务已在运行", "executed": False}
        self._autoplay_state = "running"
        return {"status": "running", "message": "尖塔半自动任务已启动", "executed": True}

    async def pause_autoplay(self, reason: str = "用户请求暂停") -> dict[str, Any]:
        self.called.append(("pause_autoplay", reason))
        self._autoplay_state = "paused"
        return {"status": "paused", "message": "尖塔已暂停", "reason": reason}

    async def resume_autoplay(self) -> dict[str, Any]:
        self.called.append(("resume_autoplay", None))
        self._autoplay_state = "running"
        return {"status": "running", "message": "尖塔已恢复"}

    async def stop_autoplay(self, reason: str = "用户请求停止") -> dict[str, Any]:
        self.called.append(("stop_autoplay", reason))
        self._autoplay_state = "idle"
        return {"status": "idle", "message": "尖塔已停止", "reason": reason}

    async def send_neko_guidance(self, guidance: dict[str, Any]) -> dict[str, Any]:
        self.called.append(("send_neko_guidance", guidance))
        return {"status": "ok", "message": "猫娘指导已入队"}


    async def answer_autoplay_question_by_neko(self, question: str) -> dict[str, Any]:
        self.called.append(("answer_autoplay_question_by_neko", question))
        return {"status": "answered", "summary": "我在根据局面解释打法", "executed": False, "observation_only": True}


@pytest.fixture()
def service() -> CommandService:
    return CommandService()


def run(coro):
    return asyncio.run(coro)


@pytest.mark.unit
def test_neko_command_advice_does_not_execute(service: CommandService) -> None:
    result = run(service.neko_command("这回合怎么打"))
    assert result["intent"] == "advice"
    assert result["executed"] is False
    assert service.called == [("recommend_one_card_by_neko", "这回合怎么打")]


@pytest.mark.unit
def test_neko_command_play_one_card_requires_explicit_wording(service: CommandService) -> None:
    result = run(service.neko_command("帮我打一张牌"))
    assert result["intent"] == "play_one_card"
    assert result["executed"] is True
    assert service.called == [("play_one_card_by_neko", "帮我打一张牌")]


@pytest.mark.unit
def test_neko_command_step_once(service: CommandService) -> None:
    result = run(service.neko_command("执行一步"))
    assert result["intent"] == "step_once"
    assert result["executed"] is True
    assert service.called == [("step_once", None)]


@pytest.mark.unit
def test_neko_command_autoplay_current_floor_by_default(service: CommandService) -> None:
    result = run(service.neko_command("帮我打这一关"))
    assert result["intent"] == "start_autoplay"
    assert result["executed"] is True
    assert service.called == [("start_autoplay", {"objective": "帮我打这一关", "stop_condition": "current_floor"})]


@pytest.mark.unit
def test_neko_command_autoplay_current_combat(service: CommandService) -> None:
    result = run(service.neko_command("打完这场战斗"))
    assert result["intent"] == "start_autoplay"
    assert result["executed"] is True
    assert service.called == [("start_autoplay", {"objective": "打完这场战斗", "stop_condition": "current_combat"})]


@pytest.mark.unit
def test_neko_command_manual_autoplay_requires_confirmation(service: CommandService) -> None:
    result = run(service.neko_command("一直托管"))
    assert result["intent"] == "manual_autoplay_confirmation"
    assert result["needs_confirmation"] is True
    assert result["executed"] is False
    assert service.called == []


@pytest.mark.unit
def test_neko_command_start_autoplay_respects_result_executed_flag(service: CommandService) -> None:
    result = run(service.neko_command("自动打一下但是已经在运行"))
    assert result["intent"] == "start_autoplay"
    assert result["executed"] is False
    assert service.called == [("start_autoplay", {"objective": "自动打一下但是已经在运行", "stop_condition": "current_floor"})]


@pytest.mark.unit
def test_neko_command_guidance_when_autoplay_running(service: CommandService) -> None:
    service._autoplay_state = "running"
    result = run(service.neko_command("先防一下，别贪"))
    assert result["intent"] == "guidance"
    assert result["executed"] is False
    assert service.called[0][0] == "send_neko_guidance"
    assert service.called[0][1]["content"] == "先防一下，别贪"


@pytest.mark.unit
def test_neko_command_guidance_degrades_to_advice_when_not_running(service: CommandService) -> None:
    result = run(service.neko_command("先防一下，别贪"))
    assert result["intent"] == "unknown"
    assert result["needs_confirmation"] is True
    assert service.called == []


@pytest.mark.unit
def test_neko_command_pause_has_priority(service: CommandService) -> None:
    service._autoplay_state = "running"
    result = run(service.neko_command("暂停一下，先防"))
    assert result["intent"] == "pause"
    assert result["executed"] is False
    assert service.called == [("pause_autoplay", "用户请求暂停")]


@pytest.mark.unit
def test_neko_command_autoplay_question_while_running_answers_from_game_context(service: CommandService) -> None:
    service._autoplay_state = "running"
    result = run(service.neko_command("这打的也不怎么样啊"))
    assert result["intent"] == "autoplay_question"
    assert result["executed"] is False
    assert result["observation_only"] is True
    assert service.called == [("answer_autoplay_question_by_neko", "这打的也不怎么样啊")]


@pytest.mark.unit
def test_neko_review_observation_mentions_visible_fact_and_card_praise(service: CommandService) -> None:
    snapshots = [
        {
            "hp": 42,
            "max_hp": 80,
            "block": 5,
            "energy": 1,
            "hand_names": ["打击", "防御"],
            "enemies": [{"name": "史莱姆", "hp": 12, "max_hp": 40, "intent_value": 8}],
        },
        {
            "hp": 45,
            "max_hp": 80,
            "block": 0,
            "energy": 3,
            "hand_names": ["打击", "防御"],
            "enemies": [{"name": "史莱姆", "hp": 24, "max_hp": 40, "intent_value": 0}],
        },
    ]

    observation = service._build_neko_review_observation(snapshots)

    assert observation["status"] == "ok"
    assert "猫娘看到" in observation["message"]
    assert "玩家血量约42/80" in observation["message"]
    assert "当前有5点格挡" in observation["message"]
    assert "【打击】" in observation["message"]
    assert "打得不错" in observation["message"]
    assert observation["signals"]["visible_fact"]
    assert observation["signals"]["card_praise"]


@pytest.mark.unit
def test_neko_command_unknown_is_conservative(service: CommandService) -> None:
    result = run(service.neko_command("你看着办"))
    assert result["intent"] == "unknown"
    assert result["needs_confirmation"] is True
    assert result["executed"] is False
    assert service.called == []
