# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The board of sessions, the list of presets, and the form of a new session filled in."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from nekopower.host.board import STATES, doing_of, state_of
from nekopower.host.decision import TIER_SESSION_DRAFT
from nekopower.host.drafts import DRAFT_REASONS, DraftError, list_folder
from tests.unit.nekopower.host_fakes import fake_model, make_host

CARD_KEYS = {
    "id", "name", "kind", "lanlan_name", "runtime", "workdir",
    "preset", "avatar", "label", "description",
    "title", "title_by", "duty", "duty_by", "summary", "summary_by",
    "doing", "state", "leader", "parent", "team", "model", "model_fallback",
    "usage", "context_tokens", "last_active_at", "tree", "commission",
}
PROJECT_KEYS = {"name", "description", "description_by", "workdir", "archived", "done_today", "members"}


class DraftLLM:
    """Answers the call that fills in the form of a new session, and records it."""

    def __init__(self, answer: Any = None) -> None:
        self.answer = answer
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []
        self.gate: asyncio.Event | None = None

    async def complete(self, tier: str, messages: list[dict[str, Any]]) -> str:
        self.calls.append((tier, messages))
        if self.gate is not None:
            await self.gate.wait()
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer if isinstance(self.answer, str) else json.dumps(self.answer, ensure_ascii=False)


@pytest.fixture
async def host(tmp_path: Path) -> Any:
    made = make_host(tmp_path, None, task_agent_model=fake_model, decision_llm=DraftLLM())
    yield made
    await made.shutdown()


async def _commission(host: Any, title: str, **values: Any) -> Any:
    values.setdefault("lanlan_name", "Lan")
    values.setdefault("source", "dispatch")
    commission, _created = await host.ledger.create(dispatch_text=title, title=title, route="task_agent", lang="en", **values)
    return commission


async def _single(host: Any, title: str, **values: Any) -> tuple[Any, Any]:
    """A session of one commission, and that commission, queued."""
    commission = await _commission(host, title, **values)
    return await host.ledger.give_session(commission.id, kind="single", name=title, duty=title)


async def _lead(host: Any, project: str, title: str, **values: Any) -> tuple[Any, Any]:
    """The session of a project, and a commission of it, queued."""
    await host.space.touch_project(project)
    commission = await _commission(host, title, project=project, **values)
    return await host.ledger.give_session(commission.id, kind="project", name=project, duty=title)


async def _child(host: Any, parent: Any, name: str, preset: str | None = None) -> tuple[Any, Any]:
    commission = await _commission(host, name, parent_id=parent.id, project=parent.project, name=name, source="task_agent")
    return await host.ledger.give_session(
        commission.id, kind="child", name=f"lead/{name}", parent_id=parent.session_id, preset=preset, duty=name
    )


async def _working(host: Any, commission_id: str) -> None:
    moved = await host.ledger.transition(commission_id, "working", reason="test")
    assert moved.applied, moved.refusal


async def _done(host: Any, commission_id: str) -> None:
    await _working(host, commission_id)
    moved = await host.ledger.transition(commission_id, "done", reason="test", result_summary="done")
    assert moved.applied, moved.refusal


def _cards(board: dict[str, Any]) -> dict[str, dict[str, Any]]:
    found = [card for project in board["projects"] for card in project["members"]] + board["loose"]
    return {card["id"]: card for card in found}


# ---------------------------------------------------------------------------
# What is on the board
# ---------------------------------------------------------------------------


async def test_an_empty_board(host):
    board = await host.board()

    assert set(board) == {"revision", "projects", "loose"}
    assert (board["projects"], board["loose"]) == ([], [])
    # Reading it started nothing.
    assert not host.task_agents.runtime_started and not host.gateway.started


async def test_sessions_are_grouped_by_project_with_every_field_of_a_card(host):
    await host.space.touch_project("Budget app", description="A budget for the household.", workdir="D:/budget")
    commission, lead = await _lead(host, "Budget app", "add a chart")
    await host.ledger.update_session(lead.id, runtime="builtin", workdir="D:/budget")
    await host.ledger.write_card(lead.id, by="user", title="记账管家")
    await host.ledger.write_card(lead.id, by="review", summary="Made the table.")
    await host.ledger.add_usage(session_id=lead.id, commission_id=commission.id, tokens_in=100, tokens_out=20, context_tokens=120)
    _errand, single = await _single(host, "sort the photos", lanlan_name="Mia")

    board = await host.board()

    (project,) = board["projects"]
    assert set(project) == PROJECT_KEYS
    assert {key: project[key] for key in PROJECT_KEYS - {"members"}} == {
        "name": "Budget app", "description": "A budget for the household.", "description_by": "creator",
        "workdir": "D:/budget", "archived": False, "done_today": 0,
    }
    (card,) = project["members"]
    assert set(card) == CARD_KEYS
    kept = await host.ledger.get_session(lead.id)
    assert card == {
        "id": lead.id, "name": "Budget app", "kind": "project", "lanlan_name": "Lan", "runtime": "builtin",
        "workdir": "D:/budget",
        "preset": "boss", "avatar": "boss", "label": "",
        "description": "The leader of a project: every commission filed under it comes to it first; it decides who "
        "does it and hands it out, and does none of the work itself.",
        "title": "记账管家", "title_by": "user", "duty": "add a chart", "duty_by": "creator",
        "summary": "Made the table.", "summary_by": "review",
        "doing": "add a chart", "state": "asleep", "leader": True, "parent": None, "team": None,
        "model": "fake-model", "model_fallback": None,
        "usage": {
            "requests": 1, "tokens_in": 100, "tokens_out": 20, "tokens_cache_read": 0, "tokens_cache_write": 0,
            "tokens": 120,
        },
        "context_tokens": 120, "last_active_at": kept.last_active_at, "tree": None,
        "commission": {"id": commission.id, "title": "add a chart", "status": "queued", "started_at": None, "ask": None},
    }
    # A session that belongs to no project is apart, and is nobody's lead.
    (loose,) = board["loose"]
    assert (loose["id"], loose["kind"], loose["preset"], loose["avatar"], loose["leader"], loose["lanlan_name"]) == (
        single.id, "single", "general", "general", False, "Mia",
    )
    # Nothing of the model but its name is on the board: not its key, not its address.
    assert "free-access" not in json.dumps(board) and "api_key" not in json.dumps(board)


async def test_a_project_s_members_are_listed_with_it_and_only_its_leader_is_one(host):
    await host.space.touch_project("Budget app", workdir="D:/budget")
    await host.space.touch_project("Photo album")
    _a, leader = await _lead(host, "Budget app", "add a chart")
    made = await _commission(host, "draw a logo", project="Budget app")
    _b, worker = await host.ledger.give_session(
        made.id, kind="line", name="Budget app/worker", parent_id=leader.id, preset="worker", duty="draws"
    )
    alone = await _commission(host, "sort the photos", project="Photo album")
    _c, member = await host.ledger.give_session(alone.id, kind="line", name="Photo album", duty="sorts")
    await host.ledger.update_session(worker.id, last_active_at=leader.last_active_at + 100)

    board = await host.board()

    by_name = {project["name"]: project["members"] for project in board["projects"]}
    # The leader first, whoever was last in use; its member after it, with who started it.
    assert [(card["id"], card["kind"], card["preset"], card["leader"], card["parent"]) for card in by_name["Budget app"]] == [
        (leader.id, "project", "boss", True, None),
        (worker.id, "line", "worker", False, leader.id),
    ]
    # A project that has no leader has none: its one member is a member.
    assert [(card["id"], card["kind"], card["preset"], card["leader"]) for card in by_name["Photo album"]] == [
        (member.id, "line", "general", False)
    ]
    assert board["loose"] == []
    # Of one project: its leader and its members.
    only = await host.board(project="Budget app")
    assert [card["id"] for card in only["projects"][0]["members"]] == [leader.id, worker.id]


async def test_the_board_of_one_project_and_of_one_character(host):
    _a, budget = await _lead(host, "Budget app", "add a chart")
    _b, garden = await _lead(host, "Garden", "water the plants", lanlan_name="Mia")
    _c, single = await _single(host, "sort the photos")

    one = await host.board(project="Budget app")
    mia = await host.board(lanlan_name="Mia")

    assert [item["name"] for item in one["projects"]] == ["Budget app"] and one["loose"] == []
    assert list(_cards(one)) == [budget.id]
    # Every project is there; only her sessions are.
    assert {item["name"]: len(item["members"]) for item in mia["projects"]} == {"Budget app": 0, "Garden": 1}
    assert list(_cards(mia)) == [garden.id]
    assert set(_cards(await host.board())) == {budget.id, garden.id, single.id}
    assert (await host.board(project="No such project"))["projects"] == []


async def test_only_the_sessions_asked_for_are_read_from_the_ledger(host):
    _a, budget = await _lead(host, "Budget app", "add a chart")
    _b, garden = await _lead(host, "Garden", "water the plants", lanlan_name="Mia")
    _c, single = await _single(host, "sort the photos")
    limits = {"recent_since": 0.0, "child_since": 0.0, "day_start": 0.0, "loose_max": 30, "children_max": 8, "events_each": 80}

    async def read(**asked: Any) -> set[str]:
        return {session.id for session in (await host.ledger.board_rows(**limits, **asked)).sessions}

    assert await read() == {budget.id, garden.id, single.id}
    # Of one project: its own session, and no session of one commission.
    assert await read(project="Budget app") == {budget.id}
    assert await read(lanlan_name="Mia") == {garden.id}
    assert await read(lanlan_name="Lan") == {budget.id, single.id}


async def test_a_session_of_one_commission_that_was_not_used_in_days_is_off_the_board(host):
    old_commission, old = await _single(host, "an errand of last month")
    await _done(host, old_commission.id)
    long_ago = time.time() - 30 * 86400
    await host.ledger.run(lambda conn: conn.execute("UPDATE sessions SET last_active_at = ?", (long_ago,)))
    commission, recent = await _single(host, "an errand of today")
    await _done(host, commission.id)
    # The folder commissions of no project are filed under is no project of the board.
    from nekopower.host.paths import DEFAULT_PROJECT

    await host.space.touch_project(DEFAULT_PROJECT)

    board = await host.board()

    # With room to spare on the board: it is its age that keeps it off.
    assert [card["id"] for card in board["loose"]] == [recent.id]
    assert board["projects"] == []


async def test_the_commission_in_hand_is_the_one_being_worked_on_before_one_that_rests_or_waits_its_turn(host):
    first, session = await _single(host, "the one that came first and waits")
    second = await _commission(host, "the one that rests", session_id=session.id)
    await _working(host, second.id)
    _wake, refusal = await host.ledger.wake_add(
        session_id=session.id, commission_id=second.id, note="look again", due_at=time.time() + 3600,
        pending_max=5, daily_max=20,
    )
    assert not refusal and await host.ledger.rest(second.id) is not None

    card = _cards(await host.board())[session.id]
    # One that rests before one that waits for its turn, though that one came first.
    assert (card["commission"]["id"], card["commission"]["status"], card["state"]) == (second.id, "resting", "asleep")

    third = await _commission(host, "the one at work", session_id=session.id)
    await _working(host, third.id)
    card = _cards(await host.board())[session.id]
    assert (card["commission"]["id"], card["commission"]["status"], card["state"]) == (third.id, "working", "working")
    assert first.id != card["commission"]["id"]


async def test_a_project_counts_what_was_done_today_and_shows_whether_it_is_archived(host):
    commission, lead = await _lead(host, "Budget app", "add a chart")
    await _done(host, commission.id)
    earlier = await _commission(host, "add a table", project="Budget app", session_id=lead.id)
    await _done(host, earlier.id)
    yesterday = time.time() - 2 * 86400
    await host.ledger.run(lambda conn: conn.execute("UPDATE commissions SET ended_at = ? WHERE id = ?", (yesterday, earlier.id)))
    # What a task agent of it did is its own business, and one that failed is not done.
    child, _session = await _child(host, commission, "agent-1")
    await host.ledger.run(lambda conn: conn.execute("UPDATE commissions SET status = 'done', ended_at = ? WHERE id = ?", (time.time(), child.id)))
    await host.space.touch_project("Old thing")
    await host.space.archive_project("Old thing", summary="It is over.", description="an old thing")

    board = await host.board()

    found = {item["name"]: item for item in board["projects"]}
    assert (found["Budget app"]["done_today"], found["Budget app"]["archived"]) == (1, False)
    assert (found["Old thing"]["archived"], found["Old thing"]["members"]) == (True, [])
    assert [item["name"] for item in board["projects"]] == ["Budget app", "Old thing"]


async def test_a_session_whose_project_has_no_record_is_shown_under_its_name(host):
    commission = await _commission(host, "add a chart", project="Ghost")
    _commission_given, lead = await host.ledger.give_session(commission.id, kind="project", name="Ghost")

    board = await host.board()

    (project,) = board["projects"]
    assert (project["name"], project["description"], project["archived"]) == ("Ghost", "", False)
    assert [card["id"] for card in project["members"]] == [lead.id]


async def test_the_sessions_are_shown_though_the_projects_or_the_templates_cannot_be_read(host, monkeypatch):
    _commission_made, lead = await _lead(host, "Budget app", "add a chart")
    _errand, single = await _single(host, "sort the photos")

    async def no_projects(**kwargs: Any) -> Any:
        raise OSError("the disk does not answer")

    def no_templates(project: Any = None) -> Any:
        raise OSError("the disk does not answer")

    monkeypatch.setattr(host.space, "list_projects", no_projects)
    monkeypatch.setattr(host.templates_store, "listing", no_templates)

    board = await host.board()

    # The project under the name its session has, and the cards without what a template says of them.
    assert [(item["name"], item["description"]) for item in board["projects"]] == [("Budget app", "")]
    cards = _cards(board)
    assert set(cards) == {lead.id, single.id}
    assert (cards[lead.id]["preset"], cards[lead.id]["avatar"], cards[lead.id]["description"]) == ("boss", "general", "")


@pytest.mark.parametrize("missing", ["projects", "templates", "model"])
async def test_a_board_shown_without_something_that_could_not_be_read_is_not_taken_as_unchanged_later(
    host, monkeypatch, missing
):
    await host.space.touch_project("Budget app", description="A budget for the household.")
    _commission_made, lead = await _lead(host, "Budget app", "add a chart")

    async def failing_async(*args: Any, **kwargs: Any) -> Any:
        raise OSError("the disk does not answer")

    def failing(*args: Any, **kwargs: Any) -> Any:
        raise OSError("the disk does not answer")

    with monkeypatch.context() as broken:
        if missing == "projects":
            broken.setattr(host.space, "list_projects", failing_async)
        elif missing == "templates":
            broken.setattr(host.templates_store, "listing", failing)
        else:
            broken.setattr(host, "_task_agent_model", failing_async)
        without = await host.board()
        # Asked again while it cannot be read: put together again, and the same as it was.
        assert await host.board(known_revision=without["revision"]) == {"revision": without["revision"], "unchanged": True}

    # It can be read again, and nothing was written meanwhile that the marks of a change would show:
    # the board is put together all the same, and what was missing is there.
    whole = await host.board(known_revision=without["revision"])
    assert "unchanged" not in whole and whole["revision"] != without["revision"]
    card = _cards(whole)[lead.id]
    assert (whole["projects"][0]["description"], card["avatar"], card["model"]) == (
        "A budget for the household.", "boss", "fake-model",
    )
    # From here on it is kept as any board is.
    assert await host.board(known_revision=whole["revision"]) == {"revision": whole["revision"], "unchanged": True}


async def test_a_copy_is_shown_under_its_session_and_a_copy_of_a_copy_is_not_on_the_board(host):
    commission, lead = await _lead(host, "Budget app", "add a chart")
    await _working(host, commission.id)
    copy = await host.ledger.create_session(
        lanlan_name="Lan", name="Budget app/fork", kind="fork", project="Budget app", forked_from=lead.id, status="idle"
    )
    again = await host.ledger.create_session(
        lanlan_name="Lan", name="Budget app/fork/fork", kind="fork", project="Budget app", forked_from=copy.id, status="idle"
    )

    (project,) = (await host.board())["projects"]

    assert [card["id"] for card in project["members"]] == [lead.id, copy.id]
    assert again.id not in [card["id"] for card in project["members"]]


# ---------------------------------------------------------------------------
# How a session stands
# ---------------------------------------------------------------------------


async def test_the_five_states_a_session_is_shown_in(host):
    assert STATES == ("working", "waiting_user", "idle", "asleep", "compacting")
    made: dict[str, Any] = {}
    for name in ("working", "waiting", "idle", "asleep", "compacting", "resting", "queued"):
        commission, session = await _single(host, f"errand {name}")
        made[name] = (commission, session)
    await _working(host, made["working"][0].id)
    await _working(host, made["waiting"][0].id)
    question = {"kind": "report", "question": "Which folder?", "details": "", "id": "q1"}
    await host.ledger.transition(
        made["waiting"][0].id, "waiting_user", reason="test", pending_question=json.dumps(question)
    )
    for name in ("idle", "asleep", "compacting"):
        await _done(host, made[name][0].id)
    await host.ledger.update_session(made["idle"][1].id, status="idle")
    await host.ledger.update_session(made["asleep"][1].id, status="closed")
    await host.ledger.update_session(made["compacting"][1].id, status="closed")
    assert await host.ledger.begin_compacting(made["compacting"][1].id)
    await _working(host, made["resting"][0].id)
    _wake, refusal = await host.ledger.wake_add(
        session_id=made["resting"][1].id, commission_id=made["resting"][0].id, note="look again",
        due_at=time.time() + 3600, pending_max=5, daily_max=20,
    )
    assert not refusal and await host.ledger.rest(made["resting"][0].id) is not None

    cards = _cards(await host.board())

    def shown(name: str) -> tuple[str, str | None]:
        card = cards[made[name][1].id]
        return card["state"], card["commission"]["status"] if card["commission"] else None

    assert shown("working") == ("working", "working")
    assert shown("waiting") == ("waiting_user", "waiting_user")
    assert shown("idle") == ("idle", None)
    assert shown("asleep") == ("asleep", None)
    assert shown("compacting") == ("compacting", None)
    # Nobody is at a commission that rests until a time it set, or waits for its turn: the session
    # is asleep, and the commission says which of the two it is.
    assert shown("resting") == ("asleep", "resting")
    assert shown("queued") == ("asleep", "queued")
    # What a waiting commission asks is there, as it was asked.
    assert cards[made["waiting"][1].id]["commission"]["ask"] == question
    assert cards[made["working"][1].id]["commission"]["ask"] is None
    assert cards[made["working"][1].id]["commission"]["started_at"] is not None
    # Without a commission in hand a session is doing nothing.
    assert cards[made["idle"][1].id]["doing"] == "" and cards[made["idle"][1].id]["commission"] is None


async def test_a_question_about_a_permission_is_given_as_the_gate_put_it(host):
    commission, session = await _single(host, "tidy the folder")
    await _working(host, commission.id)
    ask = {
        "kind": "permission", "category": "run_command", "key": None,
        "detail": {"command": "rm -r build"}, "options": ["once", "session", "always", "deny"], "id": "q7",
    }
    await host.ledger.transition(commission.id, "waiting_user", reason="test", pending_question=json.dumps(ask))

    card = _cards(await host.board())[session.id]

    assert card["state"] == "waiting_user" and card["commission"]["ask"] == ask


def test_the_state_goes_by_the_commission_in_hand_before_the_session(tmp_path):
    from nekopower.host.ledger import Commission, Session

    def session(status: str) -> Session:
        return Session(
            id="s", name="s", lanlan_name="Lan", kind="single", project=None, workdir=None, session_dir=None,
            runtime=None, parent_id=None, forked_from=None, status=status, created_at=0.0, last_active_at=0.0,
            compacted_at=None,
        )

    def commission(status: str, resting: float | None = None) -> Commission:
        return Commission(
            id="c", lanlan_name="Lan", title="t", dispatch_text="t", user_text="", source="dispatch", route="task_agent",
            project=None, workdir=None, status=status, status_reason="", runtime=None, created_at=0.0, updated_at=0.0,
            started_at=None, ended_at=None, result_summary=None, pending_question=None, delivery=None, parent_id=None,
            conversation_id=None, lang=None, plugin_id=None, entry_id=None, name=None, deferred_until=None,
            host_instance=None, resting_since=resting,
        )

    # Being summed up comes before everything; a commission that waits before the session's own word.
    assert state_of(session("compacting"), commission("working")) == "compacting"
    assert state_of(session("running"), commission("waiting_user")) == "waiting_user"
    assert state_of(session("closed"), commission("working")) == "working"
    assert state_of(session("idle"), commission("working", resting=1.0)) == "idle"
    assert state_of(session("closed"), commission("working", resting=1.0)) == "asleep"
    assert state_of(session("idle"), commission("queued")) == "idle"
    # Let go of a moment after its commission ended.
    assert state_of(session("running"), None) == "working"
    assert state_of(session("waiting"), None) == "asleep"
    assert state_of(session("cleared"), None) == "asleep"


# ---------------------------------------------------------------------------
# What it is doing
# ---------------------------------------------------------------------------


async def test_what_a_session_is_doing_is_put_together_from_its_title_its_todo_list_and_its_last_tool(host):
    from config import nekopower_settings as settings
    from utils.tokenize import count_tokens

    commission, session = await _single(host, "add an Excel export to the report page")
    await _working(host, commission.id)
    todos = [
        {"content": "read export.py", "status": "completed"},
        {"content": "change the date format in export.py", "status": "in_progress"},
        {"content": "run the tests", "status": "pending"},
    ]
    await host.ledger.append_events(
        commission.id,
        [
            ("tool_start", {"tool_call_id": "t0", "tool_name": "todo_write", "args": {"todos": [{"content": "old", "status": "in_progress"}]}}),
            ("tool_start", {"tool_call_id": "t1", "tool_name": "todo_write", "args": {"todos": todos}}),
            ("tool_start", {"tool_call_id": "t2", "tool_name": "read", "args": {"file_path": "D:\\work\\reports\\src\\export.py"}}),
            ("tool_end", {"tool_call_id": "t2", "tool_name": "read", "is_error": False, "text": "..."}),
            ("text", {"text": "Now the format."}),
            ("tool_start", {"tool_call_id": "t3", "tool_name": "edit", "args": {"file_path": "D:\\work\\reports\\src\\export.py", "old": "x"}}),
            ("tool_start", {"tool_call_id": "t4", "tool_name": "report", "args": {"status": "progress", "summary": "halfway"}}),
        ],
    )

    card = _cards(await host.board())[session.id]

    assert card["doing"] == (
        "add an Excel export to the report page · change the date format in export.py · edit: src/export.py"
    )
    assert count_tokens(card["doing"]) <= settings.BOARD_DOING_MAX_TOKENS
    # No model was asked for it.
    assert host._decision_llm.calls == []


def test_what_a_session_is_doing_without_a_todo_list_and_with_other_tools():
    from nekopower.host.ledger import Commission

    def commission(title: str = "sort the photos") -> Commission:
        return Commission(
            id="c", lanlan_name="Lan", title=title, dispatch_text=title, user_text="", source="dispatch", route="task_agent",
            project=None, workdir=None, status="working", status_reason="", runtime=None, created_at=0.0, updated_at=0.0,
            started_at=None, ended_at=None, result_summary=None, pending_question=None, delivery=None, parent_id=None,
            conversation_id=None, lang=None, plugin_id=None, entry_id=None, name=None, deferred_until=None,
            host_instance=None,
        )

    def call(name: str, args: Any) -> dict[str, Any]:
        return {"tool_name": name, "args": args}

    assert doing_of(None, (call("read", {"file_path": "a.py"}),)) == ""
    assert doing_of(commission(), ()) == "sort the photos"
    # The newest call first; its arguments may be kept as text; what it was called on is the first of these.
    assert doing_of(commission(), (call("bash", '{"command": "ls   -la\\n photos"}'), call("read", {"file_path": "a.py"}))) == (
        "sort the photos · bash: ls -la photos"
    )
    assert doing_of(commission(), (call("grep", {"pattern": "def main", "path": "src"}),)) == "sort the photos · grep: src"
    assert doing_of(commission(), (call("grep", {"path": "  ", "pattern": "def main"}),)) == "sort the photos · grep: def main"
    assert doing_of(commission(), (call("web_fetch", {"url": "https://example.com/a"}),)) == (
        "sort the photos · web_fetch: https://example.com/a"
    )
    # A tool called on nothing that can be named, and arguments that are no object.
    assert doing_of(commission(), (call("list_agents", {}),)) == "sort the photos · list_agents"
    assert doing_of(commission(), (call("read", "not json"),)) == "sort the photos · read"
    assert doing_of(commission(), (call("read", ["a.py"]),)) == "sort the photos · read"
    assert doing_of(commission(), (call("read", "[1, 2]"),)) == "sort the photos · read"
    assert doing_of(commission(), (call("read", None),)) == "sort the photos · read"
    # A todo list that is no list, and a call that names no tool, add nothing.
    assert doing_of(commission(), (call("todo_write", "not json"), call("todo_write", {"todos": "all of it"}))) == "sort the photos"
    assert doing_of(commission(), ({"args": {"file_path": "a.py"}},)) == "sort the photos"
    assert doing_of(commission(), ({"args": {}}, {"tool_name": "", "args": {"file_path": "a.py"}})) == "sort the photos"
    # The step before it is the last then.
    assert doing_of(commission(), ({"args": {"file_path": "a.py"}}, call("read", {"file_path": "b.py"}))) == (
        "sort the photos · read: b.py"
    )
    # A todo list with nothing in progress adds nothing; reports and the list itself are no step.
    idle_list = call("todo_write", {"todos": [{"content": "x", "status": "completed"}]})
    assert doing_of(commission(), (call("report", {"status": "progress"}), idle_list)) == "sort the photos"
    # Each part and the whole are cut to their caps.
    from config import nekopower_settings as settings
    from utils.tokenize import count_tokens

    long = doing_of(
        commission("title " * 80),
        (call("bash", {"command": "word " * 80}), call("todo_write", {"todos": [{"content": "item " * 80, "status": "in_progress"}]})),
    )
    assert count_tokens(long) <= settings.BOARD_DOING_MAX_TOKENS
    assert long.startswith("title title") and " · item item" in long


def test_what_a_tool_was_called_on_is_read_though_its_arguments_were_cut_where_they_were_stored():
    from config.nekopower_settings import TASK_AGENT_EVENT_TEXT_MAX_CHARS
    from nekopower.host.ledger import Commission
    from nekopower.host.task_agents import TaskAgentManager

    commission = Commission(
        id="c", lanlan_name="Lan", title="write the report", dispatch_text="x", user_text="", source="dispatch",
        route="task_agent", project=None, workdir=None, status="working", status_reason="", runtime=None, created_at=0.0,
        updated_at=0.0, started_at=None, ended_at=None, result_summary=None, pending_question=None, delivery=None,
        parent_id=None, conversation_id=None, lang=None, plugin_id=None, entry_id=None, name=None, deferred_until=None,
        host_instance=None,
    )

    def stored(name: str, args: dict[str, Any]) -> dict[str, Any]:
        # As the host puts a tool call on record: arguments longer than a row holds are the beginning of their text.
        kept = TaskAgentManager._compact({"type": "tool_start", "session_id": "s", "tool_name": name, "args": args})
        assert isinstance(kept["args"], str) and kept["args"].endswith("…") and len(kept["args"]) == TASK_AGENT_EVENT_TEXT_MAX_CHARS + 1
        return kept

    long = "a line of the file, with \"quotes\" and a \\ backslash\n" * 400
    # A file written whole, a long passage replaced: just when the user wants to see which file it is.
    assert doing_of(commission, (stored("write", {"file_path": "D:\\work\\reports\\src\\big.py", "content": long}),)) == (
        "write the report · write: src/big.py"
    )
    assert doing_of(commission, (stored("edit", {"file_path": "src/export.py", "old_string": long, "new_string": long}),)) == (
        "write the report · edit: src/export.py"
    )
    # What the text holds of a field is not taken for the field: the file's own text names a path too.
    quoted = '{"file_path": "decoy.py", "command": "rm"} ' * 200
    assert doing_of(commission, (stored("write", {"content": quoted, "file_path": "real.py"}),)) == "write the report · write"
    # Cut inside the very value that says which file: the tool alone.
    assert doing_of(commission, (stored("bash", {"command": "echo " + "x" * 5000}),)) == "write the report · bash"
    # A long todo list: the item in progress, as far as the list is whole.
    todos = [{"content": f"step {number} of the plan, spelled out at some length", "status": "completed"} for number in range(3)]
    todos.append({"content": "change the date format", "status": "in_progress"})
    todos.extend({"content": f"later step {number} " + "and more " * 30, "status": "pending"} for number in range(40))
    assert doing_of(commission, (stored("todo_write", {"todos": todos}),)) == "write the report · change the date format"
    # One that stands past where it was cut is not there to be read.
    late = [*todos[4:], todos[3]]
    assert doing_of(commission, (stored("todo_write", {"todos": late}),)) == "write the report"


async def test_the_sessions_of_one_commission_and_what_is_shown_under_them_have_a_cap_together(host, monkeypatch):
    monkeypatch.setattr("config.nekopower_settings.BOARD_LOOSE_CARDS_MAX", 4)
    made = []
    for number in range(3):
        commission, session = await _single(host, f"errand {number}")
        await _working(host, commission.id)
        children = [(await _child(host, commission, f"agent-{number}-{index}"))[1] for index in range(2)]
        made.append((session, children))

    board = await host.board()

    # Every session has its place first; what is shown under them takes what is left, each behind its
    # own, and what the first has taken is not there for the next.
    assert [card["id"] for card in board["loose"]] == [made[2][0].id, made[2][1][1].id, made[1][0].id, made[0][0].id]
    assert [card["kind"] for card in board["loose"]] == ["single", "child", "single", "single"]
    # With more sessions than cards there may be, the ones last in use.
    monkeypatch.setattr("config.nekopower_settings.BOARD_LOOSE_CARDS_MAX", 2)
    assert [card["id"] for card in (await host.board())["loose"]] == [made[2][0].id, made[1][0].id]


async def test_a_second_lead_of_a_project_is_not_crowded_out_by_the_task_agents_of_the_first(host, monkeypatch):
    monkeypatch.setattr("config.nekopower_settings.BOARD_MEMBERS_MAX", 3)
    _mine, hers = await _lead(host, "Budget app", "add a chart", lanlan_name="Mia")
    commission, lead = await _lead(host, "Budget app", "add a table")
    await _working(host, commission.id)
    children = [(await _child(host, commission, f"agent-{index}"))[1] for index in range(3)]

    (project,) = (await host.board())["projects"]

    assert [card["id"] for card in project["members"]] == [lead.id, children[2].id, hers.id]
    assert [card["leader"] for card in project["members"]] == [True, False, True]


async def test_only_the_latest_events_of_the_commission_in_hand_are_read(host, monkeypatch):
    monkeypatch.setattr("config.nekopower_settings.BOARD_DOING_EVENTS", 4)
    commission, session = await _single(host, "sort the photos")
    await _working(host, commission.id)
    todo = {"todos": [{"content": "an old step", "status": "in_progress"}]}
    rows: list[tuple[str, dict[str, Any]]] = [
        ("tool_start", {"tool_call_id": "t0", "tool_name": "todo_write", "args": todo}),
        ("tool_start", {"tool_call_id": "t1", "tool_name": "read", "args": {"file_path": "a.py"}}),
        *[("text", {"text": f"thinking {number}"}) for number in range(3)],
        ("tool_start", {"tool_call_id": "t2", "tool_name": "glob", "args": {"pattern": "*.jpg"}}),
    ]
    await host.ledger.append_events(commission.id, rows)

    card = _cards(await host.board())[session.id]

    # The todo list lies further back than is read: the title and the last tool are what there is.
    assert card["doing"] == "sort the photos · glob: *.jpg"


# ---------------------------------------------------------------------------
# Task agents another one started, and copies
# ---------------------------------------------------------------------------


async def test_task_agents_and_copies_are_shown_under_the_session_they_belong_to(host):
    commission, lead = await _lead(host, "Budget app", "add a chart")
    await _working(host, commission.id)
    scouting, scout = await _child(host, commission, "agent-1", preset="scout")
    await _working(host, scouting.id)
    copy = await host.ledger.create_session(
        lanlan_name="Lan", name="Budget app/fork", kind="fork", project="Budget app", forked_from=lead.id, status="idle"
    )
    unused = await host.ledger.create_session(
        lanlan_name="Lan", name="Budget app/fork-2", kind="fork", project="Budget app", forked_from=lead.id
    )

    board = await host.board()

    (project,) = board["projects"]
    assert [card["id"] for card in project["members"]] == [lead.id, scout.id, copy.id]
    cards = _cards(board)
    assert (cards[scout.id]["kind"], cards[scout.id]["preset"], cards[scout.id]["avatar"]) == ("child", "scout", "scout")
    assert (cards[scout.id]["parent"], cards[scout.id]["leader"], cards[scout.id]["state"]) == (lead.id, False, "working")
    assert cards[scout.id]["description"].startswith("Read-only research")
    # A copy is drawn as a copy, under the session it is a copy of. One nothing is at work in is not shown.
    assert (cards[copy.id]["kind"], cards[copy.id]["preset"], cards[copy.id]["avatar"]) == ("fork", "shadow", "shadow")
    assert (cards[copy.id]["parent"], cards[copy.id]["label"], cards[copy.id]["description"]) == (lead.id, "", "")
    assert unused.id not in cards
    # A copy with a commission under way is shown though no runtime has it open.
    asked = await _commission(host, "where does it stand", project="Budget app", session_id=unused.id)
    assert unused.id in _cards(await host.board())
    assert _cards(await host.board())[unused.id]["commission"]["id"] == asked.id


async def test_a_task_agent_is_shown_while_what_started_it_is_under_way_and_a_while_after(host, monkeypatch):
    commission, lead = await _single(host, "check the tests")
    await _working(host, commission.id)
    child, session = await _child(host, commission, "agent-1")
    await _done(host, child.id)

    assert session.id in _cards(await host.board())
    # Its starter ended: it stays for a while, and then it is gone.
    moved = await host.ledger.transition(commission.id, "done", reason="test", result_summary="done")
    assert moved.applied
    board = await host.board()
    assert [card["id"] for card in board["loose"]] == [lead.id, session.id]
    monkeypatch.setattr("config.nekopower_settings.BOARD_CHILD_RECENT_SECONDS", 0.0)
    await asyncio.sleep(0.02)
    assert [card["id"] for card in (await host.board())["loose"]] == [lead.id]


async def test_the_board_holds_no_more_sessions_than_its_limits_allow(host, monkeypatch):
    monkeypatch.setattr("config.nekopower_settings.BOARD_LOOSE_MAX", 3)
    monkeypatch.setattr("config.nekopower_settings.BOARD_CHILDREN_MAX", 2)
    monkeypatch.setattr("config.nekopower_settings.BOARD_MEMBERS_MAX", 2)
    old = []
    for number in range(5):
        commission, session = await _single(host, f"old errand {number}")
        await _done(host, commission.id)
        old.append(session)
    waiting, kept = await _single(host, "an errand that waits for its turn")
    # Long ago, all of them; the one with a commission under way too.
    long_ago = time.time() - 30 * 86400
    await host.ledger.run(lambda conn: conn.execute("UPDATE sessions SET last_active_at = ?", (long_ago,)))
    recent = []
    for number in range(4):
        commission, session = await _single(host, f"errand {number}")
        await _done(host, commission.id)
        recent.append(session)
    lead_commission, lead = await _lead(host, "Budget app", "add a chart")
    await _working(host, lead_commission.id)
    children = [(await _child(host, lead_commission, f"agent-{number}"))[1] for number in range(4)]

    board = await host.board()

    # Of the sessions of one commission: the one with something under way first, whatever its age,
    # then the ones last in use, up to the most there may be. The old ones that are over are not there.
    assert [card["id"] for card in board["loose"]] == [kept.id, recent[3].id, recent[2].id]
    assert not {session.id for session in old} & set(_cards(board))
    # Under one session, the task agents last in use; and a project's cards, its lead first.
    (project,) = board["projects"]
    assert [card["id"] for card in project["members"]] == [lead.id, children[3].id]
    monkeypatch.setattr("config.nekopower_settings.BOARD_MEMBERS_MAX", 24)
    (project,) = (await host.board())["projects"]
    assert [card["id"] for card in project["members"]] == [lead.id, children[3].id, children[2].id]


async def test_a_project_begun_anew_under_the_name_of_an_archived_one_is_on_the_board_once(host):
    await host.space.touch_project("Old thing")
    await host.space.archive_project("Old thing", summary="It is over.", description="an old thing")
    await host.space.touch_project("Old thing", description="Begun again.")
    await host.space.touch_project("Other thing")
    await host.space.archive_project("Other thing", summary="It is over.", description="another")

    board = await host.board()

    assert [(item["name"], item["archived"], item["description"]) for item in board["projects"]] == [
        ("Old thing", False, "Begun again."), ("Other thing", True, "another"),
    ]


async def test_only_so_many_archived_projects_are_on_the_board(host, monkeypatch):
    monkeypatch.setattr("config.nekopower_settings.BOARD_ARCHIVED_PROJECTS_MAX", 1)
    sessions = {}
    for name in ("First thing", "Second thing"):
        commission, session = await _lead(host, name, f"work on {name}")
        await _done(host, commission.id)
        sessions[name] = session
        await host.space.archive_project(name, summary="It is over.", description="over")
        await asyncio.sleep(0.02)

    board = await host.board()

    # The one archived last, with its session; the other is left off, session and all.
    assert [(item["name"], item["archived"]) for item in board["projects"]] == [("Second thing", True)]
    assert list(_cards(board)) == [sessions["Second thing"].id]


# ---------------------------------------------------------------------------
# Whether it changed
# ---------------------------------------------------------------------------


async def test_a_board_that_did_not_change_is_told_as_unchanged_without_being_put_together(host, monkeypatch):
    commission, session = await _single(host, "sort the photos")
    built: list[int] = []
    build = host._board._build

    async def counted(*args: Any, **kwargs: Any) -> Any:
        built.append(1)
        return await build(*args, **kwargs)

    monkeypatch.setattr(host._board, "_build", counted)
    board = await host.board()
    revision = board["revision"]
    assert isinstance(revision, str) and revision and built == [1]

    # Asked again with the revision it has: nothing is read but the marks of a change.
    assert await host.board(known_revision=revision) == {"revision": revision, "unchanged": True}
    assert await host.board(known_revision=revision) == {"revision": revision, "unchanged": True}
    assert built == [1]
    # Without one, or with another, the board is given whole.
    assert (await host.board())["revision"] == revision and len(built) == 2
    assert (await host.board(known_revision="something else"))["loose"][0]["id"] == session.id
    # What was asked for is part of what is kept: another character's board is another.
    assert "unchanged" not in await host.board(lanlan_name="Mia", known_revision=revision)


async def test_the_revision_changes_with_whatever_the_board_shows_and_with_nothing_else(host, monkeypatch):
    commission, session = await _lead(host, "Budget app", "add a chart")
    built: list[int] = []
    build = host._board._build

    async def counted(*args: Any, **kwargs: Any) -> Any:
        built.append(1)
        return await build(*args, **kwargs)

    monkeypatch.setattr(host._board, "_build", counted)

    async def changed(revision: str) -> str:
        board = await host.board(known_revision=revision)
        assert "unchanged" not in board and board["revision"] != revision
        return board["revision"]

    revision = (await host.board())["revision"]
    # The commission is taken up: its state.
    await _working(host, commission.id)
    revision = await changed(revision)
    # A tool call: what it is doing.
    await host.ledger.append_events(commission.id, [("tool_start", {"tool_call_id": "t", "tool_name": "read", "args": {"file_path": "a.py"}})])
    revision = await changed(revision)
    # Something the board does not show was written: it was put together again, and is the same.
    count = len(built)
    await host.ledger.append_events(commission.id, [("text", {"text": "thinking"})])
    assert await host.board(known_revision=revision) == {"revision": revision, "unchanged": True}
    assert len(built) == count + 1
    assert await host.board(known_revision=revision) == {"revision": revision, "unchanged": True}
    assert len(built) == count + 1
    # Its card; what it used.
    await host.ledger.write_card(session.id, by="user", title="记账管家")
    revision = await changed(revision)
    await host.ledger.add_usage(session_id=session.id, commission_id=commission.id, tokens_in=10, tokens_out=1, context_tokens=11)
    revision = await changed(revision)
    # What its project is for, which is in a file and not in the ledger.
    await host.space.describe_project("Budget app", "A budget for the household.", by="user")
    revision = await changed(revision)
    # A template of the user's replaces what its preset is shown with.
    templates = host.paths.space().templates_dir()
    templates.mkdir(parents=True, exist_ok=True)
    (templates / "boss.md").write_text("---\nname: boss\ndescription: Leads.\nlabel: 大当家\n---\n", encoding="utf-8")
    revision = await changed(revision)
    assert _cards(await host.board())[session.id]["label"] == "大当家"
    # Another session.
    await _single(host, "sort the photos")
    await changed(revision)


async def test_what_the_marks_do_not_show_is_seen_once_the_board_is_no_longer_taken_as_unchanged(host, monkeypatch):
    await _single(host, "sort the photos")
    revision = (await host.board())["revision"]
    assert (await host.board(known_revision=revision)).get("unchanged") is True

    # The model was changed in the settings: no file of the host and no row of the ledger says so.
    async def other_model() -> dict[str, Any]:
        return {"model": "another-model", "api_key": "test-key"}

    monkeypatch.setattr(host, "_task_agent_model", other_model)
    assert (await host.board(known_revision=revision)).get("unchanged") is True
    monkeypatch.setattr("config.nekopower_settings.BOARD_REVISION_KEPT_SECONDS", 0.0)

    board = await host.board(known_revision=revision)

    assert board["revision"] != revision and board["loose"][0]["model"] == "another-model"


async def test_a_board_is_shown_without_a_model_when_none_is_configured_or_it_cannot_be_read(host, monkeypatch):
    _commission_made, session = await _single(host, "sort the photos")

    async def none() -> None:
        return None

    monkeypatch.setattr(host, "_task_agent_model", none)
    assert _cards(await host.board())[session.id]["model"] is None

    async def failing() -> None:
        raise OSError("the settings cannot be read")

    monkeypatch.setattr(host, "_task_agent_model", failing)
    assert _cards(await host.board())[session.id]["model"] is None


# ---------------------------------------------------------------------------
# The presets there are
# ---------------------------------------------------------------------------


async def test_the_presets_are_listed_with_what_each_is_and_the_wrong_ones_with_what_is_wrong(host):
    space = host.paths.space()
    for directory, name, more in (
        (space.templates_dir(), "doc-writer", "label: 文档猫\navatar: planner\nmemory: none\nmodel: agent\n"),
        (space.templates_dir(), "broken", "model: gpt-9\n"),
        (space.templates_dir("Budget app"), "tester", "read_only: true\n"),
    ):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: When {name} fits.\n{more}---\n", encoding="utf-8"
        )

    everywhere = await host.templates()
    here = await host.templates("Budget app")

    assert [item["name"] for item in everywhere] == ["boss", "worker", "scout", "inspector", "planner", "general", "broken", "doc-writer"]
    assert [item["name"] for item in here][-1] == "tester"
    keys = {"name", "label", "description", "avatar", "source", "read_only", "writes", "memory", "model", "valid", "error"}
    assert all(set(item) == keys for item in here)
    found = {item["name"]: item for item in here}
    assert found["inspector"] == {
        "name": "inspector", "label": "", "avatar": "inspector", "source": "builtin", "read_only": False,
        "writes": "none", "memory": "none", "model": "inherit", "valid": True, "error": "",
        "description": found["inspector"]["description"],
    }
    assert (found["scout"]["read_only"], found["scout"]["writes"], found["boss"]["model"]) == (True, "none", "agent")
    assert found["doc-writer"] == {
        "name": "doc-writer", "label": "文档猫", "description": "When doc-writer fits.", "avatar": "planner",
        "source": "global", "read_only": False, "writes": "inherit", "memory": "none", "model": "agent",
        "valid": True, "error": "",
    }
    assert (found["tester"]["source"], found["tester"]["read_only"], found["tester"]["writes"]) == ("project", True, "none")
    assert (found["broken"]["valid"], found["broken"]["error"]) == (False, "model is one of agent, inherit, not 'gpt-9'")


# ---------------------------------------------------------------------------
# The form of a new session, filled in
# ---------------------------------------------------------------------------

FORM = {
    "name": "Monthly reports",
    "title": "Report keeper",
    "preset": "lead",
    "duty": "the monthly report page and its exports",
    "description": "Builds the household's monthly spending report.",
    "rules": "- Keep the date format as year-month-day.\n- Do not touch the raw data folder.",
}


async def test_the_form_of_a_new_session_is_filled_in_by_one_call(host, tmp_path):
    workdir = tmp_path / "reports"
    (workdir / "src").mkdir(parents=True)
    (workdir / "export.py").write_bytes(b"print('do not read me')\n" * 100)
    (workdir / "README.md").write_bytes(b"secret words")
    host._decision_llm.answer = dict(FORM)

    form = await host.draft_session("我想要一只专门管月报页面和导出的使魔", workdir=str(workdir), lanlan_name="Lan")

    assert form == {**FORM, "preset": "boss"}
    ((tier, messages),) = host._decision_llm.calls
    assert tier == TIER_SESSION_DRAFT
    system, user = (message["content"] for message in messages)
    # What the free tier knows a request of the host by.
    assert system.startswith("You are an agentic automation assessment agent, ")
    # The same words the review is told of a card's lines in, and every preset there is, by the names a model reads.
    from config.prompts import prompts_nekopower_presets as presets

    for guide in (presets.CARD_TITLE_GUIDE, presets.CARD_DUTY_GUIDE, presets.CARD_DESCRIPTION_GUIDE):
        assert guide in system
    # The presets it may name: the lead of a project, which most are, and by their lines the ones
    # that read only, check or plan. Not a worker, which another task agent starts, nor a session of one errand.
    assert "preset: which kind of task agent it is. lead for one that does the work itself" in system
    for name in ("scout", "inspector", "planner"):
        assert f"\n- {name}: " in system
    for name in ("lead", "boss", "worker", "general"):
        assert f"\n- {name}: " not in system
    # What the user wrote, and of the folder names and sizes: no file was opened.
    assert "What the user wrote:\n我想要一只专门管月报页面和导出的使魔\n\n" in user
    assert f"Folder: {workdir}\n" in user
    assert user.endswith("\nexport.py (2.3 KB)\nREADME.md (12 B)\nsrc/")
    assert "do not read me" not in user and "secret words" not in user
    # Nothing was made of it: no session, no project, no file of the space.
    assert await host.ledger.find_sessions() == [] and not host.paths.space().root.exists()


async def test_a_form_for_a_session_without_a_folder_says_so_and_lists_nothing(host):
    host._decision_llm.answer = dict(FORM)

    await host.draft_session("a task agent for the reports")

    user = host._decision_llm.calls[0][1][1]["content"]
    assert user == "What the user wrote:\na task agent for the reports\n\nFolder: (none chosen)\n"


async def test_a_folder_that_holds_nothing_or_cannot_be_listed_is_told_as_that(host, tmp_path):
    host._decision_llm.answer = dict(FORM)

    await host.draft_session("a task agent for the reports", workdir=str(tmp_path / "missing"))

    user = host._decision_llm.calls[0][1][1]["content"]
    assert user.endswith("\n(nothing in it could be listed)")


async def test_the_preset_of_a_form_is_one_there_is(host):
    space = host.paths.space().templates_dir()
    space.mkdir(parents=True)
    (space / "doc-writer.md").write_text("---\nname: doc-writer\ndescription: When documents are written.\n---\n", encoding="utf-8")
    (space / "broken.md").write_text("---\nname: broken\ndescription: x\nnope: 1\n---\n", encoding="utf-8")

    async def preset(answered: Any) -> str:
        host._decision_llm.answer = {**FORM, "preset": answered}
        return (await host.draft_session("a task agent for the reports"))["preset"]

    assert await preset("scout") == "scout"
    assert await preset("doc-writer") == "doc-writer"
    # The lead by the name a model reads, and whatever is no preset, missing or wrong: the lead of a project.
    assert await preset("lead") == "boss"
    for answered in ("wizard", "", None, 7, "broken", "boss", "worker", "general"):
        assert await preset(answered) == "boss"
    system = host._decision_llm.calls[-1][1][0]["content"]
    assert "\n- doc-writer: When documents are written." in system and "broken" not in system


@pytest.mark.parametrize("wrong", ["a file", "a folder that is a file"])
async def test_no_form_is_filled_in_while_the_preset_it_would_name_cannot_be_used(host, wrong):
    templates = host.paths.space().templates_dir()
    if wrong == "a file":
        templates.mkdir(parents=True)
        (templates / "boss.md").write_text("---\nname: boss\ndescription: Leads.\ntools: [bash]\n---\n", encoding="utf-8")
    else:
        templates.parent.mkdir(parents=True)
        templates.write_text("not a folder", encoding="utf-8")
    host._decision_llm.answer = dict(FORM)

    with pytest.raises(DraftError) as raised:
        await host.draft_session("a task agent for the reports")

    # Said before a model is asked, with what is wrong: the session could not be made with it.
    assert raised.value.reason == "no_preset" and "no_preset" in DRAFT_REASONS
    assert ("unknown fields: tools" if wrong == "a file" else "cannot be read") in str(raised.value)
    assert host._decision_llm.calls == []


async def test_a_template_task_agents_are_not_told_of_is_not_offered_for_a_new_session_either(host, monkeypatch):
    from nekopower.host.templates import builtin_templates
    from utils.tokenize import count_tokens

    builtin = sum(count_tokens(item.description) for item in builtin_templates().values())
    monkeypatch.setattr("config.nekopower_settings.TEMPLATE_DESCRIPTIONS_TOTAL_MAX_TOKENS", builtin + 5)
    templates = host.paths.space().templates_dir()
    templates.mkdir(parents=True)
    (templates / "doc-writer.md").write_text(
        "---\nname: doc-writer\ndescription: When the documents of a project are to be written or brought up to date.\n---\n",
        encoding="utf-8",
    )
    host._decision_llm.answer = {**FORM, "preset": "doc-writer"}

    form = await host.draft_session("a task agent for the reports")

    listed = {item["name"]: item for item in await host.templates()}
    assert listed["doc-writer"]["valid"] is True and listed["doc-writer"]["error"].startswith("not offered")
    assert "doc-writer" not in host._decision_llm.calls[0][1][0]["content"] and form["preset"] == "boss"


async def test_what_comes_back_is_cut_to_its_caps_and_named_as_a_folder_can_be(host):
    from config import nekopower_settings as settings
    from utils.tokenize import count_tokens

    host._decision_llm.answer = {
        "name": 'Reports: "monthly" / yearly?',
        "title": "title " * 50,
        "preset": "lead",
        "duty": "takes\nthis " * 100,
        "description": "about " * 100,
        "rules": "- rule\n" * 400,
    }

    form = await host.draft_session("a task agent for the reports")

    assert form["name"] == "Reports monthly yearly"
    assert count_tokens(form["title"]) == settings.CARD_TITLE_MAX_TOKENS
    assert count_tokens(form["duty"]) == settings.CARD_DUTY_MAX_TOKENS and "\n" not in form["duty"]
    assert count_tokens(form["description"]) == settings.SPACE_PROJECT_DESCRIPTION_MAX_TOKENS
    assert count_tokens(form["rules"]) <= settings.SESSION_DRAFT_RULES_MAX_TOKENS
    # The rules keep their lines.
    assert form["rules"].startswith("- rule\n- rule\n")
    # What is not a text is left empty.
    host._decision_llm.answer = {**FORM, "title": 5, "description": None, "rules": ["- x"]}
    form = await host.draft_session("a task agent for the reports")
    assert (form["title"], form["description"], form["rules"]) == ("", "", "")


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (RuntimeError("the provider said no"), "failed"),
        ("I would call it Reports.", "unreadable"),
        ("", "unreadable"),
        ('{"name": "Reports", "duty": "the re', "unreadable"),
        ({**FORM, "name": ""}, "incomplete"),
        ({**FORM, "name": '  /:*?"  '}, "incomplete"),
        ({**FORM, "name": "CON"}, "incomplete"),
        ({**FORM, "duty": "   "}, "incomplete"),
        ({"title": "Report keeper"}, "incomplete"),
    ],
)
async def test_a_form_that_cannot_be_filled_in_is_an_error_with_its_reason_and_never_half_a_form(host, answer, reason):
    host._decision_llm.answer = answer

    with pytest.raises(DraftError) as raised:
        await host.draft_session("a task agent for the reports")

    assert raised.value.reason == reason and reason in DRAFT_REASONS
    assert len(host._decision_llm.calls) == 1


async def test_nothing_is_asked_of_a_model_for_nothing_or_without_one(host, tmp_path):
    for text in ("", "   \n"):
        with pytest.raises(DraftError) as raised:
            await host.draft_session(text)
        assert raised.value.reason == "empty"
    assert host._decision_llm.calls == []

    class Decision:
        """Stands in for the decision model of a host that was given one, and so has no model calls of its own."""

    bare = make_host(tmp_path / "bare", None, decision=Decision())
    try:
        with pytest.raises(DraftError) as raised:
            await bare.draft_session("a task agent for the reports")
        assert raised.value.reason == "no_model"
    finally:
        await bare.shutdown()


async def test_a_call_that_takes_too_long_is_given_up(host, monkeypatch):
    monkeypatch.setattr("config.nekopower_settings.SESSION_DRAFT_SECONDS", 0.05)
    host._decision_llm.gate = asyncio.Event()
    host._decision_llm.answer = dict(FORM)

    with pytest.raises(DraftError) as raised:
        await host.draft_session("a task agent for the reports")

    assert raised.value.reason == "timeout"


async def test_what_is_sent_to_fill_in_a_form_stays_within_its_cap(host, tmp_path):
    from config import nekopower_settings as settings
    from utils.tokenize import count_tokens

    workdir = tmp_path / "many"
    workdir.mkdir()
    for number in range(200):
        (workdir / f"a file with a rather long name number {number:03d}.txt").write_text("x", encoding="utf-8")
    # And templates of the user's, more than the form is told of.
    templates = host.paths.space().templates_dir()
    templates.mkdir(parents=True)
    for number in range(12):
        (templates / f"helper-{number:02d}.md").write_text(
            f"---\nname: helper-{number:02d}\ndescription: {'When help of this particular kind is wanted. ' * 9}\n---\n",
            encoding="utf-8",
        )
    host._decision_llm.answer = {**FORM, "preset": "helper-11"}

    form = await host.draft_session("words about the task agent " * 2000, workdir=str(workdir))

    system, user = (message["content"] for message in host._decision_llm.calls[0][1])
    # The presets it is told of have a cap of their own; one it was not told of is not what comes back.
    told = [line for line in system.splitlines() if line.startswith("- helper-")]
    assert 0 < len(told) < 12 and "- helper-11: " not in system
    assert count_tokens("\n".join(line for line in system.splitlines() if line.startswith("- "))) <= settings.SESSION_DRAFT_PRESETS_MAX_TOKENS
    assert form["preset"] == "boss"
    assert all(item["valid"] for item in await host.templates())
    assert count_tokens(system) + count_tokens(user) <= settings.SESSION_DRAFT_INPUT_MAX_TOKENS == 2000
    # Of the folder the first entries by name, and no more than are looked at.
    assert "number 000.txt" in user and f"number {settings.SESSION_DRAFT_LISTING_ENTRIES:03d}.txt" not in user
    assert settings.SESSION_DRAFT_OUTPUT_MAX_TOKENS == 500


async def test_the_whole_of_what_is_sent_is_capped_whatever_its_parts_are(host, monkeypatch):
    from config import nekopower_settings as settings
    from utils.tokenize import count_tokens

    # A part that outgrows the room it was meant to have.
    monkeypatch.setattr(settings, "SESSION_DRAFT_TEXT_MAX_TOKENS", 50_000)
    host._decision_llm.answer = dict(FORM)

    await host.draft_session("words about the task agent " * 2000)

    system, user = (message["content"] for message in host._decision_llm.calls[0][1])
    assert count_tokens(system) + count_tokens(user) <= settings.SESSION_DRAFT_INPUT_MAX_TOKENS
    assert count_tokens(user) > 1000


def test_an_entry_of_a_folder_that_cannot_be_looked_at_is_listed_by_its_name(tmp_path, monkeypatch):
    import os

    class Entry:
        def __init__(self, name: str, broken: bool = False) -> None:
            self.name, self._broken = name, broken

        def is_dir(self) -> bool:
            if self._broken:
                raise PermissionError(self.name)
            return False

        def stat(self) -> Any:
            return type("Stat", (), {"st_size": 10})()

    class Scan(list):
        def __enter__(self) -> Any:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr(os, "scandir", lambda path: Scan([Entry("locked"), Entry("a.txt"), Entry("b", broken=True)]))

    assert list_folder("anywhere", 10) == ["a.txt (10 B)", "b", "locked (10 B)"]


def test_a_folder_is_listed_by_name_and_size_and_one_that_is_not_there_gives_nothing(tmp_path):
    (tmp_path / "b.txt").write_bytes(b"x" * 1536)
    (tmp_path / "A").mkdir()
    (tmp_path / "c.bin").write_bytes(b"x" * (3 * 1024 * 1024))

    assert list_folder(str(tmp_path), 10) == ["A/", "b.txt (1.5 KB)", "c.bin (3.0 MB)"]
    assert list_folder(str(tmp_path), 2) == ["A/", "b.txt (1.5 KB)"]
    assert list_folder(str(tmp_path / "missing"), 10) == []


def test_the_form_is_filled_in_on_the_summary_model_under_a_name_of_its_own(monkeypatch):
    import utils.config_manager as config_manager
    import utils.llm_client as llm_client
    import utils.token_tracker as tracker
    from nekopower.host import decision

    named: list[str] = []
    monkeypatch.setattr(tracker, "set_call_type", named.append)
    slots: list[str] = []
    budgets: list[int] = []

    class Config:
        def get_model_api_config(self, slot: str) -> dict[str, Any]:
            slots.append(slot)
            return {"model": f"{slot}-model", "base_url": "http://127.0.0.1:9/v1", "api_key": "test-key"}

    class Client:
        async def ainvoke(self, messages: Any) -> Any:
            return type("Response", (), {"content": "ok"})()

    async def create(**kwargs: Any) -> Any:
        budgets.append(kwargs["max_completion_tokens"])
        return Client()

    monkeypatch.setattr(config_manager, "get_config_manager", lambda **kwargs: Config())
    monkeypatch.setattr(llm_client, "create_chat_llm_async", create)
    llm = decision.ConfiguredDecisionLLM()

    asyncio.run(llm.complete(TIER_SESSION_DRAFT, []))

    # The model it asks is the one the setting names, with the budget of this call, counted under its own name.
    assert named == ["task_agent_session_draft"] and slots == ["memory"] and budgets == [500]
    monkeypatch.setattr("config.nekopower_settings.SESSION_DRAFT_OUTPUT_MAX_TOKENS", 321)
    monkeypatch.setattr("config.nekopower_settings.SESSION_DRAFT_MODEL_SLOT", "emotion")
    asyncio.run(llm.complete(TIER_SESSION_DRAFT, []))
    assert (slots[-1], budgets[-1]) == ("emotion", 321)
    assert TIER_SESSION_DRAFT in decision._SUMMARY_TIERS
