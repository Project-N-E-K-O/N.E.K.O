"""Departure metadata reflects the same hard gates used in Runtime settlement."""

from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_unified_pacing import story_for


def test_automatic_departure_exposes_the_first_unmet_gate_without_persisting_it():
    story = story_for("检修间")
    source = story["nodes"][0]
    route = source["route_gates"][1]
    contract = route["transition_contract"]
    contract.pop("fallback_offer")
    contract.pop("accept_input")
    contract["advance_on_completion"] = True
    source["story_beat"]["fixed_narrations"] = [{
        "id": "record", "text": "检修记录已封存。",
        "trigger": {"type": "condition", "condition": "完成检修。"},
        "after": [], "required_before_exit": True,
    }]
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id="departure_gates", catgirl_binding=_binding(),
                                    opening_performance=_opening())
    turn = TurnRequestV2("inspect", 0, "一起检查设备吧。")

    pending = engine.resolve_turn(session, turn, ())
    assert pending.route is None
    assert pending.blocked_departure_route["id"] == route["id"]
    assert pending.departure_blocker == "completion_pending"
    assert "departure_blocker" not in pending.ledger_event

    facts = ({"op": "set", "key": "scene:start:done", "value": True, "visibility": "public"},)
    pending = engine.resolve_turn(session, turn, (), fact_operations=facts)
    assert pending.route is None
    assert pending.blocked_departure_route["id"] == route["id"]
    assert pending.departure_blocker == "required_narration_pending"

    advanced = engine.resolve_turn(session, turn, (), fact_operations=facts, condition_narrations_enabled=False)
    assert advanced.route["id"] == route["id"]
    assert advanced.blocked_departure_route is None
    assert advanced.departure_blocker is None
