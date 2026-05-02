from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


class DummyLogger:
    def debug(self, message: Any, *args: Any, **kwargs: Any) -> None:
        pass

    def warning(self, message: Any, *args: Any, **kwargs: Any) -> None:
        pass

    def error(self, message: Any, *args: Any, **kwargs: Any) -> None:
        pass

    def info(self, message: Any, *args: Any, **kwargs: Any) -> None:
        pass


def make_service(**cfg: Any) -> STS2AutoplayService:
    service = STS2AutoplayService(DummyLogger(), lambda status: None)
    service._cfg = {
        "neko_commentary_enabled": True,
        "neko_commentary_probability": 1.0,
        "neko_commentary_min_interval_seconds": 4,
        "neko_critical_commentary_always": True,
        "neko_desperate_hp_threshold": 0.2,
        "neko_auto_low_hp_threshold": 0.3,
        "character_strategy": "defect",
        **cfg,
    }
    return service


def base_report(**overrides: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "step": 1,
        "screen": "combat",
        "floor": 3,
        "act": 1,
        "player_hp": 60,
        "max_hp": 80,
        "in_combat": True,
    }
    report.update(overrides)
    return report


def build_commentary(service: STS2AutoplayService, *, report: dict[str, Any] | None = None, tactical: dict[str, Any] | None = None, action: str = "play_card Strike") -> dict[str, Any]:
    return service._build_neko_live_commentary(
        report=report or base_report(),
        hand_names=["打击", "防御", "电击"],
        enemies_str="史莱姆 12/20 attack8",
        chosen_action=action,
        decision_reason="测试理由",
        tactical_brief=tactical or {"atk": 0, "need_block": 0, "lethal": False, "def": False},
    )


@pytest.mark.unit
def test_live_commentary_low_hp_uses_high_urgency_and_strategy_style() -> None:
    service = make_service(character_strategy="ironclad")

    commentary = build_commentary(service, report=base_report(player_hp=20, max_hp=80))

    assert commentary["should_speak"] is True
    assert commentary["scene"] == "low_hp"
    assert commentary["urgency"] == "high"
    assert commentary["mood"] == "关心"
    assert commentary["priority"] == 8
    assert commentary["interrupt"] is False
    assert commentary["action_hint"] == "打出关键牌"
    assert any(token in commentary["text"] for token in {"20/80", "血量", "少掉血"})
    assert any(token in commentary["text"] for token in {"稳住节奏", "血量", "少掉血"})


@pytest.mark.unit
def test_live_commentary_lethal_opportunity_speaks() -> None:
    service = make_service()

    commentary = build_commentary(service, tactical={"atk": 0, "need_block": 0, "lethal": True, "def": False})

    assert commentary["should_speak"] is True
    assert commentary["scene"] == "lethal"
    assert commentary["urgency"] == "high"
    assert "斩杀" in commentary["text"] or "收尾" in commentary["text"]


@pytest.mark.unit
def test_live_commentary_high_incoming_attack_speaks_with_high_priority() -> None:
    service = make_service()

    commentary = build_commentary(service, tactical={"atk": 24, "need_block": 12, "lethal": False, "def": True})

    assert commentary["should_speak"] is True
    assert commentary["scene"] == "incoming_attack"
    assert commentary["urgency"] == "high"
    assert commentary["priority"] == 7
    assert "24" in commentary["text"]
    assert "12" in commentary["text"]


@pytest.mark.unit
def test_live_commentary_normal_combat_uses_hand_and_action_hint() -> None:
    service = make_service(neko_commentary_min_interval_seconds=0)

    commentary = build_commentary(service)

    assert commentary["should_speak"] is True
    assert commentary["scene"] == "combat"
    assert commentary["urgency"] == "low"
    assert "打击" in commentary["text"]
    assert commentary["action_hint"] == "打出关键牌"


@pytest.mark.unit
def test_live_commentary_reward_screen() -> None:
    service = make_service(neko_commentary_min_interval_seconds=0)

    commentary = build_commentary(service, report=base_report(screen="card_reward", in_combat=False))

    assert commentary["should_speak"] is True
    assert commentary["scene"] == "reward"
    assert commentary["mood"] == "开心"
    assert "奖励" in commentary["text"] or "战利品" in commentary["text"]


@pytest.mark.unit
def test_live_commentary_throttles_repeated_low_urgency_scene() -> None:
    service = make_service(neko_commentary_probability=1.0, neko_commentary_min_interval_seconds=60)
    service._last_neko_commentary_at = time.time()
    service._last_neko_commentary_scene = "combat"

    commentary = build_commentary(service)

    assert commentary["should_speak"] is False
    assert commentary["scene"] == "combat"
    assert commentary["text"] == ""
    assert commentary["interrupt"] is False
    assert commentary["action_hint"] == "打出关键牌"


@pytest.mark.unit
def test_live_commentary_event_scenes_for_combat_end_key_relic_and_route_chosen() -> None:
    service = make_service(neko_commentary_min_interval_seconds=0)
    service._last_neko_commentary_scene = "combat"

    combat_end = build_commentary(service, report=base_report(screen="reward", in_combat=False, floor=5))
    assert combat_end["scene"] == "combat_end"
    assert "战斗结束" in combat_end["text"] or "这一场" in combat_end["text"]

    relic = build_commentary(service, report=base_report(screen="treasure", in_combat=False, floor=6), action="choose_treasure_relic")
    assert relic["scene"] == "key_relic"
    assert "遗物" in relic["text"]

    route = build_commentary(service, report=base_report(screen="map", in_combat=False, floor=7), action="choose_map_node")
    assert route["scene"] == "route_chosen"
    assert "路线" in route["text"]


@pytest.mark.unit
def test_neko_card_task_events_are_hud_only() -> None:
    notifications: list[dict[str, Any]] = []

    async def notifier(**kwargs: Any) -> None:
        notifications.append(kwargs)

    service = STS2AutoplayService(DummyLogger(), lambda status: None, frontend_notifier=notifier)

    asyncio.run(
        service._notify_neko_card_task_event(
            "completed",
            objective="帮我打一张牌",
            snapshot={"screen": "combat", "floor": 15, "act": 1, "hp": 20, "max_hp": 70},
            card_name="冲刺",
            reason="测试理由",
        )
    )

    assert len(notifications) == 1
    notification = notifications[0]
    assert notification["visibility"] == ["hud"]
    assert notification["ai_behavior"] == "blind"
    assert notification["message_type"] == "neko_observation"
    assert notification["metadata"]["event_type"] == "neko_card_task_completed"


def make_combat_snapshot() -> dict[str, Any]:
    return {
        "screen": "combat",
        "floor": 3,
        "act": 1,
        "in_combat": True,
        "raw_state": {
            "combat": {
                "turn": 1,
                "player": {"hp": 60, "max_hp": 80, "energy": 3},
                "hand": [{"name": "打击", "playable": True, "cost": 1}],
                "enemies": [{"name": "史莱姆", "hp": 12, "max_hp": 20, "intent": {"type": "attack", "value": 8}}],
            },
            "run": {"act": 1, "hp": 60, "max_hp": 80},
        },
    }


@pytest.mark.unit
def test_neko_step_reports_do_not_push_hud_by_default() -> None:
    notifications: list[dict[str, Any]] = []

    async def notifier(**kwargs: Any) -> None:
        notifications.append(kwargs)

    service = make_service(neko_commentary_min_interval_seconds=0)
    service._frontend_notifier = notifier
    service._snapshot = make_combat_snapshot()

    asyncio.run(
        service._push_neko_report(
            {
                "snapshot": service._snapshot,
                "reasoning": {"chosen_action": "end_turn", "reason": "测试理由"},
            }
        )
    )

    assert notifications == []


@pytest.mark.unit
def test_neko_step_reports_are_hud_only_when_enabled() -> None:
    notifications: list[dict[str, Any]] = []

    async def notifier(**kwargs: Any) -> None:
        notifications.append(kwargs)

    service = make_service(neko_commentary_min_interval_seconds=0, neko_report_hud_enabled=True)
    service._frontend_notifier = notifier
    service._snapshot = make_combat_snapshot()

    asyncio.run(
        service._push_neko_report(
            {
                "snapshot": service._snapshot,
                "reasoning": {"chosen_action": "end_turn", "reason": "测试理由"},
            }
        )
    )

    assert len(notifications) == 1
    notification = notifications[0]
    assert notification["visibility"] == ["hud"]
    assert notification["ai_behavior"] == "blind"
    assert notification["message_type"] == "neko_observation"
    assert notification["metadata"]["event_type"] == "neko_report"
    assert notification["metadata"]["observation_only"] is True
    assert notification["content"].startswith("尖塔观察#")


@pytest.mark.unit
def test_autonomous_low_hp_pause_notifies_main_program() -> None:
    notifications: list[dict[str, Any]] = []

    async def notifier(**kwargs: Any) -> None:
        notifications.append(kwargs)

    service = STS2AutoplayService(DummyLogger(), lambda status: None, frontend_notifier=notifier)
    service._cfg = {"neko_auto_low_hp_threshold": 0.3}
    service._autoplay_state = "running"
    service._snapshot = {
        "screen": "combat",
        "floor": 3,
        "act": 1,
        "raw_state": {
            "combat": {
                "turn": 2,
                "player": {"hp": 10, "max_hp": 50},
            }
        },
    }

    action = service._assess_neko_autonomous_action(prev_screen="combat")
    assert action == {"action": "pause", "reason": "low_hp", "hp_ratio": 0.2}

    asyncio.run(service._execute_autonomous_action(action))

    assert service._paused is True
    assert service._autoplay_state == "paused"
    assert len(notifications) == 1
    notification = notifications[0]
    assert notification["message_type"] == "proactive_notification"
    assert notification["priority"] == 9
    assert "血量过低" in notification["content"]
    assert "需要用户确认" in notification["content"]
    assert notification["visibility"] == []
    assert notification["ai_behavior"] == "respond"
    assert notification["metadata"]["event_type"] == "neko_autonomous_action"
    assert notification["metadata"]["reason"] == "low_hp"
    assert notification["metadata"]["reason_label"] == "血量过低"
    assert notification["metadata"]["requires_user_attention"] is True


@pytest.mark.unit
def test_terminal_task_events_notify_main_program() -> None:
    terminal_events = {"completed", "paused", "stopped", "error"}

    for event in terminal_events:
        notifications: list[dict[str, Any]] = []

        async def notifier(**kwargs: Any) -> None:
            notifications.append(kwargs)

        service = STS2AutoplayService(DummyLogger(), lambda status: None, frontend_notifier=notifier)
        service._snapshot = {"screen": "combat", "floor": 3, "act": 1}
        task = {"objective": "帮我打这一关", "stop_condition": "current_floor"}

        asyncio.run(service._notify_neko_task_event(event, task=task, reason="测试终止原因"))

        assert len(notifications) == 1
        notification = notifications[0]
        assert notification["message_type"] == "proactive_notification"
        assert notification["visibility"] == []
        assert notification["ai_behavior"] == "respond"
        assert notification["metadata"]["event_type"] == f"semi_auto_task_{event}"
        assert notification["metadata"]["requires_user_attention"] is (event in {"paused", "stopped", "error"})


@pytest.mark.unit
def test_started_task_event_remains_hud_only_observation() -> None:
    notifications: list[dict[str, Any]] = []

    async def notifier(**kwargs: Any) -> None:
        notifications.append(kwargs)

    service = STS2AutoplayService(DummyLogger(), lambda status: None, frontend_notifier=notifier)
    service._snapshot = {"screen": "combat", "floor": 3, "act": 1}

    asyncio.run(service._notify_neko_task_event("started", task={"objective": "帮我打这一关"}))

    assert len(notifications) == 1
    notification = notifications[0]
    assert notification["message_type"] == "neko_observation"
    assert notification["visibility"] == ["hud"]
    assert notification["ai_behavior"] == "blind"
    assert notification["metadata"]["event_type"] == "semi_auto_task_started"


@pytest.mark.unit
def test_autonomous_action_ignores_invalid_numeric_config() -> None:
    service = make_service(
        neko_auto_low_hp_threshold="bad",
        neko_auto_safe_hp_threshold="bad",
        action_interval_seconds="bad",
    )
    service._autoplay_state = "running"
    service._snapshot = {
        "screen": "event",
        "floor": 3,
        "act": 1,
        "raw_state": {
            "combat": {
                "player": {"hp": 40, "max_hp": 50},
                "enemies": [],
            }
        },
    }

    assert service._assess_neko_autonomous_action(prev_screen="combat") is None
