"""Only rendered results combine; player choices settle before the Actor drafts."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_options as options, numeric_v2_workflow as workflow
from services.theater.numeric_v2_actor_output import _parse_output, NumericV2ActorOutputError
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_unified_pacing import story_for


def _performance_story():
    story = story_for('山径的避风棚')
    story['nodes'][2]['route_gates'][0]['conditions'] = deepcopy(story['nodes'][0]['route_gates'][1]['conditions'])
    story['nodes'][2]['route_gates'].append(story['nodes'][0]['route_gates'][0])
    story['nodes'][0]['route_gates'] = [story['nodes'][0]['route_gates'][1]]
    story['fact_contract']['facts']['scene:start:done']['evidence_source'] = 'performance'
    return story


@pytest.mark.asyncio
async def test_pending_metric_branch_choice_is_evaluated_before_actor_draft(tmp_path, monkeypatch):
    story = story_for('青柠调饮位')
    start = story['nodes'][0]
    orange_route, lime_route = start['route_gates']
    orange_destination = deepcopy(story['nodes'][2])
    orange_destination.update(id='orange', chapter='橙皮温茶')
    orange_destination['route_gates'][0].update(id='orange_to_stay', target_node_id=orange_route['target_node_id'])
    story['nodes'].append(orange_destination)
    orange_route['target_node_id'] = 'orange'
    orange_route['transition_contract']['fallback_offer'] = '要和我去保温壶旁做橙皮温茶吗？'
    orange_route['conditions']['all'][0]['value'] = 21
    lime_route['conditions']['all'][0]['value'] = 21
    story['metric_schema']['trust'].update(
        increase_criteria=['玩家明确点一杯橙皮温茶。'],
        decrease_criteria=['玩家明确点一杯青柠气泡水。'],
    )
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id='lime-order', catgirl_binding=_binding(), opening_performance=_opening())
    assert runtime.engine.completion_contract_satisfied(current.session) is False
    message = '我点一杯青柠气泡水，就这一杯。'
    calls = []

    async def evaluate(self, **kwargs):
        calls.append('evaluator')
        assert kwargs['message'] == message
        return workflow._parse_evaluation_output(json.dumps({
            'scene_complete': False,
            'transition_intent': 'unclear',
            'metric_changes': {'trust': {'strength': 'normal', 'criterion_id': 'trust.decrease.1'}},
        }), runtime.engine, message, current.session)

    async def generate(self, **kwargs):
        assert calls == ['evaluator']
        assert not kwargs.get('combined_evaluation')
        assert kwargs['outcome'].session.metrics['trust'] == 18
        calls.append('actor')
        return {'performance': '青柠气泡水，行。那就做这杯。', 'suggested_inputs': [], 'transition_offered': False}

    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(workflow, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('order', 0, message), ensure_current_binding=lambda _: _binding())
    assert calls == ['evaluator', 'actor']
    assert result.stored.session.current_node_id == 'start'
    assert result.stored.ledger_events[-1]['metric_changes'][0]['evidence'] == message
    assert result.stored.session.metrics['trust'] == 18


@pytest.mark.asyncio
async def test_player_route_fact_in_body_bucket_retains_independent_evaluation(tmp_path, monkeypatch):
    story = json.loads((Path(__file__).resolve().parents[2] / 'assets/theater/story_a400cd1c2783.json').read_text(encoding='utf-8'))
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='route-choice', catgirl_binding=_binding(), opening_performance=_opening())
    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(workflow, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    message = '走官道吧，稳妥些。'
    key = 'scene:mainline_01:route_decided'
    calls = []

    async def evaluate(self, **kwargs):
        calls.append('evaluator')
        return workflow.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        assert calls == ['evaluator'] and not kwargs.get('combined_evaluation')
        calls.append('actor')
        return {'performance': '官道稳妥，先检查行装。', 'suggested_inputs': [], 'transition_offered': False,
                'fact_candidates': [{'key': key, 'value': '官道', 'evidence_quote': message}]}

    async def forbidden(*args, **kwargs):
        raise AssertionError('A misplaced player source must not start full review')

    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', forbidden)
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('choose', 0, message), ensure_current_binding=lambda _: _binding())
    assert result.stored.session.story_state['facts'][key]['value'] == '官道'
    assert 'scene:mainline_01:departure_confirmed' not in result.stored.session.story_state['facts']
    assert result.stored.session.current_node_id == 'mainline_01'
    assert result.diagnostics['actor_generation_attempts'] == 1
    assert calls == ['evaluator', 'actor']
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['nested', 'body', 'new_choice'])
async def test_independent_judgment_can_recover_earlier_player_route_without_departing(tmp_path, monkeypatch, case):
    from services.theater.numeric_v2_evaluator import _build_messages
    story = json.loads((Path(__file__).resolve().parents[2] / 'assets/theater/story_a400cd1c2783.json').read_text(encoding='utf-8'))
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='missed-route-choice', catgirl_binding=_binding(),
                                          opening_performance=_opening())
    quote = '（拿起木匣检查）稳妥起见，走官道吧。'
    prior = runtime.prepare_turn(current, TurnRequestV2('missed-choice', 0, quote), ())
    current = await runtime.commit_turn(prior, {'performance': '官道稳妥，先检查行装。', 'suggested_inputs': []})
    message = '等等，我改选山径。' if case == 'new_choice' else '（看看鞍袋）还需要准备什么？'
    key = 'scene:mainline_01:route_decided'
    candidate = {'key': key, 'value': '官道', 'evidence_quote': quote}
    calls = []

    async def evaluate(self, **kwargs):
        calls.append('evaluator')
        evidence = {}
        messages = _build_messages(engine, current.session, message,
            recent_ledger_events=current.ledger_events, committed_player_route_evidence=evidence)
        assert quote in messages[1].content
        claim = {'op': 'set', 'key': key, 'value': '官道', 'visibility': 'public', 'confidence': 'confirmed',
                 'subject': 'player', 'action': 'choose', 'object': 'route', 'result': '官道',
                 'evidence': [{'source': 'committed_player_input', 'quote': quote}]}
        return workflow._parse_evaluation_output(json.dumps({'scene_complete': False, 'metric_changes': {},
            'fact_candidates': [] if case == 'body' else [claim]}, ensure_ascii=False),
            engine, message, current.session, current.ledger_events, committed_player_route_evidence=evidence)

    async def invoke(self, messages, **kwargs):
        assert calls == ['evaluator'] and kwargs.get('combined_metric_ids') is None
        calls.append('actor')
        return _parse_output(json.dumps({
            'performance': '文书在这里，缰绳也已经备好。', 'suggested_inputs': [], 'transition_offer': '',
            'fact_candidates': [candidate] if case == 'body' else [],
        }, ensure_ascii=False), fact_candidates_expected=True)

    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(workflow, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    monkeypatch.setattr(workflow.NumericV2Actor, '_invoke', invoke)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('recover-choice', current.session.revision, message), ensure_current_binding=lambda _: _binding())
    facts = result.stored.session.story_state['facts']
    assert facts.get(key, {}).get('value') == ('官道' if case == 'nested' else None)
    assert 'scene:mainline_01:departure_confirmed' not in facts
    assert result.stored.session.current_node_id == 'mainline_01'
    assert calls == ['evaluator', 'actor']
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize('case,expected,source', [
    ('current', True, 'player_input'),
    ('old_input', None, None),
    ('performance_only', None, None),
    ('already_set', False, None),
    ('wrong_value', None, None),
    ('visible_body', True, 'actor_performance'),
])
async def test_standalone_actor_recovers_only_current_player_evidence(tmp_path, monkeypatch, case, expected, source):
    story = story_for('长街')
    start = story['nodes'][0]
    key = 'scene:start:done'
    start['completion_contract'] = {'all': [{'fixed_narration_id': 'visible-options'}]}
    start['story_beat']['fixed_narrations'] = [{
        'id': 'visible-options', 'text': '眼前有两种做法，可以先选一种。',
        'trigger': {'type': 'entry'}, 'after': [], 'required_before_exit': True,
    }]
    start['route_gates'][0]['conditions']['all'][0]['value'] = 90
    start['route_gates'][1]['conditions'] = {'all': [{'type': 'fact_equals', 'key': key, 'value': True}]}
    if case == 'performance_only':
        story['fact_contract']['facts'][key]['evidence_source'] = 'performance'
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id='standalone-choice', catgirl_binding=_binding(),
                                          opening_performance=_opening())
    assert runtime.engine.completion_contract_satisfied(current.session) is True
    message = quote = '我确认现在就这么办。'
    if case == 'old_input':
        prior = runtime.prepare_turn(current, TurnRequestV2('earlier', 0, quote), ())
        current = await runtime.commit_turn(prior, {'performance': '我明白了。', 'suggested_inputs': []})
        message = '现在先等一等。'
    if case == 'already_set':
        message = quote = '我选暂且不办。'
    calls, sources = [], []

    async def evaluate(self, **kwargs):
        calls.append('evaluator')
        return workflow.NumericV2EvaluationResult((), False, fact_operations=({
            'op': 'set', 'key': key, 'value': False, 'visibility': 'public',
        },) if case == 'already_set' else (), fact_audit=({
            'key': key, 'evidence': [{'source': 'player_input', 'quote': message}],
        },) if case == 'already_set' else ())

    async def generate(self, **kwargs):
        calls.append('actor')
        assert not kwargs.get('combined_evaluation')
        return {'performance': quote if case == 'visible_body' else '我已经听清楚了。',
                'transition_offered': False, 'suggested_inputs': [],
                'fact_candidates': [{'key': key, 'value': 1 if case == 'wrong_value' else True,
                                     'evidence_quote': quote}]}

    def trace(event, **data):
        if event == 'fact_candidates.accepted':
            sources.extend(audit['evidence'][0]['source'] for audit in data['audit'])

    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(workflow, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow, 'trace_event', trace)
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('choose', current.session.revision, message), ensure_current_binding=lambda _: _binding())
    assert calls == ['evaluator', 'actor']
    assert sources == ([source] if source else [])
    assert result.stored.session.story_state['facts'].get(key, {}).get('value') is expected
    assert result.stored.session.current_node_id == 'start'
    assert result.stored.ledger_events[-1].get('routing_fact_operations', []) == (
        [{'op': 'set', 'key': key, 'value': False, 'visibility': 'public'}] if case == 'already_set' else [])
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize('fact_source,malformed_changes', [
    ('actor', False), ('actor', True), ('misplaced_actor', False),
    ('misplaced_mixed_performance', False), ('history', False), ('forged', False),
    ('grouped_actor', False), ('grouped_misplaced_actor', False),
])
async def test_combined_turn_reuses_body_and_only_commits_valid_state(tmp_path, monkeypatch, fact_source, malformed_changes):
    story = _performance_story()
    grouped = fact_source.startswith('grouped_')
    if grouped:
        story['fact_contract']['facts']['scene:start:done'].update(
            evidence_source='performance', evidence_anchor=[['揭下'], ['摊稳', '平铺']])
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    top_level = fact_source in {'actor', 'grouped_actor'}
    misplaced = fact_source in {'misplaced_actor', 'misplaced_mixed_performance', 'grouped_misplaced_actor'}
    text = ('（拓完一小块，把纸揭下来平铺在地上）成了，这块算是留住了。'
            if grouped or fact_source == 'misplaced_mixed_performance' else '眼前的事情已经处理完。')
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
        fact = {'key': 'scene:start:done', 'value': True, 'evidence_quote': '平铺在地上' if grouped else text}
        return {'performance': text if top_level or misplaced else '我记得刚才的经过。',
                'suggested_inputs': [], 'transition_offered': False,
                'fact_candidates': [fact] if top_level else [],
                '_turn_visible_history': [] if misplaced else [text],
                '_turn_evaluation': {'scene_complete': True, 'metric_changes': {
                    'trust': {} if malformed_changes else {'strength': 'weak', 'criterion_id': 'trust.increase.1'}},
                    'fact_candidates': [] if top_level else [fact]}}
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


@pytest.mark.parametrize('body,quote,player_input,offer,expected', [
    ('（揭下纸样后，将拓样平铺摊稳）成了。', '将拓样平铺摊稳', '', '', '揭下纸样后，将拓样平铺摊稳'),
    ('（揭下纸样）（将拓样平铺摊稳）成了。', '将拓样平铺摊稳', '', '', None),
    ('（揭下纸样后，将拓样平铺摊稳）（将拓样平铺摊稳）成了。', '将拓样平铺摊稳', '', '', None),
    ('揭下纸样后，将拓样平铺摊稳。', '将拓样平铺摊稳', '', '', None),
    ('（揭下纸样后，将拓样平铺摊稳）成了。', '将拓样平铺摊稳', '将拓样平铺摊稳', '', None),
    ('（揭下纸样后，将拓样平铺摊稳）一起走吧？', '将拓样平铺摊稳', '', '（揭下纸样后，将拓样平铺摊稳）一起走吧？', None),
    ('（揭下纸样后，将拓样平铺摊稳）成了。', '纸样', '', '', None),
    ('（揭下纸样后，我等你回来再摊稳）成了。', '揭下纸样后', '', '', None),
])
def test_grouped_fact_quote_expands_only_its_unique_visible_action(body, quote, player_input, offer, expected):
    candidate = {'key': 'scene:start:done', 'value': True, 'evidence_quote': quote}
    definition = {'evidence_source': 'performance', 'evidence_anchor': [['揭下'], ['摊稳', '平铺']]}
    expanded = workflow._expand_grouped_actor_fact_quote(
        candidate, definition, {'performance': body}, player_input=player_input, published_offer=offer)
    assert expanded['evidence_quote'] == (expected or quote)
    assert candidate['evidence_quote'] == quote


@pytest.mark.asyncio
@pytest.mark.parametrize("found", [True, False])
async def test_combined_history_request_reuses_lookup_and_only_rewrites_with_evidence(tmp_path, monkeypatch, found):
    story = _performance_story()
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="combined-recall", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(workflow, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    calls = []
    query = "之前把原件交给谁"
    lookup = {"status": "found" if found else "not_found",
              "evidence": [{"text": "原件交给值班员。"}] if found else []}

    async def fake_lookup(manager, session, received):
        assert session == current.session and received == query
        calls.append("lookup")
        return lookup

    async def generate(self, **kwargs):
        if kwargs.get("combined_evaluation"):
            assert kwargs["combined_history_lookup"]
            calls.append("draft")
            return {"performance": "我暂时没有原文。", "suggested_inputs": [], "transition_offered": False,
                    "_turn_evaluation": {"scene_complete": False, "metric_changes": {},
                                         "fact_candidates": [], "history_query": query}}
        assert kwargs["history_lookup"] == lookup
        calls.append("answer")
        return {"performance": "原件交给值班员了。", "suggested_inputs": [], "transition_offered": False}

    monkeypatch.setattr(workflow, "lookup_history", fake_lookup)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("recall", 0, "还记得我们之前把原件交给谁了吗？"),
        ensure_current_binding=lambda _: _binding())
    assert calls == (["draft", "lookup", "answer"] if found else ["draft", "lookup"])
    assert result.performance["performance"] == ("原件交给值班员了。" if found else "我暂时没有原文。")
    assert ("我暂时没有原文" in json.dumps(result.stored.session.to_dict(), ensure_ascii=False)) is not found
    assert await runtime.restore_session(current.session.session_id) == result.stored


def test_combined_output_requires_explicit_state_and_keeps_normal_parser_strict():
    payload = {'performance': '我在这里。', 'suggested_inputs': [], 'transition_offered': False,
               'turn_evaluation': {'scene_complete': False, 'metric_changes': {}, 'fact_candidates': []}}
    result = _parse_output(json.dumps(payload), combined_evaluation=True)
    assert result['_turn_evaluation']['scene_complete'] is False
    with_query = deepcopy(payload)
    with_query['turn_evaluation']['history_query'] = '之前把原件交给谁'
    assert _parse_output(json.dumps(with_query), combined_evaluation=True,
                         combined_history_lookup=True)['_turn_evaluation']['history_query'] == '之前把原件交给谁'
    with pytest.raises(NumericV2ActorOutputError, match="turn_evaluation_invalid"):
        _parse_output(json.dumps(with_query), combined_evaluation=True)
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
    config = SimpleNamespace(load_characters=lambda: {"当前猫娘": "小岚", "猫娘": {"小岚": {}}})
    actor = workflow.NumericV2Actor(config)
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
