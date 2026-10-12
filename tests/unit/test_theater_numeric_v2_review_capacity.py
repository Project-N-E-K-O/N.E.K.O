"""Review capacity preserves complete evidence and rejects excess input before client construction."""

from dataclasses import replace
import json
from types import SimpleNamespace

from services.theater.numeric_v2_budget import NUMERIC_V2_ACTOR_BUDGET_PROFILES
import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


def _fixture(repetitions=700):
    """Long prose tests actual token length, not literary or factual correctness."""
    engine = _engine()
    session = engine.create_session(session_id='capacity', catgirl_binding=_binding(), opening_performance=_opening(), actor_budget_profile='economy')
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '好。'), (), scene_complete=True, natural_ending_ready=True)
    candidate = {'segments': [{'phase': 'source_response', 'performance': '（' + '她收好工具。' * repetitions + '）'}], 'suggested_inputs': []}
    return engine, session, outcome, candidate


@pytest.mark.asyncio
@pytest.mark.parametrize('disputed', [False, True])
@pytest.mark.parametrize('review_kind,repetitions,allowed', [
    ('formal', 1050, True), ('formal', 1100, False),
    ('ordinary', 950, True), ('ordinary', 1000, False),
    ('missed', 850, True), ('missed', 900, False),
])
async def test_review_capacity_applies_before_fast_and_dispute_calls(monkeypatch, review_kind, repetitions, allowed, disputed):
    """All review branches accept complete input above 8000 and reject input above 8500 before sending."""
    calls = []
    clients = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages):
            calls.append(messages)
            payload = dict(offer_present=False, offer_quote="", valid=False,
                body_violations=[], unsafe_suggestion_indexes=[], failure_reason='')
            if review_kind == 'formal':
                payload['delivery_matches_route'] = True
            return SimpleNamespace(content=json.dumps(payload))

    async def config(_):
        return dict(model='test', base_url='http://test.invalid')

    async def factory(*args, **kwargs):
        clients.append(kwargs)
        assert kwargs['max_retries'] == 0
        assert kwargs['timeout'] == (
            evaluator.NUMERIC_V2_DISPUTE_JUDGE_TIMEOUT_SECONDS if disputed
            else evaluator.NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS
        )
        # 输入容量调整不改变输出余量、重试或等待时限。
        fast_output = {'formal': 512, 'ordinary': 190, 'missed': 286}[review_kind]
        assert kwargs['max_completion_tokens'] == (4096 if disputed else fast_output)
        return Client()

    monkeypatch.setattr(evaluator, '_model_config', config)
    monkeypatch.setattr(evaluator, 'create_chat_llm_async', factory)
    monkeypatch.setattr(evaluator, 'focus_extra_body', lambda _: {'enable_thinking': True})
    engine, session, outcome, candidate = _fixture(repetitions)
    if review_kind != 'formal':
        candidate = {'performance': candidate['segments'][0]['performance'], 'suggested_inputs': []}
    kwargs = dict(engine=engine, session=session, message='好。', actor_performance=candidate)
    if review_kind == 'formal':
        kwargs['transition_outcome'] = outcome
    elif review_kind == 'missed':
        kwargs['check_missed_initiation'] = True
    worker = evaluator.NumericV2MetricEvaluator(object())
    if allowed:
        await worker.validate_transition_offer(**kwargs, dispute_review=disputed)
        assert len(calls) == len(clients) == 1
        tokens = sum(evaluator.count_tokens(m.content) for m in calls[0])
        budget = NUMERIC_V2_ACTOR_BUDGET_PROFILES['economy']
        assert budget['judge_input_max_tokens'] == budget['formal_judge_input_max_tokens'] == 8500
        assert 8000 < tokens <= 8500
        data = json.loads(calls[0][1].content.split('：', 1)[1])
        # 正式快检与争议复查也须区分摘录与完整证据，不能由缺项否定已做动作。
        assert 'excerpt_only' in calls[0][0].content
        if review_kind == 'formal':
            assert data['candidate_segments'][0]['performance'] == candidate['segments'][0]['performance']
        else:
            assert data['actor_performance'] == candidate['performance']
            assert ('missed_initiation_check' in data) is (review_kind == 'missed')
    else:
        with pytest.raises(evaluator.NumericV2EvaluatorError, match='transition_review_budget_exceeded'):
            await worker.validate_transition_offer(**kwargs, dispute_review=disputed)
        assert calls == clients == []


def test_formal_budget_override_does_not_change_ordinary_packing(monkeypatch):
    """The legacy formal budget key still controls only formal packing when overridden."""
    engine, session, outcome, candidate = _fixture(200)
    history = tuple({'revision': i, 'from_node_id': 'start', 'to_node_id': 'start', 'performance_contract_version': 3,
        'input_text': '我听见了。', 'performance': '（' + '她整理好手边的工具。' * 80 + '）'} for i in range(1, 7))
    session = replace(session, revision=6, performance_history=history)

    def build(formal):
        return evaluator._build_transition_judge_messages(engine, session, actor_performance=candidate,
            player_input='好。', transition_outcome=outcome if formal else None)[0]

    expanded, ordinary = build(True), build(False)
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES['economy'], 'formal_judge_input_max_tokens', 4200)
    old_formal, ordinary_again = build(True), build(False)
    assert [m.content for m in ordinary] == [m.content for m in ordinary_again]
    assert sum(evaluator.count_tokens(m.content) for m in ordinary) <= 8500
    assert sum(evaluator.count_tokens(m.content) for m in expanded) <= 8500
    assert len(json.loads(expanded[1].content.split('：', 1)[1])['scene_context']) > len(json.loads(old_formal[1].content.split('：', 1)[1])['scene_context'])
