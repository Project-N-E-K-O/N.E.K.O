"""Relative author time reaches writing/review without becoming committed history."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2 import NumericV2CompileError
from services.theater.numeric_v2_evaluator import _build_transition_judge_messages
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.unit.test_theater_numeric_v2_author_entry_plan import _case, _payload
from services.theater_workshop.sdk.generation.numeric_v2 import _validate_idea_outline
from tests.unit.theater_workshop.test_numeric_v2_generation import _idea_outline


TIME = {"anchor": "次日清晨", "relation_to_previous": "next_day"}


@pytest.mark.parametrize("formal", [False, True])
def test_time_plan_reaches_actor_and_review_without_rewriting_session(formal):
    source, session = _case()
    story = deepcopy(source.story)
    for node in story["nodes"]:
        node["story_beat"]["character_state"]["temporal_context"] = deepcopy(TIME)
    engine = NumericV2Engine.from_mapping(story)
    session = replace(session, story_package_hash=engine.compiled.package_hash, transition_offered=formal)
    message = "好，我们进去；先不操作。" if formal else "我们先谈谈明天的安排。"
    turn = TurnRequestV2("time-plan", session.revision, message)
    outcome = engine.resolve_turn(session, turn, (), transition_intent="accept" if formal else "unclear")
    before = deepcopy(session.to_dict())
    candidate = {"performance": "明天再说，先留在这里。", "suggested_inputs": []}
    kwargs = {"transition_outcome": outcome} if formal else {}
    review = _payload(_build_transition_judge_messages(
        engine, session, player_input=message, actor_performance=candidate, **kwargs,
    )[0])
    messages = actor._turn_messages(engine, session, outcome, message, "温和。", "小岚", "你")
    data = _payload(messages)
    assert review["current_scene"]["author_entry_plan"]["temporal_context"] == TIME
    if formal:
        assert data["transition"]["target_scene"]["author_entry_plan"]["temporal_context"] == TIME
        assert review["target_scene"]["author_entry_plan"]["temporal_context"] == TIME
    else:
        assert "作者入幕时序规划：" in data["current_scene"]
        assert "次日清晨" in data["current_scene"] and "next_day" in data["current_scene"]
    assert ("不是Runtime已推进日期的回执" if formal else
            "temporal_context只规划入幕时序，不证明当前已经跨日") in messages[0].content
    assert "关系上限不是已达关系" in messages[0].content
    assert session.to_dict() == before
    assert "temporal_context" not in json.dumps(review["scene_context"], ensure_ascii=False)


@pytest.mark.parametrize("value", [None, {}, {"anchor": "", "relation_to_previous": "next_day"},
                                    {"anchor": "明天", "relation_to_previous": "auto"}])
def test_sdk_and_native_compiler_reject_malformed_optional_time(value):
    outline = _idea_outline(3)
    outline["character_state_arc"]["stages"][0]["temporal_context"] = deepcopy(value)
    issues = _validate_idea_outline(outline, minimum=3, maximum=6)
    assert any(i["code"] == "character_state_temporal_context_invalid" for i in issues)
    engine, _ = _case()
    story = deepcopy(engine.story)
    story["nodes"][0]["story_beat"]["character_state"]["temporal_context"] = deepcopy(value)
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Engine.from_mapping(story)
    assert any(i.code == "character_state_temporal_context_invalid" for i in caught.value.issues)
