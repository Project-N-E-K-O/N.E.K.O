"""Review and Actor must select the same optional author direction without changing phase budgets."""

from copy import deepcopy
import json

import pytest

from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_evaluator import _build_transition_judge_messages
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story


def _setup(beat_updates, *, target_direction=None):
    story = numeric_v2_story()
    beat = story["nodes"][0]["story_beat"]
    beat.update(
        opening_scene="柜台上摆着尚待检查的物件。",
        summary="围绕眼前的问题展开互动。",
        narrative_focus="回应当前疑问，保留玩家自己的决定。",
    )
    beat.update(beat_updates)
    if target_direction is not None:
        story["nodes"][2]["story_beat"]["narrative_summary"] = target_direction
    engine = NumericV2Engine.from_mapping(story)
    assert all(engine.nodes["start"]["story_beat"][key] == value for key, value in beat.items())
    session = engine.create_session(
        session_id="review-direction", catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"scene_narration": beat["opening_scene"],
                             "performance": "先看看眼前的情况。", "suggested_inputs": []},
    )
    return engine, session


def _payload(messages):
    content = messages[1].content
    return prompt_payload(content)


def _review(engine, session, *, opening=False, outcome=None):
    messages, _ = _build_transition_judge_messages(
        engine, session, player_input="" if opening else "请说明眼前的情况。",
        actor_performance={"performance": "先检查清楚，再由你决定。", "suggested_inputs": []},
        route_changed=opening or outcome is not None, transition_outcome=outcome,
    )
    return _payload(messages)


@pytest.mark.parametrize('opening,direction', [('修理铺里，未修好的钟摆在工作台上，师傅正在检查齿轮。', '师傅说明缺少的零件与费用，玩家决定是否委托；双方达成约定后才进行修理，不能预写钟已经修好。')])
@pytest.mark.parametrize("opening_review", [False, True])
def test_optional_direction_agrees_with_actor_in_ordinary_and_opening_review(opening, direction, opening_review):
    engine, session = _setup({"opening_scene": opening, "narrative_summary": direction})
    original = deepcopy(session.to_dict())
    turn = TurnRequestV2("observe", 0, "请说明眼前的情况。")
    outcome = engine.resolve_turn(session, turn, ())
    actor = _payload(_turn_messages(engine, session, outcome, turn.message, "温和克制。", "测试猫娘", "哥哥"))
    review = _review(engine, session, opening=opening_review)

    assert direction in actor["current_scene"]
    assert review["current_scene"]["story_direction"] == direction
    assert review["current_scene"]["opening_situation"] == opening
    assert direction not in json.dumps(review["scene_context"], ensure_ascii=False)
    assert session.to_dict() == original


def test_formal_ending_keeps_existing_source_and_target_direction_projection():
    source_direction = "角色回应最后的问题，事情解决后才自然收束。"
    target_direction = "角色依照既有关系告别，不为结束安排新的任务。"
    engine, session = _setup({"narrative_summary": source_direction}, target_direction=target_direction)
    outcome = engine.resolve_turn(
        session, TurnRequestV2("finish", 0, "谢谢你，事情处理好了。"), (),
        scene_complete=True, natural_ending_ready=True,
    )
    assert outcome.session.status == "ended"
    review = _review(engine, session, outcome=outcome)
    assert review["current_scene"]["story_direction"] == source_direction
    assert review["target_scene"]["story_direction"] == target_direction
