"""Final-draft provenance, phase ordering, missing coverage and privacy."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.theater.numeric_v2_narrative import (
    apply_narrative_record, make_narrative_record, narrative_body_fingerprint,
    narrative_event_table, parse_narrative_events, project_narrative_state,
)


def event(phase="turn", source="performance", quote="木匣交给你", value="player", known=None):
    return {"phase": phase, "subject": "character", "kind": "action",
            "changes": [{"entity": "item:木匣", "attribute": "holder", "value": value}],
            "source": source, "quote": quote, "known_to": known or ["player", "character"]}


def review(performance, indexes=None, complete=True):
    table = narrative_event_table(performance, "接过木匣")
    return SimpleNamespace(reviewed_narrative_events=table,
                           approved_narrative_event_indexes=tuple(range(len(table))) if indexes is None else indexes,
                           narrative_review_complete=complete,
                           reviewed_narrative_body=narrative_body_fingerprint(performance))


def apply(state, record, performance, revision=1):
    return apply_narrative_record(state, record, revision=revision, node_id="target",
                                  from_node_id="source", performance=performance, message="接过木匣")


@pytest.mark.parametrize("mutation", [
    {"kind": []}, {"source": {}}, {"known_to": [{}]}, {"phase": {}},
    {"changes": [{"entity": "item:木匣", "attribute": [], "value": "player"}]},
    {"changes": [{"entity": "character", "attribute": "holder", "value": "player"}]},
    {"changes": [{"entity": "item:木匣", "attribute": "holder", "value": "activity:任务"}]},
    {"changes": [{"entity": "item:木匣", "attribute": "holder", "value": "person:"}]},
])
def test_bad_auxiliary_event_is_not_an_actor_retry(mutation):
    assert parse_narrative_events([{**event(), **mutation}]) is None


def test_final_draft_binding_retains_independent_player_proof_only():
    player = {**event("player_input", "player_input", "接过木匣"), "subject": "player"}
    performance = {"performance": "木匣交给你。", "narrative_events": [player, event()]}
    approved = review(performance)
    rewritten = {**performance, "performance": "别着急。"}
    record = make_narrative_record(rewritten, "接过木匣", approved)
    assert record["coverage"] == "partial"
    assert record["events"] == [player]
    assert make_narrative_record(performance, "接过木匣", approved)["coverage"] == "confirmed_changes"
    assert make_narrative_record(performance, "接过木匣", approved, body_confirmed=False)["events"] == [player]


def test_empty_table_requires_explicit_review_and_exact_body():
    performance = {"performance": "嗯。", "narrative_events": []}
    assert make_narrative_record(performance, "", None)["coverage"] == "unconfirmed"
    approved = review(performance)
    assert make_narrative_record(performance, "", approved)["coverage"] == "confirmed_no_change"
    assert make_narrative_record({**performance, "performance": "再说。"}, "", approved)["coverage"] == "unconfirmed"
    assert make_narrative_record({"performance": "嗯。"}, "", None)["coverage"] == "unrecorded"


def test_phase_order_uses_previous_event_state_and_phase_specific_evidence():
    source = event("source_response", quote="木匣交给你")
    target = event("target_opening", quote="你将木匣交回", value="character")
    performance = {"segments": [
        {"phase": "source_response", "performance": "木匣交给你。"},
        {"phase": "transition_bridge", "scene_narration": "两人来到门口。"},
        {"phase": "target_opening", "performance": "你将木匣交回。"},
    ], "narrative_events": [target, source]}
    state = apply(None, make_narrative_record(performance, "", review(performance)), performance)
    assert state["recent_events"][0]["context"] == {"node_id": "source"}
    assert state["recent_events"][1]["context"] == {"node_id": "target"}
    assert state["recent_events"][1]["changes"][0]["before"] == "player"
    assert state["entities"]["item:木匣"]["holder"]["value"] == "character"
    wrong_phase = deepcopy(performance)
    wrong_phase["narrative_events"] = [{**source, "phase": "transition_bridge"}]
    with pytest.raises(ValueError, match="candidates_invalid"):
        narrative_event_table(wrong_phase, "")


def test_gap_marks_old_values_last_known_and_reconfirmation_is_local():
    performance = {"performance": "木匣交给你。", "narrative_events": [event()]}
    state = apply(None, make_narrative_record(performance, "", review(performance)), performance)
    previous = deepcopy(state)
    gap = {"performance": "嗯。"}
    state = apply(state, make_narrative_record(gap, ""), gap, revision=2)
    projection = project_narrative_state(SimpleNamespace(narrative_state=state, current_node_id="target"))
    assert projection["current"] == {}
    assert projection["last_known"]["item:木匣"]["holder"]["value"] == "player"
    assert previous["entities"]["item:木匣"]["holder"]["current"] is True
    state = apply(state, make_narrative_record(performance, "", review(performance)), performance, revision=3)
    assert state["entities"]["item:木匣"]["holder"]["current"] is True


def test_character_projection_does_not_expose_player_private_state_or_before_values():
    private = {"performance": "木匣交给你。", "narrative_events": [event(known=["player"])]}
    state = apply(None, make_narrative_record(private, "", review(private)), private)
    projection = project_narrative_state(SimpleNamespace(narrative_state=state, current_node_id="target"))
    assert not projection["current"] and not projection["recent_events"]
    public = {"performance": "木匣仍由你持有。", "narrative_events": [event(quote="木匣仍由你持有")]}
    state = apply(state, make_narrative_record(public, "", review(public)), public, revision=2)
    projection = project_narrative_state(SimpleNamespace(narrative_state=state, current_node_id="target"))
    assert "before" not in str(projection)


def test_ordinary_event_cannot_reorder_the_turn_as_an_opening():
    performance = {"performance": "木匣交给你。", "narrative_events": [event("opening")]}
    with pytest.raises(ValueError, match="candidates_invalid"):
        narrative_event_table(performance, "")
    assert narrative_event_table(performance, "", opening=True)[0]["phase"] == "opening"


def test_scene_change_does_not_carry_a_previous_counter_position_into_new_scene():
    position = {**event(quote="木匣在柜台"), "changes": [
        {"entity": "item:木匣", "attribute": "holder", "value": "player"},
        {"entity": "player", "attribute": "position", "value": "柜台"},
    ]}
    performance = {"performance": "木匣在柜台。", "narrative_events": [position]}
    state = apply_narrative_record(None, make_narrative_record(performance, "", review(performance)),
        revision=1, node_id="source", from_node_id="source", performance=performance, message="")
    crossing = {"segments": [{"phase": "transition_bridge", "scene_narration": "两人抵达湖畔。"}],
                "narrative_events": []}
    state = apply(state, make_narrative_record(crossing, "", review(crossing)), crossing, revision=2)
    assert state["entities"]["player"]["position"]["current"] is False
    assert state["entities"]["item:木匣"]["holder"]["current"] is True


def test_new_entity_requires_its_literal_name_in_the_original_proof():
    unbound = event(quote="交给你")
    performance = {"performance": "交给你。", "narrative_events": [unbound]}
    assert make_narrative_record(performance, "", review(performance))["events"] == []


def test_binding_order_matches_replay_when_actor_table_is_not_phase_ordered():
    performance = {"segments": [
        {"phase": "source_response", "performance": "它交给你。"},
        {"phase": "target_opening", "performance": "木匣已经交回。"},
    ], "narrative_events": [
        event("target_opening", quote="木匣已经交回", value="character"),
        event("source_response", quote="它交给你"),
    ]}
    record = make_narrative_record(performance, "", review(performance))
    assert record["coverage"] == "partial"
    assert len(record["events"]) == 1
    assert apply(None, record, performance)["entities"]["item:木匣"]["holder"]["value"] == "character"


@pytest.mark.parametrize("indexes", [(True,), (0, 0), (3,), ("0",)])
def test_invalid_approval_group_cannot_promote_events(indexes):
    performance = {"performance": "木匣交给你。", "narrative_events": [event()]}
    assert make_narrative_record(performance, "", review(performance, indexes))["events"] == []


@pytest.mark.parametrize("attribute,value,quote", [
    ("condition", "稳定悬浮/被接住", "木匣里还有微微颤动的光"),
    ("commitment", "waiting_for_result", "明天见，路上小心"),
])
def test_review_approval_cannot_promote_an_invented_state_label(attribute, value, quote):
    claim = {**event(quote=quote), "changes": [
        {"entity": "item:木匣" if attribute == "condition" else "character",
         "attribute": attribute, "value": value},
    ]}
    performance = {"performance": "木匣交给你。" + quote, "narrative_events": [event(), claim]}
    record = make_narrative_record(performance, "", review(performance))
    assert record["coverage"] == "partial"
    assert record["events"] == [event()]
    state = apply(None, record, performance)
    assert state["entities"]["item:木匣"]["holder"]["value"] == "player"
    assert attribute not in state["entities"].get(claim["changes"][0]["entity"], {})


def test_literal_state_and_canonical_holder_share_the_same_source_quote():
    changed = {**event(quote="木匣交给你，匣盖已扣紧"), "changes": [
        {"entity": "item:木匣", "attribute": "holder", "value": "player"},
        {"entity": "item:木匣", "attribute": "condition", "value": "扣紧"},
    ]}
    performance = {"performance": changed["quote"], "narrative_events": [changed]}
    record = make_narrative_record(performance, "", review(performance))
    assert record["coverage"] == "confirmed_changes"
    assert apply(None, record, performance)["entities"]["item:木匣"]["condition"]["value"] == "扣紧"


def test_legacy_auxiliary_summary_is_never_persisted_as_another_fact_account():
    performance = {"performance": "木匣交给你。", "narrative_events": [
        {**event(), "summary": "木匣已经交回，双方承诺明天再见"},
    ]}
    assert parse_narrative_events(performance["narrative_events"]) == [event()]
    record = make_narrative_record(performance, "", review(performance))
    assert record["events"] == [event()]
    assert "summary" not in str(apply(None, record, performance))


def test_inherited_player_entity_binds_before_the_final_approved_body_event():
    player = {**event("player_input", "player_input", "接过木匣"), "subject": "player"}
    body = event(quote="它交回柜台了", value="character")
    performance = {"performance": "它交回柜台了。", "narrative_events": [body]}
    record = make_narrative_record(
        performance, "接过木匣", review(performance), preserved_player_events=(player,),
    )
    assert record["coverage"] == "partial"
    assert record["events"] == [player, body]
    state = apply(None, record, performance)
    assert state["entities"]["item:木匣"]["holder"]["value"] == "character"
    assert state["recent_events"][1]["changes"][0]["before"] == "player"


@pytest.mark.parametrize("preserved", [
    event(),
    {**event("player_input", "player_input", "接过另一只木匣"), "subject": "player"},
    {**event("player_input", "player_input", "接过木匣"), "subject": "player",
     "authorization_quote": "答应留下"},
])
def test_preserved_proof_requires_the_same_player_input_and_never_inherits_body(preserved):
    performance = {"performance": "木匣交给你。", "narrative_events": []}
    record = make_narrative_record(
        performance, "接过木匣", review(performance), preserved_player_events=(preserved,),
    )
    assert record["events"] == []
    assert record["coverage"] == "confirmed_no_change"


@pytest.mark.parametrize("approved", [False, True])
def test_final_player_verdict_owns_overlapping_attributes_and_visibility(approved):
    player = {**event("player_input", "player_input", "接过木匣"), "subject": "player"}
    final = {**player, "known_to": ["player"]}
    performance = {"performance": "拿稳就好。", "narrative_events": [final]}
    record = make_narrative_record(
        performance, "接过木匣", review(performance, (0,) if approved else ()),
        preserved_player_events=(player,),
    )
    assert record["events"] == ([final] if approved else [])
    assert record["coverage"] == ("partial" if approved else "unconfirmed")


def test_exactly_repeated_player_proof_does_not_change_final_complete_coverage():
    player = {**event("player_input", "player_input", "接过木匣"), "subject": "player"}
    performance = {"performance": "拿稳就好。", "narrative_events": [player]}
    record = make_narrative_record(
        performance, "接过木匣", review(performance), preserved_player_events=(player, player),
    )
    assert record["events"] == [player]
    assert record["coverage"] == "confirmed_changes"
    assert record["proposal_count"] == 1


def test_inherited_holder_cannot_override_a_different_final_player_result():
    message = "接过木匣后，把木匣递还给她。"
    earlier = {**event("player_input", "player_input", "接过木匣"), "subject": "player"}
    final = {**event("player_input", "player_input", "把木匣递还给她", value="character"), "subject": "player"}
    performance = {"performance": "我收好。", "narrative_events": [final]}
    approved = SimpleNamespace(
        reviewed_narrative_events=narrative_event_table(performance, message),
        approved_narrative_event_indexes=(0,), narrative_review_complete=True,
        reviewed_narrative_body=narrative_body_fingerprint(performance),
    )
    record = make_narrative_record(performance, message, approved, preserved_player_events=(earlier,))
    assert record["events"] == [final]
    assert record["coverage"] == "partial"
    state = apply_narrative_record(None, record, revision=1, node_id="source", from_node_id="source",
                                   performance=performance, message=message)
    assert state["entities"]["item:木匣"]["holder"]["value"] == "character"


def test_conflicting_inherited_player_events_are_not_chosen_as_truth():
    player = {**event("player_input", "player_input", "接过木匣"), "subject": "player"}
    performance = {"performance": "拿稳就好。", "narrative_events": []}
    record = make_narrative_record(
        performance, "接过木匣", review(performance),
        preserved_player_events=(player, {**player, "known_to": ["player"]}),
    )
    assert record["events"] == []
    assert record["coverage"] == "unconfirmed"


def test_inheritance_does_not_displace_three_final_approved_events():
    player = {**event("player_input", "player_input", "接过木匣"), "subject": "player"}
    bodies = []
    for name in ("钥匙", "提灯", "手套"):
        body = event(quote=f"{name}交给你")
        body["changes"][0]["entity"] = f"item:{name}"
        bodies.append(body)
    performance = {"performance": "。".join(body["quote"] for body in bodies), "narrative_events": bodies}
    record = make_narrative_record(
        performance, "接过木匣", review(performance), preserved_player_events=(player,),
    )
    assert record["events"] == bodies
    assert record["coverage"] == "partial"
    assert record["proposal_count"] == 3
    assert len(apply(None, record, performance)["recent_events"]) == 3


@pytest.mark.parametrize("item,receiver", [("剑匣", "旧友"), ("资料袋", "班长")])
@pytest.mark.parametrize("rejected", [None, "value", "binding", "review"])
def test_later_handoff_gap_cannot_promote_the_players_intermediate_holder(item, receiver, rejected):
    message = f"我托着{item}，请{receiver}验收。"
    bridge = f"{receiver}接过{item}。"
    player = {"phase": "player_input", "subject": "player", "kind": "action",
              "changes": [{"entity": f"item:{item}", "attribute": "holder", "value": "player"}],
              "source": "player_input", "quote": message, "known_to": ["player", "character"]}
    received = {"phase": "transition_bridge", "subject": f"person:{receiver}", "kind": "action",
                "changes": [{"entity": f"item:{item}", "attribute": "holder", "value": f"person:{receiver}"}],
                "source": "scene_narration", "quote": bridge, "known_to": ["player", "character"]}
    if rejected == "value":
        received["changes"].append({"entity": "character", "attribute": "stage_result", "value": "完成"})
    elif rejected == "binding":
        received["subject"] = received["changes"][0]["value"] = "person:未出现的接收者"
    performance = {"segments": [{"phase": "source_response", "performance": "请验收。"},
                   {"phase": "transition_bridge", "scene_narration": bridge},
                   {"phase": "target_opening", "performance": "已经收好了。"}],
                   "narrative_events": [player, received]}
    approved = SimpleNamespace(reviewed_narrative_events=narrative_event_table(performance, message),
        approved_narrative_event_indexes=(0,) if rejected == "review" else (0, 1),
        narrative_review_complete=True, reviewed_narrative_body=narrative_body_fingerprint(performance))
    record = make_narrative_record(performance, message, approved)
    state = apply_narrative_record(None, record, revision=1, node_id="target", from_node_id="source",
                                   performance=performance, message=message)
    holder = state["entities"][f"item:{item}"]["holder"]
    projection = project_narrative_state(SimpleNamespace(narrative_state=state, current_node_id="target"))
    assert record["events"][0] == player
    if rejected:
        assert record["coverage"] == "partial"
        assert record["events"] == [player]
        assert holder["value"] == "player" and holder["current"] is False
        assert not projection["current"]
        assert projection["last_known"][f"item:{item}"]["holder"]["value"] == "player"
    else:
        assert record["coverage"] == "confirmed_changes"
        assert holder["value"] == f"person:{receiver}" and holder["current"] is True
        assert projection["current"][f"item:{item}"]["holder"]["value"] == f"person:{receiver}"
