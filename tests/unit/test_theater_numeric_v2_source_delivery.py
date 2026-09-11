"""Cross-genre fixtures cannot use a target template as proof of source results; assess model semantics through real sampling separately."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_evaluator import _build_transition_judge_messages
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


# 三种依赖分别是物品领取、信息获知、操作结果；不使用森林中的专有名词。
DELIVERY_TOPICS = {
    'parcel': ('邮局柜台', '门外的长椅', '包裹仍在柜台，猫娘尚未领取。',
               '猫娘可自行从柜台领取自己的包裹。', '（从柜台领起自己的包裹）拿好了。',
               '猫娘抱着自己的包裹。'),
    'archive': ('档案室', '窗外的露台', '值班员尚未向猫娘说明档案编号。',
                '值班员可以公开说明档案编号为丙七，猫娘听后得知编号。',
                '（听完值班员报出的“丙七”）我记住编号了。', '猫娘已经知道档案编号为丙七。'),
    'scan': ('实验室', '门外的休息区', '扫描尚未启动，样本完整放在扫描台上。',
             '猫娘可以自行按下扫描键，设备随即完成无风险的外观扫描，生成一份原始影像。',
             '（按下扫描键，等设备生成原始影像）扫描完成了。', '设备已完成外观扫描并生成原始影像。'),
}


def delivery_case(topic, variant):
    """Freeze normal drafts and counterexamples for deterministic projection tests and independent real review."""
    place, destination, pending, permission, delivery, result = DELIVERY_TOPICS[topic]
    engine = _engine(ordinary=True)
    source, target = engine.nodes['start'], engine.nodes['middle']
    source['story_beat'].update(
        opening_scene=f'双方在{place}。{pending}',
        summary=f'{permission}完成后可以去{destination}休息。',
        narrative_summary=f'{permission}完成后可以去{destination}休息。',
        character_state={}, acting_contract={},
    )
    target['story_beat'].update(
        opening_scene=f'双方来到{destination}。{result}',
        summary='两人闲聊眼前景色，不实施新的操作。',
        narrative_summary='两人闲聊眼前景色，不实施新的操作。',
        character_state={'catgirl_state': result}, acting_contract={},
    )
    route = source['route_gates'][0]
    route['transition_contract'].update(
        reason=f'{result}两人可前往{destination}。',
        must_deliver=[], must_preserve=[], bridge_scene_narration=f'两人来到{destination}。',
    )
    # 公开邀请是已提交开场；是否已有来源结果是唯一历史变量，不能让目标模板代替它。
    opening = _opening()
    opening.update(scene_narration=f'双方在{place}。' + (result if variant == 'history' else pending),
                   performance=f'我们去{destination}休息，好吗？')
    session = replace(engine.create_session(session_id=f'{topic}_{variant}',
        catgirl_binding=_binding(), opening_performance=opening), transition_offered=True)
    message = f'好，我们去{destination}。'
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, message), (), transition_intent='accept')
    # 省略尚未成立的模板结果也是正常改写；不应强迫模型倒叙补造来满足模板。
    text = dict(source_performance=delivery if variant == 'deliver_now' else '（点头）走吧。',
                bridge_scene_narration=f'两人离开{place}，来到{destination}。',
                target_scene_narration=f'{destination}吹来微风。' + ('' if variant == 'omit' else result),
                target_performance='（望着远处）这里安静些。', suggested_inputs=[])
    if variant == 'extra_player_action':
        # 只改变动作主体：接受休息不授权玩家另行领取、询问或操作。
        text['source_performance'] = '（看着你）你刚才已经替我完成了这件事。'
        text['bridge_scene_narration'] = f'玩家在{place}替猫娘完成了前述操作，两人随后来到{destination}。'
    candidate = engine.finalize_transition_performance(outcome, text, target_opening=target['story_beat']['opening_scene'])
    return engine, session, outcome, message, candidate


@pytest.mark.parametrize('topic', DELIVERY_TOPICS)
@pytest.mark.parametrize('variant', ['missing', 'history', 'deliver_now', 'omit', 'extra_player_action'])
def test_source_evidence_stays_separate_from_author_template_and_candidate(topic, variant):
    """Send complete actual state and author expectations to review without mutating the package or mixing templates into committed history."""
    engine, session, outcome, message, candidate = delivery_case(topic, variant)
    before = deepcopy(engine.story)
    actor = _turn_messages(engine, session, outcome, message, '谨慎友好。', '小葵', '你',
                           deterministic_transition=True)
    review = _build_transition_judge_messages(engine, session, player_input=message,
        actor_performance=candidate, transition_outcome=outcome)
    actor_data = json.loads(actor[1].content.split('\n', 1)[1])
    review_data = json.loads(review[1].content.split('：', 1)[1])
    pending, result = DELIVERY_TOPICS[topic][2], DELIVERY_TOPICS[topic][5]
    expected = result if variant == 'history' else pending
    assert expected in json.dumps(actor_data['recent_context'], ensure_ascii=False)
    assert expected in json.dumps(review_data['scene_context'], ensure_ascii=False)
    assert result in actor_data['transition']['target_scene']['opening_situation']
    assert result in review_data['target_scene']['opening_situation']
    assert review_data['candidate_segments'][-1]['scene_narration'] == candidate['segments'][-1]['scene_narration']
    assert engine.story == before


@pytest.mark.parametrize('truncated,missing', [(False, False), (True, False), (False, True)])
def test_review_history_coverage_never_claims_missing_or_packed_records_are_complete(monkeypatch, truncated, missing):
    """Update coverage markers with actual records and budget trimming; retrieval hits cannot stand in for complete history."""
    from services.theater import numeric_v2_evaluator as evaluator

    engine, session, outcome, message, candidate = delivery_case('parcel', 'missing')
    if missing:
        session = replace(session, node_turn_count=1)
    elif truncated:
        # 追加一个真实形状的当前幕回合，再收紧输入预算，强制移走开场记录。
        session = replace(session, revision=1, node_turn_count=1, performance_history=({
            'revision': 1, 'from_node_id': 'start', 'to_node_id': 'start',
            'input_text': '稍等。', 'performance': '（等在柜台旁）好。',
        },))
        original = evaluator.numeric_v2_actor_budget
        monkeypatch.setattr(evaluator, 'numeric_v2_actor_budget',
            lambda profile: {**original(profile), 'formal_judge_input_max_tokens': 100})
    messages = evaluator._build_transition_judge_messages(engine, session, player_input=message,
        actor_performance=candidate, transition_outcome=outcome)
    payload = json.loads(messages[1].content.split('：', 1)[1])
    assert payload['current_visit_history_complete'] is (not truncated and not missing)


def test_pacing_does_not_promote_authored_exit_preconditions_to_current_results():
    """Turn pacing is runtime information; do not smuggle completed-action wording from legacy exits into current facts."""
    engine, session, _, _, _ = delivery_case('parcel', 'missing')
    beat = engine.nodes['start']['story_beat']
    beat.pop('narrative_focus', None)
    beat['transition_goal'] = '猫娘已经领取包裹，玩家已经签收，可以出门。'
    message = '我们现在拿到包裹了吗？'
    outcome = engine.resolve_turn(session, TurnRequestV2('question', 0, message), ())
    messages = _turn_messages(engine, session, outcome, message, '谨慎友好。', '小葵', '你',
                             interaction_intent='chat')
    data = json.loads(messages[1].content.split('\n', 1)[1])
    assert beat['transition_goal'] not in data['pacing']
    assert '包裹仍在柜台' in data['story_so_far']
    assert '领取自己的包裹' in data['current_scene']
