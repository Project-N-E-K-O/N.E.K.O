"""Source delivery must precede its fact approval and same-turn continuation."""

from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_actor as actor, numeric_v2_evaluator as ev
from services.theater import numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_actor_output import _parse_output, NumericV2ActorOutputError
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2, MetricChangeV2
from services.theater.numeric_v2_structured_output import actor_output_schema
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.utils.theater_prompt import prompt_payload


KEY = 'scene:start:agreed'
INPUT = '我建议把设备留在这里，你有不同意见我们现在说清，再搬。'
AGREED = '没意见，就按你提出的新去向办。'


def _engine(source=None):
    story = numeric_v2_story()
    definition = {'value_type': 'bool', 'visibility': 'public', 'description': '双方已同意同一份设备去向安排。'}
    if source:
        definition['evidence_source'] = source
        if source == 'player_input':
            definition['description'] = '玩家已作出设备去向的决定。'
    story['fact_contract'] = {'facts': {KEY: definition}}
    start = story['nodes'][0]
    start['completion_contract'] = {'all': [{'key': KEY, 'equals': True}]}
    start['story_beat'].update(opening_scene='设备放在工作台上，去向尚未商定。',
        summary='本幕讨论滤芯、旧电池和一套工具的去向。双方商定后才进入收工阶段。')
    ending_route = deepcopy(start['route_gates'][1])
    route = deepcopy(start['route_gates'][0])
    route.update(target_node_id='work', conditions={'all': []})
    route['transition_contract'].pop('accept_input')
    route['transition_contract'].update(advance_on_completion=True, must_deliver=[], bridge_required=False)
    start['route_gates'] = [route]
    work = story['nodes'][1]
    work.update(id='work', type='scene', min_turns=1, route_gates=[ending_route])
    work.pop('ending_id')
    work.pop('terminal')
    story['endings'] = [story['endings'][1]]
    return NumericV2Engine.from_mapping(story)


@pytest.mark.parametrize('actual_departure', [False, True])
def test_ordinary_pending_completion_keeps_current_author_materials(actual_departure):
    engine = _engine()
    session = engine.create_session(session_id='materials', catgirl_binding=_binding(),
        opening_performance={'performance': '先商量去向。', 'suggested_inputs': []})
    outcome = engine.resolve_turn(session, TurnRequestV2('material', 0, '先给我看看物品。'), ())
    messages = actor._turn_messages(engine, session, outcome, '先给我看看物品。', '温和。', '小岚', '你',
        departure_attempt_observed=actual_departure)
    data = prompt_payload(messages[1].content)
    assert ('旧电池' in data['current_scene']) is not actual_departure
    assert '设备放在工作台上' in data['current_scene']
    assert data['next_scene']


def test_tail_schema_and_parser_cannot_generate_a_second_source_response():
    schema = actor_output_schema(transition_required=True, transition_tail_only=True)
    assert set(schema['properties']) == {
        'bridge_scene_narration', 'target_scene_narration', 'target_performance', 'suggested_inputs'}
    payload = {'bridge_scene_narration': '', 'target_scene_narration': '工作台的灯熄灭。',
               'target_performance': '收工。', 'suggested_inputs': []}
    parsed = _parse_output(json.dumps(payload), transition_required=True, bridge_required=False,
                           transition_tail_only=True)
    assert parsed['source_performance'] == ''
    with pytest.raises(NumericV2ActorOutputError):
        _parse_output(json.dumps({**payload, 'source_performance': '重新表示同意。'}),
                      transition_required=True, bridge_required=False, transition_tail_only=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['agree', 'refuse', 'timeout', 'tail_failure', 'player_source'])
async def test_delivery_then_approval_then_one_atomic_transition(tmp_path, monkeypatch, mode):
    engine = _engine('player_input' if mode == 'player_source' else None)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='source-before-route', catgirl_binding=_binding(),
        opening_performance={'performance': '我建议把设备送回仓库。', 'suggested_inputs': []})
    original = runtime.store._path(current.session.session_id).read_bytes()
    calls = []
    source_body = AGREED if mode != 'refuse' else '我不同意这个去向，我们再讨论。'

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((MetricChangeV2('trust', 2, '玩家兑现承诺', INPUT),), True,
            fact_operations=({'op': 'set', 'key': KEY, 'value': True, 'visibility': 'public'},),
            fact_audit=({'key': KEY, 'subject': 'player', 'action': 'propose', 'object': 'equipment',
                         'result': 'agreed', 'evidence': [{'source': 'player_input', 'quote': INPUT}]},))

    async def approve(self, **kwargs):
        assert runtime.store._path(current.session.session_id).read_bytes() == original
        assert KEY not in kwargs['session'].story_state['facts']
        if not kwargs['claims']:
            return ev.NumericV2StoryEvidenceReview()
        if mode == 'player_source':
            assert not kwargs.get('actor_evidence')
            calls.append('player_approval')
            return ev.NumericV2StoryEvidenceReview((0,))
        assert kwargs['actor_evidence'] == source_body
        assert calls == ['source']
        claims = kwargs['claims']
        assert any(row['source'] == 'player_input' and row['quote'] == INPUT for row in claims[0]['evidence'])
        assert any(row['source'] == 'actor_performance' and row['quote'] == source_body
                   for row in claims[0]['evidence'])
        calls.append('source_approval')
        if mode == 'timeout':
            raise ev.NumericV2EvaluatorError('numeric_v2_transition_judge_timeout')
        return ev.NumericV2StoryEvidenceReview((0,) if mode != 'refuse' else ())

    async def generate(self, **kwargs):
        assert runtime.store._path(current.session.session_id).read_bytes() == original
        outcome = kwargs['outcome']
        if outcome.route is None:
            assert KEY not in outcome.session.story_state['facts']
            assert not kwargs.get('approved_completion_facts')
            calls.append('source')
            return {'performance': source_body, 'suggested_inputs': [], 'fact_candidates': []}
        calls.append('tail' if mode != 'player_source' else 'combined')
        if mode == 'tail_failure':
            raise NumericV2ActorOutputError('numeric_v2_actor_fields_invalid')
        source = kwargs.get('transition_source')
        if mode != 'player_source':
            assert source['performance'] == AGREED
        return engine.finalize_transition_performance(outcome, {
            'source_performance': AGREED, 'bridge_scene_narration': '',
            'target_scene_narration': '工作台的灯熄灭。', 'target_performance': '收工。',
            'suggested_inputs': [],
        }, target_opening='工作台的灯熄灭。', bridge_required=False)

    async def contract(*args, **kwargs):
        return ()

    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(wf, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'review_story_evidence', approve)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'verify_contract_boundaries', contract)
    monkeypatch.setattr(wf.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(wf.NumericV2Actor, '_character_profile', lambda self: '温和克制。')
    kwargs = dict(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('source-route', 0, INPUT), ensure_current_binding=lambda _: _binding())
    if mode == 'tail_failure':
        with pytest.raises(NumericV2ActorOutputError):
            await wf.execute_numeric_v2_turn(**kwargs)
        assert runtime.store._path(current.session.session_id).read_bytes() == original
        return
    result = await wf.execute_numeric_v2_turn(**kwargs)
    transitions = [row for row in result.stored.ledger_events if row['from_node_id'] != row['to_node_id']]
    expected_advance = mode in {'agree', 'player_source'}
    assert len(transitions) == int(expected_advance)
    assert result.stored.session.revision == 1
    assert result.stored.session.metrics['trust'] == 22
    assert (KEY in result.stored.session.story_state['facts']) is expected_advance
    if expected_advance:
        assert calls == (['player_approval', 'combined'] if mode == 'player_source'
                         else ['source', 'source_approval', 'tail'])
        assert result.performance['segments'][0]['performance'] == AGREED
        assert result.performance['segments'][2]['performance'] == '收工。'
        assert result.stored.ledger_events[-1]['routing_fact_operations'][0]['key'] == KEY
    else:
        assert calls == ['source', 'source_approval']
        assert result.performance['performance'] == source_body
    restored = await runtime.restore_session(current.session.session_id)
    fork = await runtime.fork_session_for_test(current.session.session_id, session_id='copy', through_revision=1)
    assert restored.session.story_state == result.stored.session.story_state == fork.session.story_state
    assert len([row for row in fork.ledger_events if row['from_node_id'] != row['to_node_id']]) == int(expected_advance)
