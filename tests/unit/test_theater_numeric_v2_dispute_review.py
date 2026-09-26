"""Bound review and rewrite attempts; adopt the final draft after persistent semantic rejection while keeping technical failures atomic."""

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
    """After at most one dispute review and rewrite, adopt the final draft and score once, keeping display and cold recovery consistent."""
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
            assert {k: v for k, v in kwargs.items() if k not in {'dispute_review', 'timeout_seconds'}} == {
                k: v for k, v in calls[-2].items() if k != 'timeout_seconds'
            }
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
    offer_repaired_before_dispute = not formal and mode in ('offer', 'invalid_offer')
    result = await workflow.execute_numeric_v2_turn(**kwargs)
    assert result.stored.session.revision == 1 and len(result.stored.ledger_events) == 1
    assert result.stored.session.metrics['trust'] == current.session.metrics['trust'] + 2
    assert result.stored.session.current_node_id == ('ending_leave' if formal else 'start')
    # 第一版必须被末稿替代，末稿进入唯一正式历史；不能只显示在界面或同时提交两稿。
    saved = result.stored.session.performance_history[-1]
    assert f'第{2 if rejected or offer_repaired_before_dispute else 1}版回应' in str(saved)
    if rejected or offer_repaired_before_dispute:
        assert '第1版回应' not in str(saved)
    assert await NumericV2Runtime(engine, tmp_path).restore_session('dispute') == result.stored
    assert diagnostics['semantic_review_fallback'] is rejected
    assert diagnostics['semantic_review_fallback_phase'] == (('transition' if formal else 'ordinary') if rejected else '')
    if mode == 'invalid_offer' and not formal:
        # 末稿可提交，但复核无效的新邀请不能锁存到会话，也不能在后续回合被接受。
        assert not result.stored.session.transition_offered
    assert 'semantic_review_fallback' not in str(result.stored.session.to_dict())
    # 正式转场只核对三段正文；按钮本身的 valid=false 不再触发争议复查。
    disputed = mode not in ('safe', 'buttons') and not (formal and mode == 'offer')
    assert len(calls) == (3 if rejected or offer_repaired_before_dispute else 2 if disputed else 1)
    assert len(generations) == (2 if rejected or offer_repaired_before_dispute else 1)
    assert sum(bool(c.get('dispute_review')) for c in calls) == int(disputed)
    assert diagnostics['dispute_review_deferred_offer_repair'] == int(offer_repaired_before_dispute)
    assert diagnostics['transition_judge_calls'] == len(calls)
    assert diagnostics['dispute_review_degraded'] == (mode in ('timeout', 'protocol'))
    assert not diagnostics['transition_judge_degraded']


@pytest.mark.asyncio
async def test_ordinary_invalid_offer_repairs_before_dispute(monkeypatch, tmp_path):
    """普通回合仅邀请无效时先改写；改写已通过就不再等待争议复查。"""  # noqa: DOCSTRING_CJK

    engine = _engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id='repair_before_dispute',
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    generations, reviews, diagnostics = [], [], {}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        generations.append(kwargs)
        return {
            'performance': f'（点头）这是第{len(generations)}版回应。',
            'suggested_inputs': ['（点头）继续。'],
            'transition_offered': True,
        }

    async def review(self, **kwargs):
        reviews.append(kwargs)
        valid = len(generations) > 1
        return evaluator.NumericV2TransitionOfferReview(
            offer_present=True,
            valid=valid,
            body_violations=(),
            unsafe_suggestion_indexes=(),
            failure_reason='' if valid else '邀请未对应当前出口合同。',
        )

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')

    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=current,
        turn=TurnRequestV2('repair_offer', 0, '我们接下来怎么办？'),
        ensure_current_binding=lambda _: _binding(),
        diagnostics_sink=diagnostics,
    )

    assert len(generations) == 2
    assert len(reviews) == 2
    assert not any(call.get('dispute_review') for call in reviews)
    assert diagnostics['dispute_review_attempts'] == 0
    assert diagnostics['dispute_review_deferred_offer_repair'] == 1
    assert result.performance['transition_offered'] is True


@pytest.mark.asyncio
async def test_review_cannot_call_the_same_explicit_movement_unauthorized(monkeypatch, tmp_path):
    """复核理由承认移动来自本轮明确要求时，零调用清除矛盾枚举并保留正文。"""  # noqa: DOCSTRING_CJK

    engine = _engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id='explicit_movement',
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    generations, reviews = [], []

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult(
            (), False, transition_intent='unclear', interaction_intent='scene_action')

    async def generate(self, **kwargs):
        generations.append(kwargs)
        return {
            'performance': '（扶稳你的手臂）好，我们沿着墙边慢慢走。',
            'scene_narration': '两人开始向左侧走廊移动。',
            'suggested_inputs': ['（跟上她）继续走。', '（停下脚步）先等等。'],
            'transition_offered': False,
        }

    async def review(self, **kwargs):
        reviews.append(kwargs)
        return evaluator.NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            body_violations=('player_action',),
            unsafe_suggestion_indexes=(),
            failure_reason='正文scene_update直接执行了玩家本轮明确表达的移动动作，构成player_action。',
        )

    assert workflow._review_mislabels_explicit_player_movement(
        evaluator.NumericV2TransitionOfferReview(
            False,
            False,
            ('player_action',),
            (),
            '正文在scene_update中直接执行了玩家本轮才明确要求的转移动作，构成新增未授权的玩家行动。',
        )
    ) is True
    # 真正的额外操作和目的地错配不能借相似措辞清除。
    for reason in (
        '玩家本轮明确要求移动，但正文却前往不同地点。',
        '正文替玩家执行了未授权的同时按下密钥动作。',
    ):
        assert workflow._review_mislabels_explicit_player_movement(
            evaluator.NumericV2TransitionOfferReview(
                False, False, ('player_action',), (), reason)) is False

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')

    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=current,
        turn=TurnRequestV2('move_now', 0, '那带路吧，我们现在过去。'),
        ensure_current_binding=lambda _: _binding(),
    )

    assert len(generations) == 1
    assert len(reviews) == 1
    assert result.diagnostics['explicit_player_movement_flags_cleared'] == 1
    assert result.diagnostics['dispute_review_attempts'] == 0
    assert result.diagnostics['semantic_rewrite_attempts'] == 0
    assert result.performance['scene_narration'] == '两人开始向左侧走廊移动。'


@pytest.mark.asyncio
async def test_confirmed_departure_drops_conflicting_scene_update_without_actor_rewrite(
    monkeypatch,
    tmp_path,
):
    """玩家已明确离场时，只删除被定位为冲突的场景更新，不再整稿重写。"""  # noqa: DOCSTRING_CJK

    engine = _engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id='departure_scene_update_guard',
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    generations, reviews = [], []

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult(
            (), False, transition_intent='unclear', interaction_intent='scene_action')

    async def generate(self, **kwargs):
        generations.append(kwargs)
        return {
            'performance': '（点头目送你离开）好，路上小心，明天见。',
            'scene_narration': '你冒雨重新回到店里，站在刚才的位置。',
            'fact_candidates': [{'key': 'scene:start:returned', 'value': True}],
            'suggested_inputs': ['（挥挥手）明天见。', '（继续往前走）我先回去了。'],
            'transition_offered': False,
        }

    async def review(self, **kwargs):
        reviews.append(kwargs)
        return evaluator.NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            body_violations=('player_action',),
            unsafe_suggestion_indexes=(),
            failure_reason=(
                'scene_update把已离场玩家写成重新回到当前地点，'
                '和player_action_projection冲突。'
            ),
            fact_candidates=({'key': 'scene:start:returned', 'value': True},),
        )

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')

    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=current,
        turn=TurnRequestV2('leave_with_future', 0, '（推门离开）明天见。'),
        ensure_current_binding=lambda _: _binding(),
    )

    assert len(generations) == 1
    assert len(reviews) == 1
    assert result.diagnostics['player_action_projection_conflicts'] == 1
    assert result.diagnostics['player_action_projection_safe_degrades'] == 1
    assert result.diagnostics['dispute_review_attempts'] == 0
    assert result.diagnostics['semantic_rewrite_attempts'] == 0
    assert result.diagnostics['semantic_review_fallback'] is False
    assert 'scene_narration' not in result.performance
    assert 'fact_candidates' not in result.performance
    assert result.performance['performance'] == '（点头目送你离开）好，路上小心，明天见。'


def test_confirmed_departure_does_not_trim_conflict_outside_scene_update():
    """冲突涉及猫娘对白时不能假装只删场景更新就安全。"""  # noqa: DOCSTRING_CJK

    review = evaluator.NumericV2TransitionOfferReview(
        offer_present=False,
        valid=False,
        body_violations=('player_action',),
        unsafe_suggestion_indexes=(),
        failure_reason=(
            'performance与scene_update都把已离场玩家写成重新回到当前地点。'
        ),
    )
    projection = workflow.project_player_action_result('（推门离开）明天见。')
    candidate = {
        'performance': '（惊讶地抬头）你怎么又回来了？',
        'scene_narration': '你重新回到店里。',
        'suggested_inputs': [],
        'transition_offered': False,
    }

    assert workflow._player_action_projection_conflicts_with_review(
        review,
        projection,
    ) is True
    assert workflow._safe_degrade_conflicting_scene_update(
        candidate,
        review,
        projection,
    ) is None


def test_projection_can_clear_only_a_review_that_acknowledges_the_same_departure():
    """结构化离场证据可补足复核理由，但不能覆盖返回当前幕的冲突。"""  # noqa: DOCSTRING_CJK

    projection = workflow.project_player_action_result('（推门离开）明天见。')
    assert workflow._review_mislabels_explicit_player_movement(
        evaluator.NumericV2TransitionOfferReview(
            False,
            False,
            ('player_action',),
            (),
            '正文承接玩家已经完成的离开动作，仍被标成player_action。',
        ),
        projection,
    ) is True
    assert workflow._review_mislabels_explicit_player_movement(
        evaluator.NumericV2TransitionOfferReview(
            False,
            False,
            ('player_action',),
            (),
            '正文把玩家离开后重新回到当前地点写成了player_action。',
        ),
        projection,
    ) is False


@pytest.mark.asyncio
async def test_dispute_model_options_are_local_and_evidence_identical(monkeypatch):
    """Keep fast, thinking and final fast-review parameters isolated while sharing messages and the parsing contract."""
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
    # 争议时限按复核预算重定后仍须与快检区分，不能与普通时限混用。
    assert factories[1]['timeout'] == evaluator.NUMERIC_V2_DISPUTE_JUDGE_TIMEOUT_SECONDS
    assert factories[1]['timeout'] > factories[0]['timeout']
    assert evidence[0] == evidence[1] == evidence[2]
    # 不支持思考的模型明确失败，不能悄悄重复一次相同的快速请求。
    monkeypatch.setattr(evaluator, 'focus_extra_body', lambda _: None)
    with pytest.raises(evaluator.NumericV2EvaluatorUnavailableError):
        await worker.validate_transition_offer(**kwargs, dispute_review=True)
    assert len(factories) == 3


@pytest.mark.asyncio
async def test_dispute_real_timeout_becomes_evaluator_error(monkeypatch):
    """Map real timeouts to existing errors so the workflow retains the initial rejection instead of treating failure as approval."""
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
