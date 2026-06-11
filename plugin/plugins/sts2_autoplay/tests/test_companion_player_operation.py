from __future__ import annotations

import pytest

from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


class DummyLogger:
    def warning(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


@pytest.mark.unit
def test_observe_companion_player_operation_detects_combat_turn_advance() -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._cfg["companion_mode_enabled"] = True
    service._cfg["neko_commentary_enabled"] = True

    previous_snapshot = {
        "screen": "combat",
        "floor": 3,
        "act": 1,
        "in_combat": True,
        "classification": {"screen_class": "combat", "summary_kind": "combat"},
        "raw_state": {
            "turn": 1,
            "combat": {
                "turn": 1,
                "player": {"current_hp": 50, "max_hp": 80, "block": 0, "energy": 3},
                "hand": [{"name": "Strike"}, {"name": "Defend"}],
                "enemies": [{"name": "Louse", "current_hp": 20, "intent_damage": 6}],
            },
            "run": {"floor": 3, "act": 1, "gold": 99},
        },
        "available_actions": [{"type": "play_card", "raw": {"name": "play_card"}}],
    }
    current_snapshot = {
        "screen": "combat",
        "floor": 3,
        "act": 1,
        "in_combat": True,
        "classification": {"screen_class": "combat", "summary_kind": "combat"},
        "raw_state": {
            "turn": 2,
            "combat": {
                "turn": 2,
                "player": {"current_hp": 50, "max_hp": 80, "block": 0, "energy": 3},
                "hand": [{"name": "Zap"}, {"name": "Defend"}],
                "enemies": [{"name": "Louse", "current_hp": 20, "intent_damage": 4}],
            },
            "run": {"floor": 3, "act": 1, "gold": 99},
        },
        "available_actions": [{"type": "play_card", "raw": {"name": "play_card"}}],
    }

    observation = service._observe_companion_player_operation(previous_snapshot, current_snapshot)

    assert observation is not None
    assert observation["event_type"] == "combat_turn_advanced"
    assert observation["source"] == "state_observer"


@pytest.mark.unit
def test_observe_companion_player_operation_dedupes_identical_fingerprint() -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._cfg["companion_mode_enabled"] = True
    service._cfg["neko_commentary_enabled"] = True

    previous_snapshot = {
        "screen": "map",
        "floor": 3,
        "act": 1,
        "in_combat": False,
        "classification": {"screen_class": "run_navigation", "summary_kind": "map"},
        "raw_state": {"run": {"floor": 3, "act": 1, "gold": 99}, "map": {"nodes": []}},
        "available_actions": [{"type": "choose_map_node", "raw": {"name": "choose_map_node"}}],
    }
    current_snapshot = {
        "screen": "event",
        "floor": 3,
        "act": 1,
        "in_combat": False,
        "classification": {"screen_class": "run_navigation", "summary_kind": "event"},
        "raw_state": {"run": {"floor": 3, "act": 1, "gold": 99}, "event": {"event_id": "golden_idol", "name": "Golden Idol"}},
        "available_actions": [{"type": "choose_event_option", "raw": {"name": "choose_event_option"}}],
    }

    first = service._observe_companion_player_operation(previous_snapshot, current_snapshot)
    assert first is not None
    service._state.remember_companion_player_op(first)

    second = service._observe_companion_player_operation(previous_snapshot, current_snapshot)
    assert second is not None
    assert second["fingerprint"] == first["fingerprint"]


@pytest.mark.unit
def test_observe_companion_player_operation_skips_recent_plugin_action() -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._cfg["companion_mode_enabled"] = True
    service._cfg["neko_commentary_enabled"] = True
    service._state.last_action_at = 10_000.0
    service._state.latest_action_frame = {
        "before": {"screen": "combat", "turn": 1},
        "after": {"screen": "reward", "turn": 1},
    }

    previous_snapshot = {
        "screen": "combat",
        "floor": 7,
        "act": 1,
        "in_combat": True,
        "classification": {"screen_class": "combat", "summary_kind": "combat"},
        "raw_state": {
            "turn": 1,
            "combat": {
                "turn": 1,
                "player": {"current_hp": 30, "max_hp": 80, "block": 0, "energy": 1},
                "hand": [{"name": "Strike"}],
                "enemies": [{"name": "Louse", "current_hp": 4, "intent_damage": 6}],
            },
            "run": {"floor": 7, "act": 1, "gold": 20},
        },
        "available_actions": [{"type": "play_card", "raw": {"name": "play_card"}}],
    }
    current_snapshot = {
        "screen": "reward",
        "floor": 7,
        "act": 1,
        "in_combat": False,
        "classification": {"screen_class": "reward", "summary_kind": "reward"},
        "raw_state": {
            "run": {"floor": 7, "act": 1, "gold": 20, "current_hp": 30, "max_hp": 80},
            "reward": {"cards": [{"name": "Coolheaded", "index": 0}]},
        },
        "available_actions": [{"type": "choose_reward_card", "raw": {"name": "choose_reward_card"}}],
    }

    from plugin.plugins.sts2_autoplay import service as service_module

    original_time = service_module.time
    service_module.time = lambda: 10_001.0
    try:
        observation = service._observe_companion_player_operation(previous_snapshot, current_snapshot)
    finally:
        service_module.time = original_time

    assert observation is None


@pytest.mark.unit
def test_combat_turn_comments_once_per_turn() -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)

    allowed_first = service._companion_evaluator._should_comment(
        trigger="combat_turn",
        turn_key="3:combat:2",
        scene_key="3:combat",
        evaluation_key="3:combat:2",
        runtime_state=service._state,
        player_operation_observation={},
    )
    service._state.last_companion_turn_key = "3:combat:2"
    blocked_repeat = service._companion_evaluator._should_comment(
        trigger="combat_turn",
        turn_key="3:combat:2",
        scene_key="3:combat",
        evaluation_key="3:combat:2",
        runtime_state=service._state,
        player_operation_observation={},
    )
    allowed_next_turn = service._companion_evaluator._should_comment(
        trigger="combat_turn",
        turn_key="3:combat:3",
        scene_key="3:combat",
        evaluation_key="3:combat:3",
        runtime_state=service._state,
        player_operation_observation={},
    )

    assert allowed_first is True
    service._state.last_companion_turn_key = "3:combat:2"
    assert blocked_repeat is False
    assert allowed_next_turn is True


@pytest.mark.unit
def test_player_operation_allows_follow_up_in_same_turn_once() -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._state.last_companion_turn_key = "3:combat:2"

    allowed_follow_up = service._companion_evaluator._should_comment(
        trigger="player_operation",
        turn_key="3:combat:3",
        scene_key="3:combat",
        evaluation_key="obs-1",
        runtime_state=service._state,
        player_operation_observation={"fingerprint": "obs-1", "should_comment": True},
    )
    blocked_repeat = service._companion_evaluator._should_comment(
        trigger="player_operation",
        turn_key="3:combat:3",
        scene_key="3:combat",
        evaluation_key="obs-1",
        runtime_state=service._state,
        player_operation_observation={"fingerprint": "obs-1", "should_comment": True},
    )

    assert allowed_follow_up is False
    assert service._state.last_companion_player_op_fingerprint == ""
    assert blocked_repeat is False


@pytest.mark.unit
def test_player_operation_trigger_overrides_scene_entry() -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)

    trigger, turn_key, scene_key, evaluation_key = service._companion_evaluator._trigger_state(
        summary_kind="event",
        payload={"screen": "event", "floor": 3},
        runtime_state=service._state,
        player_operation_observation={"fingerprint": "obs-2", "scene_key": "1:3:EVENT:0"},
    )

    assert trigger == "player_operation"
    assert turn_key == ""
    assert scene_key == "0:3:event"
    assert evaluation_key == "obs-2"


@pytest.mark.unit
def test_scene_entry_comments_once_per_scene_node() -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)

    allowed_event = service._companion_evaluator._should_comment(
        trigger="scene_entry",
        turn_key="",
        scene_key="5:event",
        evaluation_key="5:event",
        runtime_state=service._state,
        player_operation_observation={},
    )
    service._state.last_companion_scene_key = "5:event"
    blocked_repeat = service._companion_evaluator._should_comment(
        trigger="scene_entry",
        turn_key="",
        scene_key="5:event",
        evaluation_key="5:event",
        runtime_state=service._state,
        player_operation_observation={},
    )
    allowed_shop = service._companion_evaluator._should_comment(
        trigger="scene_entry",
        turn_key="",
        scene_key="6:shop",
        evaluation_key="6:shop",
        runtime_state=service._state,
        player_operation_observation={},
    )

    assert allowed_event is True
    service._state.last_companion_scene_key = "5:event"
    assert blocked_repeat is False
    assert allowed_shop is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_operation_seeds_action_frame_before_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._state.snapshot = {"screen": "combat", "classification": {"summary_kind": "combat"}, "raw_state": {"turn": 2, "combat": {}, "run": {}}, "available_actions": []}

    class DummyClient:
        pass

    async def fake_execute(client, snapshot, operation):
        return {
            "status": "ok",
            "operation": {
                "action_type": "play_card",
                "kwargs": {"card_index": 1},
                "source": "heuristic",
                "reason": "test_reason",
            },
        }

    seeded: dict[str, object] = {}

    async def fake_refresh_state(*, trigger_sync: bool = False):
        seeded.update(service._state.latest_action_frame)
        service._state.snapshot = {
            "screen": "combat",
            "classification": {"summary_kind": "combat"},
            "raw_state": {"turn": 2, "combat": {}, "run": {}},
            "available_actions": [],
        }
        return {"status": "ok", "snapshot": service._state.snapshot}

    monkeypatch.setattr(service, "_client", DummyClient())
    monkeypatch.setattr(service._action_engine, "execute", fake_execute)
    monkeypatch.setattr(service, "refresh_state", fake_refresh_state)

    result = await service.execute_operation({"action_type": "play_card"})

    assert result["status"] == "ok"
    assert seeded["action_type"] == "play_card"
    assert seeded["action_kwargs"] == {"card_index": 1}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refresh_state_attaches_player_operation_observation_and_delivers_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    delivered: list[dict] = []
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **kwargs: delivered.append(kwargs))
    service._cfg["companion_mode_enabled"] = True
    service._cfg["neko_commentary_enabled"] = True
    service._cfg["neko_reporting_enabled"] = True
    service._state.snapshot = {
        "screen": "map",
        "floor": 3,
        "act": 1,
        "in_combat": False,
        "classification": {"screen_class": "run_navigation", "summary_kind": "map"},
        "summary_context": {"payload": {"current_hp": 50, "max_hp": 80}},
        "strategy_context": {"strategy_name": "defect"},
        "situation_summary": {"kind": "map", "text": "当前地图选择状态。"},
        "catgirl_sync": {},
        "raw_state": {"run": {"floor": 3, "act": 1, "gold": 99}, "map": {"nodes": []}},
        "available_actions": [{"type": "choose_map_node", "raw": {"name": "choose_map_node"}}],
    }

    async def fake_tick() -> dict[str, object]:
        snapshot = {
            "screen": "event",
            "floor": 3,
            "act": 1,
            "in_combat": False,
            "classification": {"screen_class": "run_navigation", "summary_kind": "event", "sync_priority": "high"},
            "summary_context": {"payload": {"current_hp": 50, "max_hp": 80, "event_name": "Golden Idol"}},
            "strategy_context": {"strategy_name": "defect"},
            "situation_summary": {"kind": "event", "text": "当前事件状态。"},
            "companion_evaluation": {"commentary": "这里先看代价。", "should_comment": True, "trigger": "scene_entry"},
            "catgirl_sync": {
                "should_sync": True,
                "fingerprint": "sync-1",
                "reason": "high_priority",
                "min_interval_seconds": 0.0,
                "force": True,
                "payload": {
                    "screen": "event",
                    "screen_class": "run_navigation",
                    "summary_kind": "event",
                    "sync_priority": "high",
                    "message": "[event] 这里先看代价。",
                    "ai_behavior": "respond",
                    "companion_evaluation": {"commentary": "这里先看代价。", "should_comment": True},
                },
            },
            "raw_state": {"run": {"floor": 3, "act": 1, "gold": 99}, "event": {"event_id": "golden_idol", "name": "Golden Idol"}},
            "available_actions": [{"type": "choose_event_option", "raw": {"name": "choose_event_option"}}],
        }
        return {"raw_state": snapshot["raw_state"], "raw_actions": {}, "snapshot": snapshot}

    monkeypatch.setattr(service._loop_runner, "tick", fake_tick)

    result = await service.refresh_state(trigger_sync=True)

    observation = result["snapshot"].get("player_operation_observation")
    assert observation is None
    assert delivered
