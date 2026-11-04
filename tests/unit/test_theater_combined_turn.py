"""A single ordinary Actor request settles state without acquiring routing authority."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from services.theater import numeric_v2_options as options, numeric_v2_workflow as workflow
from services.theater.numeric_v2_actor_output import _parse_output, NumericV2ActorOutputError
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_unified_pacing import story_for


@pytest.mark.asyncio
async def test_combined_player_route_fact_in_body_bucket_is_validated_as_player_evidence(tmp_path, monkeypatch):
    story = json.loads((Path(__file__).resolve().parents[2] / 'assets/theater/story_a400cd1c2783.json').read_text(encoding='utf-8'))
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='route-choice', catgirl_binding=_binding(), opening_performance=_opening())
    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(workflow, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    message = '走官道吧，稳妥些。'
    key = 'scene:mainline_01:route_decided'

    async def generate(self, **kwargs):
        assert kwargs['combined_evaluation']
        return {'performance': '官道稳妥，先检查行装。', 'suggested_inputs': [], 'transition_offered': False,
                'fact_candidates': [{'key': key, 'value': '官道', 'evidence_quote': message}],
                '_turn_evaluation': {'scene_complete': False, 'metric_changes': {}, 'fact_candidates': []}}

    async def forbidden(*args, **kwargs):
        raise AssertionError('No extra evaluator or review call for a misplaced source field')

    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', forbidden)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', forbidden)
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('choose', 0, message), ensure_current_binding=lambda _: _binding())
    assert result.stored.session.story_state['facts'][key]['value'] == '官道'
    assert 'scene:mainline_01:departure_confirmed' not in result.stored.session.story_state['facts']
    assert result.stored.session.current_node_id == 'mainline_01'
    assert result.diagnostics['actor_generation_attempts'] == 1
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize('fact_source,malformed_changes', [('actor', False), ('actor', True), ('misplaced_actor', False), ('history', False), ('forged', False)])
async def test_combined_turn_reuses_body_and_only_commits_valid_state(tmp_path, monkeypatch, fact_source, malformed_changes):
    story = story_for('山径的避风棚')
    # Keep both endings reachable while the first act has only a scene exit.
    story['nodes'][2]['route_gates'][0]['conditions'] = deepcopy(story['nodes'][0]['route_gates'][1]['conditions'])
    story['nodes'][2]['route_gates'].append(story['nodes'][0]['route_gates'][0])
    story['nodes'][0]['route_gates'] = [story['nodes'][0]['route_gates'][1]]
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    text = '眼前的事情已经处理完。'
    opening = {**_opening(), 'performance': text} if fact_source == 'history' else _opening()
    current = await runtime.start_session(session_id='combined', catgirl_binding=_binding(), opening_performance=opening)
    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(workflow, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和克制。')
    calls = []
    async def forbidden(*args, **kwargs):
        raise AssertionError('ordinary combined turn must not call Evaluator or model review')
    async def generate(self, **kwargs):
        calls.append('actor')
        assert kwargs['combined_evaluation'] is True
        assert kwargs['outcome'].session.current_node_id == 'start'
        fact = {'key': 'scene:start:done', 'value': True, 'evidence_quote': text}
        return {'performance': text if fact_source in {'actor', 'misplaced_actor'} else '我记得刚才的经过。',
                'suggested_inputs': [], 'transition_offered': False,
                'fact_candidates': [fact] if fact_source == 'actor' else [],
                '_turn_visible_history': [] if fact_source == 'misplaced_actor' else [text],
                '_turn_evaluation': {'scene_complete': True, 'metric_changes': {
                    'trust': {} if malformed_changes else {'strength': 'weak', 'criterion_id': 'trust.increase.1'}},
                    'fact_candidates': [] if fact_source == 'actor' else [fact]}}
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', forbidden)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', forbidden)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    before = deepcopy(current.session.to_dict())
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('one', 0, '我已经按约把东西送到了。'),
        ensure_current_binding=lambda _: _binding())
    assert calls == ['actor']
    assert result.diagnostics['ordinary_turn_combined'] is True
    assert result.diagnostics['evaluator_model_attempts'] == 0
    assert result.stored.session.current_node_id == 'start'
    if fact_source == 'forged':
        assert 'scene:start:done' not in result.stored.session.story_state['facts']
    else:
        assert result.stored.session.story_state['facts']['scene:start:done']['value'] is True
    assert current.session.to_dict() == before
    assert '_turn_evaluation' not in result.performance
    assert '_turn_evaluation' not in json.dumps(result.stored.session.to_dict())
    assert '_turn_visible_history' not in json.dumps(result.stored.session.to_dict())
    if malformed_changes:
        assert result.stored.session.metrics == before['metrics']
        assert result.diagnostics['evaluator_degraded'] is True
    else:
        assert before['metrics']['trust'] < result.stored.session.metrics['trust'] <= before['metrics']['trust'] + 5


def test_combined_output_requires_explicit_state_and_keeps_normal_parser_strict():
    payload = {'performance': '我在这里。', 'suggested_inputs': [], 'transition_offered': False,
               'turn_evaluation': {'scene_complete': False, 'metric_changes': {}, 'fact_candidates': []}}
    result = _parse_output(json.dumps(payload), combined_evaluation=True)
    assert result['_turn_evaluation']['scene_complete'] is False
    with pytest.raises(NumericV2ActorOutputError):
        _parse_output(json.dumps(payload))
    payload.pop('turn_evaluation')
    with pytest.raises(NumericV2ActorOutputError, match='turn_evaluation_invalid'):
        _parse_output(json.dumps(payload), combined_evaluation=True)


@pytest.mark.asyncio
async def test_scene_without_completion_contract_keeps_player_initiation_judgment(tmp_path, monkeypatch):
    from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
    from tests.unit.test_theater_numeric_v2_transition_history import _candidate

    case = initiation_case(message='现在带我去阅览室。')
    engine = case['engine']
    # Both exits lead to ordinary scenes, so only the missing completion gate distinguishes this case.
    engine.nodes['ending_stay'].update(type='scene', terminal=False)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='legacy-go', catgirl_binding=_binding(),
        opening_performance=case['session'].opening_performance)
    turn = TurnRequestV2('go', 0, case['message'])
    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    modules = options.default_options()
    modules.update(review=False, review_contract=False)
    calls = []
    async def load_options():
        return modules
    async def evaluate(*args, **kwargs):
        calls.append('evaluator')
        return workflow.NumericV2EvaluationResult((), False, transition_intent='initiate')
    async def generate(self, **kwargs):
        calls.append('actor')
        assert kwargs.get('combined_evaluation') is not True
        candidate = _candidate()
        candidate.update(source_performance='好，我带路。', bridge_scene_narration='两人沿左侧走廊来到阅览室。',
                         target_scene_narration='门边挂着一张借阅须知。', target_performance='先看看目录吧。')
        return engine.finalize_transition_performance(kwargs['outcome'], candidate, target_opening='阅览室入口。')
    monkeypatch.setattr(workflow, 'aload_theater_module_options', load_options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')

    assert runtime.prepare_turn(current, turn, (), transition_intent='initiate').session.current_node_id == 'ending_leave'
    assert workflow._can_combine_ordinary_turn(runtime, current, turn, modules) is False
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=turn, ensure_current_binding=lambda _: _binding())
    assert calls == ['evaluator', 'actor']
    assert result.stored.session.current_node_id == 'ending_leave'
    assert result.stored.ledger_events[-1]['transition_intent'] == 'initiate'
    assert await runtime.restore_session('legacy-go') == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['opening', 'turn'])
async def test_default_actor_keeps_empty_suggestions_without_an_extra_call(tmp_path, monkeypatch, phase):
    engine = NumericV2Engine.from_mapping(story_for('医疗站'))
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='no-fill', catgirl_binding=_binding(), opening_performance=_opening())
    actor = workflow.NumericV2Actor(object())
    calls = []
    async def invoke(*args, **kwargs):
        calls.append(kwargs)
        return {'performance': '我在这里听你说。', 'scene_narration': '店里很安静。',
                'suggested_inputs': [], 'transition_offered': False}
    monkeypatch.setattr(actor, '_invoke', invoke)
    monkeypatch.setattr(actor, '_character_profile', lambda: '温和克制。')
    if phase == 'opening':
        result = await actor.generate_opening(engine=engine)
    else:
        request = TurnRequestV2('one', 0, '先聊聊吧。')
        result = await actor.generate_turn(engine=engine, session=current.session,
            outcome=runtime.prepare_turn(current, request, ()), player_input=request.message)
    assert result['suggested_inputs'] == []
    assert len(calls) == 1 and actor.suggestion_fill_provider_call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('matching_quote,exit_matches', [(True, True), (False, True), (True, False)])
async def test_public_body_invitation_can_be_accepted_without_a_latched_flag(tmp_path, matching_quote, exit_matches):
    from services.theater.numeric_v2_evaluator import _parse_output as parse_evaluation
    engine = NumericV2Engine.from_mapping(story_for('医疗站'))
    runtime = NumericV2Runtime(engine, tmp_path)
    quote = '这条通道通向医疗站，要和我一起去吗？'
    current = await runtime.start_session(session_id='public-offer', catgirl_binding=_binding(),
        opening_performance={'performance': quote, 'suggested_inputs': ['好，我跟你走。'], 'transition_offered': False})
    result = parse_evaluation(json.dumps({'scene_complete': True, 'metric_changes': {},
        'transition_intent': 'accept', 'transition_reply_target': 'pending_transition',
        'public_destination_quote': quote if matching_quote else '一段没有演出过的邀请。',
        'exit_matches': exit_matches, 'exit_comparison': '公开医疗站与当前出口相同。'}),
        engine, '好，我跟你走。', current.session)
    assert result.transition_intent == ('initiate' if matching_quote and exit_matches else 'unclear')
