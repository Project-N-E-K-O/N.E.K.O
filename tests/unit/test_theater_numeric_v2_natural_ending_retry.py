"""Only Runtime-confirmed endings extend the existing narrow repetition retry."""

import pytest

from services.theater import numeric_v2_workflow as wf
from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError
from services.theater.numeric_v2_evaluator import NumericV2EvaluationResult, NumericV2TransitionOfferReview
from services.theater.numeric_v2_options import default_options
from services.theater.numeric_v2_runtime import MetricChangeV2, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding


OLD_SOURCE = '（收回目光）成了。这一路风雪没白走，规矩也没坏。'


async def _setup(tmp_path, monkeypatch, *, mode='natural', failure='source', permanent=False):
    engine = _engine(ordinary=mode != 'natural')
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='ending-retry', catgirl_binding=_binding(),
        opening_performance={'performance': '（望向门边）稍等片刻。', 'suggested_inputs': []})
    previous = runtime.prepare_turn(current, TurnRequestV2('previous', 0, '交接办妥了。'), ())
    previous, performance = engine.finalize_transition_offer_state(previous, {
        'performance': OLD_SOURCE + ('要一起过去吗？' if mode == 'accept' else ''),
        'suggested_inputs': []}, new_offer=mode == 'accept')
    current = await runtime.commit_turn(previous, performance)
    calls = {'evaluator': 0, 'actor': 0}
    actor_messages = []

    async def options():
        return {**{key: key == 'evaluator' for key in default_options()}, 'review': mode == 'accept', 'actor_retry': False, 'suggestion_fill': False}

    async def evaluate(self, **kwargs):
        calls['evaluator'] += 1
        return NumericV2EvaluationResult(
            (MetricChangeV2('trust', 1, '玩家兑现承诺', '任务已毕。'),),
            True, natural_ending_ready=True,
            transition_intent=mode if mode in {'accept', 'initiate'} else 'unclear')

    async def invoke(self, messages, **kwargs):
        calls['actor'] += 1
        actor_messages.append(messages)
        failing = permanent or calls['actor'] == 1
        if failing and failure != 'source':
            error = NumericV2ActorOutputError('numeric_v2_actor_invalid_json' if failure == 'format'
                                            else 'numeric_v2_actor_repeated_output')
            if failure != 'format':
                error.repetition_guard = failure
            raise error
        if not kwargs['transition_required']:
            # A claimed natural ending cannot change an ordinary route into an ending.
            error = NumericV2ActorOutputError('numeric_v2_actor_repeated_output')
            error.repetition_guard = 'transition_source'
            raise error
        return {
            'source_performance': current.session.performance_history[-1]['performance'] if failing
                                  else '（轻轻点头）一路平安。',
            'bridge_scene_narration': '窗外的雨渐渐停了。',
            'target_scene_narration': '远处街灯依次亮起。',
            'target_performance': '（望向长街）愿你在新的旅途中找到自己的方向。',
            'suggested_inputs': [],
        }

    monkeypatch.setattr(wf, 'aload_theater_module_options', options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(wf.NumericV2Actor, '_invoke', invoke)
    monkeypatch.setattr(wf.NumericV2Actor, '_character_profile', lambda self: '温和克制。')
    if mode == 'accept':
        async def review(self, **kwargs):
            return NumericV2TransitionOfferReview(False, False, (), ())

        async def propose(self, **kwargs):
            return []

        monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'validate_transition_offer', review)
        monkeypatch.setattr(wf.NumericV2NarrativeProposer, 'propose', propose)
    return runtime, current, calls, actor_messages


async def _execute(runtime, current, diagnostics):
    return await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('finish', current.session.revision, '任务已毕。'),
        ensure_current_binding=lambda _: _binding(), diagnostics_sink=diagnostics)


@pytest.mark.asyncio
@pytest.mark.parametrize('guard', ['source', 'earlier_session', 'transition_target'])
async def test_runtime_natural_ending_retries_once_and_commits_without_reevaluation(tmp_path, monkeypatch, guard):
    runtime, current, calls, messages = await _setup(tmp_path, monkeypatch, failure=guard)
    diagnostics = {}
    result = await _execute(runtime, current, diagnostics)
    assert calls == {'evaluator': 1, 'actor': 2}
    assert result.stored.session.status == 'ended'
    assert result.stored.session.current_node_id == 'ending_leave'
    assert result.stored.session.revision == current.session.revision + 1
    assert result.stored.session.metrics['trust'] == current.session.metrics['trust'] + 1
    assert result.stored.ledger_events[-1]['transition_intent'] == 'unclear'
    assert len(result.stored.ledger_events) == len(current.ledger_events) + 1
    assert result.performance['segments'][0]['performance'] == '（轻轻点头）一路平安。'
    assert diagnostics['actor_generation_attempts'] == 2
    assert diagnostics['actor_repeated_output_guards'] == {
        'transition_source' if guard == 'source' else guard: 1}
    assert '重复位置：' in messages[1][0].content
    assert await runtime.restore_session(current.session.session_id) == result.stored
    fork = await runtime.fork_session_for_test(current.session.session_id,
        session_id='ending-retry-fork', through_revision=result.stored.session.revision)
    assert fork.session.status == 'ended'
    assert fork.session.performance_history == result.stored.session.performance_history


@pytest.mark.asyncio
async def test_natural_ending_permanent_source_repetition_stops_after_two_and_rolls_back(tmp_path, monkeypatch):
    runtime, current, calls, _ = await _setup(tmp_path, monkeypatch, permanent=True)
    diagnostics = {}
    with pytest.raises(NumericV2ActorOutputError, match='repeated_output'):
        await _execute(runtime, current, diagnostics)
    assert calls == {'evaluator': 1, 'actor': 2}
    assert diagnostics['actor_repeated_output_guards'] == {'transition_source': 2}
    assert diagnostics['actor_repeated_output_retry_aborted'] == 1
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
@pytest.mark.parametrize(('mode', 'failure'), [
    ('ordinary', 'source'),
    ('natural', 'format'), ('natural', 'previous_performance'),
])
async def test_nonterminal_and_other_errors_do_not_get_narrow_retry(tmp_path, monkeypatch, mode, failure):
    runtime, current, calls, _ = await _setup(tmp_path, monkeypatch, mode=mode, failure=failure)
    diagnostics = {}
    with pytest.raises(NumericV2ActorOutputError):
        await _execute(runtime, current, diagnostics)
    assert calls == {'evaluator': 1, 'actor': 1}
    assert diagnostics['actor_generation_attempts'] == 1
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
async def test_accepted_nonterminal_transition_keeps_existing_single_retry(tmp_path, monkeypatch):
    runtime, current, calls, _ = await _setup(tmp_path, monkeypatch, mode='accept')
    result = await _execute(runtime, current, {})
    assert calls == {'evaluator': 1, 'actor': 2}
    assert result.stored.session.status == 'active'
    assert result.stored.session.current_node_id == 'middle'
    assert result.stored.ledger_events[-1]['transition_intent'] == 'accept'
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
async def test_existing_initiate_fixture_is_runtime_advanced_and_retries_once(tmp_path, monkeypatch):
    # This used to be called a nonterminal negative control, but Runtime actually
    # advances it to the active middle node. Keep the real fixture as a positive;
    # unavailable/staying initiation is covered separately with a blocked route.
    runtime, current, calls, _ = await _setup(tmp_path, monkeypatch, mode='initiate')
    prepared = runtime.prepare_turn(current, TurnRequestV2('finish', current.session.revision, '任务已毕。'),
        (MetricChangeV2('trust', 1, '玩家兑现承诺', '任务已毕。'),), scene_complete=True,
        natural_ending_ready=True, transition_intent='initiate')
    assert prepared.session.current_node_id == 'middle'
    assert prepared.session.status == 'active'
    assert prepared.ledger_event['transition_intent'] == 'initiate'
    result = await _execute(runtime, current, {})
    assert calls == {'evaluator': 1, 'actor': 2}
    assert result.stored.session.current_node_id == 'middle'
    assert result.stored.ledger_events[-1]['transition_intent'] == 'initiate'
    assert await runtime.restore_session(current.session.session_id) == result.stored
