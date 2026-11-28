# -*- coding: utf-8 -*-
"""Negative-keyword target check as one decision question (owner decisions D2, D16, D27)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.memory_server import signal_extraction as se

OBSERVATIONS = [
    {"id": "reflection.r1", "raw_id": "r1", "target_type": "reflection", "text": "User loves talking about exams"},
    {"id": "persona.p2", "raw_id": "p2", "target_type": "persona", "text": "User works at a bank", "entity_key": "u"},
]


@pytest.fixture
def hook(monkeypatch):
    """The real hook with a stub observation pool, chat call and dispatcher."""
    fact_store = SimpleNamespace(
        _aload_signal_targets=AsyncMock(return_value=list(OBSERVATIONS)),
        _allm_call_with_retries=AsyncMock(return_value={"targets": [{"target_id": "p2"}]}),
    )
    monkeypatch.setattr(se.runtime, "fact_store", fact_store, raising=False)
    monkeypatch.setattr(se.runtime, "reflection_engine", None, raising=False)
    monkeypatch.setattr(se.runtime, "persona_manager", None, raising=False)
    monkeypatch.setattr(se, "scan_negative_keywords", lambda text, lang: True)
    dispatched: list = []

    async def _dispatch(name, signals, *, source):
        dispatched.append(signals)

    monkeypatch.setattr(se, "_adispatch_evidence_signals", _dispatch)
    asked: dict = {}

    async def _ask(site, *, state, questions, **kw):
        asked.update(site=site, state=state, questions=questions, kw=kw)
        return asked.get("answers")

    import utils.decision_client as dc

    monkeypatch.setattr(dc, "ask_site_questions", _ask)
    return SimpleNamespace(fact_store=fact_store, dispatched=dispatched, asked=asked)


def _choice(dist):
    return SimpleNamespace(value=max(dist, key=dist.get), probabilities=dict(dist))


@pytest.mark.asyncio
@pytest.mark.unit
async def test_decision_target_above_threshold_is_disputed_without_the_chat_call(hook):
    hook.asked["answers"] = {"target": _choice({"o1": 0.85, "none": 0.15})}
    await se._amaybe_trigger_negative_keyword_hook("Lan", ["stop mentioning exams"], "en")
    assert hook.asked["site"] == "negative_keywords"
    assert hook.asked["kw"]["rehost"] is False
    q = hook.asked["questions"]["target"]
    assert list(q.criteria) == ["none", "o1", "o2"]
    assert q.criteria["o1"].startswith("observation [reflection.r1]: ")
    assert hook.asked["state"].startswith("User messages:\nstop mentioning exams\n\nObservations the system remembers:\n")
    assert "[persona.p2] User works at a bank" in hook.asked["state"]
    hook.fact_store._allm_call_with_retries.assert_not_awaited()
    assert hook.dispatched == [[{"signal": "negates", "target_type": "reflection", "target_id": "r1", "entity_key": None}]]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_decision_below_threshold_or_none_disputes_nothing(hook):
    hook.asked["answers"] = {"target": _choice({"o1": 0.6, "o2": 0.3, "none": 0.1})}
    await se._amaybe_trigger_negative_keyword_hook("Lan", ["so annoying"], "en")
    hook.asked["answers"] = {"target": _choice({"none": 0.9, "o1": 0.1})}
    await se._amaybe_trigger_negative_keyword_hook("Lan", ["so annoying"], "en")
    hook.fact_store._allm_call_with_retries.assert_not_awaited()
    assert hook.dispatched == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_decision_miss_falls_back_to_the_chat_prompt(hook):
    hook.asked["answers"] = None
    await se._amaybe_trigger_negative_keyword_hook("Lan", ["stop mentioning work"], "en")
    hook.fact_store._allm_call_with_retries.assert_awaited_once()
    assert hook.dispatched[0][0]["target_id"] == "p2"


@pytest.mark.unit
def test_decision_question_fits_the_state_budget(monkeypatch):
    import config

    monkeypatch.setattr(config, "DECISION_STATE_MAX_TOKENS", 300)
    many = [
        {"id": f"reflection.r{i}", "raw_id": f"r{i}", "target_type": "reflection", "text": "word " * 60}
        for i in range(30)
    ]
    state, questions, by_key = se._negative_target_decision_question(["drop it"], many)
    # Only the observations shown in the state become options.
    assert 0 < len(by_key) < 30
    for key, obs in by_key.items():
        assert f"[{obs['id']}]" in state
        assert key in questions["target"].criteria


@pytest.mark.asyncio
@pytest.mark.unit
async def test_a_choice_without_probabilities_falls_back_to_the_chat_prompt(hook):
    # A bare choice cannot pass the 0.7 threshold: no disputation from it.
    hook.asked["answers"] = {"target": SimpleNamespace(value="o1", probabilities={})}
    await se._amaybe_trigger_negative_keyword_hook("Lan", ["stop mentioning work"], "en")
    hook.fact_store._allm_call_with_retries.assert_awaited_once()
    assert hook.dispatched[0][0]["target_id"] == "p2"
