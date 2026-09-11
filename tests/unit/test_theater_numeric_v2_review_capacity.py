"""正式转场扩容不改变普通复核、完整证据保护或现有调用策略。"""

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
    """长正文只用于测实际分词长度，不代表文学或事实检查已经通过。"""
    engine = _engine()
    session = engine.create_session(session_id='capacity', catgirl_binding=_binding(), opening_performance=_opening(), actor_budget_profile='economy')
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '好。'), (), scene_complete=True, natural_ending_ready=True)
    candidate = {'segments': [{'phase': 'source_response', 'performance': '（' + '她收好工具。' * repetitions + '）'}], 'suggested_inputs': []}
    return engine, session, outcome, candidate


@pytest.mark.asyncio
@pytest.mark.parametrize('disputed', [False, True])
@pytest.mark.parametrize('repetitions,allowed', [(700, True), (950, True), (2000, False)])
async def test_formal_capacity_applies_before_fast_and_dispute_calls(monkeypatch, repetitions, allowed, disputed):
    """正式复核按现行8000上限送审，覆盖超过普通6000的正文及正式超限拒绝。"""
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content=json.dumps(dict(offer_present=False, valid=False,
                body_violations=[], unsafe_suggestion_indexes=[], failure_reason='')))

    async def config(_):
        return dict(model='test', base_url='http://test.invalid')

    async def factory(*args, **kwargs):
        assert kwargs['max_retries'] == 0
        assert kwargs['timeout'] == (30 if disputed else 8)
        # 正式快检输出余量和争议时限保持原约束。
        assert kwargs['max_completion_tokens'] == (4096 if disputed else 512)
        return Client()

    monkeypatch.setattr(evaluator, '_model_config', config)
    monkeypatch.setattr(evaluator, 'create_chat_llm_async', factory)
    monkeypatch.setattr(evaluator, 'focus_extra_body', lambda _: {'enable_thinking': True})
    engine, session, outcome, candidate = _fixture(repetitions)
    kwargs = dict(engine=engine, session=session, message='好。', actor_performance=candidate, transition_outcome=outcome)
    worker = evaluator.NumericV2MetricEvaluator(object())
    if allowed:
        await worker.validate_transition_offer(**kwargs, dispute_review=disputed)
        assert len(calls) == 1
        tokens = sum(evaluator.count_tokens(m.content) for m in calls[0])
        assert 4200 < tokens <= NUMERIC_V2_ACTOR_BUDGET_PROFILES['economy']['formal_judge_input_max_tokens'] == 8000
        if repetitions == 950:
            assert tokens > 6000
        data = json.loads(calls[0][1].content.split('：', 1)[1])
        # 正式快检与争议复查也须区分摘录与完整证据，不能由缺项否定已做动作。
        assert 'excerpt_only' in calls[0][0].content
        assert data['candidate_segments'][0]['performance'] == candidate['segments'][0]['performance']
    else:
        with pytest.raises(evaluator.NumericV2EvaluatorError, match='transition_review_budget_exceeded'):
            await worker.validate_transition_offer(**kwargs, dispute_review=disputed)
        assert calls == []


def test_formal_packing_keeps_more_history_without_changing_ordinary_review(monkeypatch):
    """正式容量实验不能连带改变已确认的普通6000预算。"""
    engine, session, outcome, candidate = _fixture(200)
    history = tuple({'revision': i, 'from_node_id': 'start', 'to_node_id': 'start', 'performance_contract_version': 3,
        'input_text': '我听见了。', 'performance': '（' + '她整理好手边的工具。' * 80 + '）'} for i in range(1, 7))
    session = replace(session, revision=6, performance_history=history)

    def build(formal):
        return evaluator._build_transition_judge_messages(engine, session, actor_performance=candidate,
            player_input='好。', transition_outcome=outcome if formal else None)

    expanded, ordinary = build(True), build(False)
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES['economy'], 'formal_judge_input_max_tokens', 4200)
    old_formal, ordinary_again = build(True), build(False)
    assert [m.content for m in ordinary] == [m.content for m in ordinary_again]
    assert sum(evaluator.count_tokens(m.content) for m in ordinary) <= 6000
    assert sum(evaluator.count_tokens(m.content) for m in expanded) <= 8000
    assert len(json.loads(expanded[1].content.split('：', 1)[1])['scene_context']) > len(json.loads(old_formal[1].content.split('：', 1)[1])['scene_context'])
