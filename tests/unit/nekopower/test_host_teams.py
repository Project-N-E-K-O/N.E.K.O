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

"""A leader's team, the inspector before a landing, and members the user adds (``tree_work.py``).

A leader starts several task agents for one commission with ``start_team``
and keeps the commission: the user is asked once, those that change files
work in git worktrees whose changes the host lands, and the leader reports
once. An inspector may look at what was changed before it lands. The last
tests run git itself, where the others use the stand-in of
``test_host_tree_work.py``.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from config import nekopower_settings as settings
from nekopower.host.reports import describe_permission
from tests.unit.nekopower.fakes import ScriptedLLM, call, finish, say
from tests.unit.nekopower.host_fakes import FakeDecisionLLM, fake_model, make_host, scripted_runtime, wait_until
from tests.unit.nekopower.test_host_tree_work import (  # noqa: F401 - the fixtures are used by name
    CHECK,
    FIRST,
    SECOND,
    _by_role,
    _dispatch,
    _events,
    _gave_way,
    _hand_over,
    _one_after_the_other,
    _report,
    _said_to,
    _session_of,
    _shop,
    _write,
    hosts,
    wait_status,
)

pytestmark = pytest.mark.unit

# A turn that ends a run without a report. (A report ends the run it is made in by itself.)
END = [say("that is all"), finish("stop")]
TEAM = "redo the footer of the shop site"
PARTS = [
    {"name": "links", "description": "repairs the links", "brief": "Repair the footer links. PART-LINKS"},
    {"name": "styles", "description": "tidies the styles", "brief": "Tidy the stylesheet. PART-STYLES"},
    {"name": "fonts", "preset": "scout", "description": "finds the fonts", "brief": "Find which fonts are used. PART-FONTS"},
]


class TeamLead:
    """A leader's model: starts the team, ends its turn until every member handed back, then reports once."""

    def __init__(self, parts: list[dict[str, Any]], expect: int | None = None, **more: Any) -> None:
        self.parts = parts
        self.expect = len(parts) if expect is None else expect
        self.more = more
        self.requests: list[list[dict[str, Any]]] = []
        self.started = False
        self.reported = False

    async def astream(self, messages: list[dict[str, Any]], **_overrides: Any):  # noqa: ANN201
        self.requests.append(copy.deepcopy(messages))
        back = json.dumps(messages).count("[child task agent ")
        if not self.started:
            self.started = True
            steps = [call("start_team", {"parts": self.parts, **self.more}), finish("tool_calls")]
        elif back >= self.expect and not self.reported:
            self.reported = True
            steps = [call("report", {"status": "ready", "summary": "the footer is redone"}), finish("tool_calls")]
        else:
            steps = [say("waiting for the team"), finish("stop")]
        for step in steps:
            yield step

    async def aclose(self) -> None:
        return None


def _team_scripts(go: asyncio.Event | None = None) -> dict[str, ScriptedLLM]:
    async def wait() -> None:
        if go is not None:
            await go.wait()

    return {
        "PART-LINKS": ScriptedLLM([[wait, *_write("links.txt", "repaired")], _report("links repaired")]),
        "PART-STYLES": ScriptedLLM([[wait, *_write("styles.css", "tidy")], _report("styles tidied")]),
        "PART-FONTS": ScriptedLLM([[wait, *_report("two fonts")]]),
    }


async def _asked(host: Any, commission_id: str) -> int:
    events = await host.ledger.events(commission_id)
    return len([event for event in events if event.type == "status" and event.payload.get("to") == "waiting_user"])


# ---------------------------------------------------------------------------
# A team
# ---------------------------------------------------------------------------


async def test_a_leader_starts_a_team_once_the_user_allowed_it_and_reports_once(hosts, tmp_path):
    go = asyncio.Event()
    lead = TeamLead(PARTS)
    members = _team_scripts(go)
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(lead, members), allow=False)

    receipt = await _dispatch(host, TEAM)
    waiting = await wait_status(host, receipt.commission_id, "waiting_user")
    question = waiting.question()
    assert (question["kind"], question["category"]) == ("permission", "open_session")
    # Asked once, for those that change files, with who they are and what becomes of what they change.
    assert question["detail"]["members"] == ["worker: repairs the links", "worker: tidies the styles"]
    assert question["detail"]["parts"] == [
        {"preset": "worker", "description": "repairs the links"}, {"preset": "worker", "description": "tidies the styles"},
    ]
    assert (question["detail"]["count"], question["detail"]["tree"], question["detail"]["tool"]) == (2, True, "start_team")
    asked = describe_permission(question, "en")
    assert asked.startswith("form a team for this task: 2 familiars change files at the same time")
    assert "worker: repairs the links; worker: tidies the styles" in asked
    in_chinese = describe_permission(question, "zh")
    assert "组一支队伍：2 只使魔同时改文件" in in_chinese and "合回项目目录" in in_chinese and "猫爪" not in in_chinese
    assert await host.ledger.select(parent_id=receipt.commission_id) == []

    assert await host.answer(receipt.commission_id, "yes", choice="once")
    await wait_until(lambda: all(len(script.requests) == 1 for script in members.values()), what="the team being at work")
    children = {child.name: child for child in await host.ledger.select(parent_id=receipt.commission_id)}
    assert sorted(children) == ["fonts", "links", "styles"]
    seats = {name: (await _session_of(host, child.id)).seat for name, child in children.items()}
    presets = {name: (await _session_of(host, child.id)).preset for name, child in children.items()}
    assert seats == {"links": "tree", "styles": "tree", "fonts": ""}
    assert presets == {"links": "worker", "styles": "worker", "fonts": "scout"}
    # Those that change files have a worktree each, and none of them waits for another.
    assert sorted(tree.commission_id for tree in await host.ledger.trees()) == sorted(
        [children["links"].id, children["styles"].id]
    )
    assert host.task_agents.queued == [] and host.task_agents._writes_in == {}

    async def team_formed() -> list[dict[str, Any]]:
        return await _events(host, receipt.commission_id, "team_formed")

    await wait_until(team_formed, what="the team being put on record")
    (formed,) = await team_formed()
    assert [(item["name"], item["preset"], item["writes"]) for item in formed["members"]] == [
        ("links", "worker", True), ("styles", "worker", True), ("fonts", "scout", False),
    ]
    # The board draws them as one team, by the commission the leader is at.
    cards = (await host.board())["projects"][0]["members"]
    assert {card["team"] for card in cards} == {receipt.commission_id} and len(cards) == 4
    assert [card["leader"] for card in cards] == [True, False, False, False]

    go.set()
    done = await wait_status(host, receipt.commission_id, "done")
    # (Each of the team is done a moment before its worktree is removed.)
    await wait_until(lambda: not host.task_agents._landings, what="what landed the team's changes being through")
    assert done.result_summary == "the footer is redone" and done.session_id == waiting.session_id
    assert (workdir / "links.txt").read_text() == "repaired" and (workdir / "styles.css").read_text() == "tidy"
    assert len(trees.made) == 2 and sorted(trees.removed) == sorted(path.name for path in trees.made)
    assert await host.ledger.trees() == []
    # What each handed back came to the leader, with what was merged.
    told = _said_to(lead)
    assert told.count("[child task agent ") >= 3 and "Merged into the project's directory" in told
    assert await _asked(host, receipt.commission_id) == 1
    assert await host.permission_store.mode("open_session") == "first_ask"
    entries = (await host.timeline(project="Shop site"))["entries"]
    team = next(entry for entry in entries if entry["kind"] == "team_formed")
    assert [item["name"] for item in team["detail"]["members"]] == ["links", "styles", "fonts"]
    assert team["detail"]["inspect"] is False


async def test_a_team_of_one_commission_is_asked_about_once_however_often_it_is_added_to(hosts, tmp_path):
    def start(part: dict[str, Any]) -> list[Any]:
        return [call("start_team", {"parts": [part]}), finish("tool_calls")]

    lead = ScriptedLLM([start(PARTS[0]), END, start(PARTS[1]), END, _report("the footer is redone")])
    members = _team_scripts()
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(lead, members), allow=False)

    receipt = await _dispatch(host, TEAM)
    await wait_status(host, receipt.commission_id, "waiting_user")
    assert await host.answer(receipt.commission_id, "yes", choice="once")
    done = await wait_status(host, receipt.commission_id, "done")

    assert done.result_summary == "the footer is redone" and await _asked(host, receipt.commission_id) == 1
    assert (workdir / "links.txt").exists() and (workdir / "styles.css").exists()
    assert len(await _events(host, receipt.commission_id, "team_formed")) == 2
    # Allowed for that commission, and no longer once it is over.
    assert host.task_agents._team_allowed == set() and host.task_agents._teams == set()


@pytest.mark.parametrize("how", ["deny", "setting"])
async def test_a_team_the_user_does_not_allow_is_not_started(hosts, tmp_path, how):
    lead = TeamLead(PARTS)
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(lead, _team_scripts()), allow=False)
    if how == "setting":
        await host.permission_store.set_mode("open_session", "deny")

    receipt = await _dispatch(host, TEAM)
    if how == "deny":
        await wait_status(host, receipt.commission_id, "waiting_user")
        assert await host.answer(receipt.commission_id, "no", choice="deny")
    await wait_status(host, receipt.commission_id, "done")

    assert "the user did not allow task agents that change files to be started for this" in _said_to(lead)
    assert await host.ledger.select(parent_id=receipt.commission_id) == [] and trees.made == []
    assert await _events(host, receipt.commission_id, "team_formed") == []


async def test_where_no_worktree_can_be_made_a_team_only_reads(hosts, tmp_path):
    lead = ScriptedLLM([
        [call("start_team", {"parts": PARTS}), finish("tool_calls")],
        [call("start_team", {"parts": [PARTS[2]]}), finish("tool_calls")],
        END, _report("two fonts, and nothing else could be done"),
    ])
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(lead, _team_scripts()), allow=False)
    trees.unusable = "not_a_repository"

    receipt = await _dispatch(host, TEAM)
    await wait_status(host, receipt.commission_id, "done")

    told = _said_to(lead)
    assert "task agents cannot change files side by side in this project" in told and "will not do" in told
    # Nobody was asked: nothing that changes files was to be started.
    assert await _asked(host, receipt.commission_id) == 0
    (child,) = await host.ledger.select(parent_id=receipt.commission_id)
    assert (child.name, child.status) == ("fonts", "done") and trees.made == []


async def test_a_team_has_no_more_at_work_than_it_may_and_parts_must_say_what_they_cover(hosts, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEAM_MAX_WORKING", 2)
    lead = ScriptedLLM([
        [call("start_team", {"parts": PARTS}), finish("tool_calls")],
        [call("start_team", {"parts": [{"description": "", "brief": "do it"}]}), finish("tool_calls")],
        [call("start_team", {"parts": [{"description": "x", "brief": "y", "preset": "wizard"}]}), finish("tool_calls")],
        END,
    ])
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(lead, _team_scripts()))
    receipt = await _dispatch(host, TEAM)
    await wait_status(host, receipt.commission_id, "done")

    told = _said_to(lead)
    assert "a team has 2 task agents at work at the most, and 0 of this commission's are" in told
    assert "part 1 has no description or no brief" in told and "unknown preset" in told
    assert await host.ledger.select(parent_id=receipt.commission_id) == []


async def test_what_a_leader_starts_with_subagent_counts_towards_how_many_work_for_it(hosts, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEAM_MAX_WORKING", 1)
    hold = asyncio.Event()

    async def wait() -> None:
        await hold.wait()

    lead = ScriptedLLM([
        [call("start_team", {"parts": [PARTS[2]]}), finish("tool_calls")],
        [call("subagent", {"description": "colours", "prompt": "Which colours? PART-COLOURS", "preset": "scout"}),
         finish("tool_calls")],
        END, _report("two fonts"),
    ])
    members = {"PART-FONTS": ScriptedLLM([[wait, *_report("two fonts")]]), "PART-COLOURS": ScriptedLLM([_report("red")])}
    host, _trees, _workdir = await _shop(hosts, tmp_path, _by_role(lead, members))

    receipt = await _dispatch(host, TEAM)
    await wait_until(lambda: len(lead.requests) >= 3, what="the leader having been refused another")
    hold.set()
    await wait_status(host, receipt.commission_id, "done")

    assert "a team has 1 task agents at work at the most, and 1 of this commission's are" in _said_to(lead)
    assert [child.name for child in await host.ledger.select(parent_id=receipt.commission_id)] == ["fonts"]
    assert members["PART-COLOURS"].requests == []


async def test_only_a_leader_has_the_tool_that_starts_a_team(hosts, tmp_path):
    specs: list[Any] = []
    leader_llm = ScriptedLLM(_hand_over())

    def pick(spec: Any) -> Any:
        specs.append(spec)
        return leader_llm if "hand_over" not in spec.disabled_tools else ScriptedLLM([_report("header fixed")])

    host, _trees, _workdir = await _shop(hosts, tmp_path, pick)
    receipt = await _dispatch(host, FIRST)
    await wait_status(host, receipt.commission_id, "done")

    leader, member = specs
    assert "start_team" not in leader.disabled_tools and "hand_over" not in leader.disabled_tools
    assert {"start_team", "hand_over"} <= set(member.disabled_tools)
    with pytest.raises(RuntimeError, match="only the lead of a project starts a team"):
        await host.task_agents._cb_start_team(member.session_id, {"parts": PARTS})


async def test_a_task_agent_of_a_team_for_which_no_worktree_can_be_made_is_not_started(hosts, tmp_path):
    from nekopower.host.worktrees import WorktreeUnavailable

    lead = TeamLead(PARTS[:1])
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(lead, _team_scripts()))
    trees.fail_create = WorktreeUnavailable("disk_space", "1 bytes free, 2 needed")

    receipt = await _dispatch(host, TEAM)
    await wait_status(host, receipt.commission_id, "done")

    (child,) = await host.ledger.select(parent_id=receipt.commission_id)
    assert child.status == "failed" and "no directory of its own could be made for it" in child.status_reason
    # It changed nothing in the project's directory, and its leader was told.
    assert not (workdir / "links.txt").exists() and "1 bytes free, 2 needed" in _said_to(lead)


async def test_a_leader_is_told_which_of_its_team_got_no_worktree_after_all(hosts, tmp_path):
    from nekopower.host.worktrees import WorktreeUnavailable

    go = asyncio.Event()
    lead = TeamLead(PARTS[:2])
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(lead, _team_scripts(go)))
    # There was room when the leader asked; the disk is full by the time the second worktree is made.
    trees.fail_create = WorktreeUnavailable("disk_space", "1 bytes free, 2 needed")
    trees.fail_creates = {2}

    receipt = await _dispatch(host, TEAM)

    async def formed() -> list[dict[str, Any]]:
        return await _events(host, receipt.commission_id, "team_formed")

    await wait_until(formed, what="the team being formed")
    ((team,),) = (await formed(),)
    # The one that got none is not of the team, and the leader was told so with what start_team answered.
    (member,) = team["members"]
    (tree,) = await host.ledger.trees()
    assert tree.commission_id == member["commission_id"]
    await wait_until(lambda: len(lead.requests) >= 2, what="the leader reading what start_team answered")
    answered = [
        message["content"] for request in lead.requests for message in request
        if message.get("role") == "tool" and "Started: " in str(message.get("content"))
    ]
    assert answered and f"Started: {member['name']}." in answered[0]
    assert " Not started: " in answered[0] and "1 bytes free, 2 needed" in answered[0]
    go.set()
    await wait_status(host, receipt.commission_id, "done")


@pytest.mark.parametrize("restarted", [False, True])
async def test_a_leader_cannot_report_its_commission_done_while_its_team_is_at_work(hosts, tmp_path, restarted):
    go = asyncio.Event()

    async def wait() -> None:
        await go.wait()

    lead = ScriptedLLM([
        [call("start_team", {"parts": PARTS[:1]}), finish("tool_calls")],
        # Too early: the part is still being worked on.
        _report("the footer is redone"),
        END,
        _report("the footer is redone, now that the links are repaired"),
    ])
    members = {"PART-LINKS": ScriptedLLM([[wait, *_write("links.txt", "repaired")], _report("links repaired")])}
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(lead, members))
    if restarted:
        # As where the host was started anew since the team was: it is told by what is on record.
        start = host.task_agents._cb_start_team

        async def and_forgets(session_id: str, payload: dict[str, Any]) -> Any:
            started = await start(session_id, payload)
            host.task_agents._teams.clear()
            return started

        host.task_agents._cb_start_team = and_forgets

    receipt = await _dispatch(host, TEAM)
    await wait_until(lambda: len(lead.requests) >= 3, what="the leader having been refused its report")
    # Not done, and nobody was stopped: the part goes on.
    assert (await host.ledger.get(receipt.commission_id)).status == "working"
    (child,) = await host.ledger.select(parent_id=receipt.commission_id)
    assert child.status == "working"
    told = _said_to(lead)
    assert "not reported: 1 of the task agents you started for this commission are still at work (links)" in told

    go.set()
    done = await wait_status(host, receipt.commission_id, "done")
    assert done.result_summary == "the footer is redone, now that the links are repaired"
    assert (workdir / "links.txt").read_text() == "repaired"
    assert (await host.ledger.get(child.id)).status == "done"


async def test_a_leader_that_gives_its_commission_up_stops_its_team(hosts, tmp_path):
    hold = asyncio.Event()

    async def wait() -> None:
        await hold.wait()

    lead = ScriptedLLM([
        [call("start_team", {"parts": PARTS[:1]}), finish("tool_calls")],
        _report("it cannot be done: the footer is generated elsewhere", status="blocked"),
    ])
    members = {"PART-LINKS": ScriptedLLM([[wait, *_write("links.txt", "repaired")], _report("links repaired")])}
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(lead, members))

    receipt = await _dispatch(host, TEAM)
    stuck = await wait_status(host, receipt.commission_id, "blocked")
    (child,) = await host.ledger.select(parent_id=receipt.commission_id)
    await wait_status(host, child.id, "cancelled")
    assert stuck.result_summary.startswith("it cannot be done") and not (workdir / "links.txt").exists()
    hold.set()


async def test_a_team_whose_changes_wait_for_the_users_word_is_not_done_again(hosts, tmp_path):
    lead = TeamLead(PARTS[:2])
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(lead, _team_scripts()))
    await host.set_landing_settings(mode="ask")

    receipt = await _dispatch(host, TEAM)
    done = await wait_status(host, receipt.commission_id, "done")

    # Each part is done and waits for the user: the leader is told that nothing is wrong with it.
    assert sorted((tree.state, tree.reason) for tree in await host.ledger.trees()) == [("held", "ask")] * 2
    assert trees.merged == [] and not (workdir / "links.txt").exists()
    children = await host.ledger.select(parent_id=receipt.commission_id)
    assert sorted(child.status for child in children) == ["blocked", "blocked"]
    told = _said_to(lead)
    last = json.dumps(lead.requests[-1])
    assert last.count("The work is done, and nothing is wrong with it.") == 2
    assert last.count("Do not do this work again and start nobody anew for it") == 2
    assert "try again, discard" not in told and done.result_summary == "the footer is redone"
    # Started once each: nobody did a part a second time.
    assert len(children) == 2 and len(await _events(host, receipt.commission_id, "team_formed")) == 1

    # Their commissions were not put down as stopped when the leader's was done: each is done
    # once the user has what it changed landed.
    for tree in await host.ledger.trees():
        assert (await host.land_worktree(tree.id))["status"] == "landed"
        assert (await wait_status(host, tree.commission_id, "done")).status == "done"
    assert (workdir / "links.txt").exists() and (workdir / "styles.css").exists()
    assert await host.ledger.trees() == []


async def test_a_leader_is_reminded_once_when_its_team_is_over_and_nothing_told_it_so(hosts, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEAM_STALL_SECONDS", 0.2)
    hold = asyncio.Event()

    async def wait() -> None:
        await hold.wait()

    lead = ScriptedLLM([
        [call("start_team", {"parts": [PARTS[2]]}), finish("tool_calls")], END,
        _report("nothing came of it: the scout was stopped"),
    ])
    members = {"PART-FONTS": ScriptedLLM([[wait, *_report("two fonts")]])}
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(lead, members))

    receipt = await _dispatch(host, TEAM)
    await wait_until(lambda: len(members["PART-FONTS"].requests) == 1, what="the scout being at work")
    await wait_until(
        lambda: getattr(host.task_agents._sessions.get(receipt.commission_id), "idle", False),
        what="the leader having ended its turn, to wait for its team",
    )
    (child,) = await host.ledger.select(parent_id=receipt.commission_id)
    # Stopped by the user: no report of its own tells the leader.
    assert await host.abort(child.id)

    done = await wait_status(host, receipt.commission_id, "done")
    assert done.result_summary == "nothing came of it: the scout was stopped"
    told = _said_to(lead)
    assert "Every task agent you started for this commission is over" in told and "fonts (cancelled)" in told
    assert await _events(host, receipt.commission_id, "stall_reminded") == [{"members": 1}]
    hold.set()


@pytest.mark.parametrize("why", ["another is still at work", "the leader is in the middle of a turn"])
async def test_a_leader_is_not_reminded_while_its_team_or_itself_is_at_work(hosts, tmp_path, monkeypatch, why):
    monkeypatch.setattr(settings, "TEAM_STALL_SECONDS", 0.2)
    hold, busy = asyncio.Event(), asyncio.Event()

    async def wait() -> None:
        await hold.wait()

    async def in_a_turn() -> None:
        await busy.wait()

    colours = {"name": "colours", "preset": "scout", "description": "finds the colours", "brief": "Which colours? PART-COLOURS"}
    if why == "another is still at work":
        lead: Any = TeamLead([PARTS[2], colours], expect=1)
    else:
        lead = ScriptedLLM([
            [call("start_team", {"parts": [PARTS[2]]}), finish("tool_calls")],
            [in_a_turn, say("the fonts could not be found out"), finish("stop")],
        ])
    members = {
        "PART-FONTS": ScriptedLLM([[wait, *_report("two fonts")]]),
        "PART-COLOURS": ScriptedLLM([[in_a_turn, *_report("red and white")]]),
    }
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(lead, members))

    receipt = await _dispatch(host, TEAM)
    await wait_until(lambda: len(members["PART-FONTS"].requests) == 1, what="the scout being at work")
    if why == "another is still at work":
        await wait_until(
            lambda: getattr(host.task_agents._sessions.get(receipt.commission_id), "idle", False),
            what="the leader having ended its turn, to wait for its team",
        )
    else:
        await wait_until(lambda: len(lead.requests) == 2, what="the leader being in the middle of its turn")
    fonts = next(child for child in await host.ledger.select(parent_id=receipt.commission_id) if child.name == "fonts")
    assert await host.abort(fonts.id)
    # (The watch that its end started has looked, and is through.)
    watches = host.task_agents._stall_watches
    await wait_until(lambda: receipt.commission_id not in watches, what="the look at whether it stands still")
    # Nobody is reminded: the commission does not stand still.
    assert await _events(host, receipt.commission_id, "stall_reminded") == []
    assert (await host.ledger.get(receipt.commission_id)).status == "working"
    busy.set()
    await wait_status(host, receipt.commission_id, "done")
    assert "Every task agent you started" not in _said_to(lead)
    hold.set()


async def test_a_team_is_not_started_where_the_project_has_no_room_for_its_worktrees(hosts, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "WORKTREES_PER_PROJECT", 1)
    lead = ScriptedLLM([
        [call("start_team", {"parts": PARTS}), finish("tool_calls")],
        [call("start_team", {"parts": [PARTS[0], PARTS[2]]}), finish("tool_calls")],
        END, _report("the links are repaired, and the fonts are known"),
    ])
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(lead, _team_scripts()), allow=False)

    receipt = await _dispatch(host, TEAM)
    # Two that change files have no room: nobody is asked about a team that cannot start. One has.
    waiting = await wait_status(host, receipt.commission_id, "waiting_user")
    assert waiting.question()["detail"]["count"] == 1
    assert "room for 1 more that change files" in _said_to(lead)
    assert await host.answer(receipt.commission_id, "yes", choice="once")
    await wait_status(host, receipt.commission_id, "done")
    assert sorted(child.name for child in await host.ledger.select(parent_id=receipt.commission_id)) == ["fonts", "links"]
    assert (workdir / "links.txt").exists() and len(trees.made) == 1


async def test_a_leader_that_is_told_of_its_team_as_it_ends_is_not_reminded(hosts, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEAM_STALL_SECONDS", 0.1)
    lead = TeamLead(PARTS[2:])
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(lead, _team_scripts()))
    receipt = await _dispatch(host, TEAM)
    await wait_status(host, receipt.commission_id, "done")
    await wait_until(lambda: not host.task_agents._stall_watches, what="no watch being left")
    await wait_until(lambda: not host.task_agents._tree_tasks, what="what was under way being through")
    assert await _events(host, receipt.commission_id, "stall_reminded") == []
    assert "Every task agent you started" not in _said_to(lead)


# ---------------------------------------------------------------------------
# An inspector before a landing
# ---------------------------------------------------------------------------


async def _inspected(hosts: list[Any], tmp_path: Path, member: list[list[Any]], inspector: list[list[Any]]) -> tuple[Any, ...]:
    """The shop where every landing is checked first: a second member's changes, looked at by an inspector."""
    leader_llm = ScriptedLLM([*_hand_over(), *_hand_over(description="second writer")])
    members = {
        "the header": ScriptedLLM([_report("header fixed")]),
        "broken links": ScriptedLLM(member),
        CHECK: ScriptedLLM(inspector),
    }
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(leader_llm, members))
    assert (await host.set_landing_settings(inspect=True))["inspect"] is True
    await wait_status(host, (await _dispatch(host, FIRST)).commission_id, "done")
    two = await _dispatch(host, SECOND)
    return host, trees, workdir, two, members


async def test_what_an_inspector_finds_wrong_goes_back_to_the_task_agent_before_anything_lands(hosts, tmp_path):
    host, trees, workdir, two, members = await _inspected(
        hosts, tmp_path,
        member=[_write("links.txt", "first try"), _report("links repaired"),
                _write("links.txt", "second try"), _report("fixed what was found")],
        inspector=[_report("links.txt line 3 points nowhere", status="blocked"), _report("it holds")],
    )
    done = await wait_status(host, two.commission_id, "done")

    assert (workdir / "links.txt").read_text() == "second try" and len(trees.merged) == 1
    checks = await host.ledger.select(parent_id=two.commission_id, oldest_first=True)
    # (The first one reported what is wrong, which is on record as its verdict; like every task agent
    # that was stuck when the commission it worked for was done, it is put down as stopped then.)
    assert [(item.name, item.status) for item in checks] == [("inspector-1", "cancelled"), ("inspector-2", "done")]
    tree_dir = str(host.paths.worktree_dir(two.commission_id[:12]))
    for item in checks:
        session = await host.ledger.get_session(item.session_id)
        # It works in the worktree itself, and is told what was to be done.
        assert (session.preset, session.kind, item.workdir) == ("inspector", "child", tree_dir)
    asked = _said_to(members[CHECK])
    assert "a file it made new is only named there, so read it" in asked and SECOND in asked
    assert "ready: you read the changes" in asked and "failed: you could not check it at all" in asked
    verdicts = [(item["verdict"], item["round"]) for item in await _events(host, two.commission_id, "inspected")]
    assert verdicts == [("fail", 1), ("pass", 2)]
    told = _said_to(members["broken links"])
    assert "the host had an inspector check it, and the inspector found problems" in told
    assert "links.txt line 3 points nowhere" in told and "round 1 of 2" in told
    # What the inspector said is no result of a task agent the member started.
    assert "[child task agent inspector" not in told
    assert done.result_summary.startswith("fixed what was found")
    entry = next(e for e in (await host.timeline(project="Shop site"))["entries"] if e["kind"] == "inspected")
    assert entry["detail"] == {"tree": two.commission_id[:12], "verdict": "fail", "round": 1}


async def test_what_does_not_pass_in_two_rounds_is_left_for_the_user(hosts, tmp_path):
    host, trees, workdir, two, members = await _inspected(
        hosts, tmp_path,
        member=[_write("links.txt", "first try"), _report("links repaired"), _report("it is fine as it is")],
        inspector=[_report("still wrong", status="blocked"), _report("wrong as before", status="blocked")],
    )
    stuck = await wait_status(host, two.commission_id, "blocked")

    (tree,) = await host.ledger.trees()
    assert (tree.state, tree.reason, tree.checks) == ("unlanded", "inspection", 1) and trees.merged == []
    assert not (workdir / "links.txt").exists()
    assert "an inspector found problems in it, and it did not pass in 2 checks" in stuck.result_summary
    assert "wrong as before" in stuck.result_summary
    assert (await _events(host, two.commission_id, "not_landed"))[0]["reason"] == "inspection"


async def test_an_inspector_that_could_not_check_does_not_keep_the_changes_from_landing(hosts, tmp_path):
    host, trees, workdir, two, members = await _inspected(
        hosts, tmp_path,
        member=[_write("links.txt", "repaired"), _report("links repaired")],
        inspector=[_report("nothing can be run here: no dependencies", status="failed")],
    )
    done = await wait_status(host, two.commission_id, "done")

    assert (workdir / "links.txt").read_text() == "repaired" and done.result_summary.startswith("links repaired")
    assert [(item["verdict"], item["round"]) for item in await _events(host, two.commission_id, "inspected")] == [("none", 1)]
    assert "found problems" not in _said_to(members["broken links"])


async def test_an_inspector_the_user_stops_does_not_keep_the_changes_from_landing(hosts, tmp_path):
    hold = asyncio.Event()

    async def wait() -> None:
        await hold.wait()

    host, trees, workdir, two, members = await _inspected(
        hosts, tmp_path,
        member=[_write("links.txt", "repaired"), _report("links repaired")],
        inspector=[[wait, *_report("it holds")]],
    )
    await wait_until(lambda: len(members[CHECK].requests) == 1, what="the inspector being at work")
    (check,) = await host.ledger.select(parent_id=two.commission_id)
    assert await host.abort(check.id)

    done = await wait_status(host, two.commission_id, "done")
    assert (workdir / "links.txt").exists() and done.result_summary.startswith("links repaired")
    assert [item["verdict"] for item in await _events(host, two.commission_id, "inspected")] == ["none"]
    hold.set()


async def test_a_task_agent_stopped_while_it_is_checked_has_its_inspector_stopped_with_it(hosts, tmp_path):
    hold = asyncio.Event()

    async def wait() -> None:
        await hold.wait()

    host, trees, workdir, two, members = await _inspected(
        hosts, tmp_path,
        member=[_write("links.txt", "repaired"), _report("links repaired")],
        inspector=[[wait, *_report("it holds")]],
    )
    await wait_until(lambda: len(members[CHECK].requests) == 1, what="the inspector being at work")
    (check,) = await host.ledger.select(parent_id=two.commission_id)

    assert await host.abort(two.commission_id)
    await wait_status(host, check.id, "cancelled")
    await wait_until(lambda: not host.task_agents._tree_tasks, what="its worktree being seen to")
    (tree,) = await host.ledger.trees()
    # What it changed is kept for the user, and nothing landed.
    assert (tree.state, tree.reason) == ("unlanded", "stopped") and trees.merged == []
    assert not (workdir / "links.txt").exists() and host.task_agents._inspections == {}
    hold.set()


async def test_a_team_whose_leader_asks_for_it_is_checked_though_the_setting_is_off(hosts, tmp_path):
    lead = TeamLead(PARTS[:1], inspect=True)
    members = {**_team_scripts(), CHECK: ScriptedLLM([_report("it holds")])}
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(lead, members))
    assert (await host.landing_settings())["inspect"] is False

    receipt = await _dispatch(host, TEAM)
    await wait_status(host, receipt.commission_id, "done")

    (child,) = await host.ledger.select(parent_id=receipt.commission_id)
    assert [(item["verdict"]) for item in await _events(host, child.id, "inspected")] == ["pass"]
    assert (workdir / "links.txt").exists()
    assert (await _events(host, receipt.commission_id, "team_formed"))[0]["inspect"] is True


# ---------------------------------------------------------------------------
# Members the user or a leader adds
# ---------------------------------------------------------------------------


async def test_a_task_agent_the_user_makes_without_naming_a_preset_does_the_work(hosts, tmp_path):
    host, _trees, _workdir = await _shop(hosts, tmp_path, ScriptedLLM([]))

    made = await host.open_session("Lan", "Blog")

    session = await host.ledger.get_session(made["session_id"])
    assert (session.kind, session.preset, session.seat) == ("line", "general", "")
    assert await host.ledger.lead_session("Lan", "Blog") is None


async def test_a_member_the_user_adds_beside_one_that_works_in_the_directory_gets_worktrees(hosts, tmp_path, monkeypatch):
    host, _trees, workdir = await _shop(hosts, tmp_path, ScriptedLLM([]))

    # The first one of a project works in its directory.
    first = await host.open_member("Lan", "Shop site", duty="keeps the shop running")
    assert first["seat"] == ""
    # Of every character: the directory is the project's.
    assert (await host.open_member("Mei", "Shop site"))["seat"] == "tree"
    added = await host.open_member("Lan", "Shop site", preset="worker", title="Footer", duty="fixes the footer")
    scout = await host.open_member("Lan", "Shop site", preset="scout")

    assert (added["seat"], scout["seat"]) == ("tree", "")
    # Beside one that still takes work: once none of those that work in the directory does, the
    # next one works there itself.
    await host.ledger.run(lambda conn: conn.execute("UPDATE sessions SET last_active_at = 0 WHERE project = 'Shop site'"))
    assert not await host.ledger.session_alive(await host.ledger.get_session(first["session_id"]))
    assert (await host.open_member("Lan", "Shop site", preset="worker"))["seat"] == ""
    assert (await host.open_member("Lan", "Shop site", preset="worker"))["seat"] == "tree"
    session = await host.ledger.get_session(added["session_id"])
    assert (session.kind, session.preset, session.project, session.workdir) == ("line", "worker", "Shop site", str(workdir))
    assert (session.title, session.title_by, session.duty_by) == ("Footer", "user", "user")
    assert session.name == "Shop site/worker" and session.model_name
    # No more members that take work than a project may have, for one character.
    count = len([m for m in await host.ledger.members("Lan", "Shop site") if await host.ledger.session_alive(m)])
    monkeypatch.setattr(settings, "PROJECT_MEMBERS_MAX", count)
    with pytest.raises(ValueError, match="too_many_members"):
        await host.open_member("Lan", "Shop site", preset="scout")
    assert (await host.open_member("Mei", "Shop site", preset="scout"))["seat"] == ""
    monkeypatch.setattr(settings, "PROJECT_MEMBERS_MAX", count + 1)
    assert (await host.open_member("Lan", "Shop site", preset="scout"))["seat"] == ""
    for wrong, error in (
        (("", "Shop site"), "no_character"), (("Lan", "Nowhere"), LookupError), (("Lan", "Shop site", "boss"), "bad_preset"),
        (("Lan", "Shop site", "wizard"), "bad_preset"),
    ):
        kwargs = {"preset": wrong[2]} if len(wrong) == 3 else {}
        if isinstance(error, str):
            with pytest.raises(ValueError, match=error):
                await host.open_member(*wrong[:2], **kwargs)
        else:
            with pytest.raises(error) as refused:
                await host.open_member(*wrong[:2], **kwargs)
            # Said as that, of the project: not what looking it up happened to raise.
            assert type(refused.value) is LookupError and refused.value.args == ("Nowhere",)


async def test_a_new_member_beside_another_is_asked_about_as_one_that_works_in_a_copy(hosts, tmp_path):
    leader_llm = ScriptedLLM([*_hand_over(), *_hand_over(description="second writer")])
    members = {
        "the header": ScriptedLLM([_report("header fixed")]),
        "broken links": ScriptedLLM([_write("links.txt"), _report("links repaired")]),
    }
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(leader_llm, members), allow=False)

    one = await _dispatch(host, FIRST)
    first = (await wait_status(host, one.commission_id, "waiting_user")).question()
    # The first member works in the project's directory: asked about as ever.
    assert "tree" not in first["detail"]
    assert describe_permission(first, "zh").startswith("另开一只能改文件的使魔来办这件事：")
    assert await host.answer(one.commission_id, "yes", choice="once")
    await wait_status(host, one.commission_id, "done")

    two = await _dispatch(host, SECOND)
    second = (await wait_status(host, two.commission_id, "waiting_user")).question()
    assert second["detail"]["tree"] is True and second["detail"]["tool"] == "hand_over"
    assert "在一份独立的副本里改，做完合回项目目录" in describe_permission(second, "zh")
    assert "works in a copy of its own, merged back into the project folder" in describe_permission(second, "en")
    assert await host.answer(two.commission_id, "yes", choice="once")
    await wait_status(host, two.commission_id, "done")
    assert (workdir / "links.txt").exists() and len(trees.made) == 1


async def test_a_project_has_no_more_members_that_take_work_than_it_may(hosts, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROJECT_MEMBERS_MAX", 1)
    refused, _ends = _hand_over(description="second writer")
    leader_llm = ScriptedLLM([*_hand_over(), refused, *_hand_over(to="Shop site/worker")])
    members = {
        "the header": ScriptedLLM([_report("header fixed")]),
        "broken links": ScriptedLLM([_report("links repaired")]),
    }
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(leader_llm, members))

    await wait_status(host, (await _dispatch(host, FIRST)).commission_id, "done")
    two = await _dispatch(host, SECOND)
    done = await wait_status(host, two.commission_id, "done")

    told = _said_to(leader_llm)
    assert "this project has 1 members that take work, which is as many as it may have" in told
    assert "hand over to one of them: Shop site/worker" in told
    # Handed to the one that is there instead.
    assert len(await host.ledger.members("Lan", "Shop site")) == 1 and done.result_summary == "links repaired"

    # One that takes no more work is not counted: another may be opened in its stead.
    await host.ledger.run(lambda conn: conn.execute("UPDATE sessions SET last_active_at = 0 WHERE kind = 'line'"))
    leader_llm.turns.extend(_hand_over(description="third writer"))
    members["the sitemap"] = ScriptedLLM([_report("sitemap written")])
    three = await _dispatch(host, "write the sitemap of the shop site anew from the pages it has")
    assert (await wait_status(host, three.commission_id, "done")).result_summary == "sitemap written"
    assert len(await host.ledger.members("Lan", "Shop site")) == 2


# ---------------------------------------------------------------------------
# With git itself
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    # (Not in a repository the caller's environment names: a test run from a git hook has those set.)
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd, capture_output=True, text=True, check=True, timeout=60, env=env,
    )
    return done.stdout


def _repository(tmp_path: Path, below: str = "") -> tuple[Path, Path]:
    """A git repository with one commit; returns it, and the project's directory in it (``below``, or itself)."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "repo"
    project = repo / below if below else repo
    project.mkdir(parents=True)
    _git(repo, "init", "-q")
    (project / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8", newline="\n")
    (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-gpg-sign", "-m", "first")
    return repo, project


async def _real_shop(hosts: list[Any], tmp_path: Path, project: Path, pick: Any) -> Any:
    """A host that runs git itself, with the project "Shop site" in ``project``, which gets a leader."""
    host = make_host(
        tmp_path, None, runtime_factory=scripted_runtime(pick), task_agent_model=fake_model, decision_llm=FakeDecisionLLM()
    )
    hosts.append(host)
    await host.space.touch_project("Shop site", workdir=str(project))

    async def probe(directory: str) -> bool:
        return directory == str(project)

    host.task_agents.families._probe = probe
    await host.permission_store.set_mode("open_session", "allow")
    found = await host.task_agents.worktrees.probe(project)
    if found.reason in ("git_missing", "git_too_old"):
        pytest.skip("the git that is installed is older than worktrees need")
    # (Any other reason is a fault of the probe, and is not passed over.)
    assert found.usable, (found.reason, found.detail)
    return host


async def test_with_git_itself_a_members_changes_land_as_uncommitted_changes(hosts, tmp_path):
    repo, project = _repository(tmp_path, below="site")
    leader_llm = ScriptedLLM([*_hand_over(), *_hand_over(description="second writer")])
    members = {
        "the header": ScriptedLLM([_report("header fixed")]),
        "broken links": ScriptedLLM([
            [call("read", {"file_path": "c.txt"}), finish("tool_calls")],
            _write("a.txt", "one\ntwo\nthree\nfour\n"), _write("b.txt", "new\n"), _report("links repaired"),
        ]),
    }
    host = await _real_shop(hosts, tmp_path, project, _by_role(leader_llm, members))
    await wait_status(host, (await _dispatch(host, FIRST)).commission_id, "done")
    # What the user changed and did not commit is in the worktree the member starts from.
    (project / "c.txt").write_text("the user's\n", encoding="utf-8", newline="\n")

    two = await _dispatch(host, SECOND)
    done = await wait_status(host, two.commission_id, "done", timeout=120)
    # (It read the file there, in its worktree.)
    assert "the user's" in _said_to(members["broken links"])

    (made,) = await _events(host, two.commission_id, "tree_made")
    # The commission worked in the project's place inside the worktree, which is of the whole repository.
    assert Path(done.workdir) == Path(made["path"]) / "site"
    assert (project / "a.txt").read_text(encoding="utf-8") == "one\ntwo\nthree\nfour\n"
    assert (project / "b.txt").read_text(encoding="utf-8") == "new\n"
    (landed,) = await _events(host, two.commission_id, "landed")
    assert sorted((item["path"], item["status"]) for item in landed["files"]) == [
        ("site/a.txt", "modified"), ("site/b.txt", "added"),
    ]
    assert landed["outside"] == [] and (landed["lines_added"], landed["lines_deleted"]) == (2, 0)
    # Nothing was committed, staged or branched, and nothing of the host's is left in the repository.
    status = _git(repo, "status", "--porcelain", "--untracked-files=all").splitlines()
    assert sorted(status) == [" M site/a.txt", "?? site/b.txt", "?? site/c.txt"]
    assert _git(repo, "rev-list", "--count", "HEAD").strip() == "1" and _git(repo, "branch", "--list").count("\n") == 1
    assert len(_git(repo, "worktree", "list").splitlines()) == 1
    assert _git(repo, "for-each-ref", "refs/nekopower/").strip() == ""
    assert await host.ledger.trees() == [] and not Path(made["path"]).exists()


async def test_with_git_itself_what_clashes_is_merged_by_the_task_agent_and_lands_then(hosts, tmp_path):
    repo, project = _repository(tmp_path)
    hold = asyncio.Event()

    async def wait() -> None:
        await hold.wait()

    leader_llm = ScriptedLLM([*_hand_over(), *_hand_over(description="second writer")])
    members = {
        "the header": ScriptedLLM([_report("header fixed")]),
        "broken links": ScriptedLLM([
            _write("a.txt", "one\nmine\nthree\n"), [wait, *_report("links repaired")],
            # Sent back, it says it is done without having merged anything: not landed so.
            _report("merged"),
            [call("read", {"file_path": "a.txt"}), finish("tool_calls")],
            _write("a.txt", "one\nboth\nthree\n"), _report("merged now"),
        ]),
    }
    host = await _real_shop(hosts, tmp_path, project, _by_role(leader_llm, members))
    await wait_status(host, (await _dispatch(host, FIRST)).commission_id, "done")

    two = await _dispatch(host, SECOND)
    await wait_until(lambda: len(members["broken links"].requests) == 2, timeout=120, what="the member having written")
    # The user changes the same line in the project's directory meanwhile.
    (project / "a.txt").write_text("one\ntheirs\nthree\n", encoding="utf-8", newline="\n")
    hold.set()
    done = await wait_status(host, two.commission_id, "done", timeout=120)

    clashes = await _events(host, two.commission_id, "landing_conflict")
    assert [(clash["conflicts"], clash["round"]) for clash in clashes] == [(["a.txt"], 1), (["a.txt"], 2)]
    told = _said_to(members["broken links"])
    assert "these files clash with what is there by now: a.txt" in told
    # The markers it left were not merged into the project as text: it was sent back once more.
    assert "these files still hold conflict markers" in told and "try 2 of 3" in told
    assert (project / "a.txt").read_text(encoding="utf-8") == "one\nboth\nthree\n"
    assert done.result_summary.startswith("merged now") and await host.ledger.trees() == []
    assert _git(repo, "status", "--porcelain").splitlines() == [" M a.txt"]
    assert _git(repo, "for-each-ref", "refs/nekopower/").strip() == ""


async def test_with_git_itself_a_worktree_left_by_an_earlier_process_is_kept_or_removed(hosts, tmp_path):
    repo, project = _repository(tmp_path)
    host = await _real_shop(hosts, tmp_path, project, ScriptedLLM([]))
    manager = host.task_agents.worktrees
    kept = await manager.create(project, host.paths.worktree_dir("leftchanged"))
    (kept.workdir / "d.txt").write_text("unfinished\n", encoding="utf-8", newline="\n")
    clean = await manager.create(project, host.paths.worktree_dir("leftclean"))
    for handle, state in ((kept, "active"), (clean, "active")):
        await host.ledger.add_tree(
            id=handle.id, project="Shop site", project_dir=str(project), path=str(handle.path),
            workdir=str(handle.workdir), token=handle.token, state=state,
        )
    unknown = await manager.create(project, host.paths.worktree_dir("nobodys"))
    (unknown.workdir / "e.txt").write_text("whose\n", encoding="utf-8", newline="\n")

    found = await host.task_agents.reconcile_trees()

    assert sorted(found["kept"]) == ["leftchanged", "nobodys"] and found["removed"] == ["leftclean"]
    rows = {tree.id: (tree.state, tree.reason) for tree in await host.ledger.trees()}
    assert rows == {"leftchanged": ("unlanded", "restart"), "nobodys": ("unlanded", "restart")}
    assert not clean.path.exists() and kept.path.is_dir() and unknown.path.is_dir()
    # The user has the one nobody knew of landed, and discards the other.
    assert (await host.land_worktree("nobodys"))["status"] == "landed"
    assert (project / "e.txt").read_text(encoding="utf-8") == "whose\n"
    assert await host.discard_worktree("leftchanged") == {"removed": True}
    assert await host.ledger.trees() == [] and len(_git(repo, "worktree", "list").splitlines()) == 1
    assert _git(repo, "for-each-ref", "refs/nekopower/").strip() == ""


async def test_with_git_itself_what_lies_beside_the_project_in_a_worktree_is_asked_about_before_it_is_read(hosts, tmp_path):
    repo, project = _repository(tmp_path, below="site")
    leader_llm = ScriptedLLM([*_hand_over(), *_hand_over(description="second writer")])
    members = {
        "the header": ScriptedLLM([_report("header fixed")]),
        # (The worktree is of the whole repository: the file above the project is there as well.)
        "broken links": ScriptedLLM([
            [call("read", {"file_path": "../.gitignore"}), finish("tool_calls")], _report("links repaired"),
        ]),
    }
    host = await _real_shop(hosts, tmp_path, project, _by_role(leader_llm, members))
    await wait_status(host, (await _dispatch(host, FIRST)).commission_id, "done")

    two = await _dispatch(host, SECOND)
    waiting = await wait_status(host, two.commission_id, "waiting_user", timeout=120)

    # As anything outside the directory it works in: its own worktree is no other session's place to it.
    question = waiting.question()
    assert (question["kind"], question["category"]) == ("permission", "outside_read")
    assert await host.answer(two.commission_id, "", choice="deny")
    await wait_status(host, two.commission_id, "done", timeout=120)
    assert "node_modules" not in _said_to(members["broken links"])


async def test_an_inspector_that_ends_without_a_report_passed_nothing(hosts, tmp_path):
    host, trees, workdir, two, members = await _inspected(
        hosts, tmp_path,
        member=[_write("links.txt", "repaired"), _report("links repaired")],
        inspector=[END],
    )
    done = await wait_status(host, two.commission_id, "done")

    # It lands as if nobody had looked, and is put on record so: not as checked and passed.
    assert (workdir / "links.txt").read_text() == "repaired" and done.result_summary.startswith("links repaired")
    assert [(item["verdict"], item["round"]) for item in await _events(host, two.commission_id, "inspected")] == [("none", 1)]


async def test_an_inspector_the_user_stops_is_out_of_the_worktree_before_anything_lands(hosts, tmp_path):
    hold = asyncio.Event()

    async def stubborn() -> None:
        # Slow to stop: its run is over a while after it was told to.
        try:
            await hold.wait()
        except asyncio.CancelledError:
            await asyncio.sleep(1.0)
            raise

    host, trees, workdir, two, members = await _inspected(
        hosts, tmp_path,
        member=[_write("links.txt", "repaired"), _report("links repaired")],
        inspector=[[stubborn, *_report("it holds")]],
    )
    await wait_until(lambda: len(members[CHECK].requests) == 1, what="the inspector being at work")
    (check,) = await host.ledger.select(parent_id=two.commission_id)
    seen: list[bool] = []
    merge_back = trees.merge_back

    async def merging(worktree: Any, landing: Any = None) -> Any:
        live = host.task_agents._sessions.get(check.id)
        seen.append(live is not None and not live.idle)
        return await merge_back(worktree, landing)

    trees.merge_back = merging
    assert await host.abort(check.id)
    await wait_status(host, two.commission_id, "done")
    assert seen == [False] and (workdir / "links.txt").exists()
    hold.set()


async def test_what_is_said_to_a_task_agent_while_its_changes_are_checked_is_not_taken_for_an_inspector_at_work(
    hosts, tmp_path, monkeypatch
):
    from nekopower.host import tree_work

    monkeypatch.setattr(tree_work, "_GONE_SECONDS", 0.3)
    checking, more = asyncio.Event(), asyncio.Event()

    async def until_checked() -> None:
        await checking.wait()

    async def wait() -> None:
        await more.wait()

    host, trees, workdir, two, members = await _inspected(
        hosts, tmp_path,
        member=[_write("links.txt", "repaired"), _report("links repaired"),
                [wait, *_write("sitemap.txt", "new")], _report("the sitemap is there as well")],
        inspector=[[until_checked, *_report("it holds")], _report("it holds, the sitemap too")],
    )
    await wait_until(lambda: len(members[CHECK].requests) == 1, what="the inspector being at work")
    gave_way = _gave_way(host.task_agents)
    # The user says more to the member while its changes are checked: it is at work again.
    assert await host.tell(two.commission_id, "write the sitemap anew as well")
    await wait_until(lambda: len(members["broken links"].requests) == 3, what="it being at work again")
    checking.set()
    # What the inspector said is dealt with, the member's run being still under way: the landing gave way to it.
    await wait_until(lambda: gave_way, what="the landing giving way")
    # The inspector is gone, and the member's own run is no inspector that would not stop: nothing is given up.
    assert (await host.ledger.get(two.commission_id)).status == "working"
    assert await _events(host, two.commission_id, "not_landed") == []
    # Nor is what it said of the changes as they were put on record, or acted on, under the run that began.
    assert await _events(host, two.commission_id, "inspected") == []
    more.set()
    done = await wait_status(host, two.commission_id, "done")
    assert done.result_summary.startswith("the sitemap is there as well")
    assert (workdir / "links.txt").exists() and (workdir / "sitemap.txt").exists()


async def test_a_task_agent_started_for_what_was_added_while_its_changes_were_checked_is_no_inspector_at_work(
    hosts, tmp_path, monkeypatch
):
    from nekopower.host import tree_work

    monkeypatch.setattr(tree_work, "_GONE_SECONDS", 0.3)
    checking, more, never = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def until_checked() -> None:
        await checking.wait()

    async def wait() -> None:
        await more.wait()

    stopped: list[str] = []

    async def for_long() -> None:
        try:
            await never.wait()
        finally:
            stopped.append("the task agent it started")

    helper = ScriptedLLM([[for_long, *_write("styles.css", "half")]])
    leader_llm = ScriptedLLM([*_hand_over(), *_hand_over(description="second writer")])
    members = {
        "the header": ScriptedLLM([_report("header fixed")]),
        "broken links": ScriptedLLM([
            _write("links.txt", "repaired"), _report("links repaired"),
            [call("subagent", {"description": "styles", "prompt": "Tidy the stylesheet. PART-STYLES"}), finish("tool_calls")],
            [wait, *_report("the styles are being seen to as well")],
        ]),
        CHECK: ScriptedLLM([[until_checked, *_report("it holds")], _report("it holds still")]),
        "PART-STYLES": helper,
    }
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(leader_llm, members))
    assert (await host.set_landing_settings(inspect=True))["inspect"] is True
    await wait_status(host, (await _dispatch(host, FIRST)).commission_id, "done")
    two = await _dispatch(host, SECOND)
    await wait_until(lambda: len(members[CHECK].requests) == 1, what="the inspector being at work")
    gave_way = _gave_way(host.task_agents)
    # The user says more while its changes are checked: it goes on, and starts a task agent for that.
    assert await host.tell(two.commission_id, "tidy the stylesheet as well")
    await wait_until(lambda: len(helper.requests) == 1, what="the task agent it started being at work")
    checking.set()
    # What the inspector said is dealt with while that task agent is still at work: the landing gave way.
    await wait_until(lambda: gave_way, what="the landing giving way")
    (started,) = [
        child for child in await host.ledger.select(parent_id=two.commission_id) if "PART-STYLES" in child.dispatch_text
    ]
    assert started.status == "working" and host.task_agents._at_it(started.id) and len(helper.requests) == 1
    assert stopped == []
    # The inspector is gone; what was started for the new work is nobody that would not stop.
    assert (await host.ledger.get(two.commission_id)).status == "working"
    assert await _events(host, two.commission_id, "not_landed") == []
    more.set()
    done = await wait_status(host, two.commission_id, "done")
    assert done.result_summary.startswith("the styles are being seen to as well") and (workdir / "links.txt").exists()


async def test_an_inspector_that_cannot_be_stopped_keeps_the_changes_from_landing(hosts, tmp_path, monkeypatch):
    from nekopower.host import tree_work
    from tests.unit.nekopower.test_host_tree_work import _slow_to_stop

    monkeypatch.setattr(tree_work, "_GONE_SECONDS", 0.3)
    hold, gone = asyncio.Event(), asyncio.Event()
    host, trees, workdir, two, members = await _inspected(
        hosts, tmp_path,
        member=[_write("links.txt", "repaired"), _report("links repaired")],
        inspector=[[_slow_to_stop(hold, gone), *_report("it holds")]],
    )
    await wait_until(lambda: len(members[CHECK].requests) == 1, what="the inspector being at work")
    (check,) = await host.ledger.select(parent_id=two.commission_id)
    stopping = asyncio.ensure_future(host.abort(check.id))
    try:
        stuck = await wait_status(host, two.commission_id, "blocked")
        # Stopped, and still at it in the worktree when the wait was over: nothing was merged under it.
        (tree,) = await host.ledger.trees()
        assert (tree.state, tree.reason) == ("unlanded", "failed") and trees.merged == [] and not (workdir / "links.txt").exists()
        assert "an inspector could not be stopped in its worktree" in stuck.result_summary
        assert await _events(host, two.commission_id, "inspected") == []
    finally:
        gone.set()
    assert await stopping
    hold.set()


async def test_a_leader_is_told_which_of_its_team_came_to_nothing_before_a_worktree_was_made_for_it(hosts, tmp_path):
    go = asyncio.Event()
    lead = TeamLead(PARTS[:2])
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(lead, _team_scripts(go)))
    manager = host.task_agents
    tree_to_work_in = manager._tree_to_work_in
    started: list[str] = []

    async def not_for_the_second(commission: Any, kept: Any, home: str, changes: bool) -> Any:
        if commission.parent_id and commission.id not in started:
            started.append(commission.id)
        if started[1:] == [commission.id]:
            # Its start comes to nothing before any worktree is tried for it (its model is not set up, say).
            raise RuntimeError("its model is not set up")
        return await tree_to_work_in(commission, kept, home, changes)

    manager._tree_to_work_in = not_for_the_second
    receipt = await _dispatch(host, TEAM)

    async def formed() -> list[dict[str, Any]]:
        return await _events(host, receipt.commission_id, "team_formed")

    await wait_until(formed, what="the team being formed")
    ((team,),) = (await formed(),)
    # The one that never started is not of the team, though no worktree was ever tried and failed for it.
    (member,) = team["members"]
    assert member["commission_id"] == started[0] and await _events(host, started[1], "tree_not_made") == []
    await wait_until(lambda: len(lead.requests) >= 2, what="the leader reading what start_team answered")
    answered = [
        message["content"] for request in lead.requests for message in request
        if message.get("role") == "tool" and "Started: " in str(message.get("content"))
    ]
    assert answered and f"Started: {member['name']}." in answered[0] and " Not started: " in answered[0]
    go.set()
    await wait_status(host, receipt.commission_id, "done")
