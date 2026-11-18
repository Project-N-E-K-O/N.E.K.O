"""Completion discoveries retain typed values and exact evidence provenance."""

from copy import deepcopy
from dataclasses import replace

import pytest

from services.theater import numeric_v2_evaluator as ev
from services.theater import numeric_v2_fact_discovery as discovery
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2RuntimeError
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_unified_pacing import story_for


KEY = "scene:start:done"
OTHER = "scene:start:other"
TARGET_KEY = "scene:ending_leave:unpacked"
COMPLETE = "工作人员核对了通行证，又将这张通行证交到玩家手中。"
CHECKED = "工作人员核对了通行证，确认姓名和编号无误。"
HANDED = "工作人员将这张已核对的通行证交到玩家手中。"
WAITING = "工作人员仍在核对通行证，尚未交还。"


def _story(*, source="performance", last=True, two=False):
    story = story_for("候车厅")
    story["fact_contract"]["facts"][KEY].update(
        description="工作人员已核对通行证并将它交到玩家手中。", evidence_source=source)
    start = story["nodes"][0]
    start.update(min_turns=1, recommended_turns=40)
    start["story_beat"]["must_not_happen"] = ["未经程序许可，不得把进入候车厅写成已经发生。"]
    for gate in start["route_gates"]:
        gate["conditions"]["all"].append({"type": "fact_equals", "key": KEY, "value": True})
    if not last or two:
        story["fact_contract"]["facts"][OTHER] = {
            "value_type": "bool", "visibility": "public", "evidence_source": "performance",
            "description": "工作人员已完成独立的行李检查。"}
        start["completion_contract"]["all"].append({"key": OTHER, "equals": True})
    target = next(node for node in story["nodes"] if node["id"] == "ending_leave")
    story["fact_contract"]["facts"][TARGET_KEY] = {
        "value_type": "bool", "visibility": "public", "evidence_source": "performance",
        "description": "两人已在候车厅把行李全部整理完毕。"}
    target["completion_contract"] = {"all": [{"key": TARGET_KEY, "equals": True}]}
    for gate in target["route_gates"]:
        gate["conditions"]["all"].append({"type": "fact_equals", "key": TARGET_KEY, "value": True})
    return story


def _body(narration=COMPLETE, *, candidate=False):
    body = {"scene_narration": narration, "performance": "（留在柜台旁）我在这里等候。",
            "suggested_inputs": [], "transition_offered": False, "fact_candidates": []}
    if candidate:
        body["fact_candidates"] = [{"key": KEY, "value": True, "evidence_quote": narration}]
    return body


def _frame(**kwargs):
    engine = NumericV2Engine.from_mapping(_story(**kwargs))
    session = engine.create_session(session_id="discovery-guard", catgirl_binding=_binding(),
        opening_performance={"performance": "在柜台旁等候。", "suggested_inputs": []})
    return engine, session


def _packet(engine, session, body=None, message="请工作人员核对。"):
    metadata, originals = ev._committed_completion_evidence(engine, session)
    return discovery.build_packet(engine, session, body or _body(), message,
        pending_targets=ev._pending_completion_facts(engine, session),
        history_metadata=metadata, history_originals=originals)


def _items(packet, *, field="scene_narration", keys=(KEY,)):
    ref = next(row["ref"] for row in packet["sources"] if row.get("field") == field)
    return [{"target_index": index, "refs": [ref]} for index, target in enumerate(packet["targets"])
            if target["key"] in keys]


def test_discovery_restores_contract_value_and_does_not_write_session():
    engine, session = _frame()
    before = deepcopy(session)
    packet = _packet(engine, session)
    batch = discovery.parse_discoveries(_items(packet), packet)
    batch.validate()
    assert batch.operations == ({"op": "set", "key": KEY, "value": True, "visibility": "public"},)
    assert batch.claims[0]["evidence"] == [{"source": "actor_performance", "quote": COMPLETE}]
    assert session == before and KEY not in session.story_state["facts"]


def test_actor_and_discovery_merge_preserves_separate_original_bindings():
    engine, session = _frame(two=True)
    packet = _packet(engine, session)
    batch = discovery.parse_discoveries(_items(packet, keys=(KEY, OTHER)), packet)
    actor_claim = deepcopy(batch.claims[0])
    actor_claim.update(subject="工作人员", action="核对并交还", object="通行证", result="已交到玩家手中")
    omitted = batch.select((1,))
    before = deepcopy(omitted)
    merged = discovery.combine_actor_claims(engine, omitted, (actor_claim,))
    merged.validate()
    assert merged.claims == (actor_claim, omitted.claims[0])
    assert merged.bindings == (batch.bindings[0], omitted.bindings[0])
    assert omitted == before
    assert merged.select((0,)).operations[0]["key"] == KEY
    assert merged.select((1,)).operations[0]["key"] == OTHER


@pytest.mark.parametrize("mutation", ["foreign_target", "wrong_value", "wrong_source", "missing_quote"])
def test_combined_review_cannot_expand_actor_authority_or_replace_its_quotes(mutation):
    engine, session = _frame(two=True)
    packet = _packet(engine, session)
    batch = discovery.parse_discoveries(_items(packet, keys=(KEY, OTHER)), packet)
    claim = deepcopy(batch.claims[0])
    if mutation == "foreign_target":
        claim.update(key=TARGET_KEY, description=engine.fact_contract[TARGET_KEY]["description"])
    elif mutation == "wrong_value":
        claim["value"] = False
    elif mutation == "wrong_source":
        claim["evidence"][0]["source"] = "player_input"
    else:
        claim["evidence"][0]["quote"] = "没有出现在正文中的完成声明。"
    with pytest.raises(NumericV2RuntimeError):
        discovery.combine_actor_claims(engine, batch.select((1,)), (claim,))


@pytest.mark.parametrize("items", [
    [{"target_index": True, "refs": ["e0"]}],
    [{"target_index": 1000, "refs": ["e0"]}],
    [{"target_index": 0, "refs": ["not-issued"]}],
    [{"target_index": 0, "refs": ["e0", "e0"]}],
    [{"target_index": 0, "refs": ["e0"]}, {"target_index": 0, "refs": ["e0"]}],
    [{"target_index": 0, "refs": ["e0"], "value": True}],
])
def test_unissued_or_ambiguous_discovery_cannot_propose_a_write(items):
    engine, session = _frame()
    batch = discovery.parse_discoveries(items, _packet(engine, session))
    assert not batch.operations and batch.errors


def test_player_required_action_cannot_be_proved_by_actor_narration():
    engine, session = _frame(source="player_input")
    packet = _packet(engine, session, _body("工作人员说玩家已经签名。"), "请工作人员签名。")
    batch = discovery.parse_discoveries(_items(packet), packet)
    assert not batch.operations and batch.errors


def test_explicit_current_player_action_uses_only_player_input_source():
    engine, session = _frame(source="player_input")
    packet = _packet(engine, session, message="（在通行证上签下自己的名字）")
    batch = discovery.parse_discoveries([{"target_index": 0, "refs": ["e0"]}], packet)
    batch.validate()
    assert batch.claims[0]["evidence"][0]["source"] == "player_input"


def test_existing_false_fact_is_not_recovered_as_old_true():
    engine, session = _frame()
    session = replace(session, story_state={**session.story_state, "facts": {
        **session.story_state["facts"], KEY: {"value": False, "visibility": "public"}}})
    assert not _packet(engine, session)["targets"]


def test_opposite_route_value_does_not_expand_completion_discovery_targets():
    story = _story()
    story["nodes"][0]["route_gates"][0]["conditions"]["all"][-1]["value"] = False
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id="opposite", catgirl_binding=_binding(),
        opening_performance={"performance": "等候核对。", "suggested_inputs": []})
    packet = _packet(engine, session)
    assert [(row["key"], row["value"]) for row in packet["targets"]] == [(KEY, True)]


@pytest.mark.parametrize("expected,value_type,other", [(False, "bool", 0), (0, "int", False)])
def test_completion_target_value_type_is_exact_and_duplicate_pending_keys_are_removed(expected, value_type, other):
    story = _story()
    story["fact_contract"]["facts"][KEY]["value_type"] = value_type
    story["nodes"][0]["completion_contract"]["all"][0]["equals"] = expected
    for gate in story["nodes"][0]["route_gates"]:
        gate["conditions"]["all"][-1]["value"] = expected
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id="typed-target", catgirl_binding=_binding(),
        opening_performance={"performance": "等候核对。", "suggested_inputs": []})
    rows = [{"key": KEY, "value": other}, {"key": KEY, "value": expected}, {"key": KEY, "value": expected}]
    packet = discovery.build_packet(engine, session, _body(), "请核对。", pending_targets=rows,
        history_metadata={}, history_originals=())
    assert len(packet["targets"]) == 1
    assert type(packet["targets"][0]["value"]) is type(expected)
    assert packet["targets"][0]["value"] == expected


def test_committed_evidence_requires_the_issued_history_whitelist():
    engine, session = _frame()
    packet = discovery.build_packet(engine, session, _body(HANDED), "请继续。",
        pending_targets=ev._pending_completion_facts(engine, session),
        history_metadata={"allowed_keys": []}, history_originals=(CHECKED,))
    ref = next(row["ref"] for row in packet["sources"] if row["kind"] == "committed_current_visit")
    batch = discovery.parse_discoveries([{"target_index": 0, "refs": [ref]}], packet)
    assert not batch.operations and batch.errors


def test_packet_tampering_and_forged_claim_values_are_rejected():
    engine, session = _frame()
    packet = _packet(engine, session)
    batch = discovery.parse_discoveries(_items(packet), packet)
    forged = deepcopy(batch)
    forged.claims[0]["value"] = False
    with pytest.raises(NumericV2RuntimeError):
        forged.validate()
    changed = deepcopy(packet)
    changed["sources"][0]["text"] = "伪造玩家原话。"
    assert not discovery.parse_discoveries(_items(packet), changed).operations


@pytest.mark.parametrize("indexes", [(True,), (-1,), (99,), (0, 0)])
def test_approval_selection_requires_unique_issued_integer_indexes(indexes):
    engine, session = _frame()
    packet = _packet(engine, session)
    batch = discovery.parse_discoveries(_items(packet), packet)
    with pytest.raises(NumericV2RuntimeError):
        batch.select(indexes)


def test_final_body_rebind_keeps_the_original_claim_and_exposes_new_context():
    engine, session = _frame()
    body = _body()
    packet = _packet(engine, session, body)
    batch = discovery.parse_discoveries(_items(packet), packet)
    body["scene_narration"] += "但工作人员随即撤销核验结果。"
    body["performance"] += "要去候车厅吗？"
    final = _packet(engine, session, body)
    assert not discovery.matches(batch, final)
    rebound = discovery.rebind_final(batch, final)
    rebound.validate()
    assert rebound.claims == batch.claims
    assert discovery.matches(rebound, final)
    assert any("撤销" in row["text"] for row in rebound.packet["sources"])
    assert rebound.claims[0]["evidence"][0]["quote"] == COMPLETE


@pytest.mark.parametrize("mutation", ["remove", "other_field", "revision", "facts", "message"])
def test_final_rebind_cannot_switch_evidence_or_runtime_context(mutation):
    engine, session = _frame()
    body = _body()
    packet = _packet(engine, session, body)
    batch = discovery.parse_discoveries(_items(packet), packet)
    message = "请工作人员核对。"
    if mutation == "remove":
        body["scene_narration"] = "一切已经办妥。"
    elif mutation == "other_field":
        body["performance"] += body.pop("scene_narration")
    elif mutation == "revision":
        session = replace(session, revision=session.revision + 1)
    elif mutation == "facts":
        session = replace(session, story_state={**session.story_state, "facts": {
            **session.story_state["facts"], KEY: {"value": False, "visibility": "public"}}})
    else:
        message = "新的玩家原话。"
    with pytest.raises(NumericV2RuntimeError):
        discovery.rebind_final(batch, _packet(engine, session, body, message))
