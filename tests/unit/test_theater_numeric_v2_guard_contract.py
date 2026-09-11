"""验证 Guard 的字段归属和入口投影。"""

from __future__ import annotations

import json

import pytest

from services.theater.numeric_v2_evaluator import (
    NumericV2EvaluatorOutputError,
    _build_transition_judge_messages,
    _parse_transition_judge_output,
)
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story


def test_transition_deduplicates_only_complete_projected_target_boundaries():
    """完整重复禁令只传一次；超过摘要长度/数量的原文、正向授权和原包均须保留。"""
    from copy import deepcopy
    from services.theater.numeric_v2_runtime import TurnRequestV2
    from tests.unit.test_theater_numeric_v2_natural_ending import _engine

    engine = _engine()
    beat = engine.nodes['ending_leave']['story_beat']
    short = '不得新增旅程。'
    long = '不得隐瞒以下条件：' + '现场风险仍未排除。' * 100
    extras = [f'不得移走编号{i}的箱子。' for i in range(15)]
    beat['character_state'] = {'catgirl_state': '女主已停下脚步。', 'scene_boundaries': [short, long]}
    beat['acting_contract'] = {'forbidden_behaviors': extras, 'allowed_behaviors': ['表达感谢']}
    original = deepcopy(beat)
    session = engine.create_session(session_id='budget', catgirl_binding={'catgirl_name': '女主'}, opening_performance={'performance': '（点头）准备好了。'})
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '谢谢。'), (), scene_complete=True, natural_ending_ready=True)
    messages = _build_transition_judge_messages(engine, session, actor_performance={'segments': [], 'suggested_inputs': []}, player_input='谢谢。', transition_outcome=outcome)
    target = json.loads(messages[1].content.split('：', 1)[1])['target_scene']
    boundaries = target['hard_boundaries'] + target['character_state'].get('scene_boundaries', []) + target['acting_contract'].get('forbidden_behaviors', [])
    assert boundaries.count(short) == 1
    assert all(text in boundaries for text in [long, *extras])
    assert target['character_state']['catgirl_state'] == original['character_state']['catgirl_state']
    assert target['acting_contract']['allowed_behaviors'] == ['表达感谢']
    assert beat == original


def _verdict(**changes):
    """只有正文枚举与按钮索引是安全判断来源，不再维护重复总类。"""

    return {
        "offer_present": False,
        "valid": False,
        "body_violations": [],
        "unsafe_suggestion_indexes": [],
        "failure_reason": "",
        **changes,
    }


def _messages(candidate):
    """构造入口和目标幕后续方向不同的通用夹具，不读取正式故事或存档。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    source = engine.nodes["start"]
    source["story_beat"].update({
        "opening_scene": "玩家和猫娘站在档案接待台前，工作人员已确认预约。",
        "summary": "猫娘可以确认自己的预约；查档需要玩家接受前往阅览室的提议后开始。",
        "must_not_happen": ["不得替玩家取走桌上档案。"],
    })
    source["route_gates"][1]["transition_contract"].update({
        "reason": "预约已确认，可以邀请玩家前往阅览室查档。",
        "bridge_scene_narration": "两人从接待台来到阅览室。",
    })
    target = engine.nodes["ending_leave"]
    target["type"] = "scene"
    target["terminal"] = False
    target["story_beat"].update({
        "opening_scene": "阅览室检索终端停在目录页，查档尚未开始。",
        "narrative_focus": "查完档案后讨论回家。",
        "summary": "查完档案后讨论回家。",
    })
    session = engine.create_session(
        session_id="guard_scope_contract",
        catgirl_binding={"catgirl_id": "catgirl:test", "catgirl_name": "测试猫娘"},
        opening_performance={"performance": "（点头）预约已确认。", "suggested_inputs": []},
    )
    return _build_transition_judge_messages(
        engine,
        session,
        actor_performance=candidate,
        player_input="接下来怎么办？",
    )


# 只保留现行入口与 Prompt 检查使用的两份候选。
_BUTTON_ONLY_CANDIDATE = {
    "performance": "（点头）预约已经确认。",
    "suggested_inputs": ["我们去阅览室查档。"],
}
_BODY_OFFER_CANDIDATE = {
    "performance": "（看向玩家）我们去阅览室查档，好吗？",
    "suggested_inputs": [],
}


def test_guard_derives_safety_from_disjoint_fields():
    review = _parse_transition_judge_output(json.dumps(_verdict(
        body_violations=["player_action", "scene_boundary"],
        unsafe_suggestion_indexes=[1],
    )))

    assert review.body_violations == ("player_action", "scene_boundary")
    assert review.unsafe_suggestion_indexes == (1,)
    assert not review.player_action_preserved
    assert not review.scene_boundary_preserved
    assert not review.author_boundaries_preserved


@pytest.mark.parametrize("field", ["body_violations", "unsafe_suggestion_indexes"])
def test_guard_requires_scoped_fields(field):
    payload = _verdict()
    payload.pop(field)
    with pytest.raises(NumericV2EvaluatorOutputError):
        _parse_transition_judge_output(json.dumps(payload))


@pytest.mark.parametrize("changes", [
    {"body_violations": None},
    {"unsafe_suggestion_indexes": None},
    {"unsafe_suggestion_indexes": [True]},
    {"unsafe_suggestion_indexes": [0, 0]},
])
def test_guard_rejects_invalid_scoped_evidence(changes):
    with pytest.raises(NumericV2EvaluatorOutputError):
        _parse_transition_judge_output(json.dumps(_verdict(**changes)))


def test_guard_diagnostic_text_cannot_erase_body_evidence():
    review = _parse_transition_judge_output(json.dumps(_verdict(
        body_violations=["author_boundary"],
        failure_reason={"unexpected": "非字符串原因"},
    )))
    assert not review.author_boundaries_preserved
    assert review.failure_reason == ""


def test_guard_cannot_validate_absent_body_offer():
    review = _parse_transition_judge_output(json.dumps(_verdict(valid=True)))
    assert not review.offer_present
    assert not review.valid


def test_guard_uses_actual_entry_without_target_late_stage():
    messages = _messages(_BODY_OFFER_CANDIDATE)
    payload = json.loads(messages[1].content.split("：", 1)[1])
    direction = payload["next_scene_direction"]

    assert "after_acceptance_direction" not in direction
    assert "查完档案后讨论回家" not in messages[1].content
    assert direction["opening_boundary"] == "阅览室检索终端停在目录页，查档尚未开始。"
    assert direction["bridge_boundary"] == "两人从接待台来到阅览室。"
    assert "预约已确认" in direction["direction"]


def test_guard_offer_and_button_responsibilities_do_not_overlap():
    system = _messages(_BUTTON_ONLY_CANDIDATE)[0].content

    assert "正文与推荐组合" not in system
    assert "推荐中至少一条" not in system
    assert "body_violations：只列正文已写出的冲突" in system
    assert "offer_present：只看正文" in system
    assert "按钮不能创建、补足或否决正文提议" in system
    assert "首次提出正文尚未公开的跨阶段行动" in system
    assert "opening_boundary 与 bridge_boundary 是接受后的入口" in system
    assert "只用于发现已经偷跑" not in system


def test_guard_no_offer_does_not_skip_body_or_button_checks():
    """无转场不是正文或推荐安全结论，按钮要在正文判定后独立核对。"""

    system = _messages(_BUTTON_ONLY_CANDIDATE)[0].content
    assert "没有提议也须检查正文" in system
    assert system.index("4. unsafe_suggestion_indexes：逐条独立检查按钮") > system.index("3. valid")
    assert "仅按钮首提时应列索引，offer_present 与 valid 都为 false" in system


def test_guard_prompt_uses_actual_protocol_and_distinguishes_action_time():
    """五字段协议只讲一次；已做、尝试和未来邀请不混为同一时态。"""

    system = _messages(_BUTTON_ONLY_CANDIDATE)[0].content
    example, _ = json.JSONDecoder().raw_decode(system[system.index("{"):])
    assert set(example) == {"offer_present", "valid", "body_violations", "unsafe_suggestion_indexes", "failure_reason"}
    assert not _parse_transition_judge_output(json.dumps(example)).body_violations
    assert "_preserved" not in system
    assert "玩家已明确实施的同一动作可以被正文承接，不是 Actor 代做" in system
    # 口语执行规则放入共享合同后，尝试仍不能保证未知结果。
    assert "尝试也不保证未知的成功结果" in system
    assert "历史另一次操作也不授权本次结果" in system
    assert "未来邀请即使请求立即开始也不是已执行" in system
    assert "开场状态是入幕起点，后续状态承接历史和本轮已实施动作" in system
