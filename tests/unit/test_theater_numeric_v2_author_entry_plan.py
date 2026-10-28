"""Author staging stays available without becoming evidence of player actions."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_evaluator import _build_transition_judge_messages
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_behavior_permissions import _contract
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding


def _payload(messages):
    text = messages[1].content
    return prompt_payload(text)


def _case(*, restricted=False):
    story = deepcopy(_engine(ordinary=True).story)
    for node in story["nodes"]:
        node["story_beat"]["character_state"] = {
            "catgirl_state": "女主左手已有绷带，仍能观察眼前物件。",
            "player_state": "男主留在门边，尚未领取工具。",
            "environment_state": "环境中有一张工作台。",
            "continuity_from_previous": [],
            "scene_boundaries": [],
        }
        node["story_beat"]["acting_contract"] = _contract([], restricted=restricted)
    story["nodes"][0]["story_beat"]["opening_scene"] = "门边的工作台旁，女主左手已有绷带。"
    target = next(node for node in story["nodes"] if node["id"] == "middle")
    target["story_beat"]["character_state"]["player_state"] = "男主拿着测试仪，正在校验线路。"
    target["story_beat"]["opening_scene"] = "工具间的工作台上有尚未使用的测试仪。"
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(
        session_id="author-entry-plan", catgirl_binding=_binding(),
        opening_performance={"scene_narration": story["nodes"][0]["story_beat"]["opening_scene"],
                             "performance": "我们可以先在门边商量。", "suggested_inputs": []},
    )
    return engine, session


@pytest.mark.parametrize('mode', ['ordinary'])
def test_review_separates_author_plan_from_actual_opening_and_keeps_initial_injury(mode):
    engine, session = _case()
    before = deepcopy((engine.story, session.to_dict()))
    messages, _ = _build_transition_judge_messages(
        engine, session, player_input="你的手怎么样？",
        actor_performance={"performance": "左手包着绷带，但可以观察。", "suggested_inputs": []},
        route_changed=mode == "opening", completion_fact_review_only=mode == "completion",
    )
    data = _payload(messages)
    assert "authoritative_state" not in data["current_scene"]
    plan = data["current_scene"]["author_entry_plan"]
    assert plan["catgirl_state"] == "女主左手已有绷带，仍能观察眼前物件。"
    assert "左手已有绷带" in json.dumps(data["scene_context"], ensure_ascii=False)
    assert "作者预期的入幕起点" in messages[0].content
    assert "首幕明确给出的合法初始身份、伤势和环境" in messages[0].content
    assert (engine.story, session.to_dict()) == before


def test_ordinary_actor_labels_staging_without_losing_committed_continuous_action():
    engine, session = _case()
    actual = "你已领取测试仪并完成线路校验，把仪器放回工作台。"
    state = deepcopy(session.story_state)
    state["revision"] = 1
    session = replace(session, revision=1, node_turn_count=1, story_state=state, performance_history=({
        "revision": 1, "from_node_id": "start", "to_node_id": "start",
        "input_text": "我取下测试仪，检查完就放回去。", "scene_narration": actual,
        "performance": "读数已经核对好了。", "suggested_inputs": [],
    },))
    before = deepcopy(session.to_dict())
    turn = TurnRequestV2("continue", 1, "接下来呢？")
    outcome = engine.resolve_turn(session, turn, ())
    messages = actor._turn_messages(engine, session, outcome, turn.message, "谨慎。", "测试猫娘", "哥哥")
    data = _payload(messages)
    assert "作者入幕规划中的玩家处境：男主留在门边，尚未领取工具。" in data["current_scene"]
    assert "玩家开场演完后的已知处境" not in data["role"]
    assert actual in data["story_so_far"]
    assert "须与实际开场及已提交历史核对" in data["current_scene"]
    assert session.to_dict() == before


@pytest.mark.parametrize('message', ['好，我们进去；先不操作。'])
def test_accepting_movement_does_not_promote_target_player_plan_to_review_evidence(message):
    engine, session = _case()
    session = replace(session, transition_offered=True)
    outcome = engine.resolve_turn(session, TurnRequestV2("move", 0, message), (), transition_intent="accept")
    assert outcome.session.current_node_id == "middle"
    candidate = {"segments": [
        {"phase": "source_response", "performance": ""},
        {"phase": "transition_bridge", "scene_narration": "两人来到工具间。"},
        {"phase": "target_opening", "scene_narration": "测试仪放在工作台上。",
         "performance": "先看看要做哪一步。"},
    ], "suggested_inputs": []}
    messages, _ = _build_transition_judge_messages(
        engine, session, player_input=message, actor_performance=candidate,
        route_changed=True, transition_outcome=outcome,
    )
    data = _payload(messages)
    assert data["target_scene"]["author_entry_plan"]["player_state"] == "男主拿着测试仪，正在校验线路。"
    assert "character_state" not in data["target_scene"]
    assert "正在校验线路" not in json.dumps(data["scene_context"], ensure_ascii=False)
    assert "正在校验线路" not in json.dumps(data["candidate_segments"], ensure_ascii=False)
    assert data["player_input"] == message
    assert "状态描述不授权新增玩家行动" in messages[0].content
    assert "作者预期的入幕起点" in messages[0].content


@pytest.mark.parametrize('phase', ['opening', 'transition'])
def test_author_plan_labels_do_not_restore_restricted_identity_or_secrets(phase):
    engine, session = _case(restricted=True)
    if phase == "opening":
        messages = actor._opening_messages(engine, "谨慎。", "测试猫娘", "哥哥", max_tokens=10000)
    else:
        session = replace(session, transition_offered=phase == "transition")
        outcome = engine.resolve_turn(
            session, TurnRequestV2("next", 0, "先进去。"), (),
            transition_intent="accept" if phase == "transition" else "unclear",
        )
        messages = actor._turn_messages(engine, session, outcome, "先进去。", "谨慎。", "测试猫娘", "哥哥")
    text = json.dumps(_payload(messages), ensure_ascii=False)
    assert "回乡整理旧屋的年轻男性" not in text
    assert "经营花店、保留旧信" not in text
    assert "左手已有绷带" in text
    assert "limited" in text and "partial" in text
