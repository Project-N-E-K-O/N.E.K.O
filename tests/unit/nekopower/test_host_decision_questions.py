# -*- coding: utf-8 -*-
"""NekoPower's small-model questions on decision models (owner decisions D16, D19, D26-D28)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from nekopower.host import decision as dm
from nekopower.host.decision import LexicalDecisionModel

pytestmark = pytest.mark.unit


def _answer(probs: dict[str, float]) -> Any:
    return SimpleNamespace(value=max(probs, key=probs.get) if probs else None, probabilities=dict(probs))


class _LLM:
    """The chat tier answers ``chat``; ``decide`` answers ``decided`` (None: the chat prompt is asked)."""

    def __init__(self, *, chat: str = "", decided: Any = None) -> None:
        self.chat = chat
        self.decided = decided
        self.completes: list[tuple[str, list[dict[str, Any]]]] = []
        self.decides: list[dict[str, Any]] = []

    async def complete(self, tier: str, messages: list[dict[str, Any]]) -> str:
        self.completes.append((tier, messages))
        return self.chat

    async def decide(self, tier: str, site: str, *, state: str, questions: dict[str, Any], rehost: bool) -> Any:
        self.decides.append({"tier": tier, "site": site, "state": state, "questions": questions, "rehost": rehost})
        if isinstance(self.decided, Exception):
            raise self.decided
        return self.decided


# ── side question (q2, D19) ───────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(("probs", "expected"), [
    ({"look": 0.6, "change": 0.4}, True),
    ({"look": 0.45, "change": 0.55}, False),
    ({"change": 0.9, "run_or_send": 0.1}, False),  # look not among them: work
])
async def test_side_question_reads_p_look(probs, expected):
    llm = _LLM(chat='{"side_question": true}', decided={"kind": _answer(probs)})
    assert await LexicalDecisionModel(llm).side_question("where is the export code?", "where is it?") is expected
    assert llm.completes == []
    asked = llm.decides[0]
    assert asked["site"] == "side_question" and asked["rehost"] is True
    assert set(asked["questions"]["kind"].criteria) == {"look", "change", "run_or_send", "decide"}
    assert asked["state"].startswith("Request: where is the export code?")


@pytest.mark.asyncio
async def test_side_question_offers_pending_only_while_the_session_waits_for_an_answer():
    llm = _LLM(decided={"kind": _answer({"pending": 0.7, "look": 0.3})})
    assert await LexicalDecisionModel(llm).side_question("use the second one", "", waiting_for="Which theme?") is False
    assert "pending" in llm.decides[0]["questions"]["kind"].criteria


@pytest.mark.asyncio
@pytest.mark.parametrize("decided", [None, {"kind": _answer({})}, RuntimeError("boom")])
async def test_side_question_falls_back_to_the_chat_prompt(decided):
    llm = _LLM(chat='{"side_question": false}', decided=decided)
    assert await LexicalDecisionModel(llm).side_question("what does it do?", "") is False
    assert len(llm.completes) == 1


@pytest.mark.asyncio
async def test_side_question_without_decide_asks_the_chat_prompt_only():
    class _ChatOnly:
        async def complete(self, tier, messages):
            return '{"side_question": true}'

    assert await LexicalDecisionModel(_ChatOnly()).side_question("what does it do?", "") is True


# ── coarse screen (q_onec, D16) ───────────────────────────────────────

PLUGINS = [
    {"id": "weather", "short_description": "Looks up the weather forecast for a city"},
    {"id": "music", "short_description": "Plays songs and playlists"},
    {"id": "notes", "short_description": "Keeps notes"},
]


@pytest.mark.asyncio
async def test_coarse_screen_keeps_plugins_at_or_above_the_threshold_in_catalog_order():
    llm = _LLM(chat='["notes"]', decided={"pick": _answer({"p1": 0.6, "p0": 0.05, "p2": 0.04, "none": 0.31})})
    got = await LexicalDecisionModel(llm)._coarse_screen("play some jazz", PLUGINS, "en")
    assert got == (["weather", "music"], 1)
    assert llm.completes == []
    asked = llm.decides[0]
    assert asked["site"] == "coarse_screen" and asked["rehost"] is False
    criteria = asked["questions"]["pick"].criteria
    assert criteria["p0"].startswith("[weather] ") and "none" in criteria
    assert asked["state"] == "User request:\nplay some jazz"


@pytest.mark.asyncio
async def test_coarse_screen_none_only_keeps_nothing_and_a_miss_asks_the_chat():
    llm = _LLM(chat='["notes"]', decided={"pick": _answer({"none": 1.0})})
    assert await LexicalDecisionModel(llm)._coarse_screen("hello", PLUGINS, "en") == ([], 1)
    # Not sent (the vendor keeps its chat): only the chat request.
    llm = _LLM(chat='["notes"]', decided=None)
    assert await LexicalDecisionModel(llm)._coarse_screen("hello", PLUGINS, "en") == (["notes"], 1)
    # Sent and missed, then the chat: both requests are counted.
    llm = _LLM(chat='["notes"]', decided={})
    assert await LexicalDecisionModel(llm)._coarse_screen("hello", PLUGINS, "en") == (["notes"], 2)


# ── where a commission belongs (q1, D19) ─────────────────────────────


def _belongs_messages(n: int) -> list[dict[str, Any]]:
    from config.prompts.prompts_nekopower_brain import ORCHESTRATOR_PROMPT, build_orchestrator_request

    lines = [f"[project] P{k}: does things\n2. not a candidate line" if k == 1 else f"[agent] A{k}" for k in range(1, n + 1)]
    return [
        {"role": "system", "content": ORCHESTRATOR_PROMPT},
        {"role": "user", "content": build_orchestrator_request(task="the next weekly report", user_said="", candidates=lines)},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("probs", "expected"), [
    ({"c2_follow_up": 0.5, "c2_recurrence": 0.1, "none": 0.4}, {"goes_to": 2, "relation": "follow_up"}),
    ({"c1_follow_up": 0.2, "c1_recurrence": 0.3, "none": 0.1}, {"goes_to": 1, "relation": "recurrence"}),
    ({"c1_follow_up": 0.25, "none": 0.75}, {"goes_to": 0, "relation": "new"}),  # under the 0.3 bar
    ({"c1_follow_up": 0.35, "none": 0.4}, {"goes_to": 0, "relation": "new"}),  # not above none
])
async def test_belongs_is_answered_in_the_orchestrators_json(probs, expected):
    from nekopower.host import orchestrator as orch

    llm = _LLM(chat='{"goes_to": 3, "relation": "follow_up"}', decided={"goes": _answer(probs)})
    raw = await LexicalDecisionModel(llm).belongs(_belongs_messages(3), candidates=3)
    assert json.loads(raw) == expected
    assert llm.completes == []
    asked = llm.decides[0]
    assert asked["site"] == "belongs" and asked["rehost"] is False
    assert set(asked["questions"]["goes"].criteria) == {
        "none", "c1_follow_up", "c1_recurrence", "c2_follow_up", "c2_recurrence", "c3_follow_up", "c3_recurrence",
    }
    assert "Reply with JSON" not in asked["questions"]["goes"].instructions
    # Downstream reads it as it reads the chat's answer.
    keys = ["k1", "k2", "k3"]
    read = orch.read_answer(raw, keys)
    assert read == ((keys[expected["goes_to"] - 1] if expected["goes_to"] else None), expected["relation"])


@pytest.mark.asyncio
async def test_belongs_miss_asks_the_chat_prompt():
    llm = _LLM(chat='{"goes_to": 1, "relation": "follow_up"}', decided={"goes": _answer({})})
    assert await LexicalDecisionModel(llm).belongs(_belongs_messages(2)) == '{"goes_to": 1, "relation": "follow_up"}'
    assert len(llm.completes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("decided", "calls", "chat_asked"), [
    ({"goes": _answer({"c1_follow_up": 0.9, "none": 0.1})}, 1, False),  # the decision answered
    ({"goes": _answer({})}, 2, True),  # sent, unread, then the chat
    (None, 1, True),  # not sent: the chat only
])
async def test_belongs_counted_reports_the_requests_made(decided, calls, chat_asked):
    llm = _LLM(chat='{"goes_to": 1, "relation": "follow_up"}', decided=decided)
    raw, got_calls = await LexicalDecisionModel(llm).belongs_counted(_belongs_messages(2), candidates=2)
    assert json.loads(raw)["goes_to"] == 1
    assert got_calls == calls and bool(llm.completes) is chat_asked


def test_candidates_are_counted_from_the_numbered_block_only():
    assert dm._candidate_count(_belongs_messages(3)[1]["content"]) == 3
    assert dm._candidate_count("REQUEST: x\nno candidates") == 0


# ── turn check, chat first then a second look (D28) ──────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(("chat", "probs", "kind", "calls", "task"), [
    # The second look says nothing to act on: nothing is dispatched.
    ({"verdict": "promised", "task": "play jazz"}, {"chat": 0.6, "will_do": 0.4}, "none", 2, ""),
    # It agrees: its verdict with the task the chat wrote.
    ({"verdict": "promised", "task": "play jazz"}, {"will_do": 0.7, "chat": 0.3}, "promised", 2, "play jazz"),
    # It reads it as already done: its kind wins.
    ({"verdict": "promised", "task": "set the alarm"}, {"done_action": 0.8, "will_do": 0.2}, "claimed_done", 2, "set the alarm"),
    # none weighs 0.75: 0.55 none vs 0.45 will_do -> 0.4125 < 0.45.
    ({"verdict": "promised", "task": "x"}, {"chat": 0.55, "will_do": 0.45}, "promised", 2, "x"),
])
async def test_turn_check_second_look(chat, probs, kind, calls, task):
    llm = _LLM(chat=json.dumps(chat), decided={"verdict": _answer(probs)})
    verdict = await LexicalDecisionModel(llm).judge_turn("play some jazz", "Playing it now!", "en")
    assert (verdict.kind, verdict.model_calls, verdict.task) == (kind, calls, task)
    asked = llm.decides[0]
    assert asked["site"] == "turn_check" and asked["rehost"] is True
    assert asked["state"] == "USER: play some jazz\nREPLY: Playing it now!"
    assert len(asked["questions"]["verdict"].criteria) == 8


@pytest.mark.asyncio
async def test_turn_check_asks_no_second_look_when_the_chat_finds_nothing():
    llm = _LLM(chat='{"verdict": "none", "task": ""}', decided={"verdict": _answer({"will_do": 1.0})})
    verdict = await LexicalDecisionModel(llm).judge_turn("hi", "hello!", "en")
    assert verdict.kind == "none" and verdict.model_calls == 1 and llm.decides == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("decided", "calls"), [
    (None, 1),  # not sent (today's chat route): one request
    ({}, 2),  # sent and missed (timeout, error): both requests are counted
    ({"verdict": _answer({})}, 2),  # sent, no probabilities to read
    (RuntimeError("down"), 2),
])
async def test_turn_check_keeps_the_chat_verdict_when_the_second_look_misses(decided, calls):
    llm = _LLM(chat='{"verdict": "claimed_done", "task": "turned off the lights"}', decided=decided)
    verdict = await LexicalDecisionModel(llm).judge_turn("lights off", "Done, they're off.", "en")
    assert (verdict.kind, verdict.model_calls, verdict.task) == ("claimed_done", calls, "turned off the lights")


# ── ConfiguredDecisionLLM.decide ──────────────────────────────────────


@pytest.mark.asyncio
async def test_configured_decide_routes_with_the_tiers_chat_slot(monkeypatch):
    import utils.config_manager as cmod
    import utils.decision_client as dc

    asked_slots: list[str] = []

    class _Manager:
        def get_model_api_config(self, slot):
            asked_slots.append(slot)
            return {"model": slot, "base_url": "https://x.example/v1"}

    monkeypatch.setattr(cmod, "get_config_manager", lambda migrate=True: _Manager())
    seen: dict[str, Any] = {}

    async def _ask(site, **kw):
        seen.update(site=site, **kw)
        return {"q": "answer"}

    monkeypatch.setattr(dc, "ask_site_questions", _ask)
    monkeypatch.setattr(dc, "site_question_would_send", lambda site, **kw: True)
    got = await dm.ConfiguredDecisionLLM().decide(dm.TIER_TURN_CHECK, "turn_check", state="s", questions={"q": 1}, rehost=True)
    assert got == {"q": "answer"}
    assert asked_slots == ["emotion", "decision"]
    assert seen["chat_config"]["model"] == "emotion" and seen["decision_config"]["model"] == "decision"
    assert seen["rehost"] is True and seen["timeout"] > 0

    # Sent but missed: empty, so the caller counts the request.
    async def _miss(site, **kw):
        return None

    monkeypatch.setattr(dc, "ask_site_questions", _miss)
    assert await dm.ConfiguredDecisionLLM().decide(dm.TIER_TURN_CHECK, "turn_check", state="s", questions={}, rehost=True) == {}
    # Today's chat route, or the native endpoint in its cooldown: nothing is sent at all.
    monkeypatch.setattr(dc, "site_question_would_send", lambda site, **kw: False)
    seen.clear()
    assert await dm.ConfiguredDecisionLLM().decide(dm.TIER_TURN_CHECK, "turn_check", state="s", questions={}, rehost=True) is None
    assert seen == {}


@pytest.mark.asyncio
async def test_belongs_uses_the_count_it_is_given_over_the_text():
    # A candidate line may hold a numbered line of its own; the count passed in wins.
    llm = _LLM(decided={"goes": _answer({"c3_follow_up": 0.9, "none": 0.1})})
    raw = await LexicalDecisionModel(llm).belongs(_belongs_messages(2), candidates=2)
    assert set(llm.decides[0]["questions"]["goes"].criteria) == {
        "none", "c1_follow_up", "c1_recurrence", "c2_follow_up", "c2_recurrence",
    }
    # c3 is not an option: nothing reaches the bar, so the request is new.
    assert json.loads(raw) == {"goes_to": 0, "relation": "new"}


def test_families_asks_a_judge_of_the_older_form_with_the_messages_only():
    from nekopower.host import families

    async def old(messages):
        return None

    async def new(messages, candidates=None):
        return None

    async def any_kw(messages, **kw):
        return None

    async def named_alike(candidates):
        return None

    assert not families._takes_candidates(old)
    # One parameter that is only named alike: the call with both does not bind.
    assert not families._takes_candidates(named_alike)
    assert families._takes_candidates(new) and families._takes_candidates(any_kw)
    assert families._takes_candidates(LexicalDecisionModel(_LLM()).belongs_counted)
