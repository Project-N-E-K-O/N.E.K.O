"""主动转场或接受邀请去向错误时共用一次留幕改稿；不重复计分与提交。"""
import json
import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as workflow
from services.theater.numeric_v2_actor import NumericV2ActorOutputError
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2, MetricChangeV2
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


@pytest.mark.parametrize('invalid', [False, True])
def test_pending_invitation_invalid_is_independent_of_player_acceptance(invalid):
    review = ev._parse_transition_judge_output(json.dumps(dict(
        offer_present=False, valid=False, body_violations=[], unsafe_suggestion_indexes=[],
        acceptance_authorized=True, pending_invitation_invalid=invalid)), acceptance_review=True)
    assert review.pending_invitation_invalid is invalid
    assert review.acceptance_authorized is (not invalid)
    assert ('player_action' in review.body_violations) is invalid


@pytest.mark.parametrize('value', ['false', 0, None])
def test_pending_invitation_invalidation_requires_boolean(value):
    with pytest.raises(ev.NumericV2EvaluatorOutputError, match='fields_invalid'):
        ev._parse_transition_judge_output(json.dumps(dict(
            offer_present=False, valid=False, body_violations=[], unsafe_suggestion_indexes=[],
            acceptance_authorized=False, pending_invitation_invalid=value)), acceptance_review=True)


async def _commit_invitation(runtime, current, text):
    """通过真实提交建立待确认邀请，开场布尔字段本身不会锁存邀请。"""
    outcome = runtime.prepare_turn(current, TurnRequestV2('invite', current.session.revision, '接下来呢？'), ())
    outcome, performance = runtime.engine.finalize_transition_offer_state(outcome,
        {'performance': text, 'suggested_inputs': [], 'transition_offered': True}, new_offer=True)
    return await runtime.commit_turn(outcome, performance)


@pytest.mark.parametrize('quote,rejected', [('', True), ('虚构的公开去向。', True), ('左侧走廊通往阅览室，通道已经开放。', False)])
def test_formal_quote_rejection_is_structured_and_keeps_reason(quote, rejected):
    """依原文核验结果标记，不能靠解析中文错误理由决定改路。"""
    c = initiation_case()
    payload = dict(offer_present=False, valid=False, body_violations=['player_action'],
                   unsafe_suggestion_indexes=[], failure_reason='玩家只答应去店铺，没有答应返回住处。',
                   public_destination_quote=quote, initiation_authorized=not rejected)
    review = ev._parse_transition_judge_output(json.dumps(payload), initiation_session=c['session'])
    assert review.initiation_authorized is (not rejected)
    assert '玩家只答应去店铺' in review.failure_reason


@pytest.mark.asyncio
@pytest.mark.parametrize('intent', ['initiate', 'accept'])
@pytest.mark.parametrize('dispute', ['reject', 'timeout', 'allow'])
@pytest.mark.parametrize('ordinary_result', ['pass', 'body', 'technical', 'new_offer', 'bad_offer'])
@pytest.mark.parametrize('invalid_invitation', [False, True])
async def test_wrong_destination_reuses_rewrite_and_commits_only_current_scene(tmp_path, monkeypatch, intent, dispute, ordinary_result, invalid_invitation):
    """复核纠正后只写一条原节点记录；争议放行、改稿失败和剩余语义兜底分别验收。"""
    c = initiation_case(); engine = c['engine']; runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='wrong_destination', catgirl_binding=_binding(),
        opening_performance=c['session'].opening_performance)
    if intent == 'accept':
        current = await _commit_invitation(runtime, current, '（收好工具）咱们去街市看看，好吗？')
    actor_nodes = []; review_modes = []; evaluation_calls = []

    async def evaluate(self, **kwargs):
        evaluation_calls.append(kwargs)
        return ev.NumericV2EvaluationResult((MetricChangeV2('trust', 2, '玩家兑现承诺', c['message']),),
                                            False, transition_intent=intent, public_destination_quote='左侧走廊通往阅览室，通道已经开放。' if intent == 'initiate' else '')

    async def generate(self, **kwargs):
        outcome = kwargs['outcome']; actor_nodes.append(outcome.session.current_node_id)
        assert outcome.session.metrics['trust'] == current.session.metrics['trust'] + 2
        if outcome.session.current_node_id == 'start':
            # 取消去向后不能把复核中的“玩家想去别处”当成执行另一场景的命令。
            assert '留幕不代表改去另一个跨幕地点' in kwargs['retry_hint']
            assert '承认自己先前邀约有误' in kwargs['retry_hint']
            assert '已获准的幕内行动照常回应' in kwargs['retry_hint']
            assert '玩家只同意前往旧地点' not in kwargs['retry_hint']
            if ordinary_result == 'technical':
                raise NumericV2ActorOutputError('numeric_v2_actor_test_failed')
            assert kwargs['interaction_intent'] == 'scene_action'
            return {'performance': '刚才我说错了，去阅览室看看，好吗？' if ordinary_result == 'new_offer' else '先沿你刚才指的方向看看。',
                    'suggested_inputs': [], 'transition_offered': False}
        return engine.finalize_transition_performance(outcome, _candidate(), target_opening='错误转场待审候选。')

    async def review(self, **kwargs):
        review_modes.append((kwargs['route_changed'], bool(kwargs.get('dispute_review'))))
        if kwargs['route_changed']:
            if kwargs.get('dispute_review') and dispute == 'timeout':
                raise ev.NumericV2EvaluatorError('test_dispute_timeout')
            quote = '左侧走廊通往阅览室，通道已经开放。' if kwargs.get('dispute_review') and dispute == 'allow' else ''
            fields = {'acceptance_authorized': bool(quote), 'pending_invitation_invalid': invalid_invitation and not bool(quote)} if intent == 'accept' else {
                'public_destination_quote': quote, 'initiation_authorized': bool(quote)}
            return ev._parse_transition_judge_output(json.dumps(dict(
                offer_present=False, valid=False, body_violations=[] if quote else ['player_action'],
                unsafe_suggestion_indexes=[], failure_reason='玩家只同意前往旧地点，候选去向不符。', **fields)),
                **({'acceptance_review': True} if intent == 'accept' else {'initiation_session': current.session}))
        assert not kwargs.get('check_missed_initiation'), '已撤销的请求不能在同轮重新恢复转场'
        assert kwargs.get('cancelled_transition') is True
        return ev.NumericV2TransitionOfferReview(ordinary_result in {'new_offer', 'bad_offer'}, ordinary_result == 'new_offer',
            ('author_boundary',) if ordinary_result == 'body' else (), ())

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    kwargs = dict(config_manager=object(), runtime=runtime, current=current,
                  turn=TurnRequestV2('go', current.session.revision, c['message']), ensure_current_binding=lambda _: _binding())
    if ordinary_result == 'technical' and dispute != 'allow':
        with pytest.raises(NumericV2ActorOutputError):
            await workflow.execute_numeric_v2_turn(**kwargs)
        assert await runtime.restore_session('wrong_destination') == current
        return
    result = await workflow.execute_numeric_v2_turn(**kwargs)
    assert len(evaluation_calls) == 1
    assert result.stored.session.revision == current.session.revision + 1
    assert len(result.stored.ledger_events) == len(current.ledger_events) + 1
    assert result.stored.session.metrics['trust'] == current.session.metrics['trust'] + 2
    if dispute == 'allow':
        assert result.stored.session.current_node_id == 'ending_leave'
        assert actor_nodes == ['ending_leave']
    else:
        assert result.stored.session.current_node_id == 'start'
        assert actor_nodes == ['ending_leave', 'start']
        assert result.stored.ledger_events[-1]['transition_intent'] == 'unclear'
        assert result.diagnostics['transition_cancellations'] == 1
        assert result.diagnostics['semantic_rewrite_attempts'] == 1
        assert result.diagnostics['semantic_review_fallback'] is (ordinary_result in {'body', 'bad_offer'})
        assert '错误转场待审候选' not in str(result.stored)
        assert len(review_modes) == 3
        invalidated = intent == 'accept' and invalid_invitation
        assert result.stored.ledger_events[-1].get('transition_offer_invalidated', False) is invalidated
        if invalidated:
            from services.theater.numeric_v2_context import pending_transition_record
            assert result.stored.session.transition_offered is (ordinary_result == 'new_offer')
            pending = pending_transition_record(result.stored.session, include_withdrawn=True)
            assert (pending is not None) is (ordinary_result == 'new_offer')
            if pending is not None:
                assert pending['revision'] == result.stored.session.revision
        elif intent == 'accept':
            assert result.stored.session.transition_offered, '不能撤下尚未接受的合法邀请'
    assert 'initiation_authorized' not in result.stored.session.to_dict()
    assert 'acceptance_authorized' not in result.stored.session.to_dict()
    assert await NumericV2Runtime(engine, tmp_path).restore_session('wrong_destination') == result.stored


def test_real_quote_can_still_reject_wrong_destination():
    """复现真实快检：茶店引文真实存在，但候选去了别处，不能由出处有效推导授权。"""
    c = initiation_case()
    review = ev._parse_transition_judge_output(json.dumps(dict(
        offer_present=False, valid=False, body_violations=[], unsafe_suggestion_indexes=[],
        public_destination_quote='左侧走廊通往阅览室，通道已经开放。', initiation_authorized=False)),
        initiation_session=c['session'])
    assert review.initiation_authorized is False
    assert review.body_violations == ('player_action',)


@pytest.mark.parametrize('quote,expected', [
    ('左侧走廊通往阅览室，通道已经开放。', True), ('虚构的公开去向。', False),
])
def test_authorized_move_keeps_separate_action_review_and_requires_real_quote(quote, expected):
    """获准移动不代表可替玩家操作；伪造出处也不能凭 true 获准。"""
    c = initiation_case()
    review = ev._parse_transition_judge_output(json.dumps(dict(offer_present=False, valid=False,
        body_violations=['player_action'], unsafe_suggestion_indexes=[],
        initiation_authorized=True, public_destination_quote=quote)), initiation_session=c['session'])
    assert review.initiation_authorized is expected
    assert review.body_violations == ('player_action',)


def test_legacy_review_does_not_infer_destination_rejection_from_action_violation():
    """旧输出缺少移动判断时保持未知，不能把任意玩家行动问题误当成取消路线。"""
    c = initiation_case()
    review = ev._parse_transition_judge_output(json.dumps(dict(offer_present=False, valid=False,
        body_violations=['player_action'], unsafe_suggestion_indexes=[],
        public_destination_quote='左侧走廊通往阅览室，通道已经开放。')), initiation_session=c['session'])
    assert review.initiation_authorized is None


@pytest.mark.parametrize('value', ['false', 0, None])
def test_authorization_boolean_is_not_coerced(value):
    """只接受真正布尔值，错误协议不变成留幕或放行授权。"""
    c = initiation_case()
    with pytest.raises(ev.NumericV2EvaluatorOutputError):
        ev._parse_transition_judge_output(json.dumps(dict(offer_present=False, valid=False,
            body_violations=[], unsafe_suggestion_indexes=[], initiation_authorized=value)),
            initiation_session=c['session'])


@pytest.mark.parametrize('value', [True, False, 'missing'])
def test_acceptance_review_keeps_independent_authorization_and_legacy_unknown(value):
    payload = dict(offer_present=False, valid=False, body_violations=[], unsafe_suggestion_indexes=[])
    if value != 'missing':
        payload['acceptance_authorized'] = value
    review = ev._parse_transition_judge_output(json.dumps(payload), acceptance_review=True)
    assert review.acceptance_authorized is (None if value == 'missing' else value)
    assert review.initiation_authorized is None
    assert review.body_violations == (('player_action',) if value is False else ())


@pytest.mark.parametrize('value', ['false', 0, None])
def test_acceptance_authorization_requires_boolean_and_formal_acceptance_mode(value):
    payload = dict(offer_present=False, valid=False, body_violations=[], unsafe_suggestion_indexes=[], acceptance_authorized=value)
    with pytest.raises(ev.NumericV2EvaluatorOutputError):
        ev._parse_transition_judge_output(json.dumps(payload), acceptance_review=True)
    payload['acceptance_authorized'] = False
    with pytest.raises(ev.NumericV2EvaluatorOutputError):
        ev._parse_transition_judge_output(json.dumps(payload))


@pytest.mark.asyncio
@pytest.mark.parametrize('disputed', [False, True])
async def test_real_acceptance_review_call_supplies_original_invitation_and_parses_result(tmp_path, monkeypatch, disputed):
    """快检与争议走真实装箱/解析链；错误接受不再因未知字段降级丢失取消结论。"""
    from types import SimpleNamespace
    c = initiation_case(); engine = c['engine']
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='accept', catgirl_binding=_binding(), opening_performance=c['session'].opening_performance)
    current = await _commit_invitation(runtime, current, '（收好工具）咱们去街市看看，好吗？')
    session = current.session
    outcome = runtime.prepare_turn(current, TurnRequestV2('one', session.revision, '好，走吧。'), (), transition_intent='accept')
    candidate = engine.finalize_transition_performance(outcome, _candidate(), target_opening='阅览室入口。')
    calls = []

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def ainvoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content=json.dumps(dict(offer_present=False, valid=False, body_violations=[],
                unsafe_suggestion_indexes=[], acceptance_authorized=False, failure_reason='街市不是阅览室。')))

    async def config(_): return dict(model='test', base_url='http://test.invalid')
    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(ev, '_model_config', config)
    monkeypatch.setattr(ev, 'create_chat_llm_async', factory)
    monkeypatch.setattr(ev, 'focus_extra_body', lambda _: {'enable_thinking': True})
    review = await ev.NumericV2MetricEvaluator(object()).validate_transition_offer(engine=engine, session=session,
        message='好，走吧。', actor_performance=candidate, transition_outcome=outcome, dispute_review=disputed)
    assert review.acceptance_authorized is False and review.body_violations == ('player_action',)
    assert len(calls) == 1
    data = json.loads(calls[0][1].content.split('：', 1)[1])
    assert '去街市看看' in json.dumps(data['transition_authorization']['pending_invitation'], ensure_ascii=False)
    assert 'accept 标签不证明授权' in calls[0][0].content
    assert '额外操作或持物问题不否定已获准的转场' in calls[0][0].content


def test_cancelled_review_checks_fresh_invitation_without_requiring_acceptance():
    c = initiation_case()
    messages = ev._build_transition_judge_messages(c['engine'], c['session'], player_input='好，去街市吧。',
        actor_performance={'performance': '刚才说错了。我们可以去阅览室，你愿意吗？', 'suggested_inputs': []},
        cancelled_transition=True)
    assert '新邀请不要求玩家本轮已经同意' in messages[0].content
    assert '不能执行旧去向或直接执行新安排' in messages[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize('late_rejection', [False, True])
async def test_acceptance_body_repair_preserves_route_and_shares_rewrite_budget(tmp_path, monkeypatch, late_rejection):
    """合法转场的额外动作只改正文；共享改稿已耗尽时也不能另开一次取消改稿。"""
    c = initiation_case(); runtime = NumericV2Runtime(c['engine'], tmp_path)
    current = await runtime.start_session(session_id='accepted', catgirl_binding=_binding(), opening_performance=c['session'].opening_performance)
    current = await _commit_invitation(runtime, current, '（收好工具）咱们去阅览室看看，好吗？')
    actor_calls = []; evaluation_calls = []

    async def evaluate(self, **kwargs):
        evaluation_calls.append(kwargs)
        return ev.NumericV2EvaluationResult((MetricChangeV2('trust', 2, '玩家兑现承诺', '好，走吧。'),), False, transition_intent='accept')

    async def generate(self, **kwargs):
        actor_calls.append(kwargs)
        assert kwargs['outcome'].session.current_node_id == 'ending_leave'
        return c['engine'].finalize_transition_performance(kwargs['outcome'], _candidate(), target_opening='阅览室入口。')

    async def review(self, **kwargs):
        assert kwargs['route_changed'] and not kwargs.get('cancelled_transition')
        initial = len(actor_calls) == 1
        return ev._parse_transition_judge_output(json.dumps(dict(offer_present=False, valid=False,
            body_violations=['player_action'] if initial else [], unsafe_suggestion_indexes=[],
            acceptance_authorized=not (late_rejection and not initial))), acceptance_review=True)

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('go', current.session.revision, '好，走吧。'), ensure_current_binding=lambda _: _binding())
    assert len(evaluation_calls) == 1 and len(actor_calls) == 2
    assert result.stored.session.metrics['trust'] == current.session.metrics['trust'] + 2
    assert result.stored.session.revision == current.session.revision + 1
    assert result.diagnostics['transition_judge_calls'] == 3
    assert result.diagnostics['semantic_rewrite_attempts'] == 1
    assert result.diagnostics['transition_cancellations'] == 0
    assert result.diagnostics['semantic_review_fallback'] is late_rejection
    assert await runtime.restore_session('accepted') == result.stored
