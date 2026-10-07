"""Prompt packing trims long scenes in O(log N) tokenizations and off the event loop."""

from contextlib import suppress
from dataclasses import replace
import json
import random
import threading
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_budget import NUMERIC_V2_ACTOR_BUDGET_PROFILES
from tests.unit.test_theater_numeric_v2_review_capacity import _fixture


def _varied_history_session(session, turns, rng):
    longest = rng.choice([3, 10, 30])
    history = tuple({
        'revision': index,
        'from_node_id': 'start',
        'to_node_id': 'start',
        'performance_contract_version': 3,
        'input_text': rng.choice(['我听见了。', '好。', '把灯拿过来吧，我们一起看看。', '"quoted" input']),
        'performance': '（' + '她整理好手边的工具。' * rng.randint(1, longest) + '）'
        + rng.choice(['', '“嗯。”', '“先别急，我看看。”']),
    } for index in range(1, turns + 1))
    return replace(session, revision=turns, performance_history=history)


def _linear_cut(limit, fits):
    """Reference: the original scan tried cuts 0, 1, 2, ... and stopped at ``limit``."""
    for cut in range(limit):
        if fits(cut):
            return cut
    return limit


def _build_all(engine, session, outcome, candidate):
    diagnostics = {}
    evaluator_messages = evaluator._build_messages(engine, session, '好。', diagnostics=diagnostics)
    judge_messages, evidence = evaluator._build_transition_judge_messages(
        engine, session, actor_performance=candidate, player_input='好。', transition_outcome=outcome,
    )
    return (
        [item.content for item in evaluator_messages],
        diagnostics,
        [item.content for item in judge_messages],
        evidence,
    )


@pytest.mark.parametrize('seed', range(24))
def test_binary_search_packing_matches_linear_scan(monkeypatch, seed):
    rng = random.Random(seed)
    engine, session, outcome, candidate = _fixture(rng.randint(10, 300))
    session = _varied_history_session(session, rng.randint(1, 80), rng)
    profile = NUMERIC_V2_ACTOR_BUDGET_PROFILES['economy']
    monkeypatch.setitem(profile, 'evaluator_input_max_tokens', rng.randint(1500, 12000))
    monkeypatch.setitem(profile, 'formal_judge_input_max_tokens', rng.randint(1500, 12000))
    monkeypatch.setitem(profile, 'history_max_turns', rng.randint(1, 30))

    fast = _build_all(engine, session, outcome, candidate)
    monkeypatch.setattr(evaluator, '_smallest_fitting_cut', _linear_cut)
    assert _build_all(engine, session, outcome, candidate) == fast


def test_long_scene_packing_tokenizes_logarithmically(monkeypatch):
    engine, session, outcome, candidate = _fixture(50)
    session = _varied_history_session(session, 240, random.Random(7))
    calls = []
    real_count = evaluator.count_tokens

    def counting(text, *args, **kwargs):
        calls.append(1)
        return real_count(text, *args, **kwargs)

    monkeypatch.setattr(evaluator, 'count_tokens', counting)
    diagnostics = {}
    evaluator._build_messages(engine, session, '好。', diagnostics=diagnostics)
    # Most of the scene must be trimmed for the old per-record loop to be quadratic.
    assert len(diagnostics['recent_dropped_revisions']) > 150
    assert len(calls) < 40

    calls.clear()
    judge_messages, _ = evaluator._build_transition_judge_messages(
        engine, session, actor_performance=candidate, player_input='好。', transition_outcome=outcome,
    )
    payload = json.loads(judge_messages[1].content.split('：', 1)[1])
    full_index = 240 + 1 - NUMERIC_V2_ACTOR_BUDGET_PROFILES['economy']['history_max_turns']
    assert len(payload['scene_fact_index']) < full_index - 100
    assert len(calls) < 40


@pytest.mark.asyncio
async def test_prompt_packing_runs_off_event_loop(monkeypatch):
    engine, session, outcome, candidate = _fixture(50)
    session = _varied_history_session(session, 40, random.Random(3))
    loop_thread = threading.get_ident()
    packing_threads = []
    token_threads = []

    def spy(real):
        def wrapper(*args, **kwargs):
            packing_threads.append(threading.get_ident())
            return real(*args, **kwargs)
        return wrapper

    real_count = evaluator.count_tokens

    def tracked_count(text, *args, **kwargs):
        token_threads.append(threading.get_ident())
        return real_count(text, *args, **kwargs)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages):
            return SimpleNamespace(content=json.dumps(dict(
                offer_present=False, valid=False, body_violations=[],
                unsafe_suggestion_indexes=[], delivery_matches_route=True,
                failure_reason='')))

    async def config(_):
        return dict(model='test', base_url='http://test.invalid')

    async def factory(*args, **kwargs):
        return Client()

    monkeypatch.setattr(evaluator, '_build_messages', spy(evaluator._build_messages))
    monkeypatch.setattr(evaluator, '_build_transition_judge_messages', spy(evaluator._build_transition_judge_messages))
    monkeypatch.setattr(evaluator, 'count_tokens', tracked_count)
    monkeypatch.setattr(evaluator, '_model_config', config)
    monkeypatch.setattr(evaluator, 'create_chat_llm_async', factory)

    worker = evaluator.NumericV2MetricEvaluator(object())
    with suppress(evaluator.NumericV2EvaluatorError):
        await worker.evaluate(engine=engine, session=session, message='好。')
    await worker.validate_transition_offer(
        engine=engine, session=session, message='好。',
        actor_performance=candidate, transition_outcome=outcome)
    assert len(packing_threads) == 2
    assert loop_thread not in packing_threads
    # The whole-message budget checks after packing tokenise off the loop too.
    # Whole-prompt budget checks tokenise off the loop too; only a short
    # output-field check may remain on it.
    assert token_threads.count(loop_thread) <= 1 < len(token_threads)
