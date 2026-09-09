"""复查和改稿次数有界；持续语义否定后采用末稿，技术故障仍保护原子提交。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_runtime import MetricChangeV2, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


@pytest.mark.asyncio
@pytest.mark.parametrize('formal', [False, True])
@pytest.mark.parametrize('mode', ['safe', 'buttons', 'release', 'offer', 'invalid_offer', 'reject', 'timeout', 'protocol'])
async def test_dispute_once_then_commit_latest_complete_reply(monkeypatch, tmp_path, formal, mode):
    """持续否定最多复查一次、改稿一次；采用末稿并只计分一次，显示与冷恢复一致。"""
    engine = _engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='dispute', catgirl_binding=_binding(), opening_performance=_opening())
    calls, generations, diagnostics = [], [], {}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult(
            (MetricChangeV2('trust', 2, '玩家兑现承诺', '给你毛巾。'),), formal, natural_ending_ready=formal)

    async def generate(self, **kwargs):
        generations.append(kwargs)
        if formal:
            candidate = {**_candidate(), 'source_performance': f'（点头）这是第{len(generations)}版回应。'}
            return engine.finalize_transition_performance(kwargs['outcome'], candidate, target_opening='旧开场。')
        return dict(performance=f'（点头）这是第{len(generations)}版回应。',
                    suggested_inputs=['（点头）谢谢。'], transition_offered=False)

    async def review(self, **kwargs):
        calls.append(kwargs)
        if kwargs.get('dispute_review'):
            # 同一个候选、同一份历史独立判断，不传初判理由，避免复查被它牵引。
            assert {k: v for k, v in kwargs.items() if k != 'dispute_review'} == calls[0]
            if mode == 'timeout':
                raise evaluator.NumericV2EvaluatorError('numeric_v2_transition_judge_timeout')
            if mode == 'protocol':
                raise evaluator.NumericV2EvaluatorOutputError('invalid_output')
        bad = mode in ('reject', 'timeout', 'protocol') or (mode == 'release' and len(calls) == 1)
        # 无效邀请只适用于普通回合；正式转场的两个邀请布尔量不决定正文是否可交付。
        if formal and mode == 'invalid_offer':
            bad = True
        return evaluator.NumericV2TransitionOfferReview(
            offer_present=mode in ('offer', 'invalid_offer'), valid=mode == 'offer' and bool(kwargs.get('dispute_review')),
            body_violations=('player_action',) if bad else (),
            unsafe_suggestion_indexes=(0,) if mode == 'buttons' else (),
            failure_reason='被判越权。' if bad else '',
        )

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    kwargs = dict(config_manager=object(), runtime=runtime, current=current,
                  turn=TurnRequestV2('one', 0, '给你毛巾。'), ensure_current_binding=lambda _: _binding(),
                  diagnostics_sink=diagnostics)
    rejected = mode in ('invalid_offer', 'reject', 'timeout', 'protocol')
    result = await workflow.execute_numeric_v2_turn(**kwargs)
    assert result.stored.session.revision == 1 and len(result.stored.ledger_events) == 1
    assert result.stored.session.metrics['trust'] == current.session.metrics['trust'] + 2
    assert result.stored.session.current_node_id == ('ending_leave' if formal else 'start')
    # 第一版必须被末稿替代，末稿进入唯一正式历史；不能只显示在界面或同时提交两稿。
    saved = result.stored.session.performance_history[-1]
    assert f'第{2 if rejected else 1}版回应' in str(saved)
    if rejected:
        assert '第1版回应' not in str(saved)
    assert await NumericV2Runtime(engine, tmp_path).restore_session('dispute') == result.stored
    assert diagnostics['semantic_review_fallback'] is rejected
    assert diagnostics['semantic_review_fallback_phase'] == (('transition' if formal else 'ordinary') if rejected else '')
    if mode == 'invalid_offer' and not formal:
        # 已公开但被判无效的邀请也保留等待接受；不会在这一轮绕过Runtime直接换幕。
        assert result.stored.session.transition_offered
    assert 'semantic_review_fallback' not in str(result.stored.session.to_dict())
    disputed = mode not in ('safe', 'buttons')
    assert len(calls) == (3 if rejected else 2 if disputed else 1)
    assert len(generations) == (2 if rejected else 1)
    assert sum(bool(c.get('dispute_review')) for c in calls) == int(disputed)
    assert diagnostics['transition_judge_calls'] == len(calls)
    assert diagnostics['dispute_review_degraded'] == (mode in ('timeout', 'protocol'))
    assert not diagnostics['transition_judge_degraded']


@pytest.mark.asyncio
async def test_dispute_model_options_are_local_and_evidence_identical(monkeypatch):
    """快速→思考→快速的参数不互相污染；消息和解析协议保持相同。"""
    factories, evidence = [], []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages):
            evidence.append([m.content for m in messages])
            return SimpleNamespace(content=json.dumps(dict(offer_present=False, valid=False,
                body_violations=[], unsafe_suggestion_indexes=[], failure_reason='')))

    async def config(_):
        return dict(model='test-thinking', base_url='http://test.invalid')

    async def factory(*args, **kwargs):
        factories.append(kwargs)
        return Client()

    monkeypatch.setattr(evaluator, '_model_config', config)
    monkeypatch.setattr(evaluator, 'create_chat_llm_async', factory)
    monkeypatch.setattr(evaluator, 'focus_extra_body', lambda _: {'enable_thinking': True})
    engine = _engine()
    session = engine.create_session(session_id='params', catgirl_binding=_binding(), opening_performance=_opening())
    worker = evaluator.NumericV2MetricEvaluator(object())
    kwargs = dict(engine=engine, session=session, message='谢谢。', actor_performance=_opening())
    for disputed in (False, True, False):
        await worker.validate_transition_offer(**kwargs, dispute_review=disputed)
    assert factories[0] == factories[2]
    assert 'extra_body' not in factories[0]
    assert factories[0]['max_completion_tokens'] == 190
    assert factories[1]['extra_body'] == {'enable_thinking': True}
    assert factories[1]['max_completion_tokens'] == 4096
    assert factories[1]['timeout'] == 30
    assert evidence[0] == evidence[1] == evidence[2]
    # 不支持思考的模型明确失败，不能悄悄重复一次相同的快速请求。
    monkeypatch.setattr(evaluator, 'focus_extra_body', lambda _: None)
    with pytest.raises(evaluator.NumericV2EvaluatorUnavailableError):
        await worker.validate_transition_offer(**kwargs, dispute_review=True)
    assert len(factories) == 3


@pytest.mark.asyncio
async def test_dispute_real_timeout_becomes_evaluator_error(monkeypatch):
    """实际等待超时走既有错误类型，工作流才能保留原拦截而非误判放行。"""
    class SlowClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages):
            await asyncio.sleep(1)

    async def config(_):
        return dict(model='test-thinking', base_url='http://test.invalid')

    async def factory(*args, **kwargs):
        return SlowClient()

    monkeypatch.setattr(evaluator, '_model_config', config)
    monkeypatch.setattr(evaluator, 'create_chat_llm_async', factory)
    monkeypatch.setattr(evaluator, 'focus_extra_body', lambda _: {'enable_thinking': True})
    monkeypatch.setattr(evaluator, 'NUMERIC_V2_DISPUTE_JUDGE_TIMEOUT_SECONDS', 0.001)
    engine = _engine()
    session = engine.create_session(session_id='timeout', catgirl_binding=_binding(), opening_performance=_opening())
    with pytest.raises(evaluator.NumericV2EvaluatorError, match='timeout'):
        await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(
            engine=engine, session=session, message='好。', actor_performance=_opening(), dispute_review=True)
