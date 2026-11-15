"""Combined entry output preserves the target dialogue and ownership contracts."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError, _parse_output
from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_fixed_narration import add_entry
from tests.unit.test_theater_numeric_v2_entry_actor_sequence import COMBINED_CASES
from tests.unit.test_theater_numeric_v2_entry_trigger_order import _case
from tests.utils.theater_prompt import prompt_payload
from utils.tokenize import count_tokens


@pytest.mark.parametrize("topic", COMBINED_CASES)
@pytest.mark.parametrize("known", [False, True])
@pytest.mark.parametrize("tail", [False, True])
def test_program_entry_input_matches_actual_renderer_once_with_ordered_placeholders(topic, known, tail):
    engine, session, outcome, _ = _case(topic)
    session = replace(session, player_address_known=known)
    outcome = replace(outcome, session=replace(outcome.session, player_address_known=known))
    target = engine.nodes[outcome.session.current_node_id]
    target["story_beat"]["character_state"] = {"catgirl_state": "作者预期的入幕状态。"}
    target["story_beat"]["fixed_narrations"] = [
        {"id": f"entry_{index}", "text": text, "trigger": {"type": "entry"},
         "after": [], "required_before_exit": True}
        for index, text in enumerate([
            "原文甲：{{catgirl_name}}向{{player_name}}说明记录。\n编号A-007保留。",
            "原文乙：'先记录，再继续。'——{{catgirl_name}}",
        ])
    ] + [{"id": "conditional", "text": "尚未触发的私密原文不能公开。",
          "trigger": {"type": "condition", "condition": "角色揭开蓝色封签。"},
          "after": [], "required_before_exit": False}]
    before = deepcopy((engine.story, session.to_dict(), outcome.session.to_dict()))
    messages = actor._turn_messages(engine, session, outcome, "我只同意过去，不授权替我检查。", "沉着。", "小岚", "林舟",
        player_address_known=known, **({"transition_source": {"performance": "那就过去吧。"}} if tail else {}))
    data = prompt_payload(messages[1].content)
    delivery = data["transition"]["program_entry_delivery"]
    expected = [{key: piece[key] for key in ("text", "position")}
                for piece in add_entry(target, {}, outcome.session.catgirl_binding, known,
                                       session=outcome.session)["fixed_narrations"]]
    draft = {"source_performance": "", "bridge_scene_narration": "", "target_scene_narration": topic[3],
             "target_performance": topic[4], "suggested_inputs": []}
    rendered = engine.finalize_transition_performance(outcome, draft, target_opening=topic[1],
        bridge_required=False, source_dialogue_policy="optional", target_dialogue_policy="required")
    assert delivery == {"status": "scheduled_this_draft", "after": "target_entry_trigger",
                        "before": "target_performance", "fixed_narrations": expected}
    assert json.dumps(delivery["fixed_narrations"], ensure_ascii=False).encode() == json.dumps(
        [{key: piece[key] for key in ("text", "position")} for piece in
         rendered["segments"][2]["fixed_narrations"]], ensure_ascii=False).encode()
    assert [piece["text"] for piece in delivery["fixed_narrations"]] == [piece["text"] for piece in expected]
    assert all(set(piece) == {"text", "position"} for piece in delivery["fixed_narrations"])
    serialized = json.dumps(data, ensure_ascii=False)
    for piece in expected:
        assert piece["text"] not in messages[0].content
        assert serialized.count(json.dumps(piece["text"], ensure_ascii=False)) == 1
    assert "尚未触发的私密原文不能公开" not in "\n".join(message.content for message in messages)
    assert "角色揭开蓝色封签" in messages[0].content
    scope = data["transition"]["generation_scope"]
    assert scope["program_delivery"] == {"ref": "transition.program_entry_delivery", "status": "scheduled_this_draft"}
    assert "transition.target_scene.author_entry_plan" in scope["author_reference_refs"]
    assert not any("program_entry_delivery" in ref for ref in scope["author_reference_refs"])
    assert data["player_input"] == "我只同意过去，不授权替我检查。"
    assert sum(count_tokens(message.content) for message in messages) <= 10_000
    assert (engine.story, session.to_dict(), outcome.session.to_dict()) == before


@pytest.mark.parametrize("kind", ["fixed_entry_only", "condition_only", "already_displayed", "ordinary", "compat_response", "compat_prefix"])
def test_program_entry_binding_does_not_change_other_delivery_modes(kind):
    topic = COMBINED_CASES[0]
    engine, session, outcome, _ = _case(topic, fixed_entry_only=kind == "fixed_entry_only")
    target = engine.nodes[outcome.session.current_node_id]
    kwargs = {}
    if kind == "condition_only":
        target["story_beat"]["fixed_narrations"][0]["trigger"] = {"type": "condition", "condition": "角色揭开封签。"}
    elif kind == "already_displayed":
        session = replace(session, opening_performance=add_entry(target, {}, session.catgirl_binding, True))
        outcome = replace(outcome, session=replace(outcome.session, opening_performance=session.opening_performance))
    elif kind == "ordinary":
        source = engine.nodes[session.current_node_id]
        source["story_beat"]["fixed_narrations"] = deepcopy(target["story_beat"]["fixed_narrations"])
        outcome = replace(outcome, session=session, ledger_event={**outcome.ledger_event, "to_node_id": session.current_node_id})
    elif kind == "compat_response":
        kwargs = {"entry_part": "response", "entry_prefix": {"status": "uncommitted_this_turn", "blocks": [
            {"type": "narration", "text": topic[2]}]}}
    elif kind == "compat_prefix":
        kwargs = {"entry_part": "prefix"}
    messages = actor._turn_messages(engine, session, outcome, "好。", "沉着。", "小岚", "林舟",
                                   fixed_entry_only=kind == "fixed_entry_only", **kwargs)
    data = prompt_payload(messages[1].content)
    if kind != "compat_prefix":
        assert "program_entry_delivery" not in data.get("transition", {})
    else:
        assert data["transition"]["program_entry_delivery"]["after"] == "target_scene_narration"
    assert "target_entry_trigger:string" not in messages[0].content
    if kind == "ordinary":
        assert topic[2] in messages[0].content
    elif kind == "condition_only":
        assert "角色揭开封签" in messages[0].content and topic[2] not in messages[0].content


@pytest.mark.parametrize("target", ["", "（看向桌面）"])
def test_combined_fixed_entry_cannot_drop_required_target_dialogue(target):
    payload = {"source_performance": "", "bridge_scene_narration": "门开了。",
               "target_scene_narration": "角色按下播放键。", "target_performance": target,
               "suggested_inputs": []}
    with pytest.raises(NumericV2ActorOutputError):
        _parse_output(json.dumps(payload, ensure_ascii=False), transition_required=True,
                      source_dialogue_policy="optional", target_dialogue_policy="required", entry_narration=True)


@pytest.mark.parametrize("policy", ["optional", "forbidden"])
def test_combined_fixed_entry_keeps_empty_target_when_contract_allows_it(policy):
    payload = {"source_performance": "", "bridge_scene_narration": "门开了。",
               "target_scene_narration": "", "target_performance": "", "suggested_inputs": []}
    parsed = _parse_output(json.dumps(payload, ensure_ascii=False), transition_required=True,
                          source_dialogue_policy="optional", target_dialogue_policy=policy, entry_narration=True)
    assert parsed["target_performance"] == ""


@pytest.mark.parametrize("unauthorized_field", ["player_authority", "fact_operations", "transition_authorized"])
def test_combined_entry_output_cannot_issue_player_or_runtime_approval(unauthorized_field):
    payload = {"source_performance": "", "bridge_scene_narration": "门开了。",
               "target_scene_narration": "", "target_performance": "我来说明已经展示的内容。",
               "suggested_inputs": [], unauthorized_field: True}
    with pytest.raises(NumericV2ActorOutputError):
        _parse_output(json.dumps(payload, ensure_ascii=False), transition_required=True,
                      source_dialogue_policy="optional", target_dialogue_policy="required", entry_narration=True)
