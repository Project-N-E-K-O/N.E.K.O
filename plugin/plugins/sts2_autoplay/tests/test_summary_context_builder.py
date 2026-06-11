from __future__ import annotations

from plugin.plugins.sts2_autoplay.summary_context_builder import STS2SummaryContextBuilder
from plugin.plugins.sts2_autoplay.state_machine import STS2StateMachine


class RuntimeStateStub:
    def __init__(self) -> None:
        self.latest_continuous_delta = {"source": "continuous_snapshot", "text": "玩家血量 -6"}
        self.latest_action_frame = {
            "delta": {"source": "action_paired", "text": "护盾 +8"},
            "before": {"screen": "combat"},
            "after": {"screen": "combat"},
        }
        self.latest_player_operation_observation = {}


def test_build_snapshot_summary_extracts_combat_fields() -> None:
    builder = STS2SummaryContextBuilder()
    state_machine = STS2StateMachine()
    snapshot = {
        "screen": "combat",
        "floor": 12,
        "act": 2,
        "in_combat": True,
        "available_actions": [{"type": "play_card", "raw": {"name": "play_card"}}],
        "raw_state": {
            "turn": 3,
            "combat": {
                "player": {"current_hp": 44, "max_hp": 80, "block": 8, "energy": 1},
                "hand": [{"name": "Defend"}, {"name": "Dualcast"}],
                "enemies": [{"name": "Slime", "current_hp": 18, "intent_damage": 10}],
            },
            "run": {"gold": 77},
        },
    }
    snapshot["classification"] = state_machine.classify(snapshot)

    summary = builder.build_snapshot_summary(snapshot)

    assert summary["screen"] == "combat"
    assert summary["summary_kind"] == "combat"
    assert summary["floor"] == 12
    assert summary["turn"] == 3
    assert summary["player"]["current_hp"] == 44
    assert summary["player"]["block"] == 8
    assert summary["hand"]["count"] == 2
    assert summary["enemies"]["total_hp"] == 18
    assert summary["available_actions"] == ["play_card"]


def test_build_context_includes_runtime_deltas() -> None:
    builder = STS2SummaryContextBuilder()
    runtime_state = RuntimeStateStub()
    snapshot = {
        "screen": "event",
        "classification": {"summary_kind": "event"},
        "raw_state": {
            "run": {"floor": 5, "current_hp": 60, "max_hp": 80, "gold": 99},
            "event": {"event_id": "golden_shrine", "name": "Golden Shrine"},
        },
        "available_actions": [{"type": "choose_event_option", "raw": {"name": "choose_event_option"}}],
    }

    context = builder.build(snapshot, runtime_state=runtime_state)

    assert context["summary_kind"] == "event"
    assert context["continuous_delta"]["text"] == "玩家血量 -6"
    assert context["action_frame"]["delta"]["text"] == "护盾 +8"
    assert context["payload"]["event_name"] == "Golden Shrine"


def test_build_map_context_exposes_node_types_and_indices() -> None:
    builder = STS2SummaryContextBuilder()
    snapshot = {
        "screen": "map",
        "classification": {"summary_kind": "map"},
        "raw_state": {
            "run": {"floor": 7, "current_hp": 50, "max_hp": 80, "gold": 120},
            "map": {
                "current_node": {"id": "m2"},
                "nodes": [
                    {"index": 3, "type": "elite", "is_available": True},
                    {"index": 4, "type": "rest", "is_available": False},
                    {"index": 5, "type": "shop", "is_available": True},
                ],
                "future_nodes": [{"type": "treasure"}],
            },
        },
        "available_actions": [{"type": "choose_map_node", "raw": {"name": "choose_map_node"}}],
    }

    context = builder.build(snapshot)

    assert context["payload"]["travelable_node_types"] == ["elite", "shop"]
    assert context["payload"]["travelable_node_indices"] == [3, 5]


def test_build_combat_context_exposes_card_cost_summaries() -> None:
    builder = STS2SummaryContextBuilder()
    snapshot = {
        "screen": "combat",
        "classification": {"summary_kind": "combat"},
        "raw_state": {
            "turn": 1,
            "combat": {
                "player": {"current_hp": 44, "max_hp": 80, "block": 8, "energy": 1},
                "hand": [
                    {"name": "Zap", "energy_cost": 1, "star_cost": 0, "costs_x": False, "requires_target": False, "playable": True},
                    {"name": "Nova", "energy_cost": 2, "star_cost": 3, "costs_x": False, "requires_target": True, "playable": True},
                ],
                "enemies": [{"name": "Slime", "current_hp": 18, "intent_damage": 10}],
            },
            "run": {"gold": 77},
        },
        "available_actions": [{"type": "play_card", "raw": {"name": "play_card"}}],
    }

    context = builder.build(snapshot)

    cards = context["payload"]["playable_card_summaries"]
    assert cards[0]["name"] == "Zap"
    assert cards[0]["energy_cost"] == 1
    assert cards[1]["star_cost"] == 3
    assert cards[1]["requires_target"] is True


    builder = STS2SummaryContextBuilder()
    snapshot = {
        "screen": "reward",
        "classification": {"summary_kind": "reward"},
        "raw_state": {
            "run": {"floor": 2, "current_hp": 49, "max_hp": 80, "gold": 99},
            "reward": {
                "cards": [
                    {"index": 0, "name": "Bash"},
                    {"index": 1, "card_id": "POMMEL_STRIKE"},
                ]
            },
        },
        "available_actions": [{"type": "claim_reward", "raw": {"name": "claim_reward"}}],
    }

    context = builder.build(snapshot)

    assert context["payload"]["reward_card_names"] == ["Bash", "POMMEL_STRIKE"]
    assert context["payload"]["reward_card_indices"] == [0, 1]
