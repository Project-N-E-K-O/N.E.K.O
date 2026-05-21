from __future__ import annotations

import pytest

from plugin.plugins.sts2_autoplay import STS2AutoplayPlugin
from plugin.plugins.sts2_autoplay.catgirl_bridge import STS2CatgirlBridge
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


class NotificationPlugin(STS2AutoplayPlugin):
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def push_message(self, **kwargs):
        self.messages.append(kwargs)


class DummyLogger:
    def warning(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


@pytest.mark.unit
def test_push_frontend_notification_uses_v2_fields_when_visibility_given() -> None:
    plugin = NotificationPlugin()

    plugin._push_frontend_notification(
        content="sync message",
        description="desc",
        metadata={"kind": "catgirl_sync"},
        visibility=[],
        ai_behavior="read",
        message_type="sts2_catgirl_sync",
    )

    assert len(plugin.messages) == 1
    payload = plugin.messages[0]
    assert payload["visibility"] == []
    assert payload["ai_behavior"] == "read"
    assert payload["parts"][0]["text"] == "sync message"
    assert payload["metadata"]["message_type"] == "sts2_catgirl_sync"






@pytest.mark.unit
def test_push_frontend_notification_truncates_host_reply_to_30_chars() -> None:
    plugin = NotificationPlugin()

    plugin._push_frontend_notification(
        content="1234567890123456789012345678901234567890",
        description="desc",
        metadata={"kind": "catgirl_sync"},
        visibility=[],
        ai_behavior="respond",
        message_type="sts2_catgirl_sync",
    )

    payload = plugin.messages[0]
    assert payload["parts"][0]["text"] == "123456789012345678901234567..."
    assert len(payload["parts"][0]["text"]) == 30


@pytest.mark.unit
def test_catgirl_sync_uses_fingerprint_and_read_delivery_by_default() -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._state.last_sync_fingerprint = "same"
    service._state.last_sync_at = 100.0
    service._state.last_sync_screen = "combat"
    service._state.last_sync_summary_kind = "combat"

    from plugin.plugins.sts2_autoplay import service as service_module

    original_time = service_module.time
    service_module.time = lambda: 105.0
    try:
        allowed = service._should_deliver_sync(
            {
                "fingerprint": "same",
                "min_interval_seconds": 10.0,
                "force": False,
                "payload": {"screen": "combat", "summary_kind": "combat"},
            }
        )
    finally:
        service_module.time = original_time



@pytest.mark.unit
def test_catgirl_sync_prefers_single_primary_message() -> None:
    bridge = STS2CatgirlBridge()

    sync = bridge.build_sync_packet(
        {
            "screen": "combat",
            "floor": 3,
            "act": 1,
            "classification": {"screen_class": "combat", "sync_priority": "high"},
            "situation_summary": {"kind": "combat", "text": "当前战斗状态"},
            "companion_evaluation": {
                "commentary": "当前局势偏危险；建议优先防御或找减伤线。",
                "primary_message": "建议优先防御或找减伤线。",
                "strategy_name": "defect",
            },
        },
        standby=False,
    )

    assert len(sync["payload"]["message"]) <= 20
    assert "49/80" not in sync["payload"]["message"]
    assert sync["payload"]["companion_evaluation"]["strategy_name"] == "defect"


@pytest.mark.unit
def test_catgirl_sync_payload_is_lightweight_structured_package() -> None:
    bridge = STS2CatgirlBridge()

    sync = bridge.build_sync_packet(
        {
            "screen": "combat",
            "floor": 3,
            "act": 1,
            "classification": {"screen_class": "combat", "sync_priority": "high"},
            "strategy_context": {
                "strategy_name": "defect",
                "scene_name": "combat",
                "event_override": None,
                "enemy_override": {"value": {"instruction": "优先集火"}},
            },
            "situation_summary": {
                "kind": "combat",
                "text": "当前战斗状态",
                "payload": {
                    "player": {"current_hp": 47, "max_hp": 80, "block": 0},
                    "enemies": [{"name": "海洋混混", "intent": "SEA_KICK_MOVE"}],
                    "playable_card_summaries": [{"name": "耸肩无视", "energy_cost": 1, "star_cost": 0, "costs_x": False, "star_costs_x": False, "effect": "获得格挡并过牌"}],
                },
            },
            "companion_evaluation": {
                "primary_message": "先补甲，再找高收益出牌。",
                "strategy_name": "defect",
                "trigger": "player_operation",
            },
            "player_operation_observation": {"event_type": "player_card_or_action_committed", "summary": "玩家刚打出一张攻击牌。"},
        },
        standby=False,
    )

    payload = sync["payload"]
    assert len(payload["message"]) <= 20
    assert "49/80" not in payload["message"]
    assert payload["strategy"]["name"] == "defect"
    assert payload["strategy"]["enemy_override"] is True
    assert payload["player"]["current_hp"] == 47
    assert payload["enemies"][0]["name"] == "海洋混混"
    assert payload["cards"][0]["cost"] == "1费"
    assert payload["cards"][0]["effect"] == "获得格挡并过牌"
    assert payload["player_operation"] == {}




@pytest.mark.unit
def test_catgirl_sync_truncates_long_host_reply_to_20_chars() -> None:
    bridge = STS2CatgirlBridge()

    sync = bridge.build_sync_packet(
        {
            "screen": "combat",
            "floor": 3,
            "act": 1,
            "classification": {"screen_class": "combat", "sync_priority": "high"},
            "situation_summary": {"kind": "combat", "text": "当前战斗状态"},
            "companion_evaluation": {
                "primary_message": "1234567890123456789012345678901234567890",
                "strategy_name": "defect",
            },
        },
        standby=False,
    )

    assert sync["payload"]["message"] == "12345678901234567..."
    assert len(sync["payload"]["message"]) == 20
    bridge = STS2CatgirlBridge()

    sync = bridge.build_sync_packet(
        {
            "screen": "event",
            "floor": 3,
            "act": 1,
            "classification": {"screen_class": "run_navigation", "sync_priority": "medium"},
            "situation_summary": {"kind": "event", "text": "当前事件状态"},
            "companion_evaluation": {
                "commentary": "这里先观察代价。",
                "strategy_name": "defect",
                "should_comment": False,
            },
        },
        standby=False,
    )

    assert sync["should_comment"] is False




@pytest.mark.unit
def test_catgirl_sync_player_operation_forces_read_behavior() -> None:
    bridge = STS2CatgirlBridge()

    sync = bridge.build_sync_packet(
        {
            "screen": "combat",
            "floor": 3,
            "act": 1,
            "classification": {"screen_class": "combat", "sync_priority": "medium"},
            "situation_summary": {"kind": "combat", "text": "当前战斗状态"},
            "companion_evaluation": {
                "primary_message": "玩家刚完成了一步操作。",
                "trigger": "player_operation",
                "should_comment": False,
            },
            "player_operation_observation": {"event_type": "player_card_or_action_committed", "summary": "玩家打出了一张攻击牌。"},
        },
        standby=False,
    )

    assert sync["payload"]["ai_behavior"] == "read"


@pytest.mark.unit
def test_service_disabling_companion_mode_clears_runtime_companion_state() -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._state.latest_player_operation_observation = {"event_type": "choice_committed"}
    service._state.last_companion_scene_key = "3:event"
    service._state.last_companion_turn_key = "3:combat:2"
    service._state.last_companion_evaluation_key = "3:event"
    service._state.last_companion_combat_comment_key = "3:combat:2"
    service._state.last_companion_player_op_fingerprint = "obs-1"
    service._state.latest_sync_packet = {"message": "test"}

    result = service.set_companion_mode(False)

    assert result["enabled"] is False
    assert service._state.latest_player_operation_observation == {}
    assert service._state.last_companion_scene_key == ""
    assert service._state.last_companion_turn_key == ""
    assert service._state.last_companion_evaluation_key == ""
    assert service._state.last_companion_combat_comment_key == ""
    assert service._state.last_companion_player_op_fingerprint == ""
    assert service._state.latest_sync_packet == {}




@pytest.mark.unit
def test_service_probability_gate_skips_non_forced_companion_push(monkeypatch: pytest.MonkeyPatch) -> None:
    messages: list[dict] = []
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **kwargs: messages.append(kwargs))
    service._cfg["companion_mode_enabled"] = True
    service._cfg["neko_commentary_enabled"] = True
    service._cfg["companion_push_probability"] = 0.0
    service._cfg["autoplay_push_probability"] = 1.0

    snapshot = {
        "catgirl_sync": {
            "should_sync": True,
            "fingerprint": "sync-probability-1",
            "reason": "screen_class:event",
            "min_interval_seconds": 0.0,
            "force": False,
            "payload": {
                "screen": "event",
                "summary_kind": "event",
                "trigger": "scene_entry",
                "message": "这里先观察一下代价。",
                "ai_behavior": "respond",
                "companion_evaluation": {"should_comment": True},
            },
        }
    }

    monkeypatch.setattr("plugin.plugins.sts2_autoplay.service.random", lambda: 0.5)

    service._deliver_catgirl_sync(snapshot)

    assert messages == []


@pytest.mark.unit
def test_service_throttles_duplicate_companion_sync_pushes() -> None:
    messages: list[dict] = []
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **kwargs: messages.append(kwargs))
    service._cfg["companion_mode_enabled"] = True
    service._cfg["neko_commentary_enabled"] = True
    service._cfg["companion_push_probability"] = 1.0
    service._cfg["autoplay_push_probability"] = 1.0
    service._state.step_count = 3

    snapshot = {
        "catgirl_sync": {
            "should_sync": True,
            "fingerprint": "sync-1",
            "reason": "screen_class:combat",
            "min_interval_seconds": 0.0,
            "force": True,
            "payload": {
                "screen": "combat",
                "summary_kind": "combat",
                "trigger": "player_operation",
                "message": "建议优先防御或找减伤线。",
                "ai_behavior": "respond",
                "companion_evaluation": {"should_comment": True},
            },
        }
    }

    service._deliver_catgirl_sync(snapshot)
    service._state.step_count = 4
    service._deliver_catgirl_sync(snapshot)

    assert len(messages) == 1
