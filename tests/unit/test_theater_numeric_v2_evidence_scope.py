"""Delivered evidence retains its source without becoming an omniscient account."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater import numeric_v2_actor as actor, numeric_v2_context as context
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_author_entry_plan import _case


def _payload(messages):
    text = messages[1].content
    return prompt_payload(text)


@pytest.mark.parametrize("formal", [False, True])
def test_actual_history_keeps_uncertain_speech_and_test_evidence_without_rewriting(formal):
    engine, session = _case()
    hypothesis = "也许只是临时接触不良，我还不能判断原因。"
    observation = "管理员只观察到屏幕没有亮起，尚未启动测试。"
    performed = "测试员启动检查，记录的温度读数为十八度。"
    state = deepcopy(session.story_state)
    state["revision"] = 1
    session = replace(session, revision=1, node_turn_count=1,
        transition_offered=formal, story_state=state, performance_history=({
            "revision": 1, "from_node_id": "start", "to_node_id": "start",
            "input_text": "我还没有检查。", "scene_narration": observation + performed,
            "performance": hypothesis, "suggested_inputs": [],
        },))
    before = deepcopy(session.to_dict())
    message = "好，先进去。" if formal else "能确定原因了吗？"
    outcome = engine.resolve_turn(session, TurnRequestV2("evidence", 1, message), (),
        transition_intent="accept" if formal else "unclear")
    messages = actor._turn_messages(engine, session, outcome, message, "谨慎。", "测试猫娘", "哥哥")
    data = _payload(messages)
    history = json.dumps(data.get("recent_context", data.get("story_so_far")), ensure_ascii=False)
    assert hypothesis in history
    assert observation in history
    assert performed in history
    assert data["player_input"] == message
    assert session.to_dict() == before
    if formal:
        assert context.EVIDENCE_SCOPE_RULE in messages[0].content
    else:
        assert "传闻、猜测与假设保留来源和不确定，不升为客观事实" in messages[0].content
        assert "未操作不等于已测试失败" in messages[0].content
        assert "有限推断须有已公开世界规则和实际证据" in messages[0].content
        assert "有权限的在场者可首次给出确知答复" in messages[0].content
