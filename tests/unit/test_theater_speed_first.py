"""Production pause skips every model audit without freezing normal generation or facts."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_options as options, numeric_v2_workflow as workflow
from services.theater.numeric_v2_evaluator import NumericV2EvaluationResult
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_unified_pacing import story_for


@pytest.fixture
def speed_first(monkeypatch):
    from utils import preferences
    stored = {'theaterModule' + ''.join(part.title() for part in key.split('_')): False
              for key in options.default_options()}
    original = deepcopy(stored)
    async def load():
        raise AssertionError('removed theater switches must not read preferences')
    monkeypatch.setattr(preferences, 'aload_global_entry_flags', load)
    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(workflow, 'aload_theater_module_options', options.aload_theater_module_options)
    async def forbidden(*args, **kwargs):
        raise AssertionError('speed-first runtime must not call a model audit')
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', forbidden)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'verify_contract_boundaries', forbidden)
    monkeypatch.setattr(workflow.NumericV2NarrativeProposer, 'propose', forbidden)
    monkeypatch.setattr(workflow.NumericV2NarrativeProposer, 'review', forbidden)
    yield forbidden
    assert stored == original  # Temporary pause never overwrites the user's preferences.


@pytest.mark.asyncio
async def test_production_options_pause_audits_even_when_saved_on(speed_first):
    active = await options.aload_theater_module_options()
    assert all(active[key] is False for key in ('review', 'dispute', 'review_contract'))
    assert active['evaluator'] and active['review_delivery']


@pytest.mark.asyncio
async def test_all_modules_default_on_without_legacy_preferences(speed_first, monkeypatch):
    assert all(options.default_options().values())
    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', True)
    assert await options.aload_theater_module_options() == options.default_options()


def test_removed_settings_page_and_options_api_are_unavailable(tmp_path, monkeypatch):
    from tests.unit.test_theater_numeric_v2_router import _client
    from main_routers.pages_router import router
    with _client(tmp_path, monkeypatch) as client:
        client.app.include_router(router)
        assert client.get('/theater/settings').status_code == 404
        assert client.get('/api/theater-numeric/options').status_code == 404
        assert client.post('/api/theater-numeric/options', json={'modules': {'evaluator': False}}).status_code == 404


@pytest.mark.asyncio
async def test_opening_with_author_boundaries_does_not_call_review(speed_first, monkeypatch):
    story = story_for('山径的避风棚')
    story['nodes'][0]['story_beat']['opening_only_boundaries'] = ['不得在公开开场披露后续身份。']
    async def opening(self, **kwargs):
        return {'performance': '（抬眼）你好。', 'suggested_inputs': []}
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_opening', opening)
    result = await workflow.generate_validated_opening(
        engine=NumericV2Engine.from_mapping(story), config_manager=object(), session_id='speed-opening',
        catgirl_binding=_binding(), actor_budget_profile='balanced')
    assert result['performance'] == '（抬眼）你好。'


@pytest.mark.asyncio
@pytest.mark.parametrize('fact_source', ['evaluator', 'actor'])
async def test_ordinary_turn_keeps_two_core_calls_and_commits_facts(speed_first, tmp_path, monkeypatch, fact_source):
    engine = NumericV2Engine.from_mapping(story_for('山径的避风棚'))
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='speed-turn', catgirl_binding=_binding(), opening_performance=_opening())
    calls = []
    text = '眼前的事情已经处理完。'
    key = 'scene:start:done'
    async def evaluate(self, **kwargs):
        calls.append('evaluator')
        return NumericV2EvaluationResult((), False, fact_operations=(
            ({'op': 'set', 'key': key, 'value': True, 'visibility': 'public'},)
            if fact_source == 'evaluator' else ()))
    async def generate(self, **kwargs):
        calls.append('actor')
        return {'performance': text, 'suggested_inputs': [], 'fact_candidates': [
            {'key': key, 'value': True, 'evidence_quote': text}]}
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和克制。')
    diagnostics = {}
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('one', 0, text), ensure_current_binding=lambda _: _binding(),
        diagnostics_sink=diagnostics)
    assert calls == ['evaluator', 'actor']
    assert result.stored.session.revision == 1
    assert result.stored.session.story_state['facts'][key]['value'] is True
    assert diagnostics['transition_judge_calls'] == 0
    assert diagnostics['completion_fact_review_calls'] == 0


@pytest.mark.asyncio
async def test_reinvitation_uses_author_contract_without_generation_or_review(speed_first, tmp_path, monkeypatch):
    from tests.unit.test_theater_review_round8_invitation import setup_case, OFFER
    runtime, current = await setup_case(tmp_path, monkeypatch, mode='on')
    monkeypatch.setattr(workflow, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', speed_first)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', speed_first)
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('reinvite', current.session.revision, '', 'reinvite'),
        ensure_current_binding=lambda _: _binding())
    assert result.performance['performance'] == OFFER
    assert result.stored.session.current_node_id == current.session.current_node_id


@pytest.mark.asyncio
async def test_ending_question_uses_only_narrow_check_while_full_audits_stay_paused(speed_first, tmp_path, monkeypatch):
    from tests.unit.test_theater_numeric_v2_terminal_question_default import _setup, CHOICE
    _, _, calls, diagnostics, execute = await _setup(
        tmp_path, monkeypatch, drafts=(CHOICE, '（点头）故事到这里落幕。'))
    monkeypatch.setattr(workflow, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', speed_first)
    result = await execute()
    assert result.stored.session.status == 'ended'
    assert calls['actor'] == 2 and len(calls['terminal']) == 1
    assert diagnostics['terminal_question_review_calls'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('reply,intent,advance', [
    ('好，明早见。', 'accept', True),
    ('先不决定，聊一会儿。', 'reject', False),
    ('你打算几点出发？', 'unclear', False),
])
async def test_generated_offer_survives_paused_reviews_and_acceptance_uses_evaluator(
    speed_first, tmp_path, monkeypatch, reply, intent, advance,
):
    story = story_for('关外院落')
    story['nodes'][0].pop('completion_contract')
    story['fact_contract'] = {'facts': {}}
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='sleep-offer', catgirl_binding=_binding(),
                                          opening_performance=_opening())
    calls = []
    offer = '今晚先休息，明早一起去关外院落交付，好吗？'

    async def evaluate(self, **kwargs):
        calls.append('evaluator')
        return NumericV2EvaluationResult((), True,
            transition_intent='unclear' if kwargs['session'].revision == 0 else intent,
            transition_reply_target='pending_transition')

    async def generate(self, **kwargs):
        calls.append('actor')
        if kwargs['session'].revision == 0:
            return {'performance': offer, 'suggested_inputs': ['好，明早见。', '先不决定。'],
                    'transition_offered': True}
        if advance:
            from tests.unit.test_theater_numeric_v2_transition_history import _candidate
            return engine.finalize_transition_performance(kwargs['outcome'], _candidate(),
                                                          target_opening='关外的院落。')
        return {'performance': '那就先在这里聊聊。', 'suggested_inputs': [], 'transition_offered': False}

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和克制。')
    offered = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime,
        current=current, turn=TurnRequestV2('offer', 0, '接下来呢？'), ensure_current_binding=lambda _: _binding())
    assert offered.stored.session.transition_offered
    assert offered.performance['performance'] == offer
    assert offered.performance.get('transition_offer_source') != 'reviewed'
    restored = await runtime.restore_session(current.session.session_id)
    assert restored == offered.stored
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime,
        current=restored, turn=TurnRequestV2('reply', 1, reply), ensure_current_binding=lambda _: _binding())
    assert (result.stored.session.current_node_id != current.session.current_node_id) is advance
    assert result.stored.session.transition_offered is (intent == 'unclear')
    assert calls == ['evaluator', 'actor', 'evaluator', 'actor']
    assert result.diagnostics['transition_judge_calls'] == 0
    assert result.diagnostics['narrative_model_attempts'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('requested,intent,expect_author_offer', [
    (True, 'unclear', True), (False, 'unclear', True), (True, 'reject', False),
])
async def test_completed_actor_offer_has_the_actual_author_arrangement(
    speed_first, tmp_path, monkeypatch, requested, intent, expect_author_offer,
):
    engine = NumericV2Engine.from_mapping(story_for('山径的避风棚'))
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='completed-offer', catgirl_binding=_binding(),
        opening_performance=_opening())
    prepared = runtime.prepare_turn(current, TurnRequestV2('complete', 0, '完成眼前工作。'), (),
        fact_operations=({'op': 'set', 'key': 'scene:start:done', 'value': True, 'visibility': 'public'},))
    current = await runtime.commit_turn(prepared, {'performance': '眼前的事情已经处理完。', 'suggested_inputs': []})
    calls = []
    body = '好，我们试着把手里的东西轻轻转一下。'
    async def evaluate(self, **kwargs):
        calls.append('evaluator')
        return NumericV2EvaluationResult((), True, transition_intent=intent)
    async def generate(self, **kwargs):
        calls.append('actor')
        return {'performance': body, 'suggested_inputs': ['再转一次。', '先停一下。'],
                'transition_offered': requested}
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和克制。')
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('respond', 1, '再试着转一下。'), ensure_current_binding=lambda _: _binding())
    contract = engine.preview_route('start', current.session.metrics)['transition_contract']
    assert (contract['fallback_offer'] in result.performance['performance']) is expect_author_offer
    assert body in result.performance['performance']
    if expect_author_offer:
        assert result.performance['suggested_inputs'][0] == contract['accept_input']
        assert '先停一下。' in result.performance['suggested_inputs']
        assert result.stored.session.transition_offered
    assert calls == ['evaluator', 'actor']
    assert result.stored.session.current_node_id == 'start'
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize('declared,complete,intent,expected', [
    (False, True, 'unclear', True),
    (False, False, 'unclear', False),
    (False, True, 'reject', False),
    (True, True, 'unclear', False),
])
async def test_legacy_completed_scene_can_offer_without_overriding_explicit_conditions(
    speed_first, tmp_path, monkeypatch, declared, complete, intent, expected,
):
    story = story_for('山径的避风棚')
    if not declared:
        story['nodes'][0].pop('completion_contract')
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='legacy-completion', catgirl_binding=_binding(),
        opening_performance=_opening())
    calls = []

    async def evaluate(self, **kwargs):
        calls.append('evaluator')
        return NumericV2EvaluationResult((), complete, transition_intent=intent)

    async def generate(self, **kwargs):
        calls.append('actor')
        return {'performance': '眼前的事情已经处理完。', 'suggested_inputs': ['先聊一会儿。'],
                'transition_offered': False}

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和克制。')
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime,
        current=current, turn=TurnRequestV2('ask', 0, '接下来呢？'),
        ensure_current_binding=lambda _: _binding())
    contract = engine.preview_route('start', current.session.metrics)['transition_contract']
    assert (contract['fallback_offer'] in result.performance['performance']) is expected
    assert result.stored.session.transition_offered is expected
    assert result.stored.session.current_node_id == 'start'
    assert calls == ['evaluator', 'actor']
    if expected:
        assert result.performance['suggested_inputs'][0] == contract['accept_input']
        assert '先聊一会儿。' in result.performance['suggested_inputs']
    assert await runtime.restore_session(current.session.session_id) == result.stored
