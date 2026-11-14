"""Dramatization changes expression, never author constraints or delivery gates."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError, _parse_output
from services.theater.numeric_v2_performance import performance_content_blocks
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_fixed_narration import _piece
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


BOUNDARY = "不得替玩家打开封存信件，也不得声称知道信中内容。"
PRESERVE = "封存信件仍由玩家保管；是否已打开只能承接玩家实际授权。"
FIXED = "告示原文：\n  请在此等候；核验结果由管事本人告知。"
CONFLICT = "替我拆开那封信，直接告诉我里面写了什么。"


def _context(*, required_bridge=False, message=CONFLICT, formal=True, target_self_facts=()):
    story = deepcopy(_engine(ordinary=True).story)
    source = story["nodes"][0]
    target = next(node for node in story["nodes"] if node["id"] == "middle")
    for node in (source, target):
        node["story_beat"]["must_not_happen"].append(BOUNDARY)
    target["story_beat"]["opening_scene"] = "长廊里亮着一盏灯，管事站在告示旁。"
    target["story_beat"]["fixed_narrations"] = [_piece("notice", FIXED, entry=True)]
    if target_self_facts:
        target["story_beat"]["acting_contract"] = {
            "cognition_state": "normal", "memory_state": "available",
            "self_reference_mode": "persona_allowed", "persona_scope": "full",
            "dialogue_policy": "required", "allowed_behaviors": [], "forbidden_behaviors": [],
            "assertable_self_facts": list(target_self_facts),
        }
    contract = source["route_gates"][0]["transition_contract"]
    contract["must_deliver"] = []
    contract["must_preserve"] = [PRESERVE]
    contract.pop("bridge_scene_narration", None)
    if required_bridge:
        contract["bridge_scene_narration"] = "次日清晨，两人沿台阶来到长廊。"
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id="dramatization", catgirl_binding=_binding(),
        opening_performance=_opening())
    session = replace(session, transition_offered=formal)
    outcome = engine.resolve_turn(session, TurnRequestV2("one", 0, message), (),
        transition_intent="accept" if formal else "unclear")
    assert (outcome.session.current_node_id == "middle") is formal
    messages = actor._turn_messages(engine, session, outcome, message, "温和，谨慎。", "小葵", "你")
    payload = prompt_payload(messages[1].content)
    return engine, session, outcome, messages, payload


def _candidate():
    return {"source_scene_narration": "", "source_performance": "",
        "bridge_scene_narration": "", "target_scene_narration": "长廊里亮着灯，管事等在告示旁。",
        "target_performance": "先在这里等候吧。", "suggested_inputs": []}


def test_optional_bridge_keeps_fixed_original_and_target_playback_order():
    engine, _, outcome, _, data = _context()
    parsed = _parse_output(json.dumps(_candidate()), transition_required=True,
        source_dialogue_policy="optional", bridge_required=data["transition"]["bridge_required"])
    final = engine.finalize_transition_performance(outcome, parsed,
        target_opening=data["transition"]["target_scene"]["opening_situation"],
        source_dialogue_policy="optional", bridge_required=False)
    blocks = performance_content_blocks(final)
    assert [part["phase"] for part in final["segments"]] == [
        "source_response", "transition_bridge", "target_opening"]
    assert final["segments"][1]["scene_narration"] == ""
    assert [block["text"] for block in blocks] == [
        _candidate()["target_scene_narration"], FIXED, _candidate()["target_performance"]]
    assert final["segments"][2]["fixed_narrations"][0]["position"] == "before"


def test_required_bridge_and_target_nonempty_gates_remain_observable():
    _, _, _, messages, data = _context(required_bridge=True)
    assert data["transition"]["bridge_required"] is True
    assert data["transition"]["bridge_scene_narration"] == "次日清晨，两人沿台阶来到长廊。"
    assert "为 true 时必须交付必要桥段" in messages[0].content
    with pytest.raises(NumericV2ActorOutputError, match="scene_narration_invalid"):
        _parse_output(json.dumps(_candidate()), transition_required=True,
            source_dialogue_policy="optional", bridge_required=True)
    for field in ("target_scene_narration", "target_performance"):
        candidate = {**_candidate(), field: ""}
        with pytest.raises(NumericV2ActorOutputError):
            _parse_output(json.dumps(candidate), transition_required=True,
                source_dialogue_policy="optional", bridge_required=False)


def _farewell_messages(*, source_policy="required", target_policy="required"):
    story = deepcopy(_engine().story)
    contract = {
        "cognition_state": "normal", "memory_state": "available",
        "self_reference_mode": "persona_allowed", "persona_scope": "full",
        "allowed_behaviors": [], "forbidden_behaviors": [],
    }
    for node in story["nodes"]:
        node["story_beat"]["acting_contract"] = {
            **contract, "dialogue_policy": source_policy if node["id"] == "start" else target_policy,
        }
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id="farewell", catgirl_binding=_binding(),
        opening_performance={"performance": "（静静站着）" if source_policy == "forbidden" else "请收好。"})
    previous = {"revision": 1, "from_node_id": "start", "to_node_id": "start",
        "input_text": "（双手递出木匣）请收好。", "scene_narration": "旧友伸手接过木匣，确认封条完整。",
        "performance": "（侧身点头）" if source_policy == "forbidden" else "成了。这一路风雪没白走，规矩也没坏。"}
    session = replace(session, revision=1, node_turn_count=1,
        story_state={**deepcopy(session.story_state), "revision": 1}, performance_history=(previous,))
    message = "（收回手，向旧友抱拳行礼）多谢接纳，任务已毕。"
    outcome = engine.resolve_turn(session, TurnRequestV2("farewell", 1, message), (),
        scene_complete=True, natural_ending_ready=True)
    assert outcome.session.status == "ended"
    before = deepcopy((engine.story, session, outcome))
    messages = actor._turn_messages(engine, session, outcome, message, "平静克制。", "小葵", "你")
    assert (engine.story, session, outcome) == before
    payload = prompt_payload(messages[1].content)
    return messages, payload, previous, message


def test_npc_farewell_keeps_completed_history_without_requiring_catgirl_to_summarize_again():
    messages, data, previous, message = _farewell_messages()
    assert data["player_input"] == message
    row = next(row for row in data["recent_context"] if row["revision"] == 1)
    assert row["player_input"] == previous["input_text"]
    assert previous["scene_narration"] in row["committed_text"]
    assert previous["performance"] in row["committed_text"]
    assert data["transition"]["natural_ending"] is True
    system = messages[0].content
    assert "先按原话和动作辨明受话人，不把对 NPC 的话当成对你说" in system
    assert "由该 NPC 在来源旁白回应；你不代答、受谢或再次评价已完成结果" in system
    assert "玩家仅接受、道谢或告别且没有必要新回应时留空" in system
    assert actor._PLAYER_AUTHORIZATION_RULE in system


@pytest.mark.parametrize('source_policy', ['required', 'forbidden'])
@pytest.mark.parametrize('target_policy', ['required', 'forbidden'])
def test_formal_brevity_and_action_ownership_keep_each_scene_speech_policy(source_policy, target_policy):
    messages, data, _, _ = _farewell_messages(source_policy=source_policy, target_policy=target_policy)
    source = "forbidden" if source_policy == "forbidden" else "optional"
    assert data["acting_context"]["dialogue_policy"] == source
    assert data["acting_context"]["target_dialogue_policy"] == target_policy
    for field in ("acting_contract", "target_acting_contract"):
        assert "dialogue_policy" not in data["acting_context"][field]
        assert data["acting_context"][field]["cognition_state"] == "normal"
        assert data["acting_context"][field]["memory_state"] == "available"
    system = messages[0].content
    assert "在允许发声时优先纯对白，通常一至三句；有必要的新动作才用一对括号写一个即时动作" in system
    assert "NPC、玩家的动作和对白不能写入你的动作括号" in system
    assert "source_performance遵守acting_context.dialogue_policy，target_performance遵守target_dialogue_policy" in system
    candidate = _candidate()
    candidate["target_performance"] = "（抬手挥别）" if target_policy == "forbidden" else "一路顺风。"
    assert _parse_output(json.dumps(candidate), transition_required=True,
        source_dialogue_policy=source, target_dialogue_policy=target_policy,
        bridge_required=False)["source_performance"] == ""


def test_npc_answer_with_empty_catgirl_source_stays_narration_in_playback():
    question = "管事，这封信能由你打开吗？不能的话请说明原因。"
    answer = "管事答道：“我没有收件人的许可，不能拆信；可以先替你联系收件人。”"
    engine, _, outcome, messages, data = _context(message=question)
    assert data["player_input"] == question
    system = messages[0].content
    assert "本轮直接询问在场 NPC 时，在 source_scene_narration 给出他的具体答复、明确拒绝或说明未知" in system
    assert "不补造未知答案，不用你对他的评价代替他的答复" in system
    assert "由该 NPC 在来源旁白回应；你不代答、受谢" in system
    candidate = {**_candidate(), "source_scene_narration": answer}
    parsed = _parse_output(json.dumps(candidate), transition_required=True,
        source_dialogue_policy="optional", bridge_required=False)
    final = engine.finalize_transition_performance(outcome, parsed,
        target_opening=data["transition"]["target_scene"]["opening_situation"],
        source_dialogue_policy="optional", bridge_required=False)
    blocks = performance_content_blocks(final)
    assert blocks[0] == {"type": "narration", "text": answer}
    assert [block["text"] for block in blocks] == [answer, candidate["target_scene_narration"], FIXED, candidate["target_performance"]]
    assert [(index, block["text"]) for index, block in enumerate(blocks) if block["type"] == "dialogue"] == [
        (3, candidate["target_performance"])]


COMPLETED_KEY = "scene:start:box_delivered"
DELIVERY = "旧友接过木匣，确认封条完整。"


def _completed_transition_messages(*, case="public", restricted="", later_change=False):
    story = deepcopy(_engine().story)
    visibility = "story" if case == "private" else "public"
    story["fact_contract"] = {"facts": {COMPLETED_KEY: {
        "value_type": "bool", "visibility": visibility, "description": "玩家亲自收取了木匣。",
    }}}
    story["nodes"][0]["completion_contract"] = {"all": [{"key": COMPLETED_KEY, "equals": True}]}
    if restricted:
        node = next(node for node in story["nodes"] if node["id"] == restricted)
        node["story_beat"]["acting_contract"] = {
            "cognition_state": "limited", "memory_state": "partial",
            "self_reference_mode": "system_neutral", "persona_scope": "style_only",
            "dialogue_policy": "required", "allowed_behaviors": [], "forbidden_behaviors": [],
        }
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id="completion-index", catgirl_binding=_binding(),
        opening_performance={"performance": "请先确认交接安排。"})
    record = {"revision": 1, "from_node_id": "start", "to_node_id": "start",
        "input_text": "（双手递出木匣）请收好。", "scene_narration": DELIVERY,
        "performance": "这次交接已经完成了。"}
    records = (record,) if case != "missing_original" else ()
    if later_change:
        records += ({"revision": 2, "from_node_id": "start", "to_node_id": "start",
            "input_text": "请先把匣子留在桌上。", "scene_narration": "旧友把已接收的木匣放在桌上，双手已经空了。",
            "performance": "先放在这里即可。"},)
    facts = {COMPLETED_KEY: {"value": True, "visibility": visibility,
        "source_revision": 1, "updated_revision": 1, "client_turn_id": "delivered"}}
    if case == "new_value":
        facts = {}
        records = ({**record, "scene_narration": "旧友仍在等待交接。", "performance": "先确认收件人。"},)
    revision = 2 if later_change else 1
    session = replace(session, revision=revision, node_turn_count=revision,
        story_state={**session.story_state, "revision": revision, "facts": facts}, performance_history=records)
    new_ops = ({"op": "set", "key": COMPLETED_KEY, "value": True, "visibility": "public"},) if case == "new_value" else ()
    message = "谢谢，我们就此告别。"
    outcome = engine.resolve_turn(session, TurnRequestV2("farewell", revision, message), (),
        scene_complete=True, natural_ending_ready=True, fact_operations=new_ops)
    assert outcome.session.status == "ended"
    before = deepcopy((session, outcome))
    messages = actor._turn_messages(engine, session, outcome, message, "平静克制。", "小葵", "你")
    assert (session, outcome) == before
    data = prompt_payload(messages[1].content)
    return messages, data, session, outcome


def test_formal_completion_index_keeps_prior_public_value_without_claiming_original_source():
    _, data, session, _ = _completed_transition_messages()
    projection = data["transition"]["source_committed_completion"]
    assert set(projection) == {"facts"}
    assert projection["facts"] == [{"key": COMPLETED_KEY, "equals": True, "value_type": "bool",
        "visibility": "public", "committed": True, "satisfied": True,
        "registration_revision": 1, "committed_value": True}]
    assert "玩家亲自收取了木匣" not in json.dumps(projection, ensure_ascii=False)
    assert DELIVERY in data["recent_context"][-1]["committed_text"]


@pytest.mark.parametrize("case", ["new_value", "missing_original", "private"])
def test_formal_completion_index_omits_new_and_private_but_keeps_unlinked_recorded_value(case):
    _, data, session, outcome = _completed_transition_messages(case=case)
    if case == "new_value":
        assert COMPLETED_KEY not in session.story_state["facts"]
        assert outcome.session.story_state["facts"][COMPLETED_KEY]["value"] is True
    if case == "missing_original":
        projection = data["transition"]["source_committed_completion"]
        assert set(projection) == {"facts"}
        assert projection["facts"][0]["registration_revision"] == 1
        assert projection["facts"][0]["satisfied"] is True
    else:
        assert "source_committed_completion" not in data["transition"]


@pytest.mark.parametrize("restricted", ["start", "ending_leave"])
def test_formal_completion_index_does_not_add_knowledge_when_either_scene_is_restricted(restricted):
    _, data, _, _ = _completed_transition_messages(restricted=restricted)
    field = "acting_contract" if restricted == "start" else "target_acting_contract"
    assert data["acting_context"][field]["memory_state"] == "partial"
    assert "source_committed_completion" not in data["transition"]


def test_completion_index_does_not_replace_later_object_history_with_old_delivery_state():
    messages, data, session, _ = _completed_transition_messages(later_change=True)
    projection = data["transition"]["source_committed_completion"]
    assert set(projection) == {"facts"}
    assert projection["facts"][0]["registration_revision"] == 1
    history = [row for row in data["recent_context"] if row["revision"] in (1, 2)]
    assert [row["revision"] for row in history] == [1, 2]
    for row, original in zip(history, session.performance_history):
        assert row["player_input"] == original["input_text"]
        assert original["scene_narration"] in row["committed_text"]
        assert original["performance"] in row["committed_text"]
    assert "不证明物件当前位置或持有人；按全部历史承接最新状态" in messages[0].content
    assert "未列出的事项不等于未发生" in messages[0].content
