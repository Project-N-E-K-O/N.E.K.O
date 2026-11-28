# -*- coding: utf-8 -*-
"""Soccer PassiveGuard as one decision question (owner decisions D4, D15, D24, D27)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from main_routers.game_router import passive_guard_decision as pgd
from main_routers.game_router import runtime as gr_runtime


def _answer(dist):
    return SimpleNamespace(value=max(dist, key=dist.get), probability=max(dist.values()), probabilities=dict(dist))


ROUTE = {
    "game_dialog_log": [
        {"id": "d1", "type": "assistant", "line": "I give up..."},
        {"id": "d2", "type": "game_event", "kind": "goal", "result_line": "Not again..."},
        {"id": "d3", "type": "user", "text": "come on, play for real!"},
        {"id": "d4", "type": "assistant", "line": "Okay! I'll play for real"},
    ],
}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stage", "prompt_type", "dist", "expected"),
    [
        ("user_recovery", "surrender", {"encourage": 0.6, "taunt": 0.4}, pgd.CANCEL),
        ("user_recovery", "surrender", {"encourage": 0.3, "ease_or_rule": 0.15, "taunt": 0.55}, pgd.OBSERVE),
        ("user_soothing", "rest", {"comfort": 0.3, "reply_better": 0.25, "send_rest": 0.45}, pgd.CANCEL),
        (7, "surrender", {"giving_up": 0.7, "neutral": 0.3}, pgd.RESCUE),
        (8, "surrender", {"giving_up": 0.7, "neutral": 0.3}, pgd.PREPARE),
        (8, "surrender", {"deliberate": 0.8, "giving_up": 0.2}, pgd.CANCEL),
        # fighting and one_slip add up on the rescue hint.
        (8, "surrender", {"fighting": 0.3, "one_slip": 0.3, "giving_up": 0.4}, pgd.RESCUE),
        (7, "rest", {"still_low": 0.9, "neutral": 0.1}, pgd.RESCUE),
        (8, "rest", {"still_low": 0.9, "neutral": 0.1}, pgd.PREPARE),
        (8, "rest", {"recovered": 0.5, "slightly_better": 0.3, "still_low": 0.2}, pgd.CANCEL),
        (8, "rest", {"slightly_better": 0.6, "still_low": 0.4}, pgd.OBSERVE),
    ],
)
def test_the_stage_maps_the_class_to_the_action(stage, prompt_type, dist, expected):
    data = {"stage": stage, "promptType": prompt_type}
    assert pgd.action_from_answer(_answer(dist), data) == expected


@pytest.mark.unit
def test_no_probabilities_means_no_action():
    assert pgd.action_from_answer(SimpleNamespace(value=None, probabilities={}), {"stage": 8}) is None


@pytest.mark.unit
def test_user_stage_question_carries_the_speech_and_her_reply():
    from config.prompts.prompts_soccer import SOCCER_PASSIVE_GUARD_DECISION_TEXT as T

    data = {"stage": "user_recovery", "promptType": "surrender", "userSpeech": "come on, play for real!"}
    state, qs = pgd.build_question(data, ROUTE, T)
    assert list(qs) == ["situation"]
    assert set(qs["situation"].criteria) == set(T["ur"])
    assert qs["situation"].instructions == T["ur_ins"]
    assert state.splitlines()[-2] == "Player speech just now: come on, play for real!"
    assert state.endswith(f"{T['reply']} Okay! I'll play for real")


@pytest.mark.unit
def test_stage_question_ends_with_nekos_latest_lines():
    from config.prompts.prompts_soccer import SOCCER_PASSIVE_GUARD_DECISION_TEXT as T

    state, qs = pgd.build_question({"stage": 8, "promptType": "rest"}, ROUTE, T)
    assert set(qs["situation"].criteria) == set(T["r"])
    tail = state.splitlines()[-4:]
    assert tail == [T["latest"], "- I give up...", "- Not again...", "- Okay! I'll play for real"]
    empty_state, _ = pgd.build_question({"stage": 7}, None, T)
    assert empty_state.splitlines()[-1] == "- (none)"


def _routes(monkeypatch, *, native: bool, rehost: bool):
    from utils import decision_client as dc
    from utils.config_manager import get_config_manager

    cm = get_config_manager()

    async def _cfg(tier):
        return {"model": "d", "decision_protocol": "systemone" if native else "chat_fallback",
                "decision_url": "https://ws.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone"}

    monkeypatch.setattr(cm, "aget_model_api_config", _cfg)
    seen: dict = {}

    async def _ask(site, *, state, questions, **kw):
        seen.update(site=site, state=state, questions=questions, kw=kw)
        return seen.get("answers")

    monkeypatch.setattr(dc, "ask_site_questions", _ask)
    chat = {"model": "c", "base_url": "https://ark.cn-beijing.volces.com/api/v3" if rehost else "https://x.example/v1",
            "api_key": "k"}
    return seen, chat


@pytest.mark.unit
def test_native_route_asks_in_english_whatever_the_ui(monkeypatch):
    from config.prompts.prompts_soccer import SOCCER_PASSIVE_GUARD_DECISION_TEXT as T

    seen, chat = _routes(monkeypatch, native=True, rehost=False)
    seen["answers"] = {"situation": _answer({"giving_up": 0.9, "neutral": 0.1})}
    out = asyncio.run(pgd.decide_passive_guard({"stage": 8, "promptType": "surrender"}, ROUTE,
                                               chat_config=chat, language="zh-CN"))
    assert seen["site"] == "soccer_guard"
    assert seen["questions"]["situation"].instructions == T["s_ins"]
    assert seen["kw"]["timeout"] == pgd.DECISION_TIMEOUT_SECONDS
    assert out["recommendedAction"] == pgd.PREPARE and out["classification"] == "giving_up"


@pytest.mark.unit
@pytest.mark.parametrize(("language", "zh"), [("zh-CN", True), ("zh-TW", True), ("en", False), ("ja", False)])
def test_rehost_on_a_chinese_ui_uses_the_evaluated_chinese_wording(monkeypatch, language, zh):
    from config.prompts.prompts_soccer import SOCCER_PASSIVE_GUARD_DECISION_TEXT as EN
    from config.prompts.prompts_soccer import SOCCER_PASSIVE_GUARD_REHOST_TEXT_ZH as ZH

    seen, chat = _routes(monkeypatch, native=False, rehost=True)
    seen["answers"] = {"situation": _answer({"neutral": 1.0})}
    asyncio.run(pgd.decide_passive_guard({"stage": 7}, ROUTE, chat_config=chat, language=language))
    assert seen["questions"]["situation"].instructions == (ZH if zh else EN)["s_ins"]


@pytest.mark.unit
def test_chat_route_and_misses_return_none(monkeypatch):
    seen, chat = _routes(monkeypatch, native=False, rehost=False)
    assert asyncio.run(pgd.decide_passive_guard({"stage": 7}, ROUTE, chat_config=chat, language="en")) is None
    assert "site" not in seen  # class C never asks
    seen, chat = _routes(monkeypatch, native=True, rehost=False)
    seen["answers"] = None
    assert asyncio.run(pgd.decide_passive_guard({"stage": 7}, ROUTE, chat_config=chat, language="en")) is None


@pytest.mark.unit
def test_runtime_returns_the_decision_and_skips_the_chat_prompt(monkeypatch):
    from utils import llm_client as llm_client_module

    monkeypatch.setattr(gr_runtime, "_find_game_route_state_for_session", lambda *a, **k: ROUTE)
    monkeypatch.setattr(gr_runtime, "_get_game_route_recognition_llm_info",
                        lambda *a, **k: {"model": "m", "base_url": "http://localhost", "api_key": "k",
                                         "user_language_full": "en"})
    monkeypatch.setattr(gr_runtime, "_resolve_game_prompt_locale", lambda *a, **k: "en")

    async def _decided(data, route_state, *, chat_config, language):
        return {"recommendedAction": pgd.PREPARE, "classification": "giving_up", "confidence": 0.9}

    async def _no_chat(*a, **k):
        raise AssertionError("the chat prompt must not run")

    monkeypatch.setattr(pgd, "decide_passive_guard", _decided)
    monkeypatch.setattr(llm_client_module, "create_chat_llm_async", _no_chat)
    out = asyncio.run(gr_runtime._run_soccer_passive_guard_ai({"session_id": "s", "stage": 8}, "Lan"))
    assert out["recommendedAction"] == pgd.PREPARE and out["exitPromptType"] == "surrender"

    # Stage 7 cannot open the window: the normalizer keeps the old rule.
    out7 = asyncio.run(gr_runtime._run_soccer_passive_guard_ai({"session_id": "s", "stage": 7}, "Lan"))
    assert out7["recommendedAction"] == pgd.OBSERVE


@pytest.mark.unit
def test_a_bare_choice_without_probabilities_gives_no_action():
    bare = SimpleNamespace(value="giving_up", probability=None, probabilities={})
    assert pgd.action_from_answer(bare, {"stage": 8}) is None


@pytest.mark.unit
def test_a_teaching_game_never_opens_the_exit_window():
    data = {"stage": 8, "promptType": "surrender", "preGameContext": {"gameStance": "teaching"}}
    assert pgd.action_from_answer(_answer({"giving_up": 1.0}), data) == pgd.OBSERVE
    # An ordinary game at stage 8 still can.
    assert pgd.action_from_answer(_answer({"giving_up": 1.0}), {"stage": 8}) == pgd.PREPARE


@pytest.mark.unit
def test_her_reply_counts_only_for_the_same_whole_speech():
    head = "x" * 240
    route = {"game_dialog_log": [
        {"id": "d1", "type": "user", "text": head + " play for real"},
        {"id": "d2", "type": "assistant", "line": "Okay! I'll play for real"},
    ]}
    assert pgd._reply_to_speech({"userSpeech": head + " play for real"}, route) == "Okay! I'll play for real"
    # Same first 240 characters, different speech: not her reply to it.
    assert pgd._reply_to_speech({"userSpeech": head + " just give up"}, route) is None



@pytest.mark.unit
def test_a_route_keeping_a_live_task_is_read_without_copying_it():
    from config.prompts.prompts_soccer import SOCCER_PASSIVE_GUARD_DECISION_TEXT as T

    async def _run():
        task = asyncio.ensure_future(asyncio.sleep(0))
        await task
        route = dict(ROUTE, _game_context_organizer_task=task)
        return pgd.build_question({"stage": 8}, route, T)

    state, qs = asyncio.run(_run())
    assert "Not again..." in state and "situation" in qs


@pytest.mark.unit
def test_a_teaching_game_kept_on_the_route_never_opens_the_exit_window():
    route = {"preGameContext": {"gameStance": "teaching"}}
    assert pgd.action_from_answer(_answer({"giving_up": 1.0}), {"stage": 8}, route) == pgd.OBSERVE
    # The request's own context comes first.
    own = {"stage": 8, "preGameContext": {"gameStance": "normal"}}
    assert pgd.action_from_answer(_answer({"giving_up": 1.0}), own, route) == pgd.PREPARE


@pytest.mark.unit
def test_all_zero_probabilities_mean_the_chat_prompt():
    zero = SimpleNamespace(value="giving_up", probability=0.0, probabilities={"giving_up": 0.0})
    assert pgd.action_from_answer(zero, {"stage": 8}) is None
    assert pgd.action_from_answer(zero, {"stage": "user_recovery"}) is None


@pytest.mark.unit
def test_two_long_speeches_differing_only_at_the_end_are_different():
    head = "x" * 200_000
    route = {"game_dialog_log": [
        {"id": "d1", "type": "user", "text": head + " play for real"},
        {"id": "d2", "type": "assistant", "line": "Okay!"},
    ]}
    assert pgd._reply_to_speech({"userSpeech": head + " play for real"}, route) == "Okay!"
    assert pgd._reply_to_speech({"userSpeech": head + " just give up"}, route) is None


@pytest.mark.unit
def test_probability_only_on_an_unknown_option_means_the_chat_prompt():
    mixed = SimpleNamespace(value="unexpected", probability=1.0, probabilities={"giving_up": 0.0, "unexpected": 1.0})
    assert pgd.action_from_answer(mixed, {"stage": 8}) is None
    assert pgd.action_from_answer(mixed, {"stage": 8, "promptType": "rest"}) is None

