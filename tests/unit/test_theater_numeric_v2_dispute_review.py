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
            (), False, transition_intent='unclear', )

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
            player_action_kind='requested_movement',
        )

    assert workflow._review_mislabels_explicit_player_movement(
        evaluator.NumericV2TransitionOfferReview(
            False,
            False,
            ('player_action',),
            (),
            '正文存在问题。',
            player_action_kind='requested_movement',
        )
    ) is True
    # 真正的额外操作和目的地错配由结构化 unauthorized 或缺省值保留否决。
    for kind in ('unauthorized', ''):
        assert workflow._review_mislabels_explicit_player_movement(
            evaluator.NumericV2TransitionOfferReview(
                False, False, ('player_action',), (), '正文存在问题。',
                player_action_kind=kind)) is False

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
@pytest.mark.parametrize('violations', [('player_action',), ('player_action', 'scene_boundary')])
@pytest.mark.parametrize('scene_update, player_input, safe_reply', [
    ('你冒雨重新回到店里，站在刚才的位置。', '（推门离开）明天见。', '（点头目送你离开）好，路上小心，明天见。'),
    ('你重新回到控制室，站在刚才的位置。', '（转身离开控制室）通讯保持畅通。', '（点头目送你离开）收到，我会守着通讯。'),
])
async def test_confirmed_departure_drops_conflicting_scene_update_without_actor_rewrite(
    monkeypatch,
    tmp_path,
    scene_update,
    player_input,
    safe_reply,
    violations,
):
    """玩家已明确离场时，只删除被定位为冲突的场景更新，不再整稿重写。"""  # noqa: DOCSTRING_CJK

    engine = _engine()
    engine.nodes['start']['story_beat']['fixed_narrations'] = [{
        'id': 'return_piece', 'text': '玩家返回后才交付的作者旁白。',
        'trigger': {'type': 'condition', 'condition': '玩家已经返回当前场景。'},
        'after': [], 'required_before_exit': False,
    }]
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id='departure_scene_update_guard',
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    generations, reviews = [], []

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult(
            (), False, transition_intent='unclear', )

    async def generate(self, **kwargs):
        generations.append(kwargs)
        return {
            'performance': safe_reply,
            'scene_narration': scene_update,
            'fact_candidates': [{'key': 'scene:start:returned', 'value': True}],
            'suggested_inputs': ['（挥挥手）明天见。', '（继续往前走）我先回去了。'],
            'transition_offered': False,
        }

    async def review(self, **kwargs):
        reviews.append(kwargs)
        return evaluator.NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            body_violations=violations,
            unsafe_suggestion_indexes=(),
            failure_reason='已确认的离场结果与候选内容冲突。',
            body_issues=({'code': 'player_return_after_departure', 'field': 'scene_update',
                          'quote': scene_update, 'violations': list(violations)},),
            fact_candidates=({'key': 'scene:start:returned', 'value': True},),
            fixed_narration_triggers=({'id': 'return_piece', 'evidence': scene_update},),
        )

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')

    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=current,
        turn=TurnRequestV2('leave_with_future', 0, player_input),
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
    assert result.performance['performance'] == safe_reply
    assert result.diagnostics['transition_review_results'][0]['body_issues'][0]['quote'] == scene_update
    restored = await NumericV2Runtime(engine, tmp_path).restore_session('departure_scene_update_guard')
    assert restored == result.stored
    assert scene_update not in str(restored.session.performance_history)
    assert 'return_piece' not in str(restored.session.performance_history)


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
        body_issues=(
            {'code': 'player_return_after_departure', 'field': 'actor_performance',
             'quote': '你怎么又回来了？', 'violations': ['player_action']},
            {'code': 'player_return_after_departure', 'field': 'scene_update',
             'quote': '你重新回到店里。', 'violations': ['player_action']},
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


def test_free_text_reason_alone_never_clears_a_player_action_veto():
    """Keep the veto when only failure_reason prose claims the movement was requested; the Guard must say so in a structured field."""
    for reason in (
        '正文scene_update直接执行了玩家本轮明确表达的移动动作，构成player_action。',
        '正文在scene_update中直接执行了玩家本轮才明确要求的转移动作，构成新增未授权的玩家行动。',
        '正文承接玩家已经完成的离开动作，仍被标成player_action。',
    ):
        review = evaluator.NumericV2TransitionOfferReview(False, False, ('player_action',), (), reason)
        assert workflow._review_mislabels_explicit_player_movement(review) is False
    # The structured code only clears the sole player_action veto without a body offer.
    for review in (
        evaluator.NumericV2TransitionOfferReview(
            False, False, ('player_action', 'author_boundary'), (), '', player_action_kind='requested_movement'),
        evaluator.NumericV2TransitionOfferReview(
            True, False, ('player_action',), (), '', offer_quote='走吧', player_action_kind='requested_movement'),
    ):
        assert workflow._review_mislabels_explicit_player_movement(review) is False


@pytest.mark.asyncio
async def test_prose_only_movement_reason_keeps_veto_in_workflow(monkeypatch, tmp_path):
    """A Guard reply that admits the requested movement only in prose is not cleared by the workflow."""
    engine = _engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='prose_movement', catgirl_binding=_binding(),
                                          opening_performance=_opening())

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult(
            (), False, transition_intent='unclear')

    async def generate(self, **kwargs):
        return {'performance': '（扶稳你的手臂）好，我们沿着墙边慢慢走。', 'scene_narration': '两人开始向左侧走廊移动。',
                'suggested_inputs': ['（跟上她）继续走。', '（停下脚步）先等等。'], 'transition_offered': False}

    async def review(self, **kwargs):
        return evaluator.NumericV2TransitionOfferReview(
            False, False, ('player_action',), (),
            '正文scene_update直接执行了玩家本轮明确表达的移动动作，构成player_action。')

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('move_now', 0, '那带路吧，我们现在过去。'), ensure_current_binding=lambda _: _binding())
    assert result.diagnostics['explicit_player_movement_flags_cleared'] == 0


def test_guard_parser_reads_player_action_kind_fail_closed():
    """Parse the structured player_action kind; absent, unknown, mistyped or orphaned values default to empty."""
    parse = evaluator._parse_transition_judge_output

    def payload(**changes):
        return json.dumps({'offer_present': False, 'valid': False, 'body_violations': ['player_action'],
                           'unsafe_suggestion_indexes': [], 'failure_reason': '', **changes}, ensure_ascii=False)

    assert parse(payload(player_action_kind='requested_movement')).player_action_kind == 'requested_movement'
    assert parse(payload(player_action_kind='unauthorized')).player_action_kind == 'unauthorized'
    assert parse(payload()).player_action_kind == ''
    for bad in ('REQUESTED_MOVEMENT', 'allowed', 1, True, None, ['requested_movement']):
        review = parse(payload(player_action_kind=bad))
        assert review.player_action_kind == ''
        assert review.body_violations == ('player_action',)
    orphan = parse(payload(body_violations=[], player_action_kind='requested_movement'))
    assert orphan.player_action_kind == ''


def test_guard_parser_reads_offer_kind_fail_closed():
    """Parse the structured offer kind; absent, unknown, mistyped, unverified or formal values default to empty."""
    parse = evaluator._parse_transition_judge_output
    quote = '走廊尽头的红灯标识显示“地下信标室·检修入口”。'

    def payload(**changes):
        return json.dumps({'offer_present': True, 'offer_quote': quote, 'valid': False, 'body_violations': [],
                           'unsafe_suggestion_indexes': [], 'failure_reason': '', **changes}, ensure_ascii=False)

    assert parse(payload(offer_kind='exit_mention_only'), offer_evidence_text=quote).offer_kind == 'exit_mention_only'
    assert parse(payload(offer_kind='invitation'), offer_evidence_text=quote).offer_kind == 'invitation'
    assert parse(payload(), offer_evidence_text=quote).offer_kind == ''
    for bad in ('EXIT_MENTION_ONLY', 'location_only', 1, True, None, ['exit_mention_only']):
        review = parse(payload(offer_kind=bad), offer_evidence_text=quote)
        assert review.offer_kind == ''
        assert review.offer_present is True
    # 引文无法在正文中核验时邀请本身被撤销，结构化码也不能单独存活。
    assert parse(payload(offer_kind='exit_mention_only'), offer_evidence_text='别的正文').offer_kind == ''
    assert parse(payload(offer_present=False, offer_quote='', offer_kind='exit_mention_only')).offer_kind == ''
    formal = parse(payload(offer_kind='exit_mention_only', delivery_matches_route=True, pending_invitation_invalid=False,
                           acceptance_authorized=True),
                   acceptance_review=True, transition_delivery_review=True, offer_evidence_text=quote)
    assert formal.offer_kind == ''


def test_guard_prompt_asks_for_structured_player_action_kind():
    """The ordinary Guard output contract names the structured field that replaces reason-keyword matching."""
    engine = _engine()
    session = engine.create_session(session_id='prompt_kind', catgirl_binding=_binding(), opening_performance=_opening())
    messages = evaluator._build_transition_judge_messages(
        engine, session, actor_performance={'performance': '（点头）好。', 'suggested_inputs': []},
        player_input='带路吧。')[0]
    assert '"player_action_kind":""' in messages[0].content
    assert 'requested_movement' in messages[0].content
    assert '"offer_kind":""' in messages[0].content
    assert 'exit_mention_only' in messages[0].content


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


@pytest.mark.asyncio
async def test_dispute_verdict_gets_the_same_explicit_movement_correction(monkeypatch, tmp_path):
    """A dispute verdict that mislabels the player's own requested movement is corrected like a fast one."""
    engine = _engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='dispute_movement', catgirl_binding=_binding(),
                                          opening_performance=_opening())
    generations, reviews = [], []

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult(
            (), False, transition_intent='unclear')

    async def generate(self, **kwargs):
        generations.append(kwargs)
        return {'performance': '（扶稳你的手臂）好，我们沿着墙边慢慢走。', 'scene_narration': '两人开始向左侧走廊移动。',
                'suggested_inputs': ['（跟上她）继续走。', '（停下脚步）先等等。'], 'transition_offered': False}

    async def review(self, **kwargs):
        reviews.append(kwargs)
        # The fast verdict is vague enough to earn a dispute; the dispute then blames the requested movement.
        reason = ('正文scene_update直接执行了玩家本轮明确表达的移动动作，构成player_action。'
                  if kwargs.get('dispute_review') else '正文存在问题。')
        return evaluator.NumericV2TransitionOfferReview(
            False, False, ('player_action',), (), reason,
            player_action_kind='requested_movement' if kwargs.get('dispute_review') else 'unauthorized')

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('move_now', 0, '那带路吧，我们现在过去。'), ensure_current_binding=lambda _: _binding())
    assert [bool(call.get('dispute_review')) for call in reviews] == [False, True]
    assert result.diagnostics['explicit_player_movement_flags_cleared'] == 1
    assert result.diagnostics['semantic_rewrite_attempts'] == 0
    assert len(generations) == 1
    assert result.performance['scene_narration'] == '两人开始向左侧走廊移动。'
