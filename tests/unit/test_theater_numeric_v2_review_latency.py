"""复核链等待优化：时限位置、改写后定向复检与整回合复核预算。  # noqa: DOCSTRING_CJK

这些改动只约束等待与第二次判定看到的材料，不改变玩家授权、去向公开、数值结算或原子提交。
"""

from dataclasses import replace
import asyncio
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow
from services.theater.numeric_v2_workflow import NUMERIC_V2_REVIEW_BUDGET_SECONDS
from services.theater.numeric_v2_actor import NumericV2ActorOutputError
from tests.unit.test_theater_numeric_v2_review_capacity import _fixture
from tests.unit.test_theater_numeric_v2_runtime import _binding


def _long_history_session(engine, session, prose_repeats=20):
    history = tuple({
        'revision': index,
        'from_node_id': 'start',
        'to_node_id': 'start',
        'performance_contract_version': 3,
        'input_text': '我听见了。',
        'performance': '（' + '她整理好手边的工具。' * prose_repeats + '）',
    } for index in range(1, 7))
    return replace(session, revision=6, performance_history=history)


def _payload(messages):
    return json.loads(messages[1].content.split('：', 1)[1])


def _judge_payload(engine, session, outcome, candidate, **kwargs):
    messages = evaluator._build_transition_judge_messages(
        engine, session, actor_performance=candidate, player_input='好。',
        transition_outcome=outcome, **kwargs)[0]
    return _payload(messages)


def test_recheck_only_trims_earlier_turns_and_keeps_absence_conservative():
    """改写后复检只保留最新完整回合，并把历史完整性按保守方向置为不完整。"""  # noqa: DOCSTRING_CJK

    engine, session, outcome, candidate = _fixture(50)
    session = _long_history_session(engine, session)
    full = _judge_payload(engine, session, outcome, candidate)
    narrowed = _judge_payload(engine, session, outcome, candidate, recheck_only=True)

    assert len(full['scene_context']) > 1
    assert len(narrowed['scene_context']) == 1
    assert narrowed['scene_fact_index'] == []
    # 不允许凭缺项断言"从未发生"：裁掉更早回合后必须显式声明历史不完整。
    assert narrowed['current_visit_history_complete'] is False
    # 候选、本轮输入与作者合同不受收窄影响。
    assert narrowed['candidate_segments'] == full['candidate_segments']
    assert narrowed['player_input'] == full['player_input']
    assert narrowed['current_scene'] == full['current_scene']


def test_recheck_only_survives_emptied_index_under_pressure(monkeypatch):
    """收窄后仍超预算时，装箱循环必须能处理已置空的索引而不是崩溃。"""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_budget import NUMERIC_V2_ACTOR_BUDGET_PROFILES

    engine, session, outcome, candidate = _fixture(950)
    session = _long_history_session(engine, session)
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES['economy'], 'formal_judge_input_max_tokens', 1500)
    narrowed = _judge_payload(engine, session, outcome, candidate, recheck_only=True)
    assert narrowed['scene_fact_index'] == []
    assert narrowed['current_visit_history_complete'] is False


def test_missed_initiation_recheck_keeps_full_history():
    """补查漏判必须先看完整公开历史，不能套用改写后的定向收窄。"""  # noqa: DOCSTRING_CJK

    engine, session, outcome, candidate = _fixture(200)
    session = _long_history_session(engine, session)
    full = _judge_payload(engine, session, outcome, candidate, check_missed_initiation=True)
    narrowed = _judge_payload(
        engine, session, outcome, candidate, check_missed_initiation=True, recheck_only=True)
    assert len(narrowed['scene_context']) == len(full['scene_context']) > 1


@pytest.mark.asyncio
async def test_judge_messages_are_built_before_client_and_deadline_covers_close(monkeypatch):
    """消息构造不占用时限，且连接、请求与关闭同属一个时限。"""  # noqa: DOCSTRING_CJK

    engine, session, outcome, candidate = _fixture()
    built = []
    real_build = evaluator._build_transition_judge_messages

    def spy(*args, **kwargs):
        built.append(True)
        return real_build(*args, **kwargs)

    class SlowCloseClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            # 旧实现里关闭发生在时限之外，这里可以拖过时限而不报超时。
            await asyncio.sleep(0.5)
            return False

        async def ainvoke(self, messages):
            return SimpleNamespace(content=json.dumps(dict(
                offer_present=False, valid=False, body_violations=[],
                unsafe_suggestion_indexes=[], failure_reason='')))

    order = []

    async def config(_):
        return dict(model='test', base_url='http://test.invalid')

    async def factory(*args, **kwargs):
        order.append(bool(built))
        return SlowCloseClient()

    monkeypatch.setattr(evaluator, '_build_transition_judge_messages', spy)
    monkeypatch.setattr(evaluator, '_model_config', config)
    monkeypatch.setattr(evaluator, 'create_chat_llm_async', factory)
    monkeypatch.setattr(evaluator, 'NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS', 0.05)

    worker = evaluator.NumericV2MetricEvaluator(object())
    with pytest.raises(evaluator.NumericV2EvaluatorError, match='transition_judge_timeout'):
        await worker.validate_transition_offer(
            engine=engine, session=session, message='好。',
            actor_performance=candidate, transition_outcome=outcome)

    # 先装配消息再建立连接，且关闭时间计入时限。
    assert order == [True]


@pytest.mark.asyncio
async def test_review_call_accepts_remaining_budget_timeout(monkeypatch):
    """Workflow can pass a smaller remaining budget without changing the default timeout."""

    engine, session, _outcome, candidate = _fixture()
    captured = {}

    async def config(_):
        return {'model': 'test', 'base_url': 'http://test.invalid'}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            return SimpleNamespace(content=json.dumps({
                'offer_present': False,
                'offer_quote': '',
                'valid': False,
                'body_violations': [],
                'unsafe_suggestion_indexes': [],
                'failure_reason': '',
            }))

    async def factory(*_args, **kwargs):
        captured.update(kwargs)
        return Client()

    monkeypatch.setattr(evaluator, '_model_config', config)
    monkeypatch.setattr(evaluator, 'create_chat_llm_async', factory)
    worker = evaluator.NumericV2MetricEvaluator(object())
    await worker.validate_transition_offer(
        engine=engine, session=session, message='好。', actor_performance=candidate,
        route_changed=True, transition_outcome=_outcome, timeout_seconds=1.5,
    )
    assert captured['timeout'] == 1.5


def _violating_review(**kwargs):
    return evaluator.NumericV2TransitionOfferReview(
        False, False, ('author_boundary',), (), '需要修正初稿。')


async def _all_off() -> dict:
    return {key: False for key in (
        'evaluator', 'review', 'dispute', 'suggestion_fill', 'history_lookup', 'actor_retry')}


def test_only_metric_judgment_is_on_by_default():
    """除"回复"外的每个可选模块默认关闭。"""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_options import default_options

    options = default_options()
    # 数值增减来自前置判定，必须保留；其余可选模块默认关闭。
    assert options['evaluator'] is True
    assert {key: value for key, value in options.items() if key != 'evaluator'} == {
        'review': False, 'dispute': False, 'review_delivery': False, 'review_contract': False,
        'suggestion_fill': False, 'history_lookup': False, 'actor_retry': False}


def test_evaluator_off_accepts_the_public_offer_deterministically():
    """判定关闭时不猜数值与意图，但接受已公开提议的第一条推荐仍可放行选路。"""  # noqa: DOCSTRING_CJK

    engine, session, _outcome, _candidate = _fixture(50)
    suggestion = '（点头）好，带路吧。'
    # 提议必须来自最近一次提交且自带 transition_offered，避免接受更早回合的旧提议。
    session = replace(session, transition_offered=True,
                      performance_history=({'revision': 1, 'transition_offered': True,
                                            'suggested_inputs': [suggestion, '再等等。']},))
    current = SimpleNamespace(session=session)
    accepted = numeric_v2_workflow._evaluation_without_evaluator(
        current, SimpleNamespace(message=suggestion))
    assert accepted.transition_intent == 'accept'
    assert accepted.metric_changes == ()
    stalled = numeric_v2_workflow._evaluation_without_evaluator(
        current, SimpleNamespace(message='我想再看看别的地方。'))
    assert stalled.transition_intent == 'unclear'
    # 没有待确认提议时，即使文本相同也不放行。
    session = replace(session, transition_offered=False)
    blocked = numeric_v2_workflow._evaluation_without_evaluator(
        SimpleNamespace(session=session), SimpleNamespace(message=suggestion))
    assert blocked.transition_intent == 'unclear'


def test_pending_followup_keeps_original_acceptance_button_first():
    """追问回复可以更新推荐，但不能覆盖仍有效邀请的原始接受按钮。"""  # noqa: DOCSTRING_CJK

    _engine, session, _outcome, _candidate = _fixture(50)
    acceptance = '（起身）好，我们现在出发。'
    session = replace(
        session,
        transition_offered=True,
        performance_history=({
            'revision': 1,
            'from_node_id': session.current_node_id,
            'to_node_id': session.current_node_id,
            'transition_offered': True,
            'transition_offer_presented': True,
            'performance': '雪停了，我们现在出发吗？',
            'suggested_inputs': [acceptance, '再休息一会儿。'],
        },),
    )
    current = SimpleNamespace(session=session, ledger_events=())

    result, changed = numeric_v2_workflow._preserve_pending_acceptance_suggestion(
        {
            'performance': '你可以先睡一会儿。',
            'suggested_inputs': ['路上还要多久？', '我再看看雪势。'],
        },
        current=current,
        keep_pending=True,
    )

    assert changed is True
    assert result['suggested_inputs'] == [acceptance, '路上还要多久？', '我再看看雪势。']


def test_pending_acceptance_button_is_not_kept_after_offer_lifecycle_ends():
    """换幕、撤下旧邀请或公开新邀请时，由调用方终止按钮继承。"""  # noqa: DOCSTRING_CJK

    _engine, session, _outcome, _candidate = _fixture(50)
    session = replace(
        session,
        transition_offered=True,
        performance_history=({
            'revision': 1,
            'from_node_id': session.current_node_id,
            'to_node_id': session.current_node_id,
            'transition_offered': True,
            'transition_offer_presented': True,
            'suggested_inputs': ['接受旧邀请。'],
        },),
    )
    candidate = {'performance': '这是新状态。', 'suggested_inputs': ['留在这里。']}

    result, changed = numeric_v2_workflow._preserve_pending_acceptance_suggestion(
        candidate,
        current=SimpleNamespace(session=session, ledger_events=()),
        keep_pending=False,
    )

    assert changed is False
    assert result == candidate


def test_verified_offer_gets_the_authored_acceptance_button():
    """有效新邀请使用作者写定的接受输入，其余可见选择保持原顺序。"""  # noqa: DOCSTRING_CJK

    result, changed = numeric_v2_workflow._insert_verified_offer_acceptance_suggestion({
        'performance': '我们现在离开这里，好吗？',
        'suggested_inputs': ['我想先问问路况。', '我暂时不走。', '我再检查一次行囊。'],
    }, accept_input='（背起行囊）好，我们现在离开。')

    assert changed is True
    assert result['suggested_inputs'] == [
        '（背起行囊）好，我们现在离开。',
        '我想先问问路况。',
        '我暂时不走。',
    ]


@pytest.mark.asyncio
async def test_workflow_inserts_acceptance_only_after_offer_review(tmp_path, monkeypatch):
    """Workflow 只为复核确认有效的新邀请插入固定接受按钮。"""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
    from tests.unit.test_theater_numeric_v2_player_transition import initiation_case

    case = initiation_case()
    selected_route = case['engine'].preview_route(
        case['session'].current_node_id,
        case['session'].metrics,
    )
    stored_route = next(
        route
        for route in case['engine'].nodes[case['session'].current_node_id]['route_gates']
        if route['id'] == selected_route['id']
    )
    stored_route['transition_contract']['accept_input'] = (
        '（收好文书）好，我们现在去阅览室。'
    )
    runtime = NumericV2Runtime(case['engine'], tmp_path)
    current = await runtime.start_session(
        session_id='verified_offer_button',
        catgirl_binding=_binding(),
        opening_performance=case['session'].opening_performance,
    )

    async def evaluate(self, **_kwargs):
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **_kwargs):
        return {
            'performance': '手续办妥了，我们现在去阅览室吧。',
            'suggested_inputs': ['我想先问问路况。', '我暂时不走。'],
            'transition_offered': True,
        }

    async def review(self, **_kwargs):
        return evaluator.NumericV2TransitionOfferReview(True, True, (), ())

    async def review_on():
        return {
            'evaluator': True,
            'review': True,
            'dispute': False,
            'review_delivery': False,
            'review_contract': False,
            'suggestion_fill': False,
            'history_lookup': False,
            'actor_retry': False,
        }

    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(
        numeric_v2_workflow.NumericV2MetricEvaluator,
        'validate_transition_offer',
        review,
    )
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    monkeypatch.setattr(numeric_v2_workflow, 'aload_theater_module_options', review_on)

    result = await numeric_v2_workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=current,
        turn=TurnRequestV2('offer', current.session.revision, '手续已经办好了。'),
        ensure_current_binding=lambda _: _binding(),
    )

    assert result.performance['suggested_inputs'][0] == '（收好文书）好，我们现在去阅览室。'
    assert result.diagnostics['verified_offer_acceptance_suggestions_inserted'] == 1


@pytest.mark.asyncio
async def test_evaluator_failure_keeps_exact_public_acceptance_button(tmp_path, monkeypatch):
    """判定服务故障时，玩家逐字点击已公开的接受按钮仍应换幕。"""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
    from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
    from tests.unit.test_theater_numeric_v2_transition_history import _candidate

    case = initiation_case()
    engine = case['engine']
    runtime = NumericV2Runtime(engine, tmp_path)
    suggestion = '（点头）好，带路吧。'
    current = await runtime.start_session(
        session_id='evaluator_failure_acceptance',
        catgirl_binding=_binding(),
        opening_performance=case['session'].opening_performance,
    )
    evaluations = []
    generations = []

    async def fail_evaluation(self, **kwargs):
        evaluations.append(kwargs)
        if len(evaluations) == 1:
            return evaluator.NumericV2EvaluationResult((), True)
        raise evaluator.NumericV2EvaluatorError('numeric_v2_evaluator_invalid_json')

    async def generate(self, **kwargs):
        generations.append(kwargs)
        if len(generations) == 1:
            return {
                'performance': '手续办妥了，我们现在去阅览室吧。',
                'suggested_inputs': [suggestion, '再等等。'],
                'transition_offered': True,
            }
        return engine.finalize_transition_performance(
            kwargs['outcome'],
            _candidate(),
            target_opening='两人在阅览室入口，后续操作尚未开始。',
            bridge_scene_narration='两人沿左侧走廊来到阅览室。',
        )

    async def evaluator_only():
        return {
            'evaluator': True,
            'review': False,
            'dispute': False,
            'review_delivery': False,
            'review_contract': False,
            'suggestion_fill': False,
            'history_lookup': False,
            'actor_retry': False,
        }

    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'evaluate', fail_evaluation)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    monkeypatch.setattr(numeric_v2_workflow, 'aload_theater_module_options', evaluator_only)

    offered = await numeric_v2_workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=current,
        turn=TurnRequestV2('offer', current.session.revision, '先办好眼前的手续。'),
        ensure_current_binding=lambda _: _binding(),
    )
    current = offered.stored
    assert current.session.transition_offered is True

    diagnostics = {}
    result = await numeric_v2_workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=current,
        turn=TurnRequestV2('accept', current.session.revision, suggestion),
        ensure_current_binding=lambda _: _binding(),
        diagnostics_sink=diagnostics,
    )

    assert diagnostics['evaluator_degraded'] is True
    assert result.stored.session.current_node_id != current.session.current_node_id


@pytest.mark.asyncio
async def test_all_modules_off_keeps_only_the_actor_call(tmp_path, monkeypatch):
    """模块全关时只剩一次演员调用：判定与复核都不发请求，回合仍原子提交。"""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2, MetricChangeV2
    from tests.unit.test_theater_numeric_v2_player_transition import initiation_case

    case = initiation_case()
    engine = case['engine']
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id='modules_off', catgirl_binding=_binding(),
        opening_performance=case['session'].opening_performance)

    evaluate_calls = []
    review_calls = []
    generations = []

    async def evaluate(self, **kwargs):
        evaluate_calls.append(kwargs)
        return evaluator.NumericV2EvaluationResult(
            (MetricChangeV2('trust', 2, '玩家兑现承诺', '我来帮你。'),), False)

    async def generate(self, **kwargs):
        generations.append(kwargs)
        return {'performance': '（点头）好，我们继续。', 'suggested_inputs': []}

    async def review(self, **kwargs):
        review_calls.append(kwargs)
        return _violating_review(**kwargs)

    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    monkeypatch.setattr(numeric_v2_workflow, 'aload_theater_module_options', _all_off)

    diagnostics = {}
    result = await numeric_v2_workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('new', current.session.revision, '我来帮你。'),
        ensure_current_binding=lambda _: _binding(), diagnostics_sink=diagnostics)

    assert evaluate_calls == [] and review_calls == []
    assert len(generations) == 1
    assert diagnostics['theater_module_options'] == await _all_off()
    assert diagnostics['evaluator_skipped'] is True and diagnostics['review_skipped'] is True
    # 演员内部也不再补推荐（开关随本轮传入）。
    assert generations[0]['allow_suggestion_fill'] is False
    assert result.stored.session.revision == current.session.revision + 1


@pytest.mark.asyncio
async def test_review_budget_skips_later_rechecks_and_uses_existing_fallback(tmp_path, monkeypatch):
    """预算是等待上限：首次快检仍执行，改写后的复检被跳过并走既有末稿兜底。"""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2, MetricChangeV2
    from tests.unit.test_theater_numeric_v2_player_transition import initiation_case

    case = initiation_case()
    engine = case['engine']
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id='review_budget', catgirl_binding=_binding(),
        opening_performance=case['session'].opening_performance)

    reviews = []
    generations = []

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult(
            (MetricChangeV2('trust', 2, '玩家兑现承诺', '我来帮你。'),), False)

    async def generate(self, **kwargs):
        generations.append(kwargs)
        return {'performance': '尚未提交的初稿。' if len(generations) == 1 else '修正后的最终回应。',
                'suggested_inputs': []}

    async def review(self, **kwargs):
        reviews.append(kwargs.get('recheck_only'))
        return _violating_review(**kwargs)

    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    monkeypatch.setattr(numeric_v2_workflow, 'NUMERIC_V2_REVIEW_BUDGET_SECONDS', 0.0)

    diagnostics = {}
    result = await numeric_v2_workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('new', current.session.revision, '我来帮你。'),
        ensure_current_binding=lambda _: _binding(), diagnostics_sink=diagnostics)

    # 首次快检执行一次，改写后的复检按预算跳过，且走既有末稿兜底而不是无限等待。
    assert len(reviews) == 1
    assert len(generations) == 2
    assert diagnostics['review_budget_skips'] == 1
    assert diagnostics['semantic_review_fallback'] is True
    assert result.stored.session.revision == current.session.revision + 1


@pytest.mark.asyncio
async def test_review_budget_exhaustion_keeps_transition_rollback(tmp_path, monkeypatch):
    """正式转场不能因预算跳过复检而提交：沿用未完成复核的回滚语义。"""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2, MetricChangeV2
    from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
    from tests.unit.test_theater_numeric_v2_transition_history import _candidate

    case = initiation_case()
    engine = case['engine']
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id='review_budget_transition', catgirl_binding=_binding(),
        opening_performance=case['session'].opening_performance)

    generations = []

    async def evaluate(self, **kwargs):
        # 必须真的跨幕，才能覆盖正式转场的"未完成复核不提交"分支。
        return evaluator.NumericV2EvaluationResult(
            (MetricChangeV2('trust', 2, '玩家兑现承诺', '我来帮你。'),), False,
            transition_intent='initiate', interaction_intent='scene_action')

    async def generate(self, **kwargs):
        generations.append(kwargs)
        return _candidate()

    async def review(self, **kwargs):
        return _violating_review(**kwargs)

    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    monkeypatch.setattr(numeric_v2_workflow, 'NUMERIC_V2_REVIEW_BUDGET_SECONDS', 0.0)

    with pytest.raises(NumericV2ActorOutputError, match='numeric_v2_transition_review_failed'):
        await numeric_v2_workflow.execute_numeric_v2_turn(
            config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2('new', current.session.revision, '我来帮你。'),
            ensure_current_binding=lambda _: _binding())
    # 事务回滚：未提交的换场不能进入存档。
    assert await runtime.restore_session('review_budget_transition') == current


@pytest.mark.asyncio
async def test_dispute_review_receives_only_remaining_budget(tmp_path, monkeypatch):
    """The second review cannot receive the full dispute timeout after a slow fast check."""

    from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2, MetricChangeV2
    from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
    from tests.unit.test_theater_numeric_v2_transition_history import _candidate

    case = initiation_case()
    runtime = NumericV2Runtime(case['engine'], tmp_path)
    current = await runtime.start_session(
        session_id='review_remaining_budget', catgirl_binding=_binding(),
        opening_performance=case['session'].opening_performance)
    calls = []

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult(
            (MetricChangeV2('trust', 2, '玩家兑现承诺', '我来帮你。'),), False,
            transition_intent='initiate', interaction_intent='scene_action')

    async def generate(self, **kwargs):
        return case['engine'].finalize_transition_performance(
            kwargs['outcome'], _candidate(), target_opening='阅览室入口。')

    async def review(self, **kwargs):
        calls.append(kwargs)
        if not kwargs.get('dispute_review'):
            await asyncio.sleep(0.1)
        if kwargs.get('dispute_review'):
            return evaluator.NumericV2TransitionOfferReview(False, False, (), ())
        return _violating_review(**kwargs)

    async def options():
        return {'evaluator': True, 'review': True, 'dispute': True, 'review_delivery': False,
                'review_contract': False, 'suggestion_fill': False, 'history_lookup': False,
                'actor_retry': False}

    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    monkeypatch.setattr(numeric_v2_workflow, 'aload_theater_module_options', options)
    monkeypatch.setattr(numeric_v2_workflow, 'NUMERIC_V2_REVIEW_BUDGET_SECONDS', 3.0)

    await numeric_v2_workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('new', current.session.revision, '我来帮你。'),
        ensure_current_binding=lambda _: _binding())

    assert len(calls) >= 2
    fast, dispute = calls[:2]
    assert fast.get('dispute_review') is not True
    assert dispute.get('dispute_review') is True
    assert dispute['timeout_seconds'] < evaluator.NUMERIC_V2_DISPUTE_JUDGE_TIMEOUT_SECONDS


def test_review_budget_is_a_wait_limit_only():
    """预算只限制等待，不替代授权、去向或提交判定。"""  # noqa: DOCSTRING_CJK

    assert NUMERIC_V2_REVIEW_BUDGET_SECONDS > 0
    assert evaluator.NUMERIC_V2_DISPUTE_JUDGE_TIMEOUT_SECONDS < NUMERIC_V2_REVIEW_BUDGET_SECONDS
    assert evaluator.NUMERIC_V2_DISPUTE_JUDGE_TIMEOUT_SECONDS > evaluator.NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS

def test_delivery_check_finds_authored_props_only_from_contract():
    """交付校验只核对本轮必须交付的道具；保留项无需逐幕复述。"""  # noqa: DOCSTRING_CJK

    from types import SimpleNamespace

    from services.theater.numeric_v2_context import contract_required_names, missing_contract_names

    node = {
        'route_gates': [{
            'target_node_id': 'mainline_03',
            'transition_contract': {
                'must_preserve': [
                    '关键道具“传统风筝图谱”[kite_blueprint]：由小葵妥善保管。',
                ],
                'must_deliver': ['普通换场旁白。'],
            },
        }],
    }
    session = SimpleNamespace(performance_history=())
    assert contract_required_names(node, 'mainline_03') == ()
    assert missing_contract_names(node, 'mainline_03', {'performance': '继续前行。'}, session) == ()

    # 作者明确要求本轮交付时才检查；三段正文与旁白都属于玩家实际可见内容。
    node['route_gates'][0]['transition_contract']['must_deliver'].append(
        '关键道具“传统风筝图谱”[kite_blueprint]：本轮必须交给老匠人。'
    )
    assert contract_required_names(node, 'mainline_03') == ('传统风筝图谱',)
    assert missing_contract_names(
        node,
        'mainline_03',
        {'segments': [{'phase': 'source_response', 'performance': '（展开传统风筝图谱）您请看。'}]},
        session,
    ) == ()


def test_contract_check_accepts_only_authored_boundary_items():
    """窄判定只接受逐字来自作者禁令列表的条目，模型自造的违规被丢弃。"""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_evaluator import _parse_contract_check_output

    required = ('不得直接到达最高处平台', '不得引入NPC或摄影任务')
    assert _parse_contract_check_output(
        '{"violated":["不得直接到达最高处平台"]}', required) == ('不得直接到达最高处平台',)
    assert _parse_contract_check_output('{"violated":["我自己编的违规"]}', required) == ()


@pytest.mark.asyncio
async def test_boundary_check_reads_author_boundaries_and_reports_violation(monkeypatch):
    """窄判定必须真的读到作者禁令，并把逐字命中的违规返回给调用方。"""  # noqa: DOCSTRING_CJK

    captured = {}

    async def _model_config(_config_manager):
        return {'model': 'm', 'base_url': 'u', 'api_key': None, 'provider_type': 'openai'}

    async def _client_factory(*_args, **_kwargs):
        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        return _Client()

    async def _invoke(_client, messages, **_kwargs):
        captured['messages'] = messages
        return SimpleNamespace(content='{"violated":["不得直接到达最高处平台"]}')

    monkeypatch.setattr(evaluator, '_model_config', _model_config)
    monkeypatch.setattr(evaluator, 'create_chat_llm_async', _client_factory)
    monkeypatch.setattr(evaluator, 'invoke_with_usage', _invoke)

    node = {'story_beat': {
        'must_not_happen': ['不得直接到达最高处平台', '不得引入NPC或摄影任务'],
        'character_state': {'scene_boundaries': ['不得描述已到达终点平台']},
    }}
    worker = evaluator.NumericV2MetricEvaluator(object())
    violated = await worker.verify_contract_boundaries(
        node=node, actor_performance={'performance': '两人抵达观景坡顶端的平台。'},
        player_input='（加快步伐走完最后几级台阶）呼，终于到了！')

    assert violated == ('不得直接到达最高处平台',)
    sent = captured['messages'][0].content + captured['messages'][1].content
    assert '不得直接到达最高处平台' in sent
    assert '不得描述已到达终点平台' in sent
    # 没有禁令的幕不发请求，直接返回空。
    captured.clear()
    assert await worker.verify_contract_boundaries(
        node={'story_beat': {}}, actor_performance={'performance': '随便。'},
        player_input='随便。') == ()
    assert captured == {}


@pytest.mark.asyncio
async def test_contract_switch_checks_stay_turns_and_rewrites_once(tmp_path, monkeypatch):
    """留幕回合也要核对作者禁令：命中一次就共用一次改稿，复检结果单独留诊断。"""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2, MetricChangeV2
    from tests.unit.test_theater_numeric_v2_player_transition import initiation_case

    case = initiation_case()
    runtime = NumericV2Runtime(case['engine'], tmp_path)
    current = await runtime.start_session(
        session_id='contract_stay', catgirl_binding=_binding(),
        opening_performance=case['session'].opening_performance)

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult(
            (MetricChangeV2('trust', 2, '玩家兑现承诺', '我来帮你。'),), False)

    generations = []
    boundary_calls = []

    async def generate(self, **kwargs):
        generations.append(kwargs.get('retry_hint', ''))
        return {'performance': '（指向远处的平台）我们到了最高的地方。', 'suggested_inputs': []}

    async def boundaries(self, **kwargs):
        boundary_calls.append(kwargs)
        return ('不得直接到达最高处平台',) if len(boundary_calls) == 1 else ()

    async def options():
        return {'evaluator': True, 'review': False, 'dispute': False, 'review_delivery': False,
                'review_contract': True, 'suggestion_fill': False, 'history_lookup': False,
                'actor_retry': False}

    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'verify_contract_boundaries', boundaries)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    monkeypatch.setattr(numeric_v2_workflow, 'aload_theater_module_options', options)

    diagnostics = {}
    await numeric_v2_workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('new', current.session.revision, '我们继续往前走。'),
        ensure_current_binding=lambda _: _binding(), diagnostics_sink=diagnostics)

    assert len(boundary_calls) == 2 and len(generations) == 2
    assert diagnostics['contract_violated'] == ['不得直接到达最高处平台']
    assert diagnostics['contract_violated_after_rewrite'] == []
    assert diagnostics['semantic_rewrite_attempts'] == 1
    assert '作者禁令' in generations[1]


@pytest.mark.asyncio
async def test_contract_switch_off_sends_no_boundary_call(tmp_path, monkeypatch):
    """开关关闭时留幕回合不发窄判定请求。"""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2, MetricChangeV2
    from tests.unit.test_theater_numeric_v2_player_transition import initiation_case

    case = initiation_case()
    runtime = NumericV2Runtime(case['engine'], tmp_path)
    current = await runtime.start_session(
        session_id='contract_off', catgirl_binding=_binding(),
        opening_performance=case['session'].opening_performance)

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult(
            (MetricChangeV2('trust', 2, '玩家兑现承诺', '我来帮你。'),), False)

    called = []

    async def boundaries(self, **kwargs):
        called.append(kwargs)
        return ()

    async def generate(self, **kwargs):
        return {'performance': '（点头）好。', 'suggested_inputs': []}

    async def options():
        return {'evaluator': True, 'review': False, 'dispute': False, 'review_delivery': False,
                'review_contract': False, 'suggestion_fill': False, 'history_lookup': False,
                'actor_retry': False}

    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'verify_contract_boundaries', boundaries)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    monkeypatch.setattr(numeric_v2_workflow, 'aload_theater_module_options', options)

    await numeric_v2_workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('new', current.session.revision, '我们继续往前走。'),
        ensure_current_binding=lambda _: _binding(), diagnostics_sink={})

    assert called == []


def test_source_side_delivery_drops_the_target_opening():
    """换场边界核对只保留来源回应与桥段，目标幕开场不参与来源幕禁令核对。"""  # noqa: DOCSTRING_CJK

    full = {'segments': [
        {'phase': 'source_response', 'performance': '（点头）好。'},
        {'phase': 'transition_bridge', 'scene_narration': '窗外的雨渐渐停了。'},
        {'phase': 'target_opening', 'performance': '（望向窗外）终于可以歇一会儿了。'},
    ]}
    view = numeric_v2_workflow._source_side_delivery(full)
    assert [segment['phase'] for segment in view['segments']] == ['source_response', 'transition_bridge']
    # 留幕候选没有三段结构时原样返回，不做裁剪。
    plain = {'performance': '（点头）好。'}
    assert numeric_v2_workflow._source_side_delivery(plain) is plain


@pytest.mark.asyncio
async def test_transition_boundary_check_never_sends_the_target_opening(tmp_path, monkeypatch):
    """换场窄判定收到的材料必须没有目标幕开场，否则正常换场会被来源幕禁令误判。"""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2, MetricChangeV2
    from tests.unit.test_theater_numeric_v2_player_transition import initiation_case

    case = initiation_case()
    runtime = NumericV2Runtime(case['engine'], tmp_path)
    current = await runtime.start_session(
        session_id='contract_transition', catgirl_binding=_binding(),
        opening_performance=case['session'].opening_performance)

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult(
            (MetricChangeV2('trust', 2, '玩家兑现承诺', '我来帮你。'),), False,
            transition_intent='initiate', interaction_intent='scene_action')

    payloads = []

    async def boundaries(self, **kwargs):
        payloads.append(kwargs['actor_performance'])
        return ()

    async def generate(self, **kwargs):
        target = kwargs['outcome'].ledger_event['to_node_id']
        return {
            'suggested_inputs': ['（点头）好。', '（继续）走吧。'],
            'visible_node_id': target,
            'transition_delivered': True,
            'segments': [
                {'phase': 'source_response', 'performance': '（接过毛巾）谢谢，消息已经发出。'},
                {'phase': 'transition_bridge', 'scene_narration': '窗外的雨渐渐停了。'},
                {'phase': 'target_opening', 'scene_narration': '她擦干头发，把毛巾搭在椅背上。',
                 'performance': '（望向窗外）终于可以歇一会儿了。'},
            ],
        }

    async def options():
        return {'evaluator': True, 'review': False, 'dispute': False, 'review_delivery': False,
                'review_contract': True, 'suggestion_fill': False, 'history_lookup': False,
                'actor_retry': True}

    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'verify_contract_boundaries', boundaries)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    monkeypatch.setattr(numeric_v2_workflow, 'aload_theater_module_options', options)

    diagnostics = {}
    await numeric_v2_workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('new', current.session.revision, '我来帮你。'),
        ensure_current_binding=lambda _: _binding(), diagnostics_sink=diagnostics)

    assert payloads, '换场回合必须做一次边界核对'
    for payload in payloads:
        phases = [segment['phase'] for segment in payload['segments']]
        assert phases == ['source_response', 'transition_bridge']


@pytest.mark.asyncio
async def test_transition_bridge_leak_after_rewrite_rolls_back(tmp_path, monkeypatch):
    """结构重复改写后仍存在时，正式转场必须回滚而不是走末稿兜底提交。"""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
    from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
    from tests.unit.test_theater_numeric_v2_transition_history import _candidate

    case = initiation_case()
    target_opening = '视野豁然开朗，小葵快步走向平台边缘。'
    case['engine'].nodes['ending_leave']['story_beat']['opening_scene'] = target_opening
    runtime = NumericV2Runtime(case['engine'], tmp_path)
    current = await runtime.start_session(
        session_id='bridge_leak_rollback', catgirl_binding=_binding(),
        opening_performance=case['session'].opening_performance)
    generations = []

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult(
            (), False, transition_intent='initiate', interaction_intent='scene_action')

    async def generate(self, **kwargs):
        generations.append(kwargs.get('retry_hint', ''))
        candidate = _candidate()
        candidate['bridge_scene_narration'] = '沿着石阶向上，视野豁然开朗，小葵快步走向平台边缘。'
        return case['engine'].finalize_transition_performance(
            kwargs['outcome'], candidate, target_opening=target_opening)

    async def options():
        return {'evaluator': True, 'review': False, 'dispute': False, 'review_delivery': False,
                'review_contract': False, 'suggestion_fill': False, 'history_lookup': False,
                'actor_retry': False}

    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    monkeypatch.setattr(numeric_v2_workflow, 'aload_theater_module_options', options)

    diagnostics = {}
    with pytest.raises(NumericV2ActorOutputError, match='numeric_v2_transition_segment_overlap'):
        await numeric_v2_workflow.execute_numeric_v2_turn(
            config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2('new', current.session.revision, '带路吧。'),
            ensure_current_binding=lambda _: _binding(), diagnostics_sink=diagnostics)

    assert len(generations) == 2
    assert diagnostics['transition_bridge_leak_markers'] == ['小葵快步走向平台边缘']
    assert diagnostics['transition_bridge_leak_markers_after_rewrite'] == ['小葵快步走向平台边缘']
    assert diagnostics['transition_structure_rejected'] is True
    assert await runtime.restore_session('bridge_leak_rollback') == current


def test_boundary_items_exclude_player_agency_and_style_rules():
    """窄判定只核对世界事实类禁令；玩家授权与写作风格要求不进入，避免把正常邀请判成越界。"""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_context import contract_boundary_items

    node = {'story_beat': {
        'must_not_happen': ['不替玩家选择或执行动作', '不靠重复确认和配角任务拖长篇幅',
                            '不得直接到达最高处平台', '不得引入NPC或摄影任务'],
        'character_state': {'scene_boundaries': ['不得提出需要玩家回答的新问题或任务',
                                                '不得描述已到达终点平台']},
    }}
    assert contract_boundary_items(node) == ('不得直接到达最高处平台', '不得引入NPC或摄影任务', '不得描述已到达终点平台')
    assert contract_boundary_items({'story_beat': {'must_not_happen': ['不替玩家选择或执行动作']}}) == ()


def test_contract_boundary_projection_has_one_public_order_and_fact_scope():
    """运行端各消费者共享顺序；窄复核只保留可核对的事实边界。"""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_context import project_contract_boundaries

    beat = {
        'opening_only_boundaries': ['开场临时边界'],
        'must_not_happen': ['不得提前到达', '不替玩家选择'],
        'character_state': {'scene_boundaries': ['不得交换主体']},
        'acting_contract': {'forbidden_behaviors': ['不得越过关系上限']},
    }

    assert project_contract_boundaries(beat, include_opening_only=True) == (
        '开场临时边界', '不得交换主体', '不得越过关系上限', '不得提前到达', '不替玩家选择',
    )
    assert project_contract_boundaries(beat, fact_only=True) == (
        '不得提前到达', '不得交换主体',
    )
