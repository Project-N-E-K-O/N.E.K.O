"""Actor receives author permissions without turning them into goals or completed acts."""

import json

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding


def _contract(allowed, *, restricted=False, dialogue="optional"):
    """The synthetic author grants only this role's actions, with explicit limits."""
    return {
        "cognition_state": "limited" if restricted else "normal",
        "memory_state": "partial" if restricted else "available",
        "self_reference_mode": "system_neutral" if restricted else "persona_allowed",
        "persona_scope": "style_only" if restricted else "full",
        "dialogue_policy": dialogue,
        "assertable_self_facts": ["我能看到眼前的指示灯"],
        "allowed_behaviors": allowed,
        "forbidden_behaviors": ["不得替玩家打开封条"],
    }


def _messages(phase, *, restricted=False):
    story = numeric_v2_story()
    source = story["nodes"][0]["story_beat"]
    target = story["nodes"][2]["story_beat"]
    source["acting_contract"] = _contract(["取得保管人许可后，自主检查指示灯"], restricted=restricted)
    target["acting_contract"] = _contract(["在大厅自主整理已公开的记录"])
    engine = NumericV2Engine.from_mapping(story)
    if phase == "opening":
        return actor._opening_messages(engine, "性格: 谨慎", "测试猫娘", "哥哥", max_tokens=10000)
    session = engine.create_session(session_id="permissions", catgirl_binding=_binding(),
        opening_performance={"performance": "先看看吧。", "suggested_inputs": []})
    formal = phase == "transition"
    message = "我同意按约定见面。" if formal else "这个灯怎么了？"
    outcome = engine.resolve_turn(session, TurnRequestV2("permission", 0, message), (),
        scene_complete=formal, natural_ending_ready=formal)
    assert (outcome.session.current_node_id != session.current_node_id) is formal
    return actor._turn_messages(engine, session, outcome, message, "性格: 谨慎", "测试猫娘", "哥哥")


def _data(messages):
    return prompt_payload(messages[1].content)


@pytest.mark.parametrize('phase', ['turn', 'transition'])
def test_author_permission_reaches_each_actor_phase_without_implying_completion(phase):
    messages = _messages(phase)
    data = _data(messages)
    if phase == "turn":
        assert "取得保管人许可后，自主检查指示灯" in data["role"]
        assert "可选权限，不是待办或已完成事实" in data["role"]
        assert "在大厅自主整理已公开的记录" not in data["role"]
    else:
        contract = data["acting_context"]["acting_contract"]
        assert contract["allowed_behaviors"] == ["取得保管人许可后，自主检查指示灯"]
        if phase == "transition":
            assert data["acting_context"]["target_acting_contract"]["allowed_behaviors"] == [
                "在大厅自主整理已公开的记录"]
    system = messages[0].content
    assert "允许行为是条件成立后的可选权限，不是已执行事实或逐项任务" in system
    assert "不能据此替玩家作出决定" in system


@pytest.mark.parametrize('phase', ['turn'])
def test_permission_does_not_restore_restricted_identity_or_erase_boundaries(phase):
    messages = _messages(phase, restricted=True)
    data = _data(messages)
    serialized = json.dumps(data, ensure_ascii=False)
    assert "取得保管人许可后，自主检查指示灯" in serialized
    # Ordinary turns keep prohibitions in the shared system boundary block.
    assert "不得替玩家打开封条" in serialized + messages[0].content
    assert "回乡整理旧屋的年轻男性" not in serialized
    assert "经营花店、保留旧信" not in serialized
    if phase == "turn":
        assert "认知与记忆限制：limited、partial" in data["role"]
    else:
        assert data["acting_context"]["acting_contract"]["persona_scope"] == "style_only"
