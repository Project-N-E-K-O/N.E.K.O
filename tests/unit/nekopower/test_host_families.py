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

"""Families and leaders: where her commissions go, how a project comes about, and what its leader does.

A commission goes on in a task agent that did that work, or gets a new one;
loose task agents that did the same job a third time become a project; a
project gets a leader when it is in a git repository, when a third member
would be opened, when its one member has too much waiting, or when the user
asks; the leader hands commissions over and has no tool to do them with; one
member that writes works in a project at a time.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from config import nekopower_settings as settings
from config.prompts import prompts_nekopower_brain as brain_texts
from nekopower.host import orchestrator as orch
from tests.unit.nekopower.fakes import ScriptedLLM, call, finish, say
from tests.unit.nekopower.host_fakes import (
    FakeDecisionLLM,
    fake_model,
    make_host,
    scripted_runtime,
    scripts_by_commission,
    wait_status,
    wait_until,
)

pytestmark = pytest.mark.unit

LEADER_TOOLS = ("write", "edit", "bash", "pwsh")


@pytest.fixture(autouse=True)
def _one_after_the_other(monkeypatch: pytest.MonkeyPatch) -> None:
    """A later commission of a session waits for it here; copies are ``test_host_fork_continue.py``'s."""
    monkeypatch.setattr("config.nekopower_settings.TASK_AGENT_FORK_CONTINUE_ENABLED", False)
    monkeypatch.setattr("config.nekopower_settings.TASK_AGENT_REVIEW_ENABLED", False)


@pytest.fixture
async def hosts() -> Any:
    made: list[Any] = []
    yield made
    for host in made:
        await host.shutdown()


def _host(hosts: list[Any], tmp_path: Path, pick: Any, **kwargs: Any) -> Any:
    """A host with no plugins, so every dispatch goes to a task agent; nothing is in a git repository unless a test says so."""
    pick = pick if callable(pick) else (lambda spec, llm=pick: llm)
    kwargs.setdefault("task_agent_model", fake_model)
    kwargs.setdefault("decision_llm", FakeDecisionLLM())
    host = make_host(tmp_path, None, runtime_factory=scripted_runtime(pick), **kwargs)

    async def nowhere(_directory: str) -> bool:
        return False

    host.task_agents.families._probe = nowhere
    hosts.append(host)
    return host


def _in_git(host: Any, *directories: Path) -> None:
    wanted = {str(directory) for directory in directories}

    async def probe(directory: str) -> bool:
        return directory in wanted

    host.task_agents.families._probe = probe


async def _dispatch(host: Any, text: str, **kwargs: Any) -> Any:
    kwargs.setdefault("lanlan_name", "Lan")
    kwargs.setdefault("lang", "en")
    receipt = await host.dispatch(text, **kwargs)
    await host.brain.settle()
    return receipt


def _report(summary: str = "done") -> list[Any]:
    return [call("report", {"status": "ready", "summary": summary}), finish("tool_calls")]


def _hand_over(**arguments: Any) -> list[list[Any]]:
    """The two requests of a leader that hands its commission over: the call, then the end of its turn."""
    arguments.setdefault("description", "does the work")
    arguments.setdefault("brief", "Do what the commission says, in the project's directory.")
    return [[call("hand_over", arguments), finish("tool_calls")], [say("handed over"), finish("stop")]]


def _by_role(leader: ScriptedLLM, members: Any) -> Any:
    """Pick a session's model: the leader's for a session that may hand over, else the member's (by commission, for a dict)."""
    by_commission = scripts_by_commission(members) if isinstance(members, dict) else None

    def pick(spec: Any) -> Any:
        if "hand_over" not in spec.disabled_tools:
            return leader
        return by_commission(spec) if by_commission is not None else members

    return pick


async def _events(host: Any, commission_id: str, kind: str) -> list[dict[str, Any]]:
    await host.task_agents.settle()
    return [event.payload for event in await host.ledger.events(commission_id) if event.type == kind]


async def _session_of(host: Any, commission_id: str) -> Any:
    commission = await host.ledger.get(commission_id)
    return await host.ledger.get_session(commission.session_id)


async def _let_go(host: Any) -> None:
    await wait_until(lambda: host.task_agents._held_by == {}, what="every session being let go")
    await host.task_agents.settle()


def _user_messages(request: list[dict[str, Any]]) -> list[str]:
    said = [str(message["content"]) for message in request if message.get("role") == "user"]
    return [text for text in said if not text.startswith("<runtime_context>")]


async def _project(host: Any, name: str, tmp_path: Path, description: str = "") -> Path:
    workdir = tmp_path / f"{name}-files"
    workdir.mkdir()
    await host.space.touch_project(name, workdir=str(workdir))
    if description:
        await host.space.describe_project(name, description, by="user")
    return workdir


# ---------------------------------------------------------------------------
# Where a commission goes
# ---------------------------------------------------------------------------


async def test_a_one_off_gets_a_task_agent_of_its_own_and_no_model_is_asked(hosts, tmp_path):
    llm = FakeDecisionLLM()
    host = _host(hosts, tmp_path, ScriptedLLM([_report("sunny")]), decision_llm=llm)

    receipt = await _dispatch(host, "what is the weather tomorrow")
    done = await wait_status(host, receipt.commission_id, "done")

    session = await host.ledger.get_session(done.session_id)
    assert (session.kind, session.preset, session.project, done.project, done.relation) == (
        "single", "general", None, None, orch.NEW
    )
    assert llm.calls == [] and llm.belongs_calls == []
    (decided,) = await _events(host, receipt.commission_id, "orchestrated")
    assert (decided["action"], decided["layer"], decided["model_calls"], decided["candidates"]) == (
        "summon_stray", "retrieval", 0, []
    )
    assert isinstance(decided["seconds"], float) and decided["seconds"] >= 0


async def test_a_sentence_that_matches_nothing_asks_no_model_though_task_agents_are_there(hosts, tmp_path):
    llm = FakeDecisionLLM()
    scripts = {"desks": ScriptedLLM([_report("three desks")]), "weather": ScriptedLLM([_report("sunny")])}
    host = _host(hosts, tmp_path, scripts_by_commission(scripts), decision_llm=llm)

    first = await _dispatch(host, "compare standing desks under 400 dollars")
    await wait_status(host, first.commission_id, "done")
    second = await _dispatch(host, "what is the weather tomorrow")
    await wait_status(host, second.commission_id, "done")

    assert llm.belongs_calls == []
    assert (await host.ledger.get(second.commission_id)).session_id == second.commission_id


async def test_the_small_model_is_asked_once_about_what_matches_and_its_answer_decides(hosts, tmp_path):
    llm = FakeDecisionLLM()
    llm.belongs = [{"goes_to": 1, "relation": "follow_up"}, {"goes_to": 0, "relation": "new"}]
    desks = ScriptedLLM([_report("three desks compared"), _report("the E7 has ten years")])
    chairs = ScriptedLLM([_report("three chairs")])
    scripts = {"standing desks under": desks, "office chairs": chairs}
    host = _host(hosts, tmp_path, scripts_by_commission(scripts), decision_llm=llm)

    first = await _dispatch(host, "compare standing desks under 400 dollars")
    await wait_status(host, first.commission_id, "done")
    await _let_go(host)

    # About what the first one made, in other words: it goes on there.
    second = await _dispatch(host, "which of the standing desks compared has the longest warranty")
    done = await wait_status(host, second.commission_id, "done")
    assert (done.session_id, done.relation) == (first.commission_id, orch.FOLLOW_UP)
    assert len(llm.belongs_calls) == 1
    asked = llm.belongs_calls[0]
    assert asked[0]["content"] == brain_texts.ORCHESTRATOR_PROMPT
    assert "REQUEST: which of the standing desks compared has the longest warranty" in asked[1]["content"]
    assert "1. [agent] compare standing desks under 400 dollars" in asked[1]["content"]
    (decided,) = await _events(host, second.commission_id, "orchestrated")
    assert (decided["action"], decided["agent"], decided["layer"], decided["model_calls"]) == (
        "continue", first.commission_id, "model", 1
    )
    # No sentence is kept with the decision.
    assert "warranty" not in json.dumps(decided)
    await _let_go(host)

    # Something that only looks alike: the model says none, and it is a new one.
    third = await _dispatch(host, "compare office chairs and standing desks under 300 dollars")
    done = await wait_status(host, third.commission_id, "done")
    assert (done.session_id, done.relation) == (third.commission_id, orch.NEW)
    assert len(llm.belongs_calls) == 2


@pytest.mark.parametrize("answer", ["", "no idea", RuntimeError("the model is gone"), {"goes_to": 9}])
async def test_an_answer_that_cannot_be_read_makes_it_a_new_one(hosts, tmp_path, answer):
    llm = FakeDecisionLLM()
    llm.belongs = [answer]
    scripts = {"under 400": ScriptedLLM([_report("three desks")]), "warranty": ScriptedLLM([_report("ten years")])}
    host = _host(hosts, tmp_path, scripts_by_commission(scripts), decision_llm=llm)

    first = await _dispatch(host, "compare standing desks under 400 dollars")
    await wait_status(host, first.commission_id, "done")
    await _let_go(host)
    second = await _dispatch(host, "which of the standing desks compared has the longest warranty")
    done = await wait_status(host, second.commission_id, "done")

    assert done.session_id == second.commission_id
    assert len(llm.belongs_calls) == 1


async def test_a_task_agent_named_by_its_title_takes_it_without_a_model(hosts, tmp_path):
    llm = FakeDecisionLLM()
    scripts = {"under 400": ScriptedLLM([_report("three desks")]), "warranty": ScriptedLLM([_report("ten years")])}
    host = _host(hosts, tmp_path, scripts_by_commission(scripts), decision_llm=llm)

    first = await _dispatch(host, "compare standing desks under 400 dollars")
    await wait_status(host, first.commission_id, "done")
    await _let_go(host)
    await host.ledger.write_card(first.commission_id, by="user", title="Desk scout")

    second = await _dispatch(host, "ask the desk scout about the warranty")
    done = await wait_status(host, second.commission_id, "done")

    assert done.session_id == first.commission_id and llm.belongs_calls == []
    (decided,) = await _events(host, second.commission_id, "orchestrated")
    assert (decided["action"], decided["layer"]) == ("continue", "rules")


async def test_a_task_agent_that_takes_no_more_work_is_not_gone_on_with(hosts, tmp_path):
    llm = FakeDecisionLLM()
    llm.belongs = [{"goes_to": 1, "relation": "follow_up"}]
    scripts = {"under 400": ScriptedLLM([_report("three desks")]), "warranty": ScriptedLLM([_report("ten years")])}
    host = _host(hosts, tmp_path, scripts_by_commission(scripts), decision_llm=llm)

    first = await _dispatch(host, "compare standing desks under 400 dollars")
    await wait_status(host, first.commission_id, "done")
    await _let_go(host)
    old = host.ledger._clock() - (settings.SESSION_SINGLE_LIFETIME_DAYS + 1) * 86400
    await host.ledger.update_session(first.commission_id, last_active_at=old)

    second = await _dispatch(host, "which of the standing desks compared has the longest warranty")
    done = await wait_status(host, second.commission_id, "done")

    assert done.session_id == second.commission_id


# ---------------------------------------------------------------------------
# A project without a leader
# ---------------------------------------------------------------------------


async def test_a_project_without_a_leader_has_one_member_that_works_and_takes_its_commissions(hosts, tmp_path):
    scripts = {"CSV export": ScriptedLLM([_report("export added")]), "a chart": ScriptedLLM([_report("chart added")])}
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    workdir = await _project(host, "Budget app", tmp_path)

    first = await _dispatch(host, "add a CSV export to the budget app")
    done = await wait_status(host, first.commission_id, "done")
    member = await host.ledger.get_session(done.session_id)
    # A member that works and may start others; not a leader.
    assert (member.kind, member.preset, member.project, member.workdir) == ("line", "general", "Budget app", str(workdir))
    assert await _events(host, first.commission_id, "leader_derived") == []
    await _let_go(host)

    second = await _dispatch(host, "add a chart to the budget app")
    done = await wait_status(host, second.commission_id, "done")
    assert done.session_id == member.id
    assert await host.ledger.lead_session("Lan", "Budget app") is None


async def test_a_member_that_takes_no_more_work_is_followed_by_a_new_one(hosts, tmp_path):
    scripts = {"CSV export": ScriptedLLM([_report("export added")]), "a chart": ScriptedLLM([_report("chart added")])}
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    await _project(host, "Budget app", tmp_path)

    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "done")
    await _let_go(host)
    old = host.ledger._clock() - (settings.SESSION_SINGLE_LIFETIME_DAYS + 1) * 86400
    await host.ledger.update_session(first.commission_id, last_active_at=old)

    second = await _dispatch(host, "add a chart to the budget app")
    done = await wait_status(host, second.commission_id, "done")

    assert done.session_id == second.commission_id
    session = await host.ledger.get_session(done.session_id)
    assert (session.kind, session.preset, session.project) == ("line", "general", "Budget app")
    (decided,) = await _events(host, second.commission_id, "orchestrated")
    assert decided["action"] == "summon_in_project"


# ---------------------------------------------------------------------------
# Families
# ---------------------------------------------------------------------------


async def _weekly_reports(hosts, tmp_path, llm: FakeDecisionLLM, more: int = 0) -> tuple[Any, list[Any]]:
    """Three commissions of the same job, the model asked about the second and the third saying so."""
    llm.belongs = [{"goes_to": 1, "relation": "recurrence"}]
    reports = ScriptedLLM([_report(f"week {week} written") for week in range(45, 48 + more)])
    host = _host(hosts, tmp_path, reports, decision_llm=llm)
    receipts = []
    for week in (45, 46, 47):
        receipt = await _dispatch(host, f"write the weekly sales report for week {week}")
        await wait_status(host, receipt.commission_id, "done")
        await _let_go(host)
        receipts.append(receipt)
    return host, receipts


async def test_the_same_job_a_third_time_makes_its_task_agent_a_project(hosts, tmp_path):
    llm = FakeDecisionLLM()
    host, (first, second, third) = await _weekly_reports(hosts, tmp_path, llm)

    # The second time it goes on in the same one, and no project is made.
    again = await host.ledger.get(second.commission_id)
    assert (again.session_id, again.project, again.relation) == (first.commission_id, None, orch.RECURRENCE)
    assert await _events(host, second.commission_id, "family_formed") == []

    done = await host.ledger.get(third.commission_id)
    (formed,) = await _events(host, third.commission_id, "family_formed")
    assert formed["members"] == [first.commission_id]
    project = await host.space.get_project(formed["project"])
    assert project is not None and done.project == project.name
    # A directory of its own, where the host keeps its projects; made.
    assert Path(project.workdir) == host.paths.project_work_dir(project.name) and Path(project.workdir).is_dir()
    # Its task agent is the project's member, where it worked, with its card as it was.
    member = await host.ledger.get_session(first.commission_id)
    assert (member.kind, member.project, member.preset) == ("line", project.name, "general")
    assert member.workdir != project.workdir and Path(member.workdir).is_dir()
    assert done.session_id == member.id
    (decided,) = await _events(host, third.commission_id, "orchestrated")
    assert (decided["action"], decided["members"], decided["agent"]) == ("form_family", [first.commission_id], first.commission_id)


async def test_a_family_is_named_as_a_review_suggested(hosts, tmp_path):
    llm = FakeDecisionLLM()
    llm.belongs = [{"goes_to": 1, "relation": "recurrence"}]
    reports = ScriptedLLM([_report("week 45 written"), _report("week 46 written"), _report("week 47 written")])
    host = _host(hosts, tmp_path, reports, decision_llm=llm)
    receipts = []
    for week in (45, 46):
        receipt = await _dispatch(host, f"write the weekly sales report for week {week}")
        await wait_status(host, receipt.commission_id, "done")
        await _let_go(host)
        receipts.append(receipt)
    await host.ledger.append_event(
        receipts[-1].commission_id, "project_suggested", {"name": "Sales reports", "description": "The weekly sales report"}
    )

    third = await _dispatch(host, "write the weekly sales report for week 47")
    await wait_status(host, third.commission_id, "done")

    (formed,) = await _events(host, third.commission_id, "family_formed")
    project = await host.space.get_project("Sales reports")
    assert formed["project"] == "Sales reports" and project.description == "The weekly sales report"


async def test_a_family_whose_name_is_taken_gets_another(hosts, tmp_path):
    llm = FakeDecisionLLM()
    llm.belongs = [{"goes_to": 1, "relation": "recurrence"}]
    reports = ScriptedLLM([_report("a"), _report("b"), _report("c")])
    host = _host(hosts, tmp_path, reports, decision_llm=llm)
    receipts = []
    for week in (45, 46):
        receipt = await _dispatch(host, f"write the weekly sales report for week {week}")
        await wait_status(host, receipt.commission_id, "done")
        await _let_go(host)
        receipts.append(receipt)
    await host.ledger.append_event(receipts[-1].commission_id, "project_suggested", {"name": "Numbers", "description": ""})
    # (However it is spelled: a project is one by its name as the space compares names.)
    await host.space.touch_project("numbers")

    third = await _dispatch(host, "write the weekly sales report for week 47")
    await wait_status(host, third.commission_id, "done")

    (formed,) = await _events(host, third.commission_id, "family_formed")
    assert formed["project"] == "Numbers 2"


async def test_following_one_piece_of_work_up_makes_no_family_however_often(hosts, tmp_path):
    llm = FakeDecisionLLM()
    llm.belongs = [{"goes_to": 1, "relation": "follow_up"}]
    deck = ScriptedLLM([_report("deck made"), _report("page 3 changed"), _report("page 5 changed"), _report("page 7 changed")])
    host = _host(hosts, tmp_path, deck, decision_llm=llm)

    first = await _dispatch(host, "make the slide deck for the monday meeting")
    await wait_status(host, first.commission_id, "done")
    await _let_go(host)
    for page in (3, 5, 7):
        later = await _dispatch(host, f"change the chart on page {page} of the slide deck for the meeting")
        done = await wait_status(host, later.commission_id, "done")
        await _let_go(host)
        assert (done.session_id, done.project) == (first.commission_id, None)
        assert await _events(host, later.commission_id, "family_formed") == []
    assert [item.name for item in await host.space.list_projects() if not item.name.startswith("_")] == []
    # The second of them matched so clearly that nobody was asked, and its words do not say
    # that it follows something up. Where that would have made a project, the model was asked.
    relations = [(await host.ledger.get(event)).relation for event in host.task_agents._session_of]
    assert relations == [orch.NEW, orch.FOLLOW_UP, orch.RECURRENCE, orch.FOLLOW_UP]
    assert len(llm.belongs_calls) == 2


async def test_a_member_of_a_family_is_told_where_the_others_worked(hosts, tmp_path):
    llm = FakeDecisionLLM()
    host, (first, _second, third) = await _weekly_reports(hosts, tmp_path, llm, more=1)
    project = (await host.ledger.get(third.commission_id)).project
    member = await host.ledger.get_session(first.commission_id)
    old = host.ledger._clock() - (settings.SESSION_SINGLE_LIFETIME_DAYS + 1) * 86400
    await host.ledger.update_session(member.id, last_active_at=old)

    # The member takes no more work: a new one is opened in the project, where the project works.
    fourth = await _dispatch(host, f"write the weekly report of {project} for week 48")
    done = await wait_status(host, fourth.commission_id, "done")

    new = await host.ledger.get_session(done.session_id)
    assert (new.id, new.kind, new.project) == (fourth.commission_id, "line", project)
    assert new.workdir == (await host.space.get_project(project)).workdir
    # It is told where the old files are, with the commission that opens in it.
    request = host.task_agents._runtime_requests if hasattr(host.task_agents, "_runtime_requests") else None
    assert request is None
    notes = await host.task_agents.families.member_notes(new)
    assert notes == brain_texts.MEMBER_EARLIER_WORK.format(places=member.workdir)
    assert await host.task_agents.families.member_notes(member) == brain_texts.MEMBER_EARLIER_WORK.format(places=new.workdir)
    # One that works where every other member does is told nothing.
    alone = await host.ledger.create_session(name="alone", lanlan_name="Lan", kind="line", project="Elsewhere", workdir=new.workdir)
    assert await host.task_agents.families.member_notes(alone) == ""


# ---------------------------------------------------------------------------
# Leaders
# ---------------------------------------------------------------------------


def _recording(pick: Any, specs: dict[str, Any]) -> Any:
    """Pick as ``pick`` does, and keep every session's spec by its id."""

    def recorded(spec: Any) -> Any:
        specs[spec.session_id] = spec
        return pick(spec)

    return recorded


async def _allow_members(host: Any) -> None:
    await host.permission_store.set_mode("open_session", "allow")


async def test_a_project_in_a_git_repository_has_a_leader_from_its_first_commission(hosts, tmp_path):
    leader_llm = ScriptedLLM(_hand_over(description="Fixes the pages of the shop site", brief="Fix the header only."))
    member_llm = ScriptedLLM([_report("header fixed")])
    specs: dict[str, Any] = {}
    host = _host(hosts, tmp_path, _recording(_by_role(leader_llm, member_llm), specs))
    workdir = await _project(host, "Shop site", tmp_path)
    _in_git(host, workdir)
    await _allow_members(host)

    receipt = await _dispatch(host, "fix the header of the shop site")
    done = await wait_status(host, receipt.commission_id, "done")
    await _let_go(host)

    leader = await host.ledger.lead_session("Lan", "Shop site")
    assert leader is not None and (leader.kind, leader.preset, leader.id) == ("project", "boss", receipt.commission_id)
    (derived,) = await _events(host, receipt.commission_id, "leader_derived")
    assert derived == {"project": "Shop site", "reason": "git", "session_id": leader.id}
    # The leader has no tool that writes or edits a file or runs a command, and may hand over.
    assert set(LEADER_TOOLS) <= set(specs[leader.id].disabled_tools)
    assert "hand_over" not in specs[leader.id].disabled_tools and "subagent" not in specs[leader.id].disabled_tools
    # The member it handed the commission to has them, and hands nothing over.
    member = await host.ledger.get_session(done.session_id)
    assert (member.kind, member.preset, member.project, member.parent_id) == ("line", "worker", "Shop site", leader.id)
    assert member.workdir == str(workdir) and member.duty == "Fixes the pages of the shop site"
    assert not set(LEADER_TOOLS) & set(specs[member.id].disabled_tools)
    assert "hand_over" in specs[member.id].disabled_tools
    # The commission is the member's: it reported, and that is what she is told.
    assert (done.result_summary, done.handed_by, done.project) == ("header fixed", leader.id, "Shop site")
    (handed,) = await _events(host, receipt.commission_id, "handed_over")
    assert handed == {"from": leader.id, "to": member.id, "name": member.name, "preset": "worker", "new": True}
    # What opens it there: the commission itself, then the leader's brief, framed as no word of the user's.
    opened = _user_messages(member_llm.requests[0])
    assert opened[0].startswith(f"Commission {receipt.commission_id}\nfix the header of the shop site")
    assert brain_texts.TASK_AGENT_HANDED_OVER.split("{nonce}")[0] in opened[-1] and "    Fix the header only." in opened[-1]
    # The leader was told that it is the lead from now on, and that there are no members yet.
    first = "\n".join(_user_messages(leader_llm.requests[0]))
    assert brain_texts.LEADER_FIRST_OPENING in first and brain_texts.LEADER_ROSTER_NONE in first


async def test_the_leader_is_free_once_it_handed_over_and_is_told_later_what_came_of_it(hosts, tmp_path):
    leader_llm = ScriptedLLM([*_hand_over(), *_hand_over(to="Shop site/worker"), *_hand_over(to="Shop site/worker")])
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    member_llm = ScriptedLLM([[wait, *_report("header fixed")], _report("footer fixed"), _report("menu fixed")])
    host = _host(hosts, tmp_path, _by_role(leader_llm, member_llm))
    workdir = await _project(host, "Shop site", tmp_path)
    _in_git(host, workdir)
    await _allow_members(host)

    first = await _dispatch(host, "fix the header of the shop site")
    await wait_until(lambda: len(member_llm.requests) == 1, what="the member being at work")
    leader = await host.ledger.lead_session("Lan", "Shop site")
    # The member is at work on it; the leader holds nothing and takes the next one at once.
    assert host.task_agents._held_by.get(leader.id) is None
    second = await _dispatch(host, "repair the broken links in the footer on every page of the shop site")
    assert second.commission_id != first.commission_id
    await wait_until(lambda: len(leader_llm.requests) == 4, what="the leader handing the second one over")
    await wait_until(lambda: host.task_agents.queued == [second.commission_id], what="the second one waiting for the member")
    assert (await host.ledger.get(second.commission_id)).status == "queued"
    # Handed to the member that is there, by its name: no new one is made.
    (handed,) = await _events(host, second.commission_id, "handed_over")
    assert (handed["new"], handed["name"]) == (False, "Shop site/worker")
    # What it was told with the second one: the member, at work; nothing came of anything yet.
    told = _user_messages(leader_llm.requests[2])[-1]
    assert "- Shop site/worker (worker, at work): does the work" in told
    assert brain_texts.LEADER_FIRST_OPENING not in told and "What came of commissions" not in told

    release.set()
    await wait_status(host, first.commission_id, "done")
    await wait_status(host, second.commission_id, "done")
    await _let_go(host)
    third = await _dispatch(host, "make the menu of the shop site fold on small screens")
    await wait_status(host, third.commission_id, "done")
    await _let_go(host)

    # With the third one it is told what came of the first two, once.
    told = _user_messages(leader_llm.requests[4])[-1]
    assert brain_texts.LEADER_HANDED_WORK.split("\n")[0] in told
    assert "(done, by Shop site/worker): header fixed" in told and "(done, by Shop site/worker): footer fixed" in told
    for commission in (first, second):
        assert len(await _events(host, commission.commission_id, "told_to_session")) == 1
    assert await host.ledger.handed_work(leader.id, limit=9) == [await host.ledger.get(third.commission_id)]


async def test_a_third_member_is_the_leaders_to_start(hosts, tmp_path):
    leader_llm = ScriptedLLM(_hand_over())
    members = {"CSV export": ScriptedLLM([_report("export added")]), "a chart": ScriptedLLM([_report("chart added")]),
               "a logo": ScriptedLLM([_report("logo added")])}
    host = _host(hosts, tmp_path, _by_role(leader_llm, members))
    await _project(host, "Budget app", tmp_path)
    await _allow_members(host)
    old = host.ledger._clock() - (settings.SESSION_SINGLE_LIFETIME_DAYS + 1) * 86400

    for text in ("add a CSV export to the budget app", "add a chart to the budget app"):
        receipt = await _dispatch(host, text)
        await wait_status(host, receipt.commission_id, "done")
        await _let_go(host)
        assert await _events(host, receipt.commission_id, "leader_derived") == []
        # It takes no more work: the next commission of the project opens another member.
        await host.ledger.update_session(receipt.commission_id, last_active_at=old)
    assert len(await host.ledger.members("Lan", "Budget app")) == 2

    third = await _dispatch(host, "add a logo to the budget app")
    done = await wait_status(host, third.commission_id, "done")

    (derived,) = await _events(host, third.commission_id, "leader_derived")
    assert derived["reason"] == "third_member"
    leader = await host.ledger.lead_session("Lan", "Budget app")
    assert (await host.ledger.get_session(done.session_id)).parent_id == leader.id
    # Told where the project stands, the first time: both members, and what each did.
    first = "\n".join(_user_messages(leader_llm.requests[0]))
    assert brain_texts.LEADER_FIRST_OPENING in first
    assert first.count("(general, takes no more work)") == 2
    assert '  did: "add a CSV export to the budget app" (done): export added' in first
    assert '  did: "add a chart to the budget app" (done): chart added' in first


async def test_a_member_with_too_much_waiting_gets_its_project_a_leader(hosts, tmp_path):
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    leader_llm = ScriptedLLM(_hand_over())
    members = {
        "CSV export": ScriptedLLM([[wait, *_report("export added")]]),
        "a logo": ScriptedLLM([_report("logo drawn")]),
        "help page": ScriptedLLM([_report("help written")]),
        "French": ScriptedLLM([_report("translated")]),
    }
    host = _host(hosts, tmp_path, _by_role(leader_llm, members))
    await _project(host, "Budget app", tmp_path)
    await _allow_members(host)

    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "working")
    waiting = [
        await _dispatch(host, "draw a logo for the budget app"),
        await _dispatch(host, "write a help page that explains every command of the budget app"),
    ]
    assert len({first.commission_id, *(item.commission_id for item in waiting)}) == 3
    # One at work and two waiting: the member's, as ever.
    assert [(await host.ledger.get(item.commission_id)).session_id for item in waiting] == [first.commission_id] * 2
    assert await host.ledger.lead_session("Lan", "Budget app") is None

    fourth = await _dispatch(host, "translate the whole budget app into French")
    (derived,) = await _events(host, fourth.commission_id, "leader_derived")
    assert derived["reason"] == "busy"
    release.set()
    for item in (first, *waiting, fourth):
        await wait_status(host, item.commission_id, "done")
    leader = await host.ledger.lead_session("Lan", "Budget app")
    assert (await host.ledger.get(fourth.commission_id)).handed_by == leader.id


def test_whether_a_family_is_busy_goes_by_the_setting(monkeypatch):
    from nekopower.host.families import family_is_busy

    assert not family_is_busy(settings.LEADER_WHEN_UNDER_WAY - 1) and family_is_busy(settings.LEADER_WHEN_UNDER_WAY)
    monkeypatch.setattr(settings, "LEADER_WHEN_UNDER_WAY", 1)
    assert family_is_busy(1)


async def test_the_user_gives_a_project_a_leader_midway(hosts, tmp_path):
    leader_llm = ScriptedLLM(_hand_over(to="Budget app"))
    members = {"CSV export": ScriptedLLM([_report("export added")]), "a chart": ScriptedLLM([_report("chart added")])}
    host = _host(hosts, tmp_path, _by_role(leader_llm, members))
    await _project(host, "Budget app", tmp_path, "A budget app for the household")
    await _allow_members(host)
    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "done")
    await _let_go(host)
    await host.ledger.write_card(first.commission_id, by="review", summary="Built the export; charts are next.")

    made = await host.derive_leader("Budget app", "Lan")
    again = await host.derive_leader("Budget app", "Lan")
    leader = await host.ledger.lead_session("Lan", "Budget app")
    assert made == {"session_id": leader.id, "made": True} and again == {"session_id": leader.id, "made": False}
    assert (leader.kind, leader.preset, leader.duty) == ("project", "boss", "A budget app for the household")
    assert leader.model_ident  # settled as it was made
    with pytest.raises(LookupError):
        await host.derive_leader("No such project", "Lan")
    with pytest.raises(ValueError, match="no_character"):
        await host.derive_leader("Budget app", " ")

    # From then on the project's commissions come to the leader, which hands this one to the member that is there.
    second = await _dispatch(host, "add a chart to the budget app")
    done = await wait_status(host, second.commission_id, "done")
    assert (done.session_id, done.handed_by) == (first.commission_id, leader.id)
    (decided,) = await _events(host, second.commission_id, "orchestrated")
    assert decided["action"] == "to_leader"
    # What it is handed as it starts is what is on record; no model is asked for it.
    first_told = "\n".join(_user_messages(leader_llm.requests[0]))
    assert brain_texts.LEADER_FIRST_OPENING in first_told
    assert "- Budget app (general, idle): A budget app for the household" in first_told
    assert "  so far: Built the export; charts are next." in first_told
    assert '  did: "add a CSV export to the budget app" (done): export added' in first_told


async def test_what_a_leader_is_told_of_its_members_stays_within_its_caps(hosts, tmp_path, monkeypatch):
    from utils.tokenize import count_tokens

    host = _host(hosts, tmp_path, ScriptedLLM([]))
    await _project(host, "Budget app", tmp_path)
    for number in range(14):
        member = await host.ledger.create_session(
            name=f"member-{number}", lanlan_name="Lan", kind="line", project="Budget app", duty="word " * 300,
            summary="so " * 300,
        )
        await host.ledger.update_session(member.id, last_active_at=1000.0 + number)
    leader, _made = await host.ledger.project_session("Lan", "Budget app")

    first, told = await host.task_agents.families.leader_notes(leader, "")
    assert told == [] and first.startswith(brain_texts.LEADER_FIRST_OPENING)
    # The ones last in use, and no more than are listed.
    assert first.count("\n- member-") == settings.LEADER_ROSTER_MEMBERS and "member-13 " in first and "member-0 " not in first
    monkeypatch.setattr(settings, "LEADER_FIRST_ROSTER_MAX_TOKENS", 200)
    capped, _told = await host.task_agents.families.leader_notes(leader, "")
    assert count_tokens(capped.split("\n\n", 1)[1]) <= 200


async def test_a_leader_cannot_report_or_start_task_agents_once_it_handed_over(hosts, tmp_path):
    # It goes on calling tools after the hand-over: none of them undoes it.
    leader_llm = ScriptedLLM(
        [
            [call("hand_over", {"description": "does the work", "brief": "Do it."}), finish("tool_calls")],
            [call("hand_over", {"description": "again", "brief": "Do it."}), finish("tool_calls")],
            [call("subagent", {"description": "look", "prompt": "look around"}), finish("tool_calls")],
            [call("report", {"status": "ready", "summary": "the lead says it is done"}), finish("tool_calls")],
            [say("that is all"), finish("stop")],
        ]
    )
    member_llm = ScriptedLLM([_report("done by the member")])
    host = _host(hosts, tmp_path, _by_role(leader_llm, member_llm))
    workdir = await _project(host, "Shop site", tmp_path)
    _in_git(host, workdir)
    await _allow_members(host)

    receipt = await _dispatch(host, "fix the header of the shop site")
    done = await wait_status(host, receipt.commission_id, "done")

    assert done.result_summary == "done by the member"
    assert len(await _events(host, receipt.commission_id, "handed_over")) == 1
    assert await _events(host, receipt.commission_id, "subagent_started") == []
    assert len(await host.ledger.members("Lan", "Shop site")) == 1
    said = json.dumps(leader_llm.requests[-1], ensure_ascii=False)
    assert brain_texts.HAND_OVER_TWICE in said and brain_texts.HANDED_OVER_ALREADY in said


async def test_only_a_leader_hands_over_and_only_to_a_member_that_takes_work(hosts, tmp_path):
    turns = [
        [call("hand_over", {"to": "nobody", "description": "d", "brief": "b"}), finish("tool_calls")],
        [call("hand_over", {"to": "Budget app", "description": "d", "brief": "b"}), finish("tool_calls")],
        [call("hand_over", {"preset": "lead", "description": "d", "brief": "b"}), finish("tool_calls")],
        [call("report", {"status": "blocked", "summary": "nobody takes it"}), finish("tool_calls")],
    ]
    leader_llm = ScriptedLLM(turns)
    members = {"CSV export": ScriptedLLM([_report("export added")])}
    host = _host(hosts, tmp_path, _by_role(leader_llm, members))
    await _project(host, "Budget app", tmp_path)
    await _allow_members(host)
    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "done")
    await _let_go(host)
    member = await host.ledger.get_session(first.commission_id)

    # A member is no leader, whatever it calls.
    with pytest.raises(RuntimeError, match=brain_texts.HAND_OVER_NOT_A_LEADER):
        await host.task_agents._cb_hand_over(member.id, {"description": "d", "brief": "b"})

    old = host.ledger._clock() - (settings.SESSION_SINGLE_LIFETIME_DAYS + 1) * 86400
    await host.ledger.update_session(member.id, last_active_at=old)
    await host.derive_leader("Budget app", "Lan")
    second = await _dispatch(host, "draw a logo for the budget app")
    stuck = await wait_status(host, second.commission_id, "blocked")

    # Not to one that is not there, not to one that takes no more work, not to a preset no task agent is started with.
    assert stuck.handed_by is None and await _events(host, second.commission_id, "handed_over") == []
    refused = json.dumps(leader_llm.requests[-1], ensure_ascii=False)
    assert "no member of this project is called 'nobody'; its members are: Budget app" in refused
    assert "'Budget app' takes no more work" in refused
    assert "unknown preset 'lead'" in refused
    assert len(await host.ledger.members("Lan", "Budget app")) == 1


# ---------------------------------------------------------------------------
# One that writes at a time, and the user's say on opening another
# ---------------------------------------------------------------------------


async def test_one_member_that_writes_works_in_a_project_at_a_time_and_one_that_reads_does_not_wait(hosts, tmp_path):
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    leader_llm = ScriptedLLM(
        [*_hand_over(), *_hand_over(description="second writer"), *_hand_over(preset="scout", description="looks things up")]
    )
    members = {
        "the header": ScriptedLLM([[wait, *_report("header fixed")]]),
        "broken links": ScriptedLLM([_report("links repaired")]),
        "which fonts": ScriptedLLM([_report("two fonts")]),
    }
    host = _host(hosts, tmp_path, _by_role(leader_llm, members))
    workdir = await _project(host, "Shop site", tmp_path)
    _in_git(host, workdir)
    await _allow_members(host)

    first = await _dispatch(host, "fix the header of the shop site")
    await wait_until(lambda: len(members["the header"].requests) == 1, what="the first member being at work")
    second = await _dispatch(host, "repair the broken links in the footer on every page of the shop site")
    await wait_until(lambda: host.task_agents.queued == [second.commission_id], what="the second one waiting")
    writer = await _session_of(host, second.commission_id)
    # A member of its own, free, with places free: it waits all the same, the first one being at work.
    assert writer.id != (await _session_of(host, first.commission_id)).id and writer.preset == "worker"
    assert (await host.ledger.get(second.commission_id)).status == "queued"
    assert members["broken links"].requests == []

    # One that reads only starts at once.
    third = await _dispatch(host, "find out which fonts the pages of the shop site use")
    done = await wait_status(host, third.commission_id, "done")
    assert (await host.ledger.get_session(done.session_id)).preset == "scout"
    assert (await host.ledger.get(second.commission_id)).status == "queued"

    release.set()
    await wait_status(host, first.commission_id, "done")
    assert (await wait_status(host, second.commission_id, "done")).result_summary == "links repaired"


async def test_sessions_of_one_commission_and_other_projects_do_not_wait_for_a_project_s_writer(hosts, tmp_path):
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    scripts = {
        "CSV export": ScriptedLLM([[wait, *_report("export added")]]),
        "weather": ScriptedLLM([_report("sunny")]),
        "photos": ScriptedLLM([_report("sorted")]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    await _project(host, "Budget app", tmp_path)
    await _project(host, "Photo album", tmp_path)

    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "working")
    for text in ("what is the weather tomorrow", "sort the photos of the photo album by date"):
        receipt = await _dispatch(host, text)
        await wait_status(host, receipt.commission_id, "done")
    release.set()
    await wait_status(host, first.commission_id, "done")


async def _leader_asks(hosts, tmp_path, leader_turns: list[list[Any]], member_turns: list[list[Any]] | None = None) -> Any:
    leader_llm = ScriptedLLM(leader_turns)
    member_llm = ScriptedLLM(member_turns if member_turns is not None else [_report("header fixed")])
    host = _host(hosts, tmp_path, _by_role(leader_llm, member_llm))
    workdir = await _project(host, "Shop site", tmp_path)
    _in_git(host, workdir)
    return host, leader_llm


async def test_a_new_member_that_writes_is_asked_about_first_by_default(hosts, tmp_path):
    host, _leader = await _leader_asks(hosts, tmp_path, _hand_over(description="Fixes the pages"))

    receipt = await _dispatch(host, "fix the header of the shop site")
    waiting = await wait_status(host, receipt.commission_id, "waiting_user")
    question = waiting.question()
    assert (question["kind"], question["category"]) == ("permission", "open_session")
    assert question["detail"]["title"] == "Fixes the pages <- fix the header of the shop site"
    assert await host.ledger.members("Lan", "Shop site") == []
    from nekopower.host.reports import describe_permission

    asked = describe_permission(question, "en")
    assert asked.startswith("start another familiar that can change files for this task: Fixes the pages <- fix the header")
    assert "猫爪" not in describe_permission(question, "zh") and "另开一只能改文件的使魔" in describe_permission(question, "zh")

    assert await host.answer(receipt.commission_id, "yes", choice="once")
    done = await wait_status(host, receipt.commission_id, "done")
    assert done.result_summary == "header fixed" and len(await host.ledger.members("Lan", "Shop site")) == 1
    # Allowed once: the setting is as it was, and the next one is asked about again.
    assert await host.permission_store.mode("open_session") == "first_ask"


async def test_allowed_for_good_it_is_not_asked_about_again(hosts, tmp_path):
    host, _leader = await _leader_asks(
        hosts, tmp_path, [*_hand_over(), *_hand_over(description="another writer")],
        [_report("header fixed"), _report("links repaired")],
    )

    first = await _dispatch(host, "fix the header of the shop site")
    await wait_status(host, first.commission_id, "waiting_user")
    assert await host.answer(first.commission_id, "always", choice="always")
    await wait_status(host, first.commission_id, "done")
    assert await host.permission_store.mode("open_session") == "allow"
    await _let_go(host)

    second = await _dispatch(host, "repair the broken links in the footer on every page of the shop site")
    done = await wait_status(host, second.commission_id, "done")
    assert done.result_summary == "links repaired"
    assert [event for event in await host.ledger.events(second.commission_id) if event.type == "status"
            and event.payload.get("to") == "waiting_user"] == []


@pytest.mark.parametrize("how", ["declined", "forbidden"])
async def test_not_allowed_no_member_is_opened_and_the_leader_is_told(hosts, tmp_path, how):
    turns = [
        [call("hand_over", {"description": "Fixes the pages", "brief": "Fix it."}), finish("tool_calls")],
        [call("report", {"status": "blocked", "summary": "nobody may be started for it"}), finish("tool_calls")],
    ]
    host, leader_llm = await _leader_asks(hosts, tmp_path, turns)
    if how == "forbidden":
        await host.permission_store.set_mode("open_session", "deny")

    receipt = await _dispatch(host, "fix the header of the shop site")
    if how == "declined":
        await wait_status(host, receipt.commission_id, "waiting_user")
        assert await host.answer(receipt.commission_id, "no", choice="deny")
    stuck = await wait_status(host, receipt.commission_id, "blocked")

    assert stuck.result_summary == "nobody may be started for it" and stuck.handed_by is None
    assert await host.ledger.members("Lan", "Shop site") == []
    assert await _events(host, receipt.commission_id, "handed_over") == []
    refused = json.dumps(leader_llm.requests[-1], ensure_ascii=False)
    assert "the user did not allow another task agent that can change files" in refused
    if how == "forbidden":
        # Refused by the settings: the user is not asked.
        assert [event for event in await host.ledger.events(receipt.commission_id) if event.type == "status"
                and event.payload.get("to") == "waiting_user"] == []


async def test_one_that_reads_only_and_a_member_that_is_there_are_not_asked_about(hosts, tmp_path):
    host, _leader = await _leader_asks(
        hosts, tmp_path,
        [*_hand_over(preset="scout", description="looks things up"), *_hand_over(to="Shop site/scout")],
        [_report("two fonts"), _report("three colours")],
    )

    for text in ("find out which fonts the pages of the shop site use", "list the colours the shop site uses on its pages"):
        receipt = await _dispatch(host, text)
        done = await wait_status(host, receipt.commission_id, "done")
        await _let_go(host)
        assert [event for event in await host.ledger.events(receipt.commission_id) if event.type == "status"
                and event.payload.get("to") == "waiting_user"] == []
        assert (await host.ledger.get_session(done.session_id)).preset == "scout"
    assert await host.permission_store.mode("open_session") == "first_ask"


async def test_the_permission_to_open_another_is_a_category_of_the_settings_asked_first():
    from nekopower.host.permissions import AUTO_POLICY, DEFAULT_MODES, OPEN_SESSION

    assert OPEN_SESSION == "open_session" and AUTO_POLICY[OPEN_SESSION] == "first_ask" and DEFAULT_MODES[OPEN_SESSION] == "auto"
    for lang, texts in brain_texts.PERMISSION_CATEGORY_TEXTS.items():
        assert "{detail}" in texts[OPEN_SESSION], lang
        assert "猫爪" not in texts[OPEN_SESSION] and "skill" not in texts[OPEN_SESSION].lower()
