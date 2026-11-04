"""An empty transition source is absence of extra acting, never missing target delivery."""

from copy import deepcopy
import json

import pytest

from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_actor_output import _parse_output, NumericV2ActorOutputError
from services.theater.numeric_v2_performance import (
    performance_content_blocks, transition_source_dialogue_policy, valid_mixed_performance_policy,
)
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding


NPC_ANSWER = '管事答道：“文书已经核验，可以通过。”'

# Exact Actor output from holdout10-max-off, turn 32: the model chose
# target_opening but wrote no performance. Everything else is legal.
REAL_HOLDOUT10_EMPTY_TARGET_OPENING = {
    'source_scene_narration': '',
    'performance': '',
    'bridge_scene_narration': '',
    'target_scene_narration': (
        '楼道里的感应灯亮着暖黄的光，两人一前一后走进屋子。防盗门在身后咔哒一声合上，'
        '把社区中庭残留的碗碟碰撞声彻底隔在了外头。客厅里还留着出门前的样子，茶几上放着两杯水，'
        '厨房方向能看见冰箱门上贴着的便签纸。'
    ),
    'response_phase': 'target_opening',
    'suggested_inputs': [],
}


def _candidate():
    return dict(source_performance='', source_scene_narration=NPC_ANSWER,
                bridge_scene_narration='雨声渐远。', target_scene_narration='廊下亮着灯。',
                target_performance='（停在灯下）这里可以歇息。', suggested_inputs=[])


def _parse(candidate, **kwargs):
    return _parse_output(json.dumps(candidate), transition_required=True,
                         source_dialogue_policy=kwargs.pop('source_dialogue_policy', 'optional'), **kwargs)


@pytest.mark.parametrize('policy', ['optional', 'forbidden'])
@pytest.mark.parametrize('body', ['', ' \n '])
def test_parser_allows_only_empty_source_without_losing_npc_answer(policy, body):
    candidate = _candidate()
    candidate['source_performance'] = body
    parsed = _parse(candidate, source_dialogue_policy=policy)
    assert parsed['source_performance'] == ''
    assert parsed['source_scene_narration'] == NPC_ANSWER
    assert parsed['target_performance'] == candidate['target_performance']


@pytest.mark.parametrize('value', [None, 0, [], {}, {'action': '', 'dialogue': ''}])
def test_parser_rejects_nonstring_or_empty_legacy_source(value):
    candidate = _candidate()
    candidate['source_performance'] = value
    with pytest.raises(NumericV2ActorOutputError):
        _parse(candidate)


@pytest.mark.parametrize('field', ['source_performance', 'target_performance', 'target_scene_narration'])
def test_parser_still_requires_all_transition_fields(field):
    candidate = _candidate()
    candidate.pop(field)
    with pytest.raises(NumericV2ActorOutputError):
        _parse(candidate)


@pytest.mark.parametrize('policy', ['required', 'optional', 'forbidden'])
def test_target_and_ordinary_empty_remain_invalid(policy):
    candidate = _candidate()
    candidate['target_performance'] = ''
    with pytest.raises(NumericV2ActorOutputError):
        _parse(candidate, target_dialogue_policy=policy)
    with pytest.raises(NumericV2ActorOutputError):
        _parse_output(json.dumps({'performance': '', 'suggested_inputs': []}), dialogue_policy=policy)
    with pytest.raises(NumericV2ActorOutputError):
        _parse_output(json.dumps({'scene_narration': '门开着。', 'performance': '', 'suggested_inputs': []}),
                      opening_required=True, dialogue_policy=policy)


@pytest.mark.parametrize('policy', ['optional', 'forbidden'])
def test_natural_ending_can_close_with_source_response_and_target_narration(policy):
    candidate = _candidate()
    candidate.update(source_performance='（放下文书，轻轻点头）谢谢，我们就这样说定了。', target_performance='')
    parsed = _parse(candidate, natural_ending=True, target_dialogue_policy=policy)
    assert parsed['source_performance'] == candidate['source_performance']
    assert parsed['target_performance'] == ''
    assert parsed['target_scene_narration'] == candidate['target_scene_narration']
    candidate['source_performance'] = ''
    parsed = _parse(candidate, natural_ending=True, target_dialogue_policy=policy)
    assert parsed['source_performance'] == parsed['target_performance'] == ''


def test_natural_ending_does_not_remove_required_target_speech():
    candidate = _candidate()
    candidate.update(source_performance='谢谢。', target_performance='')
    with pytest.raises(NumericV2ActorOutputError):
        _parse(candidate, natural_ending=True, target_dialogue_policy='required')


@pytest.mark.parametrize('phase', ['source_response', 'target_opening'])
def test_single_response_protocol_keeps_its_position_and_permissions(phase):
    candidate = _candidate()
    candidate.pop('source_performance')
    candidate.pop('target_performance')
    candidate.update(response_phase=phase, performance='事情办妥了，谢谢。')
    parsed = _parse(candidate, natural_ending=True, target_dialogue_policy='optional')
    assert parsed['source_performance'] == (candidate['performance'] if phase == 'source_response' else '')
    assert parsed['target_performance'] == (candidate['performance'] if phase == 'target_opening' else '')
    assert 'response_phase' not in parsed
    if phase == 'source_response':
        with pytest.raises(NumericV2ActorOutputError, match='dialogue_forbidden'):
            _parse(candidate, natural_ending=True, source_dialogue_policy='forbidden', target_dialogue_policy='optional')
    else:
        assert _parse(candidate, natural_ending=True, source_dialogue_policy='forbidden',
            target_dialogue_policy='optional')['target_performance'] == candidate['performance']


def test_two_responses_still_work_for_ordinary_transition_but_not_single_ending():
    candidate = _candidate()
    candidate['source_performance'] = '事情办妥了。'
    assert _parse(candidate, target_dialogue_policy='optional')['target_performance']
    with pytest.raises(NumericV2ActorOutputError):
        _parse(candidate, natural_ending=True, target_dialogue_policy='optional')


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['source_response', 'none'])
async def test_single_ending_response_commits_restores_and_forks(tmp_path, phase):
    story = deepcopy(_engine().story)
    for node in story['nodes']:
        node['story_beat']['acting_contract'] = dict(
            cognition_state='normal', memory_state='available', self_reference_mode='persona_allowed',
            persona_scope='full', dialogue_policy='optional', allowed_behaviors=[], forbidden_behaviors=[])
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id='single-ending', catgirl_binding=_binding(),
        opening_performance={'performance': '事情已经办妥了。', 'suggested_inputs': []})
    outcome = runtime.prepare_turn(current, TurnRequestV2('finish', 0, '谢谢，告别就到这里吧。'), (),
        scene_complete=True, natural_ending_ready=True)
    assert outcome.session.status == 'ended'
    candidate = _candidate()
    candidate.pop('source_performance')
    candidate.pop('target_performance')
    candidate.update(response_phase=phase,
        performance='（轻轻点头）嗯，这样就好。' if phase == 'source_response' else '')
    parsed = _parse(candidate, natural_ending=True, target_dialogue_policy='optional')
    performance = runtime.engine.finalize_transition_performance(outcome, parsed,
        target_opening='不能替换模型旁白。', source_dialogue_policy='optional', target_dialogue_policy='optional')
    assert sum(bool(s.get('performance')) for s in performance['segments']) == (phase != 'none')
    assert NPC_ANSWER in str(performance_content_blocks(performance))
    committed = await runtime.commit_turn(outcome, performance)
    assert await runtime.restore_session('single-ending') == committed
    fork = await runtime.fork_session_for_test('single-ending', session_id='single-fork', through_revision=1)
    assert fork.session.performance_history[-1]['segments'] == performance['segments']
    assert await runtime.restore_session('single-fork') == fork


def test_real_holdout10_empty_target_opening_is_no_response():
    parsed = _parse(deepcopy(REAL_HOLDOUT10_EMPTY_TARGET_OPENING), bridge_required=False,
                    natural_ending=True, target_dialogue_policy='optional')
    assert parsed['source_performance'] == parsed['target_performance'] == ''
    assert parsed['bridge_scene_narration'] == ''
    assert parsed['target_scene_narration'] == REAL_HOLDOUT10_EMPTY_TARGET_OPENING['target_scene_narration']
    assert 'source_scene_narration' not in parsed
    assert 'response_phase' not in parsed
    assert parsed['suggested_inputs'] == []


@pytest.mark.parametrize('policy', ['optional', 'forbidden'])
@pytest.mark.parametrize('phase', ['source_response', 'target_opening'])
@pytest.mark.parametrize('body', ['', ' \n '])
def test_response_mode_accepts_empty_as_no_response_in_either_position(policy, phase, body):
    candidate = _candidate()
    candidate.pop('source_performance')
    candidate.pop('target_performance')
    candidate.update(response_phase=phase, performance=body)
    parsed = _parse(candidate, natural_ending=True, target_dialogue_policy=policy)
    assert parsed['source_performance'] == parsed['target_performance'] == ''
    assert parsed['source_scene_narration'] == NPC_ANSWER
    assert parsed['bridge_scene_narration'] == candidate['bridge_scene_narration']
    assert parsed['target_scene_narration'] == candidate['target_scene_narration']
    assert 'response_phase' not in parsed


@pytest.mark.parametrize('phase', ['other', '', None])
def test_response_mode_still_rejects_unknown_marker(phase):
    candidate = _candidate()
    candidate.pop('source_performance')
    candidate.pop('target_performance')
    candidate.update(response_phase=phase, performance='')
    with pytest.raises(NumericV2ActorOutputError, match='numeric_v2_actor_response_phase_invalid'):
        _parse(candidate, natural_ending=True, target_dialogue_policy='optional')


@pytest.mark.parametrize('field', ['response_phase', 'performance', 'source_scene_narration',
                                   'bridge_scene_narration', 'target_scene_narration', 'suggested_inputs'])
def test_response_mode_still_requires_the_complete_field_set(field):
    candidate = _candidate()
    candidate.pop('source_performance')
    candidate.pop('target_performance')
    candidate.update(response_phase='target_opening', performance='')
    candidate.pop(field)
    with pytest.raises(NumericV2ActorOutputError):
        _parse(candidate, natural_ending=True, target_dialogue_policy='optional')


@pytest.mark.parametrize('value', [None, 0, [], {}])
def test_response_mode_still_rejects_a_nonstring_performance(value):
    candidate = _candidate()
    candidate.pop('source_performance')
    candidate.pop('target_performance')
    candidate.update(response_phase='target_opening', performance=value)
    with pytest.raises(NumericV2ActorOutputError, match='numeric_v2_actor_response_phase_invalid'):
        _parse(candidate, natural_ending=True, target_dialogue_policy='optional')


def test_empty_response_marker_does_not_bypass_required_target_or_ordinary_transition():
    candidate = _candidate()
    candidate.pop('source_performance')
    candidate.pop('target_performance')
    candidate.update(response_phase='target_opening', performance='')
    with pytest.raises(NumericV2ActorOutputError):
        _parse(candidate, natural_ending=True, target_dialogue_policy='required')
    with pytest.raises(NumericV2ActorOutputError):
        _parse(candidate, natural_ending=False, target_dialogue_policy='optional')
    with pytest.raises(NumericV2ActorOutputError):
        _parse(candidate, natural_ending=True, target_dialogue_policy='invalid')


@pytest.mark.parametrize('body', ['再见。', '（在黑暗中摸索她的身体，检查生命体征）',
                                '(瞳孔中的数据流骤然熄灭，身体无力地瘫软在维修台上)'])
def test_none_rejects_a_nonempty_response_instead_of_silently_discarding_it(body):
    candidate = _candidate()
    candidate.pop('source_performance')
    candidate.pop('target_performance')
    candidate.update(response_phase='none', performance=body)
    with pytest.raises(NumericV2ActorOutputError, match='response_phase_invalid'):
        _parse(candidate, natural_ending=True, target_dialogue_policy='optional')


def test_single_ending_format_retry_corrects_phase_without_requesting_new_acting():
    from services.theater.numeric_v2_workflow import _output_retry_hint

    hint = _output_retry_hint(last_error_code='numeric_v2_actor_response_phase_invalid',
        retry_number=1, route_changed=True)
    assert 'none' in hint
    assert '全新的简短来源回应' not in hint


def test_required_direct_source_and_nonempty_forbidden_source_stay_strict():
    with pytest.raises(NumericV2ActorOutputError):
        _parse(_candidate(), source_dialogue_policy='required')
    candidate = _candidate()
    candidate['source_performance'] = '可以走了。'
    with pytest.raises(NumericV2ActorOutputError, match='dialogue_forbidden'):
        _parse(candidate, source_dialogue_policy='forbidden')
    candidate['source_performance'] = '（缓缓起身）'
    assert _parse(candidate, source_dialogue_policy='forbidden')['source_performance'] == '（缓缓起身）'


@pytest.mark.parametrize('policy', ['required', 'optional', 'forbidden', 'invalid'])
def test_shared_validator_needs_explicit_permission_and_retains_policy(policy):
    assert not valid_mixed_performance_policy({'performance': ''}, policy)
    assert valid_mixed_performance_policy({'performance': ''}, policy, allow_empty=True) is (
        policy in {'optional', 'forbidden'})
    for value in [None, 0, [], {}]:
        assert not valid_mixed_performance_policy({'performance': value}, policy, allow_empty=True)


def _runtime(tmp_path, policy):
    story = deepcopy(_engine().story)
    contract = dict(cognition_state='normal', memory_state='available', self_reference_mode='persona_allowed',
                    persona_scope='full', dialogue_policy=policy, allowed_behaviors=[], forbidden_behaviors=[])
    story['nodes'][0]['story_beat']['acting_contract'] = contract
    for node in story['nodes'][1:]:
        node['story_beat']['acting_contract'] = {**contract, 'dialogue_policy': 'required'}
    return NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)


@pytest.mark.parametrize('message', ['谢谢，事情办妥了。', '（把文书交给管事）请收好。', '管事，这份文书可以收下吗？'])
def test_ending_prompt_keeps_source_optional_without_skipping_pending_interaction(message):
    engine = _engine()
    session = engine.create_session(session_id='ending-source-duty', catgirl_binding=_binding(),
        opening_performance={'performance': '结果已确认，可以告别了。'})
    outcome = engine.resolve_turn(session, TurnRequestV2('finish', 0, message), (),
        scene_complete=True, natural_ending_ready=True)
    messages = _turn_messages(engine, session, outcome, message, '平静克制。', '小葵', '老谢')
    payload = prompt_payload(messages[1].content)
    assert outcome.session.status == 'ended'
    assert payload['player_input'] == message
    assert payload['acting_context']['dialogue_policy'] == 'optional'
    assert '来源回应必须承接本轮已授权动作的完成及直接反应' not in messages[0].content
    assert '尚未交付的最后互动' in messages[0].content
    assert '在场 NPC 问答' in messages[0].content
    assert '来源回应可以为空' in messages[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize('policy', ['required', 'forbidden'])
@pytest.mark.parametrize('narration', [False, True])
async def test_empty_source_round_trips_with_npc_narration_and_three_segments(tmp_path, policy, narration):
    runtime = _runtime(tmp_path, policy)
    current = await runtime.start_session(session_id='optional-source', catgirl_binding=_binding(),
        opening_performance={'scene_narration': '门前还有灯。',
                             'performance': '（静静站着）' if policy == 'forbidden' else '（望向门口）请进。'})
    outcome = runtime.prepare_turn(current, TurnRequestV2('finish', 0, '好，就这样。'), (),
                                   scene_complete=True, natural_ending_ready=True)
    assert outcome.ledger_event['from_node_id'] != outcome.ledger_event['to_node_id']
    candidate = _candidate()
    if not narration:
        candidate.pop('source_scene_narration')
    parsed = _parse(candidate, source_dialogue_policy=transition_source_dialogue_policy(policy))
    performance = runtime.engine.finalize_transition_performance(outcome, parsed,
        target_opening='不能覆盖已生成开场。', source_dialogue_policy=transition_source_dialogue_policy(policy))
    assert [part['phase'] for part in performance['segments']] == [
        'source_response', 'transition_bridge', 'target_opening']
    assert performance['segments'][0]['performance'] == ''
    texts = [block['text'] for block in performance_content_blocks(performance)]
    assert (NPC_ANSWER in texts) is narration
    assert texts[0] == (NPC_ANSWER if narration else '雨声渐远。')
    committed = await runtime.commit_turn(outcome, performance)
    assert await runtime.restore_session('optional-source') == committed
    fork = await runtime.fork_session_for_test('optional-source', session_id='optional-fork', through_revision=1)
    assert fork.session.performance_history[0]['segments'] == performance['segments']
    assert fork.session.current_node_id == committed.session.current_node_id
    assert await runtime.restore_session('optional-fork') == fork


@pytest.mark.parametrize('value', [None, 0, 1, [], {}])
def test_runtime_compact_source_does_not_coerce_nonstring_to_empty(value):
    engine = _engine()
    session = engine.create_session(session_id='invalid-source', catgirl_binding=_binding(),
                                   opening_performance={'performance': '（抬头）你好。'})
    outcome = engine.resolve_turn(session, TurnRequestV2('finish', 0, '好。'), (),
                                  scene_complete=True, natural_ending_ready=True)
    candidate = _candidate()
    candidate['source_performance'] = value
    with pytest.raises(ValueError, match='numeric_transition_performance_invalid'):
        engine.finalize_transition_performance(outcome, candidate, target_opening='门口。',
                                               source_dialogue_policy='optional')


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['target', 'forbidden_dialogue', 'fixed_narration'])
async def test_empty_source_does_not_bypass_commit_validation(tmp_path, invalid):
    runtime = _runtime(tmp_path, 'forbidden')
    current = await runtime.start_session(session_id='strict-commit', catgirl_binding=_binding(),
        opening_performance={'scene_narration': '门前还有灯。', 'performance': '（静静站着）'})
    outcome = runtime.prepare_turn(current, TurnRequestV2('finish', 0, '好。'), (),
                                   scene_complete=True, natural_ending_ready=True)
    candidate = _parse(_candidate(), source_dialogue_policy='forbidden')
    performance = runtime.engine.finalize_transition_performance(outcome, candidate,
        target_opening='廊下。', source_dialogue_policy='forbidden')
    if invalid == 'target':
        performance['segments'][2]['performance'] = ''
    elif invalid == 'forbidden_dialogue':
        performance['segments'][0]['performance'] = '仍然不能说出口的对白。'
    else:
        performance['segments'][0]['fixed_narrations'] = [{'id': 'not-an-authored-piece'}]
    with pytest.raises(ValueError, match='numeric_(transition_performance|fixed_narration)_invalid'):
        await runtime.commit_turn(outcome, performance)
    assert await runtime.restore_session('strict-commit') == current
