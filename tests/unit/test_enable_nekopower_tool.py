# -*- coding: utf-8 -*-
"""The ``enable_nekopower`` tool: she asks the user, in a card, to let her switch NekoPower on.

Owner (2026-11-27): the card is a NekoPower question card in the chat; only a
click on "no" is a refusal, and two in a row keep her from asking again for
five minutes (the tool fails at once meanwhile); a card nobody answers is
withdrawn after two minutes and is no refusal; on "yes" NekoPower is switched
on as the panel switch does, the session is renewed so it opens with NekoPower
on, and she is told. The tool itself returns at once.

The real ``main_logic.nekopower_enable`` rules, ``ToolCallingMixin`` methods,
``LifecycleMixin.renew_session_now`` and the main server's routes run here,
with the session, the page and the agent process stood in for.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import time

import httpx
from types import SimpleNamespace

import pytest

from config.prompts.prompts_nekopower_enable import (
    ENABLE_ALREADY_ON,
    ENABLE_ASKED,
    ENABLE_COOLDOWN,
    ENABLE_FAILURE_REASONS,
    ENABLE_NEKOPOWER_TOOL_DESCRIPTION,
    ENABLE_NEKOPOWER_TOOL_NAME,
    ENABLE_REPORT_ACCEPTED,
    ENABLE_REPORT_ACCEPTED_NOT_RENEWED,
    ENABLE_REPORT_DECLINED,
    ENABLE_REPORT_DECLINED_COOLDOWN,
    ENABLE_REPORT_EXPIRED,
    ENABLE_REPORT_FAILED,
    ENABLE_REPORT_NOTE,
    ENABLE_REPORT_WORDS,
    ENABLE_REMOTE,
    ENABLE_UNAVAILABLE,
    ENABLE_WAITING,
)
from main_logic import nekopower_enable
from main_logic.core.lifecycle import LifecycleMixin
from main_logic.core.tool_calling import ToolCallingMixin
from main_logic.tool_calling import ToolDefinition, ToolRegistry
import main_routers.agent_router as agent_router
import main_routers.websocket_router as websocket_router

pytestmark = pytest.mark.unit

NAME = "Lan"


@pytest.fixture(autouse=True)
def _fresh_rules():
    nekopower_enable.reset_for_tests()
    yield
    nekopower_enable.reset_for_tests()


# ---- the rules ---------------------------------------------------------------------------


def _ask(now=0.0):
    return nekopower_enable.ask(NAME, lang="zh", name=NAME, now=now)


def _answer(question_id, choice="", text="", now=0.0):
    return nekopower_enable.answer(NAME, question_id, choice=choice, text=text, now=now)


def test_a_card_goes_up_once_and_waits():
    outcome, question_id = _ask()
    assert outcome == nekopower_enable.ASKED and question_id
    assert _ask() == (nekopower_enable.WAITING, None)
    assert nekopower_enable.waiting_card(NAME) == question_id


def test_two_refusals_in_a_row_keep_her_from_asking_for_five_minutes():
    _, first = _ask(now=0)
    assert _answer(first, "no", now=1) == nekopower_enable.DECLINED
    _, second = _ask(now=2)
    assert _answer(second, "no", now=3) == nekopower_enable.DECLINED
    assert _ask(now=4) == (nekopower_enable.COOLING_DOWN, None)
    assert _ask(now=3 + nekopower_enable.COOLDOWN_SECONDS - 1) == (nekopower_enable.COOLING_DOWN, None)
    assert _ask(now=3 + nekopower_enable.COOLDOWN_SECONDS)[0] == nekopower_enable.ASKED


def test_a_yes_between_refusals_starts_the_count_again():
    _, card = _ask()
    _answer(card, "no")
    _, card = _ask()
    assert _answer(card, "yes") == nekopower_enable.ACCEPTED
    _, card = _ask()
    _answer(card, "no")
    assert _ask()[0] == nekopower_enable.ASKED


def test_words_and_a_card_nobody_answered_are_no_refusals():
    _, card = _ask()
    _answer(card, "no")
    _, card = _ask()
    assert _answer(card, text="等一下") == nekopower_enable.WORDS
    _, card = _ask()
    assert nekopower_enable.withdraw(NAME, card) == card
    _, card = _ask()
    _answer(card, "no")
    # One refusal, a word, a withdrawn card, one refusal: still two refusals in a row.
    assert _ask()[0] == nekopower_enable.COOLING_DOWN


def test_words_and_a_card_nobody_answered_do_not_break_the_count_either():
    # Only a "yes" starts the count again: neither of these is one.
    _, card = _ask()
    _answer(card, "no")
    _, card = _ask()
    _answer(card, text="嗯")
    _, card = _ask()
    _answer(card, "no")
    assert _ask()[0] == nekopower_enable.COOLING_DOWN


def test_words_never_add_to_the_count_of_refusals():
    for _ in range(3):
        _answer(_ask()[1], text="嗯")
    _answer(_ask()[1], "no")
    assert nekopower_enable._states[NAME].refusals == 1
    assert _ask()[0] == nekopower_enable.ASKED


def test_how_long_she_still_has_to_wait_is_counted_in_whole_minutes():
    for _ in range(2):
        _answer(_ask(now=0)[1], "no", now=0)
    assert nekopower_enable.cooldown_minutes_left(NAME, now=1) == 5
    assert nekopower_enable.cooldown_minutes_left(NAME, now=nekopower_enable.COOLDOWN_SECONDS - 61) == 2
    assert nekopower_enable.cooldown_minutes_left(NAME, now=nekopower_enable.COOLDOWN_SECONDS - 1) == 1
    assert nekopower_enable.cooldown_minutes_left(NAME, now=nekopower_enable.COOLDOWN_SECONDS) == 0


def test_an_answer_to_another_card_or_with_nothing_in_it_is_stale():
    _, card = _ask()
    assert _answer("enable-other", "yes") == nekopower_enable.STALE
    # Naming no card is answering none (CodeRabbit on #4155): a "yes" without the card's id
    # does not switch NekoPower on.
    assert _answer("", "yes") == nekopower_enable.STALE
    assert _answer(card) == nekopower_enable.STALE
    assert nekopower_enable.waiting_card(NAME) == card
    assert _answer(card, "yes") == nekopower_enable.ACCEPTED
    assert _answer(card, "yes") == nekopower_enable.STALE


def test_the_card_is_a_nekopower_confirmation_question_in_the_session_language():
    _, card = nekopower_enable.ask(NAME, lang="en", name=NAME)
    frame = nekopower_enable.card_frame(NAME)
    assert frame["type"] == "nekopower_question"
    assert frame["task_id"] == nekopower_enable.TASK_ID_PREFIX + NAME
    assert nekopower_enable.is_task_id(frame["task_id"]) and nekopower_enable.lanlan_of(frame["task_id"]) == NAME
    assert frame["question"]["id"] == card and frame["question"]["kind"] == "confirmation"
    assert NAME in frame["question"]["question"] and "NekoPower" in frame["question"]["question"]
    assert frame["lanlan_name"] == NAME
    rows = nekopower_enable.waiting_rows()
    assert rows == [{
        "task_id": frame["task_id"], "title": frame["title"], "lanlan_name": NAME, "route": None,
        "question": frame["question"],
    }]
    closed = nekopower_enable.closed_frame(NAME, card)
    assert closed["question"] is None and closed["question_id"] == card and closed["task_id"] == frame["task_id"]


# ---- the tool ----------------------------------------------------------------------------


class _Page:
    def __init__(self, fail=False):
        self.frames = []
        self.fail = fail

    async def send_json(self, frame):
        if self.fail:
            raise RuntimeError("closed")
        self.frames.append(frame)


def _session(master_on=False, page=None, lang="zh"):
    manager = object.__new__(ToolCallingMixin)
    manager.lanlan_name = NAME
    manager.user_language = lang
    manager.agent_flags = {"agent_enabled": master_on}
    manager.tool_registry = ToolRegistry()
    manager._is_agent_enabled = lambda: False
    manager.websocket = page
    manager.fired = []
    manager.reports = []

    def fire(coro):
        manager.fired.append(coro)

    manager._fire_task = fire
    manager.submit_proactive_callback = manager.reports.append
    return manager


def _close_fired(manager):
    for coro in manager.fired:
        coro.close()
    manager.fired.clear()


def test_offered_while_the_master_switch_is_off_and_taken_back_once_it_is_on(monkeypatch):
    monkeypatch.delenv("NEKO_DISABLE_BUILTIN_TOOLS", raising=False)
    manager = _session(lang="ja")
    assert manager._register_task_tools() is True
    tool = manager.tool_registry.get(ENABLE_NEKOPOWER_TOOL_NAME)
    assert tool is not None and tool.description == ENABLE_NEKOPOWER_TOOL_DESCRIPTION["ja"]
    assert tool.parameters == {"type": "object", "properties": {}}
    assert manager._register_task_tools() is False
    manager.agent_flags["agent_enabled"] = True
    assert manager._register_task_tools() is True
    assert manager.tool_registry.get(ENABLE_NEKOPOWER_TOOL_NAME) is None


def test_switched_on_from_the_panel_while_a_card_waits_takes_the_card_down(monkeypatch):
    monkeypatch.delenv("NEKO_DISABLE_BUILTIN_TOOLS", raising=False)
    page = _Page()
    manager = _session(page=page)
    manager._register_task_tools()
    asyncio.run(manager._handle_enable_nekopower_call({}))
    _close_fired(manager)
    manager.agent_flags["agent_enabled"] = True
    manager._register_task_tools()
    assert nekopower_enable.waiting_card(NAME) is None
    assert len(manager.fired) == 1
    asyncio.run(manager.fired.pop())
    assert page.frames[-1]["question"] is None


def test_not_offered_with_built_in_tools_off_nor_over_another_source(monkeypatch):
    monkeypatch.setenv("NEKO_DISABLE_BUILTIN_TOOLS", "1")
    manager = _session()
    manager._register_task_tools()
    assert manager.tool_registry.get(ENABLE_NEKOPOWER_TOOL_NAME) is None
    monkeypatch.delenv("NEKO_DISABLE_BUILTIN_TOOLS")
    theirs = ToolDefinition(
        name=ENABLE_NEKOPOWER_TOOL_NAME, description="a plugin's", parameters={"type": "object", "properties": {}},
        handler=lambda args: "", metadata={"source": "plugin"},
    )
    manager.tool_registry.register(theirs)
    manager._register_task_tools()
    assert manager.tool_registry.get(ENABLE_NEKOPOWER_TOOL_NAME).description == "a plugin's"


def test_calling_it_puts_the_card_up_and_returns_at_once():
    page = _Page()
    manager = _session(page=page)
    said = asyncio.run(manager._handle_enable_nekopower_call({}))
    assert said == ENABLE_ASKED["zh"]
    assert page.frames == [nekopower_enable.card_frame(NAME)]
    # The card is withdrawn later, if nobody answers it.
    assert len(manager.fired) == 1
    _close_fired(manager)


@pytest.mark.parametrize("case", ["waiting", "cooling_down", "no page", "page gone", "already on", "remote"])
def test_it_fails_at_once_when_it_may_not_ask_and_says_why(case, monkeypatch):
    page = _Page(fail=case == "page gone")
    manager = _session(page=None if case == "no page" else page, master_on=case == "already on")
    monkeypatch.setattr(
        "main_logic.core.tool_calling.is_remote_backend_deployment", lambda: case == "remote",
    )
    if case == "waiting":
        _ask()
    if case == "cooling_down":
        for _ in range(2):
            _answer(_ask()[1], "no", now=0)
        # Two and a half minutes left: she is told about three.
        nekopower_enable._states[NAME].cooldown_until = time.time() + 150
    said = asyncio.run(manager._handle_enable_nekopower_call({}))
    expected = {
        "waiting": ENABLE_WAITING["zh"], "cooling_down": ENABLE_COOLDOWN["zh"].format(minutes=3),
        "no page": ENABLE_UNAVAILABLE["zh"], "page gone": ENABLE_UNAVAILABLE["zh"],
        "remote": ENABLE_REMOTE["zh"],
    }
    if case == "already on":
        assert said == ENABLE_ALREADY_ON["zh"]
    else:
        assert said == {"output": expected[case], "is_error": True}
    assert page.frames == []
    assert manager.fired == []
    if case == "page gone":
        assert nekopower_enable.waiting_card(NAME) is None


class _CancelledPage(_Page):
    async def send_json(self, frame):
        raise asyncio.CancelledError()


def test_a_tool_call_cancelled_while_the_card_is_sent_leaves_no_card_up():
    # The user speaking over her cancels the tool call (CodeRabbit on #4155): the card is
    # taken down and the cancellation goes on, or she could never ask again.
    manager = _session(page=_CancelledPage())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(manager._handle_enable_nekopower_call({}))
    assert nekopower_enable.waiting_card(NAME) is None
    assert _ask()[0] == nekopower_enable.ASKED


def test_a_card_nobody_answers_is_withdrawn_and_she_is_told(monkeypatch):
    monkeypatch.setattr(nekopower_enable, "CARD_SECONDS", 0)
    page = _Page()
    manager = _session(page=page)
    asyncio.run(manager._handle_enable_nekopower_call({}))
    card = nekopower_enable.waiting_card(NAME)
    asyncio.run(manager.fired.pop())
    assert nekopower_enable.waiting_card(NAME) is None
    assert page.frames[-1] == nekopower_enable.closed_frame(NAME, card)
    assert [report["summary"] for report in manager.reports] == [ENABLE_REPORT_EXPIRED["zh"]]
    # And it is no refusal.
    assert nekopower_enable._states[NAME].refusals == 0


def test_a_card_answered_before_it_expires_is_left_alone(monkeypatch):
    monkeypatch.setattr(nekopower_enable, "CARD_SECONDS", 0)
    manager = _session(page=_Page())
    asyncio.run(manager._handle_enable_nekopower_call({}))
    expiry = manager.fired.pop()
    asyncio.run(manager.answer_enable_nekopower_card(nekopower_enable.waiting_card(NAME), choice="no"))
    manager.reports.clear()
    asyncio.run(expiry)
    assert manager.reports == []


@pytest.mark.parametrize("choice,text,report", [
    ("no", "", ENABLE_REPORT_DECLINED["zh"]),
    ("", "等我一下", ENABLE_REPORT_WORDS["zh"].format(text="等我一下")),
    ("yes", "", None),
])
def test_an_answer_takes_the_card_down_and_she_is_told_what_it_was(choice, text, report):
    page = _Page()
    manager = _session(page=page)
    asyncio.run(manager._handle_enable_nekopower_call({}))
    _close_fired(manager)
    card = nekopower_enable.waiting_card(NAME)
    outcome = asyncio.run(manager.answer_enable_nekopower_card(card, choice=choice, text=text))
    assert outcome != nekopower_enable.STALE
    assert page.frames[-1] == nekopower_enable.closed_frame(NAME, card)
    reports = [item["summary"] for item in manager.reports]
    # On "yes" she is told only once NekoPower is on (after_nekopower_enabled).
    assert reports == ([report] if report else [])
    for item in manager.reports:
        assert item["origin"] == "event" and item["delivery_mode"] == "proactive"


def test_the_second_no_in_a_row_tells_her_she_may_not_ask_again_for_a_while():
    manager = _session(page=_Page())
    for _ in range(2):
        asyncio.run(manager._handle_enable_nekopower_call({}))
        _close_fired(manager)
        asyncio.run(manager.answer_enable_nekopower_card(nekopower_enable.waiting_card(NAME), choice="no"))
    assert [report["summary"] for report in manager.reports] == [
        ENABLE_REPORT_DECLINED["zh"], ENABLE_REPORT_DECLINED_COOLDOWN["zh"],
    ]


def test_a_stale_answer_changes_nothing():
    page = _Page()
    manager = _session(page=page)
    asyncio.run(manager._handle_enable_nekopower_call({}))
    _close_fired(manager)
    sent = list(page.frames)
    assert asyncio.run(manager.answer_enable_nekopower_card("enable-other", choice="no")) == nekopower_enable.STALE
    assert page.frames == sent and manager.reports == []


@pytest.mark.parametrize("renewed", [True, False])
def test_after_yes_the_session_is_renewed_and_then_she_is_told(renewed):
    manager = _session(page=_Page())
    order = []

    async def renew():
        order.append("renew")
        return renewed

    manager.renew_session_now = renew
    manager.submit_proactive_callback = lambda report: order.append(report["summary"])
    asyncio.run(manager.after_nekopower_enabled(""))
    # Not renewed in time: the task tools may not be there yet (Gemini Live takes them at connect).
    assert order == ["renew", (ENABLE_REPORT_ACCEPTED if renewed else ENABLE_REPORT_ACCEPTED_NOT_RENEWED)["zh"]]


@pytest.mark.parametrize("failure", ["", "refused"])
def test_what_the_user_typed_beside_yes_goes_with_the_report(failure):
    manager = _session(page=_Page())

    async def renew():
        return True

    manager.renew_session_now = renew
    asyncio.run(manager.after_nekopower_enabled(failure, note=" 只开浏览器那项 "))
    summary = manager.reports[-1]["summary"]
    assert summary.endswith(ENABLE_REPORT_NOTE["zh"].format(text="只开浏览器那项"))


def test_what_the_user_typed_beside_no_goes_with_the_report():
    manager = _session(page=_Page())
    asyncio.run(manager._handle_enable_nekopower_call({}))
    _close_fired(manager)
    asyncio.run(manager.answer_enable_nekopower_card(nekopower_enable.waiting_card(NAME), choice="no", text="现在不方便"))
    assert manager.reports[-1]["summary"] == (
        ENABLE_REPORT_DECLINED["zh"] + ENABLE_REPORT_NOTE["zh"].format(text="现在不方便")
    )


@pytest.mark.parametrize("failure", ["unreachable", "refused", "unknown", "something else"])
def test_after_yes_that_could_not_switch_it_on_she_is_told_why_and_nothing_is_renewed(failure):
    manager = _session(page=_Page())
    manager.renew_session_now = None  # not to be called
    asyncio.run(manager.after_nekopower_enabled(failure))
    reason = ENABLE_FAILURE_REASONS.get(failure, ENABLE_FAILURE_REASONS["unknown"])["zh"]
    assert [report["summary"] for report in manager.reports] == [ENABLE_REPORT_FAILED["zh"].format(reason=reason)]


# ---- the session renewal -----------------------------------------------------------------


class _Renewing:
    """What ``renew_session_now`` reads and calls of the session manager."""

    def __init__(self, *, prepares=True, idle=True, swap_running=False):
        self.session = object()
        self.is_active = True
        self.final_swap_task = None
        self.is_hot_swap_imminent = False
        self.pending_session_warmed_up_event = None
        self.pending_session_final_prime_complete_event = None
        self.background_preparation_task = None
        self.calls = []
        self.prepares = prepares
        self.idle = idle
        self.swap_running = swap_running

    async def _cleanup_pending_session_resources(self):
        self.calls.append("cleanup")

    async def _reset_preparation_state(self):
        self.calls.append("reset")

    async def _trigger_immediate_preparation_for_extra(self):
        self.calls.append("prepare")
        self.pending_session_warmed_up_event = asyncio.Event()

        async def prepare():
            if self.prepares:
                self.pending_session_warmed_up_event.set()

        self.background_preparation_task = asyncio.ensure_future(prepare())

    def _can_release_proactive(self):
        return self.idle

    async def _perform_final_swap_sequence(self):
        self.calls.append("swap")
        self.session = object()
        self.is_hot_swap_imminent = False


def _renew(fake, **kwargs):
    return asyncio.run(LifecycleMixin.renew_session_now(fake, **kwargs))


def test_renewal_prepares_anew_and_swaps_at_once_when_nothing_is_under_way():
    fake = _Renewing()
    old = fake.session
    assert _renew(fake, wait_seconds=5) is True
    assert fake.session is not old
    assert fake.calls == ["cleanup", "reset", "prepare", "swap"]


def test_renewal_waits_for_a_turn_under_way_to_swap_by_itself():
    fake = _Renewing(idle=False)

    async def run():
        async def turn_ends():
            await asyncio.sleep(0.3)
            fake.session = object()  # the end of the turn swapped (turn.py)

        asyncio.ensure_future(turn_ends())
        return await LifecycleMixin.renew_session_now(fake, wait_seconds=5)

    assert asyncio.run(run()) is True
    assert "swap" not in fake.calls


def test_renewal_gives_up_without_a_session_or_when_preparing_fails():
    fake = _Renewing()
    fake.session = None
    assert _renew(fake, wait_seconds=1) is False
    assert fake.calls == []
    fake = _Renewing(prepares=False)
    assert _renew(fake, wait_seconds=1) is False
    assert "swap" not in fake.calls


def test_renewal_lets_a_swap_under_way_finish_first():
    fake = _Renewing()

    async def run():
        async def earlier_swap():
            await asyncio.sleep(0.2)
            fake.calls.append("earlier swap done")

        fake.final_swap_task = asyncio.ensure_future(earlier_swap())
        return await LifecycleMixin.renew_session_now(fake, wait_seconds=5)

    assert asyncio.run(run()) is True
    assert fake.calls[:2] == ["earlier swap done", "cleanup"]


# ---- the main server's routes ------------------------------------------------------------


class _Manager:
    def __init__(self, outcome):
        self.outcome = outcome
        self.answers = []
        self.fired = []

    async def answer_enable_nekopower_card(self, question_id, *, choice="", text=""):
        self.answers.append((question_id, choice, text))
        return self.outcome

    async def after_nekopower_enabled(self, failure="", *, note=""):
        return failure, note

    def _fire_task(self, coro):
        self.fired.append(coro)


def _route_with(monkeypatch, manager, failure=""):
    monkeypatch.setattr(agent_router, "get_session_manager", lambda: {NAME: manager})
    switched = []

    async def switch_on(mgr, lanlan):
        switched.append(lanlan)
        return failure

    monkeypatch.setattr(agent_router, "_switch_nekopower_on", switch_on)

    class _NoAgentProcess:
        async def post(self, *args, **kwargs):
            raise AssertionError("the card is not passed to the agent process")

    monkeypatch.setattr(agent_router, "_get_http_client", lambda: _NoAgentProcess())
    return switched


def test_the_answer_route_takes_a_yes_here_and_switches_nekopower_on(monkeypatch):
    manager = _Manager(nekopower_enable.ACCEPTED)
    switched = _route_with(monkeypatch, manager)
    task_id = nekopower_enable.task_id_for(NAME)
    got = asyncio.run(agent_router.proxy_commission_answer(task_id, {"question_id": "enable-1", "choice": "yes"}))
    assert got == {"success": True, "outcome": nekopower_enable.ACCEPTED}
    assert manager.answers == [("enable-1", "yes", "")]
    assert switched == [NAME]
    assert len(manager.fired) == 1
    handed_on = asyncio.run(manager.fired.pop())
    assert handed_on == ("", "")


def test_the_answer_route_hands_on_what_was_typed_beside_yes(monkeypatch):
    manager = _Manager(nekopower_enable.ACCEPTED)
    _route_with(monkeypatch, manager)
    asyncio.run(agent_router.proxy_commission_answer(
        nekopower_enable.task_id_for(NAME), {"question_id": "enable-1", "choice": "yes", "text": "只开浏览器"},
    ))
    assert manager.answers == [("enable-1", "yes", "只开浏览器")]
    handed_on = asyncio.run(manager.fired.pop())
    assert handed_on == ("", "只开浏览器")


def test_a_yes_that_could_not_switch_it_on_hands_on_why(monkeypatch):
    manager = _Manager(nekopower_enable.ACCEPTED)
    _route_with(monkeypatch, manager, failure="unreachable")
    asyncio.run(agent_router.proxy_commission_answer(
        nekopower_enable.task_id_for(NAME), {"question_id": "enable-1", "choice": "yes"},
    ))
    handed_on = asyncio.run(manager.fired.pop())
    assert handed_on == ("unreachable", "")


class _Unconfirmed:
    def __init__(self):
        self.log = []

    def write(self, mgr, mine, flags):
        self.log.append(("write", flags))

    def settle(self, mgr, mine, *, taken):
        self.log.append(("settle", taken))


@pytest.mark.parametrize("answer,failure,kept", [
    ("ok", "", True),
    ("500", "refused", False),
    ("connect", "unreachable", False),
    ("timeout", "unknown", True),
])
def test_switching_on_says_why_it_did_not_and_keeps_the_switches_as_the_panel_does(monkeypatch, answer, failure, kept):
    unconfirmed = _Unconfirmed()
    monkeypatch.setattr(agent_router, "_unconfirmed", unconfirmed)
    sent = []

    class _AgentProcess:
        async def post(self, url, json=None, timeout=None):
            sent.append((url, json))
            if answer == "connect":
                raise httpx.ConnectError("down")
            if answer == "timeout":
                raise httpx.ReadTimeout("slow")
            return SimpleNamespace(is_success=answer == "ok")

    monkeypatch.setattr(agent_router, "_get_http_client", lambda: _AgentProcess())
    assert asyncio.run(agent_router._switch_nekopower_on(object(), NAME)) == failure
    assert sent[0][0].endswith("/agent/command")
    assert {k: sent[0][1][k] for k in ("command", "enabled", "lanlan_name")} == {
        "command": "set_agent_enabled", "enabled": True, "lanlan_name": NAME,
    }
    # As the panel switch: plugins come on with it; taken back only where it did not get there.
    assert unconfirmed.log == [("write", {"agent_enabled": True, "user_plugin_enabled": True}), ("settle", kept)]


@pytest.mark.parametrize("outcome", [nekopower_enable.DECLINED, nekopower_enable.WORDS])
def test_the_answer_route_does_not_switch_anything_on_otherwise(monkeypatch, outcome):
    manager = _Manager(outcome)
    switched = _route_with(monkeypatch, manager)
    got = asyncio.run(agent_router.proxy_commission_answer(
        nekopower_enable.task_id_for(NAME), {"question_id": "enable-1", "note": "等等"},
    ))
    assert got == {"success": True, "outcome": outcome}
    assert manager.answers == [("enable-1", "", "等等")]
    assert switched == [] and manager.fired == []


def test_a_stale_answer_or_an_unknown_character_is_refused_as_not_taken(monkeypatch):
    manager = _Manager(nekopower_enable.STALE)
    _route_with(monkeypatch, manager)
    for task_id in (nekopower_enable.task_id_for(NAME), nekopower_enable.task_id_for("Someone else")):
        got = asyncio.run(agent_router.proxy_commission_answer(task_id, {"question_id": "x", "choice": "yes"}))
        assert got.status_code == 409


def test_the_question_list_carries_the_waiting_card(monkeypatch):
    nekopower_enable.ask(NAME, lang="zh", name=NAME)

    class _AgentProcess:
        async def get(self, *args, **kwargs):
            return SimpleNamespace(is_success=True, json=lambda: {"questions": [{"task_id": "t1"}]})

    monkeypatch.setattr(agent_router, "_get_http_client", lambda: _AgentProcess())
    got = asyncio.run(agent_router.proxy_questions())
    assert got["questions"] == [{"task_id": "t1"}] + nekopower_enable.waiting_rows()
    assert got["questions"][1]["task_id"] == nekopower_enable.task_id_for(NAME)


def test_the_rules_hold_what_the_owner_set():
    assert nekopower_enable.REFUSALS_TO_COOL_DOWN == 2
    assert nekopower_enable.COOLDOWN_SECONDS == 5 * 60
    assert nekopower_enable.CARD_SECONDS == 2 * 60
    assert os.path.basename(nekopower_enable.__file__) == "nekopower_enable.py"


# ---- a page that (re)connects ------------------------------------------------------------


def test_a_page_that_connects_gets_the_waiting_card_whatever_the_agent_process_says():
    # Greptile on #4155: a page reloaded while the agent process is down cannot read the
    # question list; the card lives in the main server, which sends it on connecting.
    page = _Page()
    asyncio.run(websocket_router._show_waiting_enable_card(page, NAME))
    assert page.frames == []
    _ask()
    asyncio.run(websocket_router._show_waiting_enable_card(page, NAME))
    assert page.frames == [nekopower_enable.card_frame(NAME)]
    # A page already gone is no error.
    asyncio.run(websocket_router._show_waiting_enable_card(_Page(fail=True), NAME))


def test_the_card_is_sent_as_soon_as_the_page_is_the_session_s():
    source = inspect.getsource(websocket_router)
    attach = source.index("    mgr.websocket = websocket\n")
    shown = source.index("    await _show_waiting_enable_card(websocket, lanlan_name)\n")
    assert attach < shown < attach + 300
