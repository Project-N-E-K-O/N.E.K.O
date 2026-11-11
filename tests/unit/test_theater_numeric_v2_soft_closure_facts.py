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
SELECTED_OFFER = "要一起去下一处看看吗？"
OTHER_OFFER = "要改去另一座庭院吗？"


def _setup(*, contract="fact", displayed=False, ending=False, entry_text=None):
    story = numeric_v2_story()
    middle = deepcopy(story["nodes"][0])
    middle.update(id="middle", type="scene")
    for route in middle["route_gates"]:
        route["id"] = "middle_" + route["id"]
    if not ending:
        story["nodes"].append(middle)
    source = story["nodes"][0]
    source["route_gates"][0]["target_node_id"] = "ending_stay" if ending else "middle"
    source["route_gates"][0]["conditions"]["all"][0]["value"] = 0
    source["route_gates"][0]["transition_contract"]["fallback_offer"] = SELECTED_OFFER
    source["route_gates"][1]["transition_contract"]["fallback_offer"] = OTHER_OFFER
    if contract == "fact":
        story["fact_contract"] = {"facts": {KEY: {
            "value_type": "bool", "visibility": "public",
            "description": "双方结束休息，决定下一站去向。",
        }}}
        source["completion_contract"] = {"all": [{"key": KEY, "equals": True}]}
    elif contract == "display":
        source["story_beat"]["fixed_narrations"] = [{
            "id": "record", "text": entry_text or "咖啡馆记录：下一站由玩家决定。",
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


def _messages(engine, session, *, complete=True, retry_hint=""):
    before = deepcopy(session.to_dict())
    outcome = engine.resolve_turn(
        session, TurnRequestV2("question", session.revision, QUESTION, input_source="suggestion"),
        (), scene_complete=complete,
    )
    messages = _turn_messages(
        engine, session, outcome, QUESTION, "温和。", "小葵", "老谢",
        input_source="suggestion", retry_hint=retry_hint,
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


def test_completion_candidate_does_not_allow_an_invitation_on_ending_route():
    engine, session = _setup(ending=True)
    system, payload = _messages(engine, session)

    assert KEY in payload["current_scene"]
    assert "本轮 transition_offer 必须为空字符串" in system
    assert "候选可补足本轮提出邀请的完成前提" not in system


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


@pytest.mark.parametrize("entry_text,already_public", [
    ("留在这里，或者去下一处，你想选哪种？", False),
    (f"小葵抬头问你：{OTHER_OFFER}", False),
    (f"小葵抬头问你：{SELECTED_OFFER}", True),
])
def test_displayed_entry_only_suppresses_the_selected_invitation(entry_text, already_public):
    engine, session = _setup(contract="display", displayed=True, entry_text=entry_text)
    system, payload = _messages(engine, session, complete=False)

    assert ("已实际公开当前出口的邀约" in system) is already_public
    assert ("公开一个具体的未来跨阶段行动" in system) is not already_public
    assert payload["next_scene"].count(SELECTED_OFFER) == int(not already_public)
    assert OTHER_OFFER not in payload["next_scene"]
    assert "好，就按这个安排" not in payload["next_scene"]


def test_repair_draft_does_not_gain_author_invitation():
    engine, session = _setup(contract="display", displayed=True)
    system, payload = _messages(engine, session, retry_hint="只改重复的第一句。")

    assert "本轮是输出重试" in system
    assert SELECTED_OFFER not in payload["next_scene"]
