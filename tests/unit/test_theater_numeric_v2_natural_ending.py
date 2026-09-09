"""自然结局只影响明确就绪的终局，旧记录、普通幕及失败事务仍保持原语义。"""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater.numeric_v2_runtime import MetricChangeV2, NumericV2Engine, NumericV2Runtime, TurnRequestV2
from services.theater.numeric_v2_evaluator import _parse_output, _build_messages, NumericV2EvaluatorOutputError
from services.theater.numeric_v2_actor import _turn_messages
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening, _performance, _transition_performance


def _engine(*, ordinary=False, blocked=False):
    story = numeric_v2_story()
    if ordinary:
        # 让高优先级的可达普通幕胜出；即使还有可达结局，也不得绕过既有选路结果。
        middle = deepcopy(story['nodes'][0])
        middle.update(id='middle', type='scene')
        for route in middle['route_gates']:
            route['id'] = 'middle_' + route['id']
        story['nodes'].append(middle)
        story['nodes'][0]['route_gates'][0]['target_node_id'] = 'middle'
        story['nodes'][0]['route_gates'][0]['conditions']['all'][0]['value'] = 0
    if blocked:
        # 保持各路线在合法数值范围内可达，只让当前 trust=20 不满足任何一路。
        story['nodes'][0]['route_gates'][0]['conditions']['all'][0]['value'] = 90
        story['nodes'][0]['route_gates'][1]['conditions']['all'][0]['value'] = 10
    return NumericV2Engine.from_mapping(story)


@pytest.mark.parametrize('complete,ready,ordinary,blocked,offered,intent,ended', [
    (True, True, False, False, False, 'unclear', True),
    (True, False, False, False, False, 'unclear', False),
    (False, True, False, False, False, 'unclear', False),
    (True, True, True, False, False, 'unclear', False),
    (True, True, False, True, False, 'unclear', False),
    (True, True, False, False, True, 'reject', False),
    (False, False, False, False, True, 'accept', True),
])
def test_natural_ending_respects_route_kind_conditions_and_player_refusal(complete, ready, ordinary, blocked, offered, intent, ended):
    engine = _engine(ordinary=ordinary, blocked=blocked)
    session = engine.create_session(session_id='natural', catgirl_binding=_binding(), opening_performance=_opening())
    session = replace(session, transition_offered=offered)
    result = engine.resolve_turn(session, TurnRequestV2('one', 0, '谢谢你。'), (),
        scene_complete=complete, natural_ending_ready=ready, transition_intent=intent)
    assert (result.session.status == 'ended') is ended
    if ended:
        assert result.session.current_node_id == 'ending_leave'
        assert result.ledger_event['transition_intent'] == intent
    else:
        assert result.session.current_node_id == 'start'


@pytest.mark.parametrize('ready', [None, 1, 'true', [], {}])
def test_evaluator_rejects_non_boolean_natural_ending(ready):
    with pytest.raises(NumericV2EvaluatorOutputError, match='natural_ending_invalid'):
        _parse_output(json.dumps(dict(scene_complete=True, metric_changes={}, natural_ending_ready=ready)), _engine(), '谢谢。')


def test_legacy_evaluator_completion_is_not_ending_authorization():
    result = _parse_output('{"scene_complete":true,"metric_changes":{}}', _engine(), '谢谢。')
    assert result.natural_ending_ready is False


def test_metric_change_cannot_reuse_authorization_for_another_ending():
    # Evaluator 看到离开结局，本轮加分却切到留下结局；必须重新核对后者的实际前提。
    engine = _engine()
    session = engine.create_session(session_id='route_switch', catgirl_binding=_binding(), opening_performance=_opening())
    session = replace(session, metrics={'trust': 69})
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '我兑现了承诺。'),
        (MetricChangeV2('trust', 1, '玩家兑现承诺', '我兑现了承诺。'),),
        scene_complete=True, natural_ending_ready=True)
    assert outcome.session.metrics['trust'] == 70
    assert outcome.session.current_node_id == 'start'


@pytest.mark.asyncio
@pytest.mark.parametrize('ready', [False, True])
async def test_ending_signal_replays_without_reinterpreting_old_completion(tmp_path, ready):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    stored = await runtime.start_session(session_id='source', catgirl_binding=_binding(), opening_performance=_opening())
    outcome = runtime.prepare_turn(stored, TurnRequestV2('one', 0, '谢谢。'), (), scene_complete=True, natural_ending_ready=ready)
    if ready:
        # 自然结束同样要求整套来源回应/桥段/结局原子交付，普通正文不可冒充已交付结局。
        with pytest.raises(ValueError, match='numeric_transition_performance_invalid'):
            await runtime.commit_turn(outcome, _performance('谢谢。'))
        assert await runtime.restore_session('source') == stored
    performance = _transition_performance(outcome.session.current_node_id) if ready else _performance('谢谢。')
    committed = await runtime.commit_turn(outcome, performance)
    fork = await runtime.fork_session_for_test('source', session_id='fork', through_revision=1)
    assert fork.session.status == committed.session.status
    assert fork.session.current_node_id == committed.session.current_node_id
    assert fork.ledger_events[0].get('natural_ending_ready', False) is ready
    assert fork.ledger_events[0]['transition_intent'] == 'unclear'


def test_natural_ending_actor_receives_authorization_without_fake_acceptance():
    engine = _engine()
    session = engine.create_session(session_id='prompt', catgirl_binding=_binding(), opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '谢谢。'), (), scene_complete=True, natural_ending_ready=True)
    messages = _turn_messages(engine, session, outcome, '谢谢。', '温和。', '小葵', '你')
    payload = json.loads(messages[1].content.split('\n', 1)[1])
    assert payload['transition']['natural_ending'] is True
    assert '不声称玩家接受了未提出的邀请' in messages[0].content
    # 同轮授权要包含最后动作和回应的交付职责，同时保留未知成败与玩家决定边界。
    assert '先在来源回应完成玩家已授权的最后互动' in messages[0].content
    # 自然结束授权不允许把约定的未来执行通过环境旁白补成事实。
    compact = _turn_messages(engine, session, outcome, '谢谢。', '温和。', '小葵', '你', deterministic_transition=True)
    assert '不能把约定的未来行动写成已经执行' in compact[0].content
    # 来源可静默与目标必须说话可以同时成立，模型须按两个不同字段生成。
    assert 'target_performance 遵守 acting_context.target_dialogue_policy' in compact[0].content
    evaluator_messages = _build_messages(engine, session, '谢谢。')
    assert '不必等玩家再说一句' in evaluator_messages[0].content
    # 顺序合同仍保留未知结果和真实风险门槛，不依赖旧版重复段落的措辞。
    assert '未知成败、真实风险' in evaluator_messages[0].content
    data = json.loads(evaluator_messages[1].content.split('\n', 1)[1])
    assert 'natural_ending_context' in data['transition_preview']


@pytest.mark.asyncio
@pytest.mark.parametrize('suggestions', [[], ['下次再一起做一个吧。']])
async def test_terminal_actor_never_fills_unusable_followup_choices(monkeypatch, suggestions):
    """已结束 Session 不需要补按钮，模型误附的新邀约也不能进入可见候选。"""
    from services.theater.numeric_v2_actor import NumericV2Actor

    engine = _engine()
    session = engine.create_session(session_id='ending_buttons', catgirl_binding=_binding(), opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '谢谢。'), (), scene_complete=True, natural_ending_ready=True)
    actor = NumericV2Actor(object())

    async def invoke(*args, **kwargs):
        return {'segments': [
            {'phase': 'source_response', 'performance': '（轻轻点头）我也很高兴。'},
            {'phase': 'transition_bridge', 'scene_narration': '雨后的长街安静下来。'},
            {'phase': 'target_opening', 'performance': '（抬头望向街灯）愿你一路顺风。'},
        ], 'suggested_inputs': suggestions}

    async def forbidden_fill(**kwargs):
        raise AssertionError('终局不得调用补推荐')

    monkeypatch.setattr(actor, '_invoke', invoke)
    monkeypatch.setattr(actor, '_ensure_suggestions', forbidden_fill)
    performance = await actor.generate_turn(engine=engine, session=session, outcome=outcome,
        player_input='谢谢。', character_profile='温和克制。')
    assert performance['transition_delivered'] is True
    assert performance['suggested_inputs'] == []
