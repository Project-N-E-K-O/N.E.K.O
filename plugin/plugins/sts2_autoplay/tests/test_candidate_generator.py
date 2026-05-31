from __future__ import annotations

from plugin.plugins.sts2_autoplay.candidate_generator import STS2CandidateGenerator
from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner


def test_candidate_generator_prefers_planned_operation_then_registry_actions() -> None:
    planner = STS2HeuristicPlanner()
    generator = STS2CandidateGenerator(planner)
    context = {
        "classification": {"state_name": "event"},
        "summary_context": {"payload": {}},
        "strategy_context": {"preferences": {}},
        "snapshot": {
            "available_actions": [
                {"type": "choose_event_option", "raw": {"name": "choose_event_option", "index": 1}},
                {"type": "proceed", "raw": {"name": "proceed"}},
            ],
            "action_registry": [
                {"id": "evt-1", "type": "choose_event_option", "default_kwargs": {"option_index": 1}, "category": "event"},
                {"id": "evt-2", "type": "proceed", "default_kwargs": {}, "category": "event"},
            ],
        },
    }

    candidates = generator.generate(context, mode="program")

    assert len(candidates) >= 2
    assert candidates[0]["action_type"] == "choose_event_option"
    assert candidates[0]["priority"] >= candidates[1]["priority"]
    assert any(item["action_id"] == "evt-2" for item in candidates)


def test_candidate_generator_uses_raw_fallback_when_registry_missing() -> None:
    generator = STS2CandidateGenerator(None)
    context = {
        "snapshot": {
            "available_actions": [
                {"type": "open_timeline"},
                {"type": "continue_run"},
            ]
        }
    }

    candidates = generator.generate(context, mode="program")

    assert len(candidates) == 2
    assert candidates[0]["source"] == "raw_fallback_candidate"
    assert candidates[0]["action_type"] == "open_timeline"


def test_planner_uses_default_option_index_for_map_when_no_preference() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "map"},
            "summary_context": {"payload": {"travelable_nodes": [{"index": 2, "type": "monster"}] }},
            "strategy_context": {"preferences": {}},
            "snapshot": {
                "screen": "MAP",
                "available_actions": [
                    {"type": "choose_map_node", "raw": {"name": "choose_map_node", "requires_index": True}},
                ],
            },
        }
    )

    assert operation is not None
    assert operation.action_type == "choose_map_node"
    assert operation.kwargs["option_index"] == 2








def test_planner_defense_sequence_builds_block_before_attack() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "combat"},
            "summary_context": {
                "payload": {},
                "decision_payload": {
                    "guidance": {
                        "pending": [{"content": "先防一下，别贪"}],
                        "generation": 1,
                    }
                },
            },
            "strategy_context": {"preferences": {}},
            "snapshot": {
                "screen": "COMBAT",
                "available_actions": [
                    {"type": "play_card", "raw": {"name": "play_card"}},
                    {"type": "end_turn", "raw": {"name": "end_turn"}},
                ],
                "raw_state": {
                    "combat": {
                        "player": {"energy": 3, "block": 0},
                        "hand": [
                            {"index": 0, "name": "Strike", "description": "Deal 6 damage", "damage": 6, "energy_cost": 1, "playable": True, "valid_target_indices": [0]},
                            {"index": 1, "name": "Defend", "description": "Gain 5 block", "block": 5, "energy_cost": 1, "playable": True},
                            {"index": 2, "name": "Defend", "description": "Gain 5 block", "block": 5, "energy_cost": 1, "playable": True},
                        ],
                        "enemies": [{"current_hp": 20, "intent_damage": 6}],
                    }
                },
            },
        }
    )

    assert operation is not None
    assert operation.reason == "combat_defense_sequence"
    assert operation.kwargs["card_index"] in {1, 2}


def test_planner_attack_priority_sequence_defends_when_gap_is_large() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "combat"},
            "summary_context": {
                "payload": {},
                "decision_payload": {
                    "guidance": {
                        "pending": [{"content": "优先输出"}],
                        "generation": 1,
                    }
                },
            },
            "strategy_context": {"preferences": {}},
            "snapshot": {
                "screen": "COMBAT",
                "available_actions": [
                    {"type": "play_card", "raw": {"name": "play_card"}},
                    {"type": "end_turn", "raw": {"name": "end_turn"}},
                ],
                "raw_state": {
                    "combat": {
                        "player": {"energy": 2, "block": 0},
                        "hand": [
                            {"index": 0, "name": "Strike", "description": "Deal 6 damage", "damage": 6, "energy_cost": 1, "playable": True},
                            {"index": 1, "name": "Defend", "description": "Gain 5 block", "block": 5, "energy_cost": 1, "playable": True},
                        ],
                        "enemies": [{"current_hp": 20, "intent_damage": 6}],
                    }
                },
            },
        }
    )

    assert operation is not None
    assert operation.reason == "combat_attack_priority_sequence"
    assert operation.kwargs["card_index"] == 1


def test_planner_attack_priority_sequence_attacks_when_gap_is_small() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "combat"},
            "summary_context": {
                "payload": {},
                "decision_payload": {
                    "guidance": {
                        "pending": [{"content": "优先输出"}],
                        "generation": 1,
                    }
                },
            },
            "strategy_context": {"preferences": {}},
            "snapshot": {
                "screen": "COMBAT",
                "available_actions": [
                    {"type": "play_card", "raw": {"name": "play_card"}},
                    {"type": "end_turn", "raw": {"name": "end_turn"}},
                ],
                "raw_state": {
                    "combat": {
                        "player": {"energy": 2, "block": 5},
                        "hand": [
                            {"index": 0, "name": "Strike", "description": "Deal 6 damage", "damage": 6, "energy_cost": 1, "playable": True},
                            {"index": 1, "name": "Defend", "description": "Gain 5 block", "block": 5, "energy_cost": 1, "playable": True},
                        ],
                        "enemies": [{"current_hp": 20, "intent_damage": 6}],
                    }
                },
            },
        }
    )

    assert operation is not None
    assert operation.reason == "combat_attack_priority_sequence"
    assert operation.kwargs["card_index"] == 0






def test_planner_target_priority_prefers_lethal_target() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "combat"},
            "summary_context": {"payload": {}},
            "strategy_context": {"preferences": {}},
            "snapshot": {
                "screen": "COMBAT",
                "available_actions": [
                    {"type": "play_card", "raw": {"name": "play_card"}},
                ],
                "raw_state": {
                    "combat": {
                        "player": {"energy": 1, "block": 10},
                        "hand": [
                            {"index": 0, "name": "Strike", "description": "Deal 6 damage", "damage": 6, "energy_cost": 1, "playable": True, "valid_target_indices": [0, 1]},
                        ],
                        "enemies": [
                            {"current_hp": 10, "intent_damage": 12},
                            {"current_hp": 6, "intent_damage": 4},
                        ],
                    }
                },
            },
        }
    )

    assert operation is not None
    assert operation.kwargs["target_index"] == 1


def test_planner_target_priority_prefers_higher_intent_when_no_lethal() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "combat"},
            "summary_context": {"payload": {}},
            "strategy_context": {"preferences": {}},
            "snapshot": {
                "screen": "COMBAT",
                "available_actions": [
                    {"type": "play_card", "raw": {"name": "play_card"}},
                ],
                "raw_state": {
                    "combat": {
                        "player": {"energy": 1, "block": 10},
                        "hand": [
                            {"index": 0, "name": "Strike", "description": "Deal 6 damage", "damage": 6, "energy_cost": 1, "playable": True, "valid_target_indices": [0, 1]},
                        ],
                        "enemies": [
                            {"current_hp": 20, "intent_damage": 12},
                            {"current_hp": 10, "intent_damage": 4},
                        ],
                    }
                },
            },
        }
    )

    assert operation is not None
    assert operation.kwargs["target_index"] == 0




def test_planner_remove_skips_eternal_cards() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "card_selection_delet"},
            "summary_context": {
                "payload": {
                    "selection_cards": [
                        {"index": 0, "name": "Strike", "description": "Eternal. Deal 6 damage."},
                        {"index": 1, "name": "Strike", "description": "Deal 6 damage."},
                    ]
                }
            },
            "strategy_context": {"preferences": {}},
            "snapshot": {
                "screen": "CARD_SELECTION_DELET",
                "available_actions": [
                    {"type": "select_deck_card", "raw": {"name": "select_deck_card"}},
                ],
            },
        }
    )

    assert operation is not None
    assert operation.kwargs["option_index"] == 1


def test_planner_remove_prefers_curse_before_starter() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "card_selection_delet"},
            "summary_context": {
                "payload": {
                    "selection_cards": [
                        {"index": 0, "name": "Strike", "description": "Deal 6 damage."},
                        {"index": 1, "name": "Curse of Pain", "description": "Curse."},
                    ]
                }
            },
            "strategy_context": {"preferences": {}},
            "snapshot": {
                "screen": "CARD_SELECTION_DELET",
                "available_actions": [
                    {"type": "select_deck_card", "raw": {"name": "select_deck_card"}},
                ],
            },
        }
    )

    assert operation is not None
    assert operation.kwargs["option_index"] == 1












def test_planner_reward_uses_selection_cards_when_reward_cards_missing() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "card_selection"},
            "summary_context": {
                "payload": {
                    "selection_cards": [
                        {"index": 0, "name": "Meteor Strike", "description": "3 cost attack. Big damage."},
                        {"index": 1, "name": "Coolheaded", "description": "Gain 5 block. Draw 1 card. Channel 1 Frost."},
                    ],
                    "deck": {
                        "cards": [
                            {"name": "Zap"},
                            {"name": "Coolheaded"},
                            {"name": "Charge Battery"},
                        ]
                    },
                }
            },
            "strategy_context": {
                "preferences": {
                    "records": [
                        {
                            "value": {
                                "instruction_type": "deck_policy",
                                "prefer_tags": ["draw", "defense"],
                                "avoid_tags": ["expensive_attack"],
                                "archetype_bias": ["orb_focus"],
                            }
                        }
                    ]
                }
            },
            "snapshot": {
                "screen": "CARD_SELECTION",
                "available_actions": [
                    {"type": "choose_reward_card", "raw": {"name": "choose_reward_card", "requires_index": True}},
                ],
            },
        }
    )

    assert operation is not None
    assert operation.kwargs["option_index"] == 1






def test_planner_reward_energy_refund_is_rewarded() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "card_selection"},
            "summary_context": {
                "payload": {
                    "selection_cards": [
                        {
                            "index": 0,
                            "name": "御血术",
                            "resolved_rules_text": "失去2点生命。 造成15点伤害。",
                            "dynamic_values": [
                                {"name": "Damage", "current_value": 15},
                                {"name": "HpLoss", "current_value": 2},
                            ],
                            "card_type": "Attack",
                        },
                        {
                            "index": 1,
                            "name": "战斗专注",
                            "resolved_rules_text": "获得1点能量。 抽1张牌。",
                            "dynamic_values": [
                                {"name": "Energy", "current_value": 1},
                                {"name": "Cards", "current_value": 1},
                            ],
                            "card_type": "Skill",
                        },
                    ],
                    "deck": {"cards": [{"name": "Strike"}, {"name": "Defend"}]},
                }
            },
            "strategy_context": {"preferences": {"records": []}},
            "snapshot": {
                "screen": "CARD_SELECTION",
                "available_actions": [
                    {"type": "choose_reward_card", "raw": {"name": "choose_reward_card", "requires_index": True}},
                ],
            },
        }
    )

    assert operation is not None
    assert operation.kwargs["option_index"] == 1




def test_planner_event_respects_defensive_guidance() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "event"},
            "summary_context": {
                "payload": {
                    "current_hp": 20,
                    "max_hp": 80,
                    "gold": 90,
                    "event_options": [
                        {"index": 0, "label": "Lose 10 HP"},
                        {"index": 1, "label": "Gain Relic"},
                    ],
                },
                "decision_payload": {
                    "guidance": {
                        "pending": [{"content": "保血，别贪"}],
                        "generation": 1,
                    }
                },
            },
            "strategy_context": {"preferences": {}},
            "snapshot": {
                "screen": "EVENT",
                "available_actions": [
                    {"type": "choose_event_option", "raw": {"name": "choose_event_option", "requires_index": True}},
                ],
            },
        }
    )

    assert operation is not None
    assert operation.kwargs["option_index"] == 1


def test_planner_reward_respects_defensive_guidance() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "card_selection"},
            "summary_context": {
                "payload": {
                    "selection_cards": [
                        {
                            "index": 0,
                            "name": "御血术",
                            "resolved_rules_text": "失去2点生命。 造成15点伤害。",
                            "dynamic_values": [
                                {"name": "Damage", "current_value": 15},
                                {"name": "HpLoss", "current_value": 2},
                            ],
                            "card_type": "Attack",
                        },
                        {
                            "index": 1,
                            "name": "血墙",
                            "resolved_rules_text": "失去2点生命。 获得16点格挡。",
                            "dynamic_values": [
                                {"name": "Block", "current_value": 16},
                                {"name": "HpLoss", "current_value": 2},
                            ],
                            "card_type": "Skill",
                        },
                    ],
                    "deck": {"cards": [{"name": "Strike"}, {"name": "Defend"}]},
                },
                "decision_payload": {
                    "guidance": {
                        "pending": [{"content": "先防一下，别贪"}],
                        "generation": 1,
                    }
                },
            },
            "strategy_context": {"preferences": {"records": []}},
            "snapshot": {
                "screen": "CARD_SELECTION",
                "available_actions": [
                    {"type": "choose_reward_card", "raw": {"name": "choose_reward_card", "requires_index": True}},
                ],
            },
        }
    )

    assert operation is not None
    assert operation.kwargs["option_index"] == 1
