"""Review-chain wait optimization: deadline placement, retained evidence after rewrites, and whole-turn review budget.  # noqa: DOCSTRING_CJK

These changes constrain only waiting and second-pass review material, not player authorization, destination disclosure, numeric settlement, or atomic commits.
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


def test_rewritten_candidate_preserves_full_review_evidence():
    """Rechecks retain full judgment responsibilities and must use the same history and completeness markers as initial checks."""  # noqa: DOCSTRING_CJK

    engine, session, outcome, candidate = _fixture(50)
    session = _long_history_session(engine, session)
    full = _judge_payload(engine, session, outcome, candidate)
    rewritten = _judge_payload(engine, session, outcome, {
        'segments': [{'phase': 'source_response', 'performance': '（她收好工具）今天就到这里吧。'}],
        'suggested_inputs': [],
    })

    assert len(full['scene_context']) > 1
    for key in ('scene_context', 'scene_fact_index', 'current_visit_history_complete'):
        assert rewritten[key] == full[key]


def test_review_budget_trimming_marks_history_incomplete(monkeypatch):
    """Total-budget pressure may still trim the whole turn, but cannot additionally clear recheck history."""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_budget import NUMERIC_V2_ACTOR_BUDGET_PROFILES

    engine, session, outcome, candidate = _fixture(950)
    session = _long_history_session(engine, session)
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES['economy'], 'formal_judge_input_max_tokens', 1500)
    narrowed = _judge_payload(engine, session, outcome, candidate)
    assert narrowed['scene_fact_index'] == []
    assert narrowed['current_visit_history_complete'] is False


def test_missed_initiation_recheck_keeps_full_history():
    """Supplementary missed-case checks must first inspect complete public history, without targeted narrowing for rewritten material."""  # noqa: DOCSTRING_CJK

    engine, session, outcome, candidate = _fixture(200)
    session = _long_history_session(engine, session)
    full = _judge_payload(engine, session, outcome, candidate, check_missed_initiation=True)
    assert len(full['scene_context']) > 1


@pytest.mark.asyncio
async def test_judge_messages_are_built_before_client_and_deadline_covers_close(monkeypatch):
    """Message construction is outside the deadline; connection, request, and closing share one deadline."""  # noqa: DOCSTRING_CJK

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
            # Old implementation closed outside the deadline, letting this exceed it without timeout.
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

    # Assemble messages before connecting; closing time belongs within the deadline.
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
                'delivery_matches_route': True,
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
    """Every optional module except reply defaults off."""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_options import default_options

    options = default_options()
    # Preserve numeric changes from preceding decisions; other optional modules default off.
    assert options['evaluator'] is True
    assert {key: value for key, value in options.items() if key != 'evaluator'} == {
        'review': False, 'dispute': False, 'review_delivery': False, 'review_contract': False,
        'suggestion_fill': False, 'history_lookup': False, 'actor_retry': False}


def test_evaluator_off_does_not_authorize_an_unverified_first_suggestion():
    """Recommendation position does not prove player authorization; retain the current act without author-contract verification."""  # noqa: DOCSTRING_CJK

    engine, session, _outcome, _candidate = _fixture(50)
    suggestion = '（点头）好，带路吧。'
    # Proposals must come from the latest commit with transition_offered, avoiding acceptance of stale proposals.
    session = replace(session, transition_offered=True,
                      performance_history=({'revision': 1, 'transition_offered': True,
                                            'suggested_inputs': [suggestion, '再等等。']},))
    current = SimpleNamespace(session=session)
    accepted = numeric_v2_workflow._evaluation_without_evaluator(
        current, SimpleNamespace(message=suggestion))
    assert accepted.transition_intent == 'unclear'
    assert accepted.metric_changes == ()
    stalled = numeric_v2_workflow._evaluation_without_evaluator(
        current, SimpleNamespace(message='我想再看看别的地方。'))
    assert stalled.transition_intent == 'unclear'
    # Without a pending proposal, identical text still does not pass.
    session = replace(session, transition_offered=False)
    blocked = numeric_v2_workflow._evaluation_without_evaluator(
        SimpleNamespace(session=session), SimpleNamespace(message=suggestion))
    assert blocked.transition_intent == 'unclear'


def test_pending_followup_keeps_original_acceptance_button_first():
    """Follow-up replies can update recommendations but cannot replace original acceptance buttons on still-valid invitations."""  # noqa: DOCSTRING_CJK

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
    """Callers stop inheriting buttons on act transitions, withdrawal of old invites, or disclosure of new ones."""  # noqa: DOCSTRING_CJK

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
    """Valid new invitations use the author's fixed acceptance input; other visible choices retain their order."""  # noqa: DOCSTRING_CJK

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


@pytest.mark.parametrize('acceptance', ['我知道了，我会好好分析的。', '（收起星图）我会再核对坐标。'])
@pytest.mark.parametrize('already_committed', [False, True])
def test_consumed_pending_acceptance_does_not_return(acceptance, already_committed):
    """Button preservation must not reintroduce identical input already committed this turn or since the invitation."""  # noqa: DOCSTRING_CJK

    _engine, session, _outcome, _candidate = _fixture(50)
    offer = {
        'revision': 1,
        'from_node_id': session.current_node_id,
        'to_node_id': session.current_node_id,
        'transition_offered': True,
        'transition_offer_presented': True,
        'suggested_inputs': [acceptance, '我再想想。'],
    }
    followup = {
        **offer, 'revision': 2, 'transition_offer_presented': False,
        'input_text': acceptance, 'suggested_inputs': ['我再想想。'],
    }
    session = replace(session, transition_offered=True,
                      performance_history=(offer, followup) if already_committed else (offer,))
    candidate = {'performance': '我在听。', 'suggested_inputs': [acceptance, '我再想想。']}
    result, preserved = numeric_v2_workflow._preserve_pending_acceptance_suggestion(
        candidate, current=SimpleNamespace(session=session, ledger_events=()), keep_pending=True,
        player_input='还有多久？' if already_committed else '  ' + acceptance + '\n',
    )
    assert result['suggested_inputs'] == ['我再想想。']
    assert preserved is False
    assert candidate['suggested_inputs'] == [acceptance, '我再想想。']


def test_authored_acceptance_does_not_repeat_current_input():
    result, inserted = numeric_v2_workflow._insert_verified_offer_acceptance_suggestion(
        {'suggested_inputs': ['换个办法吧。']}, accept_input='我会认真分析。',
        consumed_inputs=('我会认真\n分析。',),
    )
    assert result['suggested_inputs'] == ['换个办法吧。']
    assert inserted is False


def test_new_invitation_resets_consumed_acceptance_boundary():
    """Inputs used during an old invitation do not restrict a later newly disclosed invitation."""  # noqa: DOCSTRING_CJK

    _engine, session, _outcome, _candidate = _fixture(50)
    acceptance = '（点头）好，我们出发。'
    offer = {
        'revision': 1, 'from_node_id': session.current_node_id, 'to_node_id': session.current_node_id,
        'transition_offered': True, 'transition_offer_presented': True,
        'suggested_inputs': [acceptance],
    }
    used = {**offer, 'revision': 2, 'transition_offer_presented': False, 'input_text': acceptance}
    new_offer = {**offer, 'revision': 3, 'input_text': acceptance}
    session = replace(session, transition_offered=True, performance_history=(offer, used, new_offer))
    result, preserved = numeric_v2_workflow._preserve_pending_acceptance_suggestion(
        {'suggested_inputs': ['还要带什么？']},
        current=SimpleNamespace(session=session, ledger_events=()), keep_pending=True, player_input='还有多久？',
    )
    assert result['suggested_inputs'] == [acceptance, '还要带什么？']
    assert preserved is True


def test_fallback_does_not_treat_replacement_first_option_as_acceptance():
    """After the accept button disappears, decision failures must not treat the now-first defer button as authorization."""  # noqa: DOCSTRING_CJK

    _engine, session, _outcome, _candidate = _fixture(50)
    offer = {
        'revision': 1, 'from_node_id': session.current_node_id, 'to_node_id': session.current_node_id,
        'transition_offered': True, 'transition_offer_presented': True,
        'suggested_inputs': ['好，现在出发。', '再等等。'],
    }
    followup = {**offer, 'revision': 2, 'transition_offer_presented': False,
                'suggested_inputs': ['再等等。']}
    session = replace(session, transition_offered=True, performance_history=(offer, followup))
    result = numeric_v2_workflow._evaluation_without_evaluator(
        SimpleNamespace(session=session, ledger_events=()), SimpleNamespace(message='再等等。'))
    assert result.transition_intent == 'unclear'
    assert result.metric_changes == ()


@pytest.mark.asyncio
@pytest.mark.parametrize('acceptance, offer', [
    ('我知道了，我会好好分析的。', '哭完了就擦干，看看错在哪。'),
    ('（收起星图）我会再核对坐标。', '先检查读数，看看哪里偏了。'),
])
async def test_consumed_acceptance_stays_removed_across_commits_and_restore(
    tmp_path, monkeypatch, acceptance, offer,
):
    """Remove stale buttons in both stuck-act samples through real commit/recovery paths, without extra sampling or forced act changes."""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
    from tests.unit.test_theater_numeric_v2_player_transition import initiation_case

    engine = initiation_case()['engine']
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='consumed_acceptance', catgirl_binding=_binding(),
                                          opening_performance={'performance': '我在听。', 'suggested_inputs': []})
    outcome = runtime.prepare_turn(current, TurnRequestV2('setup', 0, '接下来呢？'), ())
    outcome, performance = engine.finalize_transition_offer_state(outcome, {
        'performance': offer, 'suggested_inputs': [acceptance, '（摇头）再等等。'],
    }, new_offer=True)
    current = await runtime.commit_turn(outcome, performance)
    calls = []

    async def evaluate(self, **kwargs):
        calls.append('evaluate')
        return evaluator.NumericV2EvaluationResult((), False, transition_intent='unclear')

    async def generate(self, **kwargs):
        calls.append('actor')
        return {'performance': '（点头）我们再商量一下。', 'transition_offered': False,
                'suggested_inputs': ['（摇头）再等等。', '（抬头）还有别的办法吗？']}

    async def review(self, **kwargs):
        calls.append('review')
        return evaluator.NumericV2TransitionOfferReview(False, False, (), ())

    async def review_on():
        return {**await _all_off(), 'evaluator': True, 'review': True}

    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    monkeypatch.setattr(numeric_v2_workflow, 'aload_theater_module_options', review_on)
    node = current.session.current_node_id
    for index, message in enumerate((acceptance, '还有多久？', '还有别的办法吗？')):
        result = await numeric_v2_workflow.execute_numeric_v2_turn(
            config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2(f'followup_{index}', current.session.revision, message),
            ensure_current_binding=lambda _: _binding(),
        )
        assert acceptance not in result.performance['suggested_inputs']
        assert len(result.performance['suggested_inputs']) == 2
        assert result.stored.session.current_node_id == node
        assert result.stored.session.transition_offered is True
        assert result.diagnostics['pending_acceptance_suggestions_preserved'] == 0
        runtime = NumericV2Runtime(engine, tmp_path)
        current = await runtime.restore_session(current.session.session_id)
        assert current.session.performance_history[-1]['suggested_inputs'] == result.performance['suggested_inputs']
        fallback = numeric_v2_workflow._evaluation_without_evaluator(
            current, SimpleNamespace(message=result.performance['suggested_inputs'][0]))
        assert fallback.transition_intent == 'unclear'
    assert calls == ['evaluate', 'actor', 'review'] * 3


@pytest.mark.asyncio
async def test_workflow_inserts_acceptance_only_after_offer_review(tmp_path, monkeypatch):
    """Workflow inserts fixed acceptance buttons only for new invitations confirmed valid by review."""  # noqa: DOCSTRING_CJK

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
@pytest.mark.parametrize('authored', [False, True])
@pytest.mark.parametrize('followup', ['', '（歪头）你觉得呢？'])
async def test_evaluator_failure_only_accepts_verified_authored_button(tmp_path, monkeypatch, authored, followup):
    """On decision failure, verify only the author contract; never treat Actor's first recommendation as acceptance permission."""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
    from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
    from tests.unit.test_theater_numeric_v2_transition_history import _candidate

    case = initiation_case()
    engine = case['engine']
    runtime = NumericV2Runtime(engine, tmp_path)
    suggestion = '（点头）好，带路吧。'
    if authored:
        engine.nodes['start']['route_gates'][1]['transition_contract'].update(
            fallback_offer='手续办妥了，我们现在去阅览室吧。', accept_input=suggestion)
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
                'performance': '手续办妥了，我们现在去阅览室吧。' + followup,
                'suggested_inputs': [suggestion, '再等等。'],
                'transition_offered': True,
            }
        if kwargs['outcome'].session.current_node_id == current.session.current_node_id:
            return {'performance': '先留在这里。', 'suggested_inputs': [], 'transition_offered': False}
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
    # A: even a literal authored quote is not a program-issued invitation.
    assert current.session.transition_offered is False

    diagnostics = {}
    result = await numeric_v2_workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=current,
        turn=TurnRequestV2('accept', current.session.revision, suggestion, input_source='suggestion'),
        ensure_current_binding=lambda _: _binding(),
        diagnostics_sink=diagnostics,
    )

    assert diagnostics['evaluator_degraded'] is True
    assert result.stored.session.current_node_id == current.session.current_node_id


@pytest.mark.asyncio
async def test_all_modules_off_keeps_only_the_actor_call(tmp_path, monkeypatch):
    """With every module off, only one actor call remains: neither decisions nor review send requests, yet turns still commit atomically."""  # noqa: DOCSTRING_CJK

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
    # Actors no longer add recommendations internally either (switch passed in this round).
    assert generations[0]['allow_suggestion_fill'] is False
    assert result.stored.session.revision == current.session.revision + 1


@pytest.mark.asyncio
async def test_review_budget_skips_later_rechecks_without_committing_unreviewed_rewrite(tmp_path, monkeypatch):
    """Run the first fast check; if budget cannot recheck the new draft, retain the original transaction without reusing old-draft judgments."""  # noqa: DOCSTRING_CJK

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
        reviews.append(kwargs)
        return _violating_review(**kwargs)

    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    monkeypatch.setattr(numeric_v2_workflow, 'NUMERIC_V2_REVIEW_BUDGET_SECONDS', 0.0)

    diagnostics = {}
    with pytest.raises(NumericV2ActorOutputError, match='numeric_v2_transition_review_failed'):
        await numeric_v2_workflow.execute_numeric_v2_turn(
            config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2('new', current.session.revision, '我来帮你。'),
            ensure_current_binding=lambda _: _binding(), diagnostics_sink=diagnostics)

    # Add no calls: the new draft is unreviewed; retain original values, location, invite, body, and Ledger together.
    assert len(reviews) == 1
    assert len(generations) == 2
    assert diagnostics['review_budget_skips'] == 1
    assert diagnostics['semantic_review_fallback'] is False
    assert diagnostics['review_timeout_aborted'] is True
    assert await runtime.restore_session('review_budget') == current


@pytest.mark.asyncio
async def test_review_budget_exhaustion_keeps_transition_rollback(tmp_path, monkeypatch):
    """Formal transitions cannot commit when budget skips rechecking: retain rollback semantics for incomplete review."""  # noqa: DOCSTRING_CJK

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
        # Actually cross acts to cover the formal transition's no-commit-without-completed-review branch.
        return evaluator.NumericV2EvaluationResult(
            (MetricChangeV2('trust', 2, '玩家兑现承诺', '我来帮你。'),), False,
            transition_intent='initiate', )

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
    # Transaction rollback: uncommitted scene changes must not enter saves.
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
            transition_intent='initiate', )

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
    """Budgets constrain waiting only, never replace authorization, destination, or commit decisions."""  # noqa: DOCSTRING_CJK

    assert NUMERIC_V2_REVIEW_BUDGET_SECONDS > 0
    assert evaluator.NUMERIC_V2_DISPUTE_JUDGE_TIMEOUT_SECONDS < NUMERIC_V2_REVIEW_BUDGET_SECONDS
    assert evaluator.NUMERIC_V2_DISPUTE_JUDGE_TIMEOUT_SECONDS > evaluator.NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS

def test_delivery_check_finds_authored_props_only_from_contract():
    """Delivery validation checks only props required this turn; retained items need no repetition every act."""  # noqa: DOCSTRING_CJK

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

    # Check only when authors explicitly request delivery this round; all three body paragraphs and narration are player-visible.
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
    """Narrow decisions accept only verbatim entries from author prohibitions, discarding model-invented violations."""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_evaluator import _parse_contract_check_output

    required = ('不得直接到达最高处平台', '不得引入NPC或摄影任务')
    assert _parse_contract_check_output(
        '{"violated":["不得直接到达最高处平台"]}', required) == ('不得直接到达最高处平台',)
    assert _parse_contract_check_output('{"violated":["我自己编的违规"]}', required) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize('include_all_segments', [False, True])
async def test_boundary_check_reads_author_boundaries_and_reports_violation(monkeypatch, include_all_segments):
    """Narrow decisions must actually read author prohibitions and return verbatim matches to callers."""  # noqa: DOCSTRING_CJK

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
        node=node, actor_performance={'segments': [
            {'phase': 'source_response', 'performance': ''.join(
                f'（点头）这是第{i}条已经确认的消息。' for i in range(8)
            )},
            {'phase': 'transition_bridge', 'scene_narration': '走过林间小径。'},
            {'phase': 'target_opening', 'performance': '两人抵达观景坡顶端的平台。'},
        ]},
        include_all_segments=include_all_segments,
        player_input='（加快步伐走完最后几级台阶）呼，终于到了！')

    assert violated == ('不得直接到达最高处平台',)
    sent = captured['messages'][0].content + captured['messages'][1].content
    assert '不得直接到达最高处平台' in sent
    assert '不得描述已到达终点平台' in sent
    assert '两人抵达观景坡顶端的平台。' in sent
    assert ('这是第0条已经确认的消息。' in sent) == include_all_segments
    # Acts without prohibitions make no request and return empty directly.
    captured.clear()
    assert await worker.verify_contract_boundaries(
        node={'story_beat': {}}, actor_performance={'performance': '随便。'},
        player_input='随便。') == ()
    assert captured == {}


@pytest.mark.asyncio
async def test_contract_switch_checks_stay_turns_and_rewrites_once(tmp_path, monkeypatch):
    """Stay-in-act turns also check author prohibitions: one hit shares one rewrite; retain recheck diagnostics separately."""  # noqa: DOCSTRING_CJK

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
    """With the switch off, stay-in-act turns send no narrow-decision request."""  # noqa: DOCSTRING_CJK

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
    """Transition boundary checks retain only source responses/bridges; target-act openings do not participate in source-act prohibition checks."""  # noqa: DOCSTRING_CJK

    full = {'segments': [
        {'phase': 'source_response', 'performance': '（点头）好。'},
        {'phase': 'transition_bridge', 'scene_narration': '窗外的雨渐渐停了。'},
        {'phase': 'target_opening', 'performance': '（望向窗外）终于可以歇一会儿了。'},
    ]}
    view = numeric_v2_workflow._source_side_delivery(full)
    assert [segment['phase'] for segment in view['segments']] == ['source_response', 'transition_bridge']
    # Retain stay-in-act candidates unchanged when missing the three-paragraph structure; do not trim.
    plain = {'performance': '（点头）好。'}
    assert numeric_v2_workflow._source_side_delivery(plain) is plain


@pytest.mark.asyncio
async def test_transition_boundary_check_never_sends_the_target_opening(tmp_path, monkeypatch):
    """Narrow transition checks must exclude target-act openings, or source prohibitions misclassify normal transitions."""  # noqa: DOCSTRING_CJK

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
            transition_intent='initiate', )

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
    """If structural repetition survives rewriting, formal transitions must roll back instead of using final-draft fallback commits."""  # noqa: DOCSTRING_CJK

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
            (), False, transition_intent='initiate', )

    async def generate(self, **kwargs):
        generations.append(kwargs.get('retry_hint', ''))
        candidate = _candidate()
        candidate['bridge_scene_narration'] = '沿着石阶向上，视野豁然开朗，小葵快步走向平台边缘。'
        candidate['target_scene_narration'] = target_opening
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
    """Narrow checks cover only world-fact prohibitions, excluding player authorization and style requirements to avoid rejecting normal invitations."""  # noqa: DOCSTRING_CJK

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
    """Runtime consumers share ordering; narrow review retains only verifiable factual boundaries."""  # noqa: DOCSTRING_CJK

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
