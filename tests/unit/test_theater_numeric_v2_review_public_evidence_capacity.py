from copy import deepcopy
from dataclasses import replace
import json
import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_budget import NUMERIC_V2_ACTOR_BUDGET_PROFILES
from tests.unit.test_theater_numeric_v2_review_capacity import _fixture


@pytest.fixture(params=['forest', 'station'])
def packed_case(monkeypatch, request):
    engine, session, _, _ = _fixture(20)
    location = '树林外侧的空地' if request.param == 'forest' else '信标室外侧的安全通道'
    quote = f'这里的事情已经完成。如果你愿意，我们就去{location}休息。' * 30
    row = {'revision': 1, 'from_node_id': 'start', 'to_node_id': 'start',
           'performance_contract_version': 3, 'input_text': '接下来怎么办？',
           'performance': quote}
    session = replace(session, revision=1, performance_history=(row,))
    original_context = evaluator._current_scene_context
    monkeypatch.setattr(evaluator, '_current_scene_context', lambda s: original_context(s)[-1:])
    monkeypatch.setattr(evaluator, 'history_evidence', lambda *a, **kw: [
        {'current_visit': True, 'source': 'performance', 'text': quote}])
    kwargs = dict(actor_performance={'performance': '我听到了。', 'suggested_inputs': []},
                  player_input='好，我们按刚才的安排继续。', check_missed_initiation=True)
    return engine, session, kwargs, quote


def build(case):
    engine, session, kwargs, _ = case
    messages, evidence = evaluator._build_transition_judge_messages(engine, session, **kwargs)
    data = json.loads(messages[1].content.split('：', 1)[1])
    return messages, data, evidence, sum(evaluator.count_tokens(m.content) for m in messages)


def test_duplicate_latest_public_evidence_uses_reference_only_under_pressure(monkeypatch, packed_case):
    messages, before, evidence, tokens = build(packed_case)
    assert evidence == (packed_case[3],)
    limit = tokens - 30
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES['economy'], 'formal_judge_input_max_tokens', limit)
    _, after, after_evidence, after_tokens = build(packed_case)
    expected = deepcopy(before)
    expected['missed_initiation_check']['public_destination_evidence'] = 'scene_context[-1].content中的dialogue/narration原文'
    assert after == expected
    assert after_tokens <= limit < tokens
    assert after_evidence == evidence
    assert after['scene_context'] == before['scene_context']
    assert after['actor_performance'] == before['actor_performance']
    assert after['player_input'] == before['player_input']


def test_exact_fit_keeps_original_payload(monkeypatch, packed_case):
    before, _, evidence, tokens = build(packed_case)
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES['economy'], 'formal_judge_input_max_tokens', tokens)
    after, _, after_evidence, _ = build(packed_case)
    assert [m.content for m in after] == [m.content for m in before]
    assert after_evidence == evidence


def test_other_public_content_prevents_reference(monkeypatch, packed_case):
    engine, session, kwargs, quote = packed_case
    record = {**session.performance_history[0], 'scene_narration': '门边还有一道刚亮起的灯。'}
    case = engine, replace(session, performance_history=(record,)), kwargs, quote
    _, before, evidence, tokens = build(case)
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES['economy'], 'formal_judge_input_max_tokens', tokens - 30)
    _, after, after_evidence, after_tokens = build(case)
    assert after == before
    assert after_tokens > tokens - 30
    assert after_evidence == evidence


def test_still_over_budget_keeps_complete_original_evidence(monkeypatch, packed_case):
    _, before, evidence, _ = build(packed_case)
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES['economy'], 'formal_judge_input_max_tokens', 1)
    _, after, after_evidence, tokens = build(packed_case)
    assert tokens > 1
    assert after_evidence == evidence
    assert after['scene_context'] == before['scene_context']
    assert after['player_input'] == before['player_input']
    assert after['actor_performance'] == before['actor_performance']
