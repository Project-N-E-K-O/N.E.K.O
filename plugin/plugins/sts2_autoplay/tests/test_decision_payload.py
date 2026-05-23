from __future__ import annotations

from plugin.plugins.sts2_autoplay.decision_payload import DecisionPayload
from plugin.plugins.sts2_autoplay.summary_context_builder import STS2SummaryContextBuilder


def test_decision_payload_as_dict_keeps_core_fields() -> None:
    payload = DecisionPayload(
        mode="program",
        screen_type="combat",
        state_name="combat",
        summary_kind="combat",
        state_signature="abc123",
        strategy_directives={"strategy_name": "defect"},
        guidance={"pending": [{"content": "先防"}]},
        instructions=[{"source": "neko_guidance", "content": "先防"}],
        run_state={"floor": 5, "act": 1},
        tactical_signals={"incoming_attack_total": 18},
        legal_actions=[{"id": "play-1", "type": "play_card"}],
        candidate_actions=[{"action_id": "play-1", "action_type": "play_card"}],
        policy={"prefers_model": False},
    ).as_dict()

    assert payload["mode"] == "program"
    assert payload["screen_type"] == "combat"
    assert payload["state_signature"] == "abc123"
    assert payload["strategy_directives"]["strategy_name"] == "defect"
    assert payload["guidance"]["pending"][0]["content"] == "先防"
    assert payload["instructions"][0]["source"] == "neko_guidance"
    assert payload["run_state"]["floor"] == 5
    assert payload["tactical_signals"]["incoming_attack_total"] == 18
    assert payload["legal_actions"][0]["type"] == "play_card"
    assert payload["candidate_actions"][0]["action_type"] == "play_card"


def test_decision_payload_can_carry_memory_and_run_intent() -> None:
    payload = DecisionPayload(
        mode="program",
        screen_type="event",
        state_name="event",
        summary_kind="event",
        state_signature="sig-2",
        strategy_directives={"strategy_name": "defect", "must": ["preserve_hp"]},
        guidance={"pending": []},
        run_state={"floor": 8, "act": 2, "current_hp": 25, "max_hp": 80},
        tactical_signals={"event_option_count": 2},
        legal_actions=[{"id": "event-0", "type": "choose_event_option"}],
        candidate_actions=[],
        policy={"prefers_model": True},
    ).as_dict()
    payload["recent_decision_memory"] = [{"screen": "event", "decision_reason": "llm_engine_event_value_choice"}]
    payload["run_intent"] = {"risk_posture": "preserve_hp", "strategy_name": "defect"}

    assert payload["recent_decision_memory"][0]["decision_reason"] == "llm_engine_event_value_choice"
    assert payload["run_intent"]["risk_posture"] == "preserve_hp"


def test_summary_builder_infers_archetype_and_route_future() -> None:
    builder = STS2SummaryContextBuilder()
    snapshot = {
        "screen": "map",
        "classification": {
            "state_name": "map",
            "summary_kind": "map",
            "sync_priority": "high",
            "action_family": "indexed",
        },
        "raw_state": {
            "run": {"current_hp": 40, "max_hp": 80, "gold": 120},
            "map": {
                "nodes": [{"type": "elite", "is_available": True}, {"type": "rest", "is_available": True}],
                "future_nodes": [{"type": "shop"}, {"type": "treasure"}],
            },
            "deck": {
                "cards": [
                    {"name": "Zap"},
                    {"name": "Coolheaded"},
                    {"name": "Charge Battery"},
                ],
                "card_count": 12,
            },
            "relics": [{"name": "Lantern"}],
            "potions": [{"name": "Fire Potion"}],
        },
    }
    summary_context = {
        "payload": builder._build_map_context(snapshot, snapshot["raw_state"]),
    }
    summary_context["payload"]["deck"] = snapshot["raw_state"]["deck"]
    summary_context["payload"]["relics"] = snapshot["raw_state"]["relics"]
    summary_context["payload"]["potions"] = snapshot["raw_state"]["potions"]
    strategy_context = {"strategy_name": "defect"}

    signals = builder.build_tactical_signals(snapshot, summary_context, strategy_context)

    assert "orb_focus" in signals["archetype_tags"]
    assert "defense" in signals["archetype_tags"]
    assert signals["future_node_types"] == ["shop", "treasure"]
    assert signals["relic_names"] == ["Lantern"]
    assert signals["potion_names"] == ["Fire Potion"]


    builder = STS2SummaryContextBuilder()
    snapshot = {
        "screen": "combat",
        "classification": {
            "state_name": "combat",
            "summary_kind": "combat",
            "sync_priority": "high",
            "action_family": "targeted",
        },
        "raw_state": {
            "combat": {
                "player": {"current_hp": 20, "max_hp": 80, "block": 6, "energy": 2},
                "enemies": [
                    {"intent_damage": 8},
                    {"intent_damage": 10},
                ],
            }
        },
    }
    summary_context = {
        "payload": {
            "player": {"current_hp": 20, "max_hp": 80, "block": 6, "energy": 2},
            "enemies": [{"intent_damage": 8}, {"intent_damage": 10}],
        }
    }
    strategy_context = {"strategy_name": "defect"}

    signals = builder.build_tactical_signals(snapshot, summary_context, strategy_context)

    assert signals["incoming_attack_total"] == 18
    assert signals["remaining_block_needed"] == 12
    assert signals["projected_survival_risk"] == "medium"
    assert signals["strategy_name"] == "defect"
