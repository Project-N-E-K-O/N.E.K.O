"""Review budget pressure preserves state values and complete public evidence."""
from copy import deepcopy
import json
import pytest
from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_budget import NUMERIC_V2_ACTOR_BUDGET_PROFILES
from tests.unit.test_theater_numeric_v2_review_capacity import _fixture


def _projection():
    return {
        'revision': 0, 'coverage_from': 0, 'coverage': 'partial', 'current_node_id': 'start',
        'current': {'player': {'position': {'value': '门口', 'source_event': 'r0.opening.1', 'quote': '玩家已站在门口。' * 40}}},
        'last_known': {'item:工具': {'holder': {'value': 'character', 'source_event': 'r0.opening.2', 'quote': '角色拿着工具等待回应。' * 40}}},
        'recent_events': [{'event_id': 'r0.opening.1', 'quote': '玩家已站在门口。', 'subject': 'player'}],
        'projection': {'retained_entities': 2, 'omitted_entities': 0, 'omitted_recent_events': 0,
                       'quotes_omitted': 3, 'over_budget': False},
    }


def _inputs(monkeypatch, formal=False):
    engine, session, outcome, candidate = _fixture(20)
    if not formal:
        candidate = {'performance': candidate['segments'][0]['performance'], 'suggested_inputs': ['我先看看。']}
    state = _projection()
    monkeypatch.setattr(evaluator, 'project_narrative_state', lambda *args, **kwargs: deepcopy(state))
    kwargs = dict(actor_performance=candidate, player_input='先别交给我，我站在门口等。')
    if formal:
        kwargs['transition_outcome'] = outcome
    return engine, session, kwargs, state


def _build(engine, session, kwargs):
    messages, _ = evaluator._build_transition_judge_messages(engine, session, **kwargs)
    data = json.loads(messages[1].content.split('：', 1)[1])
    return messages, data, sum(evaluator.count_tokens(m.content) for m in messages)


def _without_quotes(state):
    result = deepcopy(state)
    count = 0
    for view in ('current', 'last_known'):
        for attributes in result[view].values():
            for attribute in attributes.values():
                if 'quote' in attribute:
                    attribute.pop('quote')
                    count += 1
    result['projection']['quotes_omitted'] += count
    return result


@pytest.mark.parametrize('formal', [False, True])
def test_budget_pressure_omits_attribute_quotes_only(monkeypatch, formal):
    engine, session, kwargs, state = _inputs(monkeypatch, formal)
    _, before, before_tokens = _build(engine, session, kwargs)
    limit = before_tokens - 20
    key = 'formal_judge_input_max_tokens' if formal else 'judge_input_max_tokens'
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES['economy'], key, limit)
    _, after, after_tokens = _build(engine, session, kwargs)
    expected = deepcopy(before)
    expected['narrative_state'] = _without_quotes(state)
    assert after_tokens <= limit < before_tokens
    assert after == expected
    assert after['narrative_state']['recent_events'] == state['recent_events']
    assert after['player_input'] == kwargs['player_input']
    assert state == _projection()


def test_budget_that_fits_keeps_all_quotes(monkeypatch):
    engine, session, kwargs, state = _inputs(monkeypatch)
    before, before_data, tokens = _build(engine, session, kwargs)
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES['economy'], 'judge_input_max_tokens', tokens)
    after, after_data, _ = _build(engine, session, kwargs)
    assert [m.content for m in after] == [m.content for m in before]
    assert after_data == before_data
    assert after_data['narrative_state'] == state


@pytest.mark.asyncio
async def test_still_over_budget_rejects_before_sdk_without_truncating_body(monkeypatch):
    engine, session, kwargs, state = _inputs(monkeypatch)
    monkeypatch.setattr(evaluator, 'project_narrative_state', lambda *args, **kw: _without_quotes(state))
    _, _, minimum = _build(engine, session, kwargs)
    monkeypatch.setattr(evaluator, 'project_narrative_state', lambda *args, **kw: deepcopy(state))
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES['economy'], 'judge_input_max_tokens', minimum - 10)
    _, packed, packed_tokens = _build(engine, session, kwargs)
    assert packed_tokens > minimum - 10
    assert packed['actor_performance'] == kwargs['actor_performance']['performance']
    assert packed['player_input'] == kwargs['player_input']
    assert packed['narrative_state']['recent_events'] == state['recent_events']
    clients = []
    async def forbidden_client(*args, **kw):
        clients.append(True)
        raise AssertionError('Input must fail before client construction')
    async def model_config(*args, **kw):
        return {'model': 'test', 'base_url': 'http://test.invalid'}
    monkeypatch.setattr(evaluator, '_model_config', model_config)
    monkeypatch.setattr(evaluator, 'create_chat_llm_async', forbidden_client)
    with pytest.raises(evaluator.NumericV2EvaluatorError, match='transition_review_budget_exceeded'):
        await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(
            engine=engine, session=session, message=kwargs['player_input'], actor_performance=kwargs['actor_performance'])
    assert clients == []
