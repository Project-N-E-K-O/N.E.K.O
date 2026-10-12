"""Exact latest invitation references retain authorization under Review capacity pressure."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_budget import NUMERIC_V2_ACTOR_BUDGET_PROFILES
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


@pytest.fixture(params=['forest', 'station'])
def case(request, monkeypatch):
    original_context = evaluator._current_scene_context
    monkeypatch.setattr(evaluator, '_current_scene_context', lambda s: original_context(s)[-1:])
    engine = _engine(ordinary=True)
    quote = ('我们去树林外休息，好吗？' if request.param == 'forest' else '我们去轨道站休息，好吗？') * 35
    session = engine.create_session(session_id='invitation_capacity', catgirl_binding=_binding(), opening_performance=_opening(), actor_budget_profile='economy')
    row = {'revision': 0, 'from_node_id': 'start', 'to_node_id': 'start',
           'performance_contract_version': 3, 'performance': quote, 'input_text': '接下来呢？',
           'transition_offered': True, 'transition_offer_presented': True}
    session = replace(session, node_turn_count=1, transition_offered=True, performance_history=(row,))
    outcome = engine.resolve_turn(session, TurnRequestV2('accept', 0, '好，走吧。'), (), transition_intent='accept')
    return engine, session, dict(actor_performance={'segments': [{'phase': 'source_response', 'performance': '那就走吧。'}], 'suggested_inputs': []}, player_input='好，走吧。', route_changed=True, transition_outcome=outcome, actor_fact_candidates=())


def build(case):
    engine, session, kwargs = case
    messages, _ = evaluator._build_transition_judge_messages(engine, session, **kwargs)
    return json.loads(messages[-1].content.split('：', 1)[1]), sum(evaluator.count_tokens(m.content) for m in messages)


def test_latest_invitation_reference_only_under_pressure(monkeypatch, case):
    before, tokens = build(case)
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES['economy'], 'formal_judge_input_max_tokens', tokens - 30)
    after, packed = build(case)
    expected = deepcopy(before)
    expected['transition_authorization']['pending_invitation']['content'] = 'scene_context[-1].content中的完整原文（dialogue说话人为active_catgirl）'
    assert after == expected
    assert packed <= tokens - 30


def test_exact_fit_retains_invitation_copy(monkeypatch, case):
    before, tokens = build(case)
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES['economy'], 'formal_judge_input_max_tokens', tokens)
    assert build(case) == (before, tokens)


@pytest.mark.parametrize('change', ['revision', 'text', 'missing_content'])
def test_nonidentical_latest_invitation_keeps_original(monkeypatch, case, change):
    original = evaluator._current_scene_context
    def context(session):
        rows = original(session)
        if change == 'revision': rows[-1]['revision'] += 1
        elif change == 'text': rows[-1]['content'][0]['text'] += '内容不同。'
        else: rows[-1].pop('content')
        return rows
    monkeypatch.setattr(evaluator, '_current_scene_context', context)
    before, tokens = build(case)
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES['economy'], 'formal_judge_input_max_tokens', tokens - 30)
    after, packed = build(case)
    assert after == before
    assert packed > tokens - 30
