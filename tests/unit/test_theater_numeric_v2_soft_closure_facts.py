"""Soft completion cannot override the author's pending fact or display contract."""

from copy import deepcopy

import pytest

from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding


KEY = "scene:start:rest_completed"
QUESTION = "（看着窗外的街景）喝完这个，咱们接下来去哪？"


def _setup(*, contract="fact", displayed=False):
    story = numeric_v2_story()
    middle = deepcopy(story["nodes"][0])
    middle.update(id="middle", type="scene")
    for route in middle["route_gates"]:
        route["id"] = "middle_" + route["id"]
    story["nodes"].append(middle)
    source = story["nodes"][0]
    source["route_gates"][0]["target_node_id"] = "middle"
    source["route_gates"][0]["conditions"]["all"][0]["value"] = 0
    source["route_gates"][0]["transition_contract"]["fallback_offer"] = "要一起去下一处看看吗？"
    if contract == "fact":
        story["fact_contract"] = {"facts": {KEY: {
            "value_type": "bool", "visibility": "public",
            "description": "双方结束休息，决定下一站去向。",
        }}}
        source["completion_contract"] = {"all": [{"key": KEY, "equals": True}]}
    elif contract == "display":
        source["story_beat"]["fixed_narrations"] = [{
            "id": "record", "text": "咖啡馆记录：下一站由玩家决定。",
            "trigger": {"type": "entry"} if displayed else {
                "type": "condition", "condition": "玩家打开记录。",
            },
            "after": [], "required_before_exit": True,
        }]
        source["completion_contract"] = {"all": [{"fixed_narration_id": "record"}]}
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(
        session_id="soft-closure", catgirl_binding=_binding(),
        opening_performance={"performance": "这杯咖啡不错。", "suggested_inputs": []},
    )
    return engine, session


def _messages(engine, session, *, complete=True):
    before = deepcopy(session.to_dict())
    outcome = engine.resolve_turn(
        session, TurnRequestV2("question", session.revision, QUESTION, input_source="suggestion"),
        (), scene_complete=complete,
    )
    messages = _turn_messages(
        engine, session, outcome, QUESTION, "温和。", "小葵", "老谢", input_source="suggestion",
    )
    assert session.to_dict() == before
    assert outcome.session.current_node_id == "start"
    assert outcome.ledger_event["scene_complete"] is complete
    payload = prompt_payload(messages[1].content)
    assert payload["player_input"] == QUESTION
    assert "与自由输入适用同一行动授权" in messages[0].content
    return messages[0].content, payload


@pytest.mark.parametrize("value", [None, False])
def test_soft_completion_does_not_declare_pending_fact_complete(value):
    engine, session = _setup()
    if value is not None:
        session = engine.resolve_turn(
            session, TurnRequestV2("not-done", 0, "还没休息好。"), (),
            fact_operations=({"op": "set", "key": KEY, "value": value, "visibility": "public"},),
        ).session
    system, payload = _messages(engine, session)
    assert '"status":"pending"' in payload["current_scene"]
    assert KEY in payload["current_scene"]
    assert "仍缺事实时不得提前提议" in system
    assert "本轮自然收束合同" not in system
    assert "本轮确定性完成收束合同" not in system
    assert "核心变化已成立" not in system
    assert "按自然收束合同回应" not in payload["pacing"]


@pytest.mark.parametrize('complete', [False])
def test_satisfied_fact_still_uses_deterministic_completion_closure(complete):
    engine, session = _setup()
    session = engine.resolve_turn(
        session, TurnRequestV2("done", 0, "我们休息好了。"), (),
        fact_operations=({"op": "set", "key": KEY, "value": True, "visibility": "public"},),
    ).session
    system, payload = _messages(engine, session, complete=complete)
    assert '"status":"satisfied"' in payload["current_scene"]
    assert "本轮确定性完成收束合同" in system
    assert "按确定性完成收束合同回应" in payload["pacing"]
    assert "不得写成玩家已接受" in system
    assert "本轮自然收束合同" not in system


@pytest.mark.parametrize("displayed", [False, True])
def test_soft_completion_respects_actual_required_text_display(displayed):
    engine, session = _setup(contract="display", displayed=displayed)
    system, payload = _messages(engine, session)
    assert "程序展示完成条件" in payload["current_scene"]
    assert f'"displayed":{int(displayed)}' in payload["current_scene"]
    assert ("本轮确定性完成收束合同" in system) is displayed
    assert "本轮自然收束合同" not in system
    assert "只由真实展示记录决定" in payload["current_scene"]
