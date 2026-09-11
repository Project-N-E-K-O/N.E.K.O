"""Deliver stage boundaries, public destinations and latest state to real consumers together; replay model semantics separately."""

import json

from services.theater.numeric_v2_actor import _opening_messages, _suggestion_fill_messages, _turn_messages
from services.theater.numeric_v2_evaluator import _build_messages, _build_transition_judge_messages
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


def test_formal_transition_keeps_stage_scope_and_actual_state_for_both_consumers():
    engine = _engine()
    source = engine.nodes['start']['story_beat']
    target = engine.nodes['ending_leave']['story_beat']
    source['must_not_happen'] = ['不得在来源测试阶段让参观者操作。', '不得损坏样品。']
    target['must_not_happen'] = ['不得替玩家选择体验者。', '不得损坏样品。']
    session = engine.create_session(session_id='stage_state', catgirl_binding=_binding(),
        opening_performance={**_opening(), 'scene_narration': '样品已停止运转，放在工作台上；两人一直同行。'})
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '按约定办。'), (),
        scene_complete=True, natural_ending_ready=True)
    candidate = engine.finalize_transition_performance(outcome, _candidate(), target_opening='旧模板。')
    actor = _turn_messages(engine, session, outcome, '按约定办。', '温和。', '测试猫娘', '哥哥')
    review = _build_transition_judge_messages(engine, session, player_input='按约定办。',
        actor_performance=candidate, route_changed=True, transition_outcome=outcome)
    for messages in (actor, review):
        assert '放在工作台上' in messages[1].content
        assert '不得损坏样品' in messages[1].content
        assert '有阶段限定的来源禁令不延伸到目标段' in messages[0].content
    assert '工作台' not in actor[0].content  # 通用规则不能写入本反例的道具或地点。
    data = json.loads(review[1].content.split('：', 1)[1])
    assert source['must_not_happen'] == data['current_scene']['hard_boundaries']
    assert target['must_not_happen'] == data['target_scene']['hard_boundaries']


def test_acceptance_still_checks_actual_exit_in_evaluator_contract():
    engine = _engine()
    session = engine.create_session(session_id='accept_scope', catgirl_binding=_binding(),
        opening_performance=_opening())
    messages = _build_messages(engine, session, '好，去刚才说的展示室。')
    assert 'accept 同样须核对原邀请与实际出口的地点、时段和行动' in messages[0].content
    assert '不相符时判 unclear' in messages[0].content
    assert '没有 pending_transition 时，若此前已上路而节点未切换' in messages[0].content
    assert '同方向的下一步是 accept，不判 initiate' in messages[0].content


def test_player_fact_rule_reaches_all_suggestion_generation_paths():
    """Main suggestions, transitions, openings and refills must not invent player names or abilities to complete personal information."""
    engine = _engine()
    session = engine.create_session(session_id='suggestion_facts', catgirl_binding=_binding(),
        opening_performance=_opening())
    message = '请问需要登记哪些信息？'
    ordinary = engine.resolve_turn(session, TurnRequestV2('one', 0, message), (), scene_complete=False)
    transition = engine.resolve_turn(session, TurnRequestV2('two', 0, '按约定办。'), (),
        scene_complete=True, natural_ending_ready=True)
    messages = [
        _turn_messages(engine, session, ordinary, message, '温和。', '测试猫娘', '哥哥'),
        _turn_messages(engine, session, transition, '按约定办。', '温和。', '测试猫娘', '哥哥'),
        _opening_messages(engine, '温和。', '测试猫娘', '哥哥'),
        _suggestion_fill_messages(catgirl_name='测试猫娘', player_input=message,
            performance={'performance': '需要登记姓名和擅长的手工类型。'}, max_tokens=1800),
    ]
    for prompt in messages:
        assert '不得编造玩家姓名、联系方式、职业、技能或既往经历' in prompt[0].content
        assert '不能用假名、示例号码或占位符填空' in prompt[0].content
    for outcome in (None, transition):
        review = _build_transition_judge_messages(engine, session, player_input=message,
            actor_performance={'performance': '请问需要登记哪些信息？', 'suggested_inputs': ['（填写）我叫林风，擅长竹编。']},
            route_changed=outcome is not None, transition_outcome=outcome)
        assert '拒绝或解释中的个人情况也须核对依据' in review[0].content
        assert '玩家已经明确披露的称呼不能再报虚构' in review[0].content
        assert '仅按钮有误只报索引，不给正文添加违规' in review[0].content
