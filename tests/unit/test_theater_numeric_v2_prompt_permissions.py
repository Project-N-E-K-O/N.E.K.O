"""Keep delivered prompts within speech, numeric-state and ending permissions."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater import numeric_v2_actor as actor, numeric_v2_workflow as workflow
from services.theater.numeric_v2_actor_output import _parse_output, NumericV2ActorOutputError
from services.theater.numeric_v2_runtime import NumericV2Engine, MetricChangeV2, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


def _payload(messages):
    text = messages[1].content
    return json.loads(text[text.index('{'):])


def _contract(policy):
    return dict(cognition_state='normal', memory_state='available', self_reference_mode='persona_allowed',
                persona_scope='full', dialogue_policy=policy, allowed_behaviors=[], forbidden_behaviors=[])


def _policy_engine(source_policy, target_policy=None):
    story = numeric_v2_story()
    story['nodes'][0]['story_beat']['acting_contract'] = _contract(source_policy)
    if target_policy:
        for node in story['nodes'][1:]:
            node['story_beat']['acting_contract'] = _contract(target_policy)
    return NumericV2Engine.from_mapping(story)


@pytest.mark.asyncio
@pytest.mark.parametrize('policy', ['required', 'optional', 'forbidden'])
async def test_opening_call_respects_actual_speech_permission(monkeypatch, policy):
    instance = actor.NumericV2Actor(object())
    monkeypatch.setattr(instance, '_character_profile', lambda: '温和。')
    monkeypatch.setattr(instance, '_current_catgirl_name', lambda: '小葵')
    monkeypatch.setattr(actor, '_load_player_address', lambda _: '你')
    performance = '（抬起手）' + ('你好。' if policy == 'required' else '')

    async def invoke(messages, **kwargs):
        data = _payload(messages)
        assert kwargs['dialogue_policy'] == data['acting_context']['dialogue_policy'] == policy
        if policy != 'required':
            assert '再由猫娘主动说出第一句' not in data['instruction']
        if policy == 'forbidden':
            assert '不说出对白' in data['instruction']
        candidate = dict(scene_narration='门口亮起一盏灯。', performance=performance,
                         suggested_inputs=['（点头）我先看看。', '（停步）等一会儿。'], transition_offered=False)
        return _parse_output(json.dumps(candidate), opening_required=True, dialogue_policy=kwargs['dialogue_policy'])

    monkeypatch.setattr(instance, '_invoke', invoke)
    result = await instance.generate_opening(engine=_policy_engine(policy))
    assert result['performance'] == performance


@pytest.mark.asyncio
@pytest.mark.parametrize('source_policy,target_policy', [
    ('forbidden', None), ('optional', None), ('required', None),
    ('forbidden', 'required'), ('required', 'forbidden'), ('optional', 'optional'),
])
async def test_output_retry_preserves_source_and_target_speech_permissions(monkeypatch, source_policy, target_policy):
    engine = _policy_engine(source_policy, target_policy)
    opening = {**_opening(), 'performance': '（抬起手）' if source_policy == 'forbidden' else '我在听。'}
    session = engine.create_session(session_id='speech-retry', catgirl_binding=_binding(), opening_performance=opening)
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '这些细节先记下来。'), (),
                                  scene_complete=bool(target_policy), natural_ending_ready=bool(target_policy))
    instance = actor.NumericV2Actor(object())
    calls = []

    async def invoke(messages, **kwargs):
        calls.append((messages, kwargs))
        if len(calls) < 4:
            raise NumericV2ActorOutputError('numeric_v2_actor_repeated_output')
        source = '（轻轻颔首）' + ('' if source_policy == 'forbidden' else '这些细节我记下了。')
        if target_policy:
            target = '（转身望向长街）' + ('' if target_policy == 'forbidden' else '街灯已经亮起来了。')
            return _parse_output(json.dumps({**_candidate(), 'source_performance': source, 'target_performance': target}),
                transition_required=True, source_dialogue_policy=kwargs['source_dialogue_policy'],
                target_dialogue_policy=kwargs['target_dialogue_policy'])
        return _parse_output(json.dumps(dict(performance=source, transition_offered=False,
            suggested_inputs=['（点头）我先看看。', '（停步）等一会儿。'])), dialogue_policy=kwargs['dialogue_policy'])

    monkeypatch.setattr(instance, '_invoke', invoke)
    result = await workflow._generate_actor_turn_with_output_retry(instance, engine=engine, session=session,
        outcome=outcome, player_input='这些细节先记下来。', character_profile='温和。')
    assert len(calls) == 4
    assert result
    for messages, kwargs in calls[1:]:
        system = messages[0].content
        assert '来源动作和对白' not in system
        assert '全新来源动作与对白' not in system
        assert '全新的动作与对白' not in system
        assert '完全改写动作、对白和收尾' not in system
        if target_policy:
            data = _payload(messages)['acting_context']
            assert data['dialogue_policy'] == kwargs['source_dialogue_policy']
            assert data['target_dialogue_policy'] == kwargs['target_dialogue_policy'] == target_policy
        elif source_policy == 'forbidden':
            assert '不能说出对白' in _payload(messages)['role']


def test_nonrelationship_band_reaches_role_without_advancing_relationship_pose():
    story = numeric_v2_story()
    story['initial_state']['metrics']['trust'] = story['metric_schema']['trust']['initial'] = 29
    pressure = deepcopy(story['metric_schema']['trust'])
    pressure.update(name='压力值', description='当前承受的压力。', relationship_effect='none',
                    increase_criteria=['玩家提出高风险要求'], bands=[
                        {'min': 0, 'max': 29, 'label': '从容应对'},
                        {'min': 30, 'max': 69, 'label': '负担加重'},
                        {'min': 70, 'max': 100, 'label': '濒临失控'}])
    story['metric_schema']['pressure'] = pressure
    story['initial_state']['metrics']['pressure'] = 29
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id='bands', catgirl_binding=_binding(), opening_performance=_opening())
    message = '我兑现承诺，也提出一个高风险要求。'
    turn = TurnRequestV2('one', 0, message)
    unchanged = engine.resolve_turn(session, turn, ())
    changed = engine.resolve_turn(session, turn, (
        MetricChangeV2('pressure', 1, '玩家提出高风险要求', message),
        MetricChangeV2('trust', 1, '玩家兑现承诺', message),
    ))
    rows = [_payload(actor._turn_messages(engine, session, outcome, message, '温和。', '小葵', '你'))
            for outcome in (unchanged, changed)]
    assert '从容应对' in rows[0]['role']
    assert '负担加重' in rows[1]['role']
    relationship = lambda row: next(line for line in row['role'].splitlines() if line.startswith('与玩家的当前关系：'))
    assert relationship(rows[0]) == relationship(rows[1])
    for row in rows:
        assert list(row) == ['role', 'current_scene', 'story_so_far', 'pacing', 'next_scene', 'player_input']
        assert not any(value in row['role'] for value in ('29', '30', '69', '100', 'pressure', 'trust'))
    next_outcome = engine.resolve_turn(changed.session, TurnRequestV2('two', 1, '继续听。'), ())
    next_row = _payload(actor._turn_messages(engine, changed.session, next_outcome, '继续听。', '温和。', '小葵', '你'))
    assert relationship(next_row) != relationship(rows[1])


@pytest.mark.parametrize('turn_count', [0, 3, 5])
@pytest.mark.parametrize('complete,ready', [(False, False), (True, False), (False, True), (True, True)])
def test_ending_prompt_does_not_invent_a_new_invitation(turn_count, complete, ready):
    engine = _engine()
    session = engine.create_session(session_id='ending-permission', catgirl_binding=_binding(), opening_performance=_opening())
    session = replace(session, node_turn_count=turn_count)
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '先回应最后的问题。'), (),
                                  scene_complete=complete, natural_ending_ready=ready)
    messages = actor._turn_messages(engine, session, outcome, '先回应最后的问题。', '温和。', '小葵', '你',
                                    interaction_intent='scene_action')
    if complete and ready:
        assert outcome.session.status == 'ended'
        assert _payload(messages)['transition']['natural_ending']
        assert '不再接收输入' in messages[0].content
    else:
        assert outcome.session.status == 'active'
        assert outcome.session.current_node_id == session.current_node_id
        data = _payload(messages)
        assert '本轮自然收束合同' not in messages[0].content
        assert '提出具体收束行动' not in data['pacing']
        assert '提出基于已发生事实的具体未来行动' not in data['pacing']
        assert '不为结束追加邀请' in data['next_scene']


def test_ordinary_mature_exit_still_requests_a_reversible_public_offer():
    engine = _engine(ordinary=True)
    session = engine.create_session(session_id='ordinary-exit', catgirl_binding=_binding(), opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '处理好了。'), (), scene_complete=True)
    messages = actor._turn_messages(engine, session, outcome, '处理好了。', '温和。', '小葵', '你')
    assert outcome.session.current_node_id == session.current_node_id
    assert '本轮自然收束合同' in messages[0].content
    assert '提出具体收束行动' in _payload(messages)['pacing']


def test_opening_omits_retired_transition_fields_but_keeps_current_permissions():
    messages = actor._opening_messages(_engine(), '温和。', '小葵', '你', False, max_tokens=10000)
    system = messages[0].content
    for stale in ('target_scene.opening_situation', 'target_performance', 'scene_horizon.transition.intent',
                  'runtime_unresolved', 'next 是玩家接受后的下一幕计划'):
        assert stale not in system
    assert '不得假定玩家已经说话' in _payload(messages)['instruction']
    assert '玩家称呼尚未确认' in system
    assert 'forbidden 只能写动作' in system
    assert '作者剧情方向是导演信息' in system
