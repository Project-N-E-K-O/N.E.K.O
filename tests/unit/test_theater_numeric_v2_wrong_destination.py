"""错误主动转场使用同一改稿额度留幕；不重复计分，也不污染后续历史。"""
import json
import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as workflow
from services.theater.numeric_v2_actor import NumericV2ActorOutputError
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2, MetricChangeV2
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


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
@pytest.mark.parametrize('dispute', ['reject', 'timeout', 'allow'])
@pytest.mark.parametrize('ordinary_result', ['pass', 'body', 'technical'])
async def test_wrong_destination_reuses_rewrite_and_commits_only_current_scene(tmp_path, monkeypatch, dispute, ordinary_result):
    """复核纠正后只写一条原节点记录；争议放行、改稿失败和剩余语义兜底分别验收。"""
    c = initiation_case(); engine = c['engine']; runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='wrong_destination', catgirl_binding=_binding(),
                                          opening_performance=c['session'].opening_performance)
    actor_nodes = []; review_modes = []; evaluation_calls = []

    async def evaluate(self, **kwargs):
        evaluation_calls.append(kwargs)
        return ev.NumericV2EvaluationResult((MetricChangeV2('trust', 2, '玩家兑现承诺', c['message']),),
                                            False, transition_intent='initiate', public_destination_quote='左侧走廊通往阅览室，通道已经开放。')

    async def generate(self, **kwargs):
        outcome = kwargs['outcome']; actor_nodes.append(outcome.session.current_node_id)
        assert outcome.session.metrics['trust'] == current.session.metrics['trust'] + 2
        if outcome.session.current_node_id == 'start':
            if ordinary_result == 'technical':
                raise NumericV2ActorOutputError('numeric_v2_actor_test_failed')
            assert kwargs['interaction_intent'] == 'scene_action'
            return {'performance': '先沿你刚才指的方向看看。', 'suggested_inputs': [], 'transition_offered': False}
        return engine.finalize_transition_performance(outcome, _candidate(), target_opening='错误转场待审候选。')

    async def review(self, **kwargs):
        review_modes.append((kwargs['route_changed'], bool(kwargs.get('dispute_review'))))
        if kwargs['route_changed']:
            if kwargs.get('dispute_review') and dispute == 'timeout':
                raise ev.NumericV2EvaluatorError('test_dispute_timeout')
            quote = '左侧走廊通往阅览室，通道已经开放。' if kwargs.get('dispute_review') and dispute == 'allow' else ''
            return ev._parse_transition_judge_output(json.dumps(dict(
                offer_present=False, valid=False, body_violations=[] if quote else ['player_action'],
                unsafe_suggestion_indexes=[], failure_reason='目的地不符。', public_destination_quote=quote, initiation_authorized=bool(quote))),
                initiation_session=current.session)
        assert not kwargs.get('check_missed_initiation'), '已撤销的请求不能在同轮重新恢复转场'
        return ev.NumericV2TransitionOfferReview(False, False, ('author_boundary',) if ordinary_result == 'body' else (), ())

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    kwargs = dict(config_manager=object(), runtime=runtime, current=current,
                  turn=TurnRequestV2('go', 0, c['message']), ensure_current_binding=lambda _: _binding())
    if ordinary_result == 'technical' and dispute != 'allow':
        with pytest.raises(NumericV2ActorOutputError):
            await workflow.execute_numeric_v2_turn(**kwargs)
        assert await runtime.restore_session('wrong_destination') == current
        return
    result = await workflow.execute_numeric_v2_turn(**kwargs)
    assert len(evaluation_calls) == 1
    assert result.stored.session.revision == 1 and len(result.stored.ledger_events) == 1
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
        assert result.diagnostics['semantic_review_fallback'] is (ordinary_result == 'body')
        assert '错误转场待审候选' not in str(result.stored)
        assert len(review_modes) == 3
    assert 'initiation_authorized' not in result.stored.session.to_dict()
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
