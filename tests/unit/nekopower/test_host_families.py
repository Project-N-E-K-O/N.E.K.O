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


@pytest.fixture(autouse=True)
def _leaders_in_git_repositories(monkeypatch: pytest.MonkeyPatch) -> None:
    """A project in a git repository gets a leader with its first commission here (``_in_git``).

    Off as shipped (``test_a_project_in_a_git_repository_has_no_leader_as_shipped``): switched on
    for this file, where it is the shortest way to a project with a leader.
    """
    monkeypatch.setattr(settings, "LEADER_IN_GIT_REPOSITORIES", True)


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


def host_requests(host: Any) -> list[list[dict[str, Any]]]:
    """The requests the one scripted model of a host was sent (``_weekly_reports``)."""
    return host.scripted.requests


async def _weekly_reports(hosts, tmp_path, llm: FakeDecisionLLM, more: int = 0) -> tuple[Any, list[Any]]:
    """Three commissions of the same job, the model asked about the second and the third saying so."""
    llm.belongs = [{"goes_to": 1, "relation": "recurrence"}]
    reports = ScriptedLLM([_report(f"week {week} written") for week in range(45, 48 + more)])
    host = _host(hosts, tmp_path, reports, decision_llm=llm)
    host.scripted = reports
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
    assert (again.session_id, again.relation) == (first.commission_id, orch.RECURRENCE)
    assert await _events(host, second.commission_id, "family_formed") == []

    done = await host.ledger.get(third.commission_id)
    # (What its task agent did before the project was made is the project's with it.)
    assert done.project and again.project == done.project
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
    opened = _user_messages(host_requests(host)[-1])[-1]
    assert brain_texts.MEMBER_EARLIER_WORK.format(places=member.workdir) in opened
    notes = await host.task_agents.families.member_notes(new)
    assert notes == brain_texts.MEMBER_EARLIER_WORK.format(places=member.workdir)
    assert await host.task_agents.families.member_notes(member) == brain_texts.MEMBER_EARLIER_WORK.format(places=new.workdir)
    # One that works where every other member does is told nothing.
    beside = await host.ledger.create_session(name="beside", lanlan_name="Lan", kind="line", project=project, workdir=new.workdir)
    assert await host.task_agents.families.member_notes(beside) == brain_texts.MEMBER_EARLIER_WORK.format(places=member.workdir)
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
    # The leader that is there takes it: none is made, and none is noted as made.
    assert await _events(host, second.commission_id, "leader_derived") == []
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
        assert len(await _events(host, commission.commission_id, "told_to_leader")) == 1
        assert await _events(host, commission.commission_id, "told_to_session") == []
    left = [item for item, _ending in await host.ledger.handed_work(leader.id, limit=9)]
    assert left == [await host.ledger.get(third.commission_id)]


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
    # On record as decided, and as where it went instead.
    (decided,) = await _events(host, fourth.commission_id, "orchestrated")
    assert (decided["action"], decided["agent"]) == ("continue", first.commission_id)
    assert decided["placed"] == {"action": "to_leader", "session": derived["session_id"]}
    assert "placed" not in (await _events(host, first.commission_id, "orchestrated"))[0]
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

    await host.ledger.update_session(leader.id, workdir="D:/budget")
    leader = await host.ledger.get_session(leader.id)
    for name, workdir in (("member-13", "D:/an-old-place"), ("member-12", "D:/budget")):
        (found,) = [item for item in await host.ledger.members("Lan", "Budget app") if item.name == name]
        await host.ledger.update_session(found.id, workdir=workdir)

    first, told = await host.task_agents.families.leader_notes(leader, "")
    assert told == [] and first.startswith(brain_texts.LEADER_FIRST_OPENING)
    # Where a member works is said when that is somewhere else than the leader does.
    assert first.count("  works in ") == 1 and "  works in D:/an-old-place" in first
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
            [call("hand_over", {"to": "Shop site/worker", "description": "again", "brief": "Do it."}), finish("tool_calls")],
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
    assert said.count(brain_texts.HAND_OVER_TWICE) == 2 and brain_texts.HANDED_OVER_ALREADY in said


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


# ---------------------------------------------------------------------------
# Where a project works: its leader and the members that work there move with it
# ---------------------------------------------------------------------------


async def test_changing_where_a_project_works_moves_its_leader_and_the_members_that_work_there(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    was = await _project(host, "Budget app", tmp_path)
    elsewhere, wanted = tmp_path / "an-old-one", tmp_path / "new-place"
    elsewhere.mkdir()
    wanted.mkdir()
    leader, _made = await host.ledger.project_session("Lan", "Budget app", workdir=str(was))
    here = await host.ledger.create_session(name="here", lanlan_name="Lan", kind="line", project="Budget app", workdir=str(was))
    unset = await host.ledger.create_session(name="unset", lanlan_name="Lan", kind="line", project="Budget app")
    # One that was a session of its own before it became the project's keeps its directory.
    adopted = await host.ledger.create_session(
        name="adopted", lanlan_name="Lan", kind="line", project="Budget app", workdir=str(elsewhere)
    )
    child = await host.ledger.create_session(
        name="child", lanlan_name="Lan", kind="child", project="Budget app", parent_id=here.id, workdir=str(was)
    )
    # One the host gave a directory of its own, as a session of this project; and one that got its
    # directory as a session of no project, before it became this project's.
    given = await host.ledger.create_session(name="given", lanlan_name="Lan", kind="line", project="Budget app")
    await host.ledger.update_session(given.id, workdir=str(host.paths.work_dir(given.id, "Budget app")))
    loose = await host.ledger.create_session(name="loose", lanlan_name="Lan", kind="line", project="Budget app")
    await host.ledger.update_session(loose.id, workdir=str(host.paths.work_dir(loose.id, None)))

    await host.set_project_workdir("Budget app", str(wanted))

    async def works(session: Any) -> str | None:
        return (await host.ledger.get_session(session.id)).workdir

    assert [await works(item) for item in (leader, here, unset, given)] == [str(wanted)] * 4
    assert await works(adopted) == str(elsewhere) and await works(child) == str(was)
    assert await works(loose) == str(host.paths.work_dir(loose.id, None))
    # By itself the ledger moves the leader and no member.
    before = await host.ledger.move_project("Budget app", str(was))
    assert before == {leader.id: str(wanted)} and await works(here) == str(wanted)
    assert (await host.space.get_project("Budget app")).workdir == str(wanted)


async def test_a_project_is_at_work_while_one_of_its_members_is(hosts, tmp_path):
    from nekopower.host.service import ProjectBusy

    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    member_llm = ScriptedLLM([[wait, *_report("header fixed")]])
    host = _host(hosts, tmp_path, _by_role(ScriptedLLM(_hand_over()), member_llm))
    workdir = await _project(host, "Shop site", tmp_path)
    wanted = tmp_path / "new-place"
    wanted.mkdir()
    _in_git(host, workdir)
    await _allow_members(host)
    receipt = await _dispatch(host, "fix the header of the shop site")
    await wait_until(lambda: len(member_llm.requests) == 1, what="the member being at work")
    await _let_go_of(host, (await host.ledger.lead_session("Lan", "Shop site")).id)

    # The leader is closed and holds nothing: it is its member that is at work.
    assert await host._project_busy("Shop site") == "at_work"
    assert await host.ledger.move_project("Shop site", str(wanted), moves=lambda _id, _workdir: True) is None
    with pytest.raises(ProjectBusy):
        await host.set_project_workdir("Shop site", str(wanted))

    release.set()
    await wait_status(host, receipt.commission_id, "done")
    await _let_go(host)
    assert await host._project_busy("Shop site") is None
    await host.set_project_workdir("Shop site", str(wanted))
    member = await _session_of(host, receipt.commission_id)
    assert member.workdir == str(wanted)


async def _let_go_of(host: Any, session_id: str) -> None:
    await wait_until(lambda: session_id not in host.task_agents._held_by, what="the session being let go")
    await host.task_agents.settle()


async def test_a_commission_taken_up_again_starts_where_its_project_works_by_then(hosts, tmp_path):
    """A commission that failed is not under way until it is at work again: the directory changed meanwhile is the one it starts in."""
    llm = ScriptedLLM([[call("report", {"status": "failed", "summary": "the file is missing"}), finish("tool_calls")],
                       _report("export added")])
    host = _host(hosts, tmp_path, llm)
    was = await _project(host, "Budget app", tmp_path)
    wanted = tmp_path / "new-place"
    wanted.mkdir()
    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "failed")
    await _let_go(host)
    agents = host.task_agents

    # Taking it up reads where its session works, and then takes a while (the model is settled,
    # its directory made ready). The user changes where the project works in between: nothing is
    # under way in the project, so nothing keeps them from it.
    reached, go_on = asyncio.Event(), asyncio.Event()
    choose = agents._choose_runtime

    async def slowly(*args: Any, **kwargs: Any) -> Any:
        reached.set()
        await go_on.wait()
        return await choose(*args, **kwargs)

    agents._choose_runtime = slowly
    telling = asyncio.ensure_future(agents.tell(first.commission_id, "the file is there now"))
    await asyncio.wait_for(reached.wait(), 10)
    assert (await host.ledger.get_session(first.commission_id)).workdir == str(was)
    await host.set_project_workdir("Budget app", str(wanted))
    go_on.set()
    assert await asyncio.wait_for(telling, 10) is True
    done = await wait_status(host, first.commission_id, "done")

    # It worked in the new directory, and that is what is on record: of the commission, and of its session.
    assert Path(done.workdir) == wanted
    assert Path((await host.ledger.get_session(first.commission_id)).workdir) == wanted
    assert (await host.space.get_project("Budget app")).workdir == str(wanted)


# ---------------------------------------------------------------------------
# A task agent the user makes; what the ledger keeps
# ---------------------------------------------------------------------------


async def test_a_task_agent_the_user_makes_is_a_leader_only_with_the_leaders_preset(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([]))

    led = await host.open_session("Lan", "Shop site", preset="boss", duty="runs the shop site")
    worked = await host.open_session("Lan", "Photo album", preset="general", title="Photo cat")
    looked = await host.open_session("Lan", "Reading list", preset="scout")

    leader = await host.ledger.get_session(led["session_id"])
    assert (leader.kind, leader.preset, leader.project, leader.duty_by) == ("project", "boss", "Shop site", "user")
    assert (await host.ledger.lead_session("Lan", "Shop site")).id == leader.id
    for made, preset, name in ((worked, "general", "Photo album"), (looked, "scout", "Reading list")):
        member = await host.ledger.get_session(made["session_id"])
        assert (member.kind, member.preset, member.project, member.name) == ("line", preset, name, name)
        assert member.model_ident and await host.ledger.lead_session("Lan", name) is None
    assert (await host.ledger.get_session(worked["session_id"])).title_by == "user"


async def test_the_one_session_a_project_had_before_leaders_is_its_member_from_then_on(tmp_path):
    import sqlite3

    from nekopower.host.ledger import Ledger

    path = tmp_path / "ledger.db"
    first = Ledger(path)
    old = await first.create_session(lanlan_name="Lan", name="Budget app", kind="project", project="Budget app")
    named = await first.create_session(lanlan_name="Lan", name="Reader", kind="project", project="Reading", preset="scout")
    single = await first.create_session(lanlan_name="Lan", name="errand", kind="single")
    await first.close()
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA user_version=7")
        conn.commit()
    finally:
        conn.close()

    second = Ledger(path)
    try:
        kept = [await second.get_session(item.id) for item in (old, named, single)]
        # It did its project's work, and goes on doing it; each keeps the preset it was made with.
        assert [(item.kind, item.preset) for item in kept] == [("line", "boss"), ("line", "scout"), ("single", "general")]
        # A leader made from here on stays one when the ledger is opened again.
        leader, _made = await second.project_session("Lan", "Budget app")
    finally:
        await second.close()
    third = Ledger(path)
    try:
        again = await third.get_session(leader.id)
        assert (again.kind, again.preset) == ("project", "boss")
        assert [item.id for item in await third.members("Lan", "Budget app")] == [old.id]
    finally:
        await third.close()


async def test_sessions_of_one_commission_become_members_where_they_are(tmp_path):
    from nekopower.host.ledger import Ledger

    ledger = Ledger(tmp_path / "ledger.db")
    try:
        loose = await ledger.create_session(lanlan_name="Lan", name="reports", kind="single", workdir="D:/reports", title="Reports")
        theirs = await ledger.create_session(lanlan_name="Lan", name="other", kind="line", project="Other")
        child = await ledger.create_session(lanlan_name="Lan", name="child", kind="child", parent_id=loose.id)

        made = await ledger.adopt([loose.id, theirs.id, child.id, "no-such-session"], "Sales")

        assert [item.id for item in made] == [loose.id]
        kept = await ledger.get_session(loose.id)
        assert (kept.kind, kept.project, kept.workdir, kept.title, kept.preset) == ("line", "Sales", "D:/reports", "Reports", "general")
        assert (await ledger.get_session(theirs.id)).project == "Other" and (await ledger.get_session(child.id)).kind == "child"
        assert [item.id for item in await ledger.members("Lan", "Sales")] == [loose.id]
        assert await ledger.members("Mia", "Sales") == []
    finally:
        await ledger.close()


async def test_a_member_ends_like_a_session_of_one_commission_and_a_leader_does_not(tmp_path):
    from nekopower.host.ledger import Ledger

    ledger = Ledger(tmp_path / "ledger.db")
    try:
        old = ledger._clock() - (settings.SESSION_SINGLE_LIFETIME_DAYS + 1) * 86400
        made = {
            kind: await ledger.create_session(lanlan_name="Lan", name=kind, kind=kind, project="p" if kind != "single" else None)
            for kind in ("project", "line", "single")
        }
        for session in made.values():
            await ledger.update_session(session.id, last_active_at=old)

        alive = {kind: await ledger.session_alive(await ledger.get_session(session.id)) for kind, session in made.items()}

        assert alive == {"project": True, "line": False, "single": False}
        await ledger.update_session(made["line"].id, last_active_at=ledger._clock())
        assert await ledger.session_alive(await ledger.get_session(made["line"].id))
    finally:
        await ledger.close()


async def test_the_timeline_tells_where_a_commission_went_and_what_was_made_for_it(hosts, tmp_path):
    from nekopower.host.timeline import TIMELINE_KINDS

    leader_llm = ScriptedLLM(_hand_over())
    host = _host(hosts, tmp_path, _by_role(leader_llm, ScriptedLLM([_report("header fixed")])))
    workdir = await _project(host, "Shop site", tmp_path)
    _in_git(host, workdir)
    await _allow_members(host)
    receipt = await _dispatch(host, "fix the header of the shop site")
    done = await wait_status(host, receipt.commission_id, "done")
    await _let_go(host)
    leader = await host.ledger.lead_session("Lan", "Shop site")

    entries = (await host.timeline(project="Shop site"))["entries"]

    by_kind = {entry["kind"]: entry for entry in entries}
    assert {"orchestrated", "leader_derived", "handed_over"} <= set(by_kind) <= set(TIMELINE_KINDS)
    decided = by_kind["orchestrated"]
    assert (decided["text"], decided["peer"]) == ("", None)
    assert decided["detail"] == {
        "action": "to_leader", "project": "Shop site", "relation": "follow_up", "layer": "rules", "model_calls": 0,
        "seconds": decided["detail"]["seconds"],
    }
    derived = by_kind["leader_derived"]
    assert (derived["text"], derived["detail"]) == ("Shop site", {"project": "Shop site", "reason": "git"})
    assert derived["peer"]["session_id"] == leader.id
    handed = by_kind["handed_over"]
    assert (handed["text"], handed["detail"]) == ("Shop site/worker", {"preset": "worker", "new": True})
    assert handed["peer"]["session_id"] == done.session_id
    assert "fix the header" not in json.dumps([decided, derived, handed])


async def test_a_family_shows_on_the_timeline(hosts, tmp_path):
    llm = FakeDecisionLLM()
    host, (first, _second, third) = await _weekly_reports(hosts, tmp_path, llm)
    project = (await host.ledger.get(third.commission_id)).project

    entries = (await host.timeline(project=project))["entries"]

    (formed,) = [entry for entry in entries if entry["kind"] == "family_formed"]
    assert (formed["text"], formed["detail"]) == (project, {"project": project, "members": [first.commission_id]})
    # (The two before it are the project's as well by now, and went on as loose ones then.)
    decided = [entry for entry in entries if entry["kind"] == "orchestrated"]
    assert [entry["detail"]["action"] for entry in decided].count("form_family") == 1
    (formed_by,) = [entry for entry in decided if entry["detail"]["action"] == "form_family"]
    assert formed_by["peer"]["session_id"] == first.commission_id


async def test_what_a_template_of_the_leaders_name_withholds_its_members_are_without_as_well(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    await _project(host, "Shop site", tmp_path)
    templates = host.paths.space().root / "projects" / "Shop site" / "templates"
    templates.mkdir(parents=True)
    (templates / "boss.md").write_text(
        "---\nname: boss\ndescription: The lead of the shop site.\nwithhold: [web_fetch]\nwithhold_own: [write, edit, bash, pwsh]\n---\n"
        "Hand everything out.\n",
        encoding="utf-8",
    )
    leader, _made = await host.ledger.project_session("Lan", "Shop site")
    member = await host.ledger.create_session(
        name="worker", lanlan_name="Lan", kind="line", project="Shop site", parent_id=leader.id, preset="worker"
    )
    alone = await host.ledger.create_session(name="alone", lanlan_name="Lan", kind="line", project="Shop site", preset="worker")

    # What the leader's template takes from its task agents, a member it started is without; what
    # it takes from itself only, the member has.
    assert (await host.task_agents._template_of(member)).withheld() == frozenset({"web_fetch"})
    assert (await host.task_agents._template_of(leader)).withheld() == frozenset({"web_fetch", "write", "edit", "bash", "pwsh"})
    # A member nobody started goes by its own preset alone.
    assert (await host.task_agents._template_of(alone)).withheld() == frozenset()


async def test_whether_a_directory_is_in_a_git_repository_is_remembered_for_a_while(hosts, tmp_path, monkeypatch):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    families = host.task_agents.families
    asked: list[str] = []
    answers = {"D:/site": True}

    async def probe(directory: str) -> bool:
        asked.append(directory)
        return answers.get(directory, False)

    families._probe = probe
    now = {"at": 1000.0}
    families._clock = lambda: now["at"]

    assert await families.in_git("D:/site") and await families.in_git("D:/site") and not await families.in_git("D:/notes")
    assert not await families.in_git(None) and not await families.in_git("")
    assert asked == ["D:/site", "D:/notes"]
    # Looked at anew once it is old: a directory can become a repository.
    answers["D:/notes"] = True
    now["at"] += settings.LEADER_GIT_PROBE_SECONDS + 1
    assert await families.in_git("D:/notes") and asked == ["D:/site", "D:/notes", "D:/notes"]

    async def broken(directory: str) -> bool:
        raise OSError("git is gone")

    families._probe = broken
    now["at"] += settings.LEADER_GIT_PROBE_SECONDS + 1
    assert not await families.in_git("D:/site")


async def test_a_project_s_directory_is_looked_at_with_git_itself_when_nothing_else_says(hosts, tmp_path):
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    host.task_agents.families._probe = None
    repository, plain = tmp_path / "repository", tmp_path / "plain"
    (repository / "inside").mkdir(parents=True)
    plain.mkdir()
    await asyncio.to_thread(subprocess.run, ["git", "init", "-q", str(repository)], check=True)

    assert await host.task_agents.families.in_git(str(repository / "inside"))
    assert not await host.task_agents.families.in_git(str(plain))
    # Looked at only: nothing was made in either.
    assert sorted(item.name for item in plain.iterdir()) == []
    assert sorted(item.name for item in repository.iterdir()) == [".git", "inside"]


async def test_sessions_whose_project_is_archived_or_whose_record_was_removed_are_no_candidates(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    await _project(host, "Budget app", tmp_path)
    await host.ledger.create_session(name="there", lanlan_name="Lan", kind="line", project="Budget app", duty="budget work")
    await host.ledger.create_session(name="gone", lanlan_name="Lan", kind="line", project="Archived one", duty="old work")
    await host.ledger.create_session(name="cleared", lanlan_name="Lan", kind="single", duty="desks", status="cleared")
    kept = await host.ledger.create_session(name="kept", lanlan_name="Lan", kind="single", duty="chairs")
    await host.ledger.create_session(name="hers", lanlan_name="Mia", kind="single", duty="another character's")
    commission, _made = await host.ledger.create(lanlan_name="Lan", title="t", dispatch_text="t", source="dispatch")

    standing = await host.task_agents.families.standing(commission)

    assert [project.name for project in standing.projects] == ["Budget app"]
    assert sorted((agent.duty, agent.project) for agent in standing.agents) == [("budget work", "Budget app"), ("chairs", None)]
    assert standing.agent(kept.id).idle_seconds > 0
    # One with a commission under way is at work, however long ago its session's status was written.
    under_way, _made = await host.ledger.create(
        lanlan_name="Lan", title="chairs", dispatch_text="chairs", source="dispatch", session_id=kept.id
    )
    assert under_way.status == "queued"
    assert (await host.task_agents.families.standing(commission)).agent(kept.id).idle_seconds == 0.0


async def test_a_task_agent_that_ended_between_the_look_and_the_write_is_followed_by_a_new_one(hosts, tmp_path):
    from nekopower.host.orchestrator import Verdict

    host = _host(hosts, tmp_path, ScriptedLLM([]))
    await _project(host, "Budget app", tmp_path)
    old = host.ledger._clock() - (settings.SESSION_SINGLE_LIFETIME_DAYS + 1) * 86400
    loose = await host.ledger.create_session(name="loose", lanlan_name="Lan", kind="single")
    member = await host.ledger.create_session(name="member", lanlan_name="Lan", kind="line", project="Budget app")
    for session in (loose, member):
        await host.ledger.update_session(session.id, last_active_at=old)
    families = host.task_agents.families

    async def filed(text: str) -> Any:
        commission, _made = await host.ledger.create(lanlan_name="Lan", title=text, dispatch_text=text, source="dispatch")
        return commission

    # Where it was to go on took no more work by the time it is written down: a new one, of the same kind.
    first = await filed("one")
    commission, session = await families.place(first, Verdict(action=orch.CONTINUE, agent=loose.id, relation=orch.FOLLOW_UP))
    assert (session.id, session.kind, commission.project) == (first.id, "single", None)
    second = await filed("two")
    commission, session = await families.place(
        second, Verdict(action=orch.CONTINUE, agent=member.id, project="Budget app", relation=orch.FOLLOW_UP)
    )
    assert (session.id, session.kind, session.project, commission.project) == (second.id, "line", "Budget app", "Budget app")
    # Nor is one that is not on record gone on with.
    third = await filed("three")
    _commission, session = await families.place(third, Verdict(action=orch.CONTINUE, agent="no-such-session"))
    assert session.id == third.id


async def test_only_sessions_of_one_commission_make_a_family(hosts, tmp_path):
    from nekopower.host.orchestrator import Verdict

    host = _host(hosts, tmp_path, ScriptedLLM([]))
    await _project(host, "Budget app", tmp_path)
    member = await host.ledger.create_session(name="member", lanlan_name="Lan", kind="line", project="Budget app", title="Budget")
    families = host.task_agents.families
    commission, _made = await host.ledger.create(lanlan_name="Lan", title="t", dispatch_text="t", source="dispatch")

    # A member of a project is that project's: no second project is made of it.
    assert await families._form_family((member.id, "no-such-session")) is None
    assert [item.name for item in await host.space.list_projects() if not item.name.startswith("_")] == ["Budget app"]
    # The commission goes on where the verdict said, as one of no family.
    commission, session = await families.place(
        commission, Verdict(action=orch.FORM_FAMILY, agent=member.id, members=(member.id,), relation=orch.RECURRENCE)
    )
    assert session.id == member.id
    assert [event.type for event in await host.ledger.events(commission.id) if event.type == "family_formed"] == []


async def test_without_a_small_model_a_clear_match_is_gone_on_with_and_no_project_is_made(hosts, tmp_path):
    scripted = ScriptedLLM([_report("October done"), _report("November done"), _report("December done"), _report("x")])
    host = _host(hosts, tmp_path, scripted)
    host.task_agents.set_orchestrator_judge(None)
    # (The same words each time: a word that is nowhere on record would make the match less than clear.)
    texts = ["fill in the monthly invoice for the freelance client from the timesheet"] * 3
    receipts = []
    for text in texts:
        receipt = await _dispatch(host, text)
        await wait_status(host, receipt.commission_id, "done")
        await _let_go(host)
        receipts.append(receipt)

    # All three in the one session: clear by their words, the third time would make a project, and
    # nobody can say whether it is the same job once more.
    done = [await host.ledger.get(receipt.commission_id) for receipt in receipts]
    assert [item.session_id for item in done] == [receipts[0].commission_id] * 3
    assert [item.relation for item in done] == [orch.NEW, orch.RECURRENCE, orch.FOLLOW_UP]
    assert [item.name for item in await host.space.list_projects() if not item.name.startswith("_")] == []
    (decided,) = await _events(host, receipts[2].commission_id, "orchestrated")
    assert (decided["action"], decided["layer"], decided["model_calls"]) == ("continue", "retrieval", 0)
    # What matches less clearly is a new one, there being nobody to ask.
    other = await _dispatch(host, "what did the freelance client pay for the monthly invoice")
    assert (await wait_status(host, other.commission_id, "done")).session_id == other.commission_id


async def test_which_sessions_count_as_writing_in_a_project(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    await _project(host, "Budget app", tmp_path)
    agents = host.task_agents
    leader, _made = await host.ledger.project_session("Lan", "Budget app")

    async def made(name: str, **values: Any) -> Any:
        return await host.ledger.create_session(name=name, lanlan_name="Lan", **values)

    worker = await made("worker", kind="line", project="Budget app", preset="worker")
    general = await made("general", kind="line", project="Budget app")
    scout = await made("scout", kind="line", project="Budget app", preset="scout")
    inspector = await made("inspector", kind="line", project="Budget app", preset="inspector")
    child = await made("child", kind="child", project="Budget app", parent_id=worker.id)
    single = await made("single", kind="single")
    broken = await made("broken", kind="line", project="Budget app", preset="no-such-preset")
    for session in (leader, worker, general, scout, inspector, child, single, broken):
        await agents._note_writer(session.id)
    await agents._note_writer(None)
    await agents._note_writer("no-such-session")

    # The members that can change files, with a tool that writes or edits one or through the shell (the
    # inspector); one whose preset cannot be read is held to the rule. And a task agent such a member
    # started that can, noted with the member it works for.
    assert agents._writes_in == {
        **{item.id: ("Budget app", item.id) for item in (worker, general, broken, inspector)},
        child.id: ("Budget app", worker.id),
    }
    # What a leader starts is not noted: it looks and checks.
    looks = await made("looks", kind="child", project="Budget app", parent_id=leader.id, preset="inspector")
    await agents._note_writer(looks.id)
    assert looks.id not in agents._writes_in
    # One that is no member any more is no writer any more.
    await host.ledger.update_session(worker.id, project="_daily")
    await agents._note_writer(worker.id)
    assert worker.id not in agents._writes_in


async def test_a_member_at_work_cannot_hand_its_commission_over(hosts, tmp_path):
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    host = _host(hosts, tmp_path, ScriptedLLM([[wait, *_report("export added")]]))
    await _project(host, "Budget app", tmp_path)
    await _allow_members(host)
    receipt = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, receipt.commission_id, "working")
    member = await _session_of(host, receipt.commission_id)

    # At work, in a project, with its commission in hand: and no leader.
    with pytest.raises(RuntimeError, match=brain_texts.HAND_OVER_NOT_A_LEADER):
        await host.task_agents._cb_hand_over(member.id, {"description": "d", "brief": "b"})
    assert await host.ledger.members("Lan", "Budget app") == [member]
    release.set()
    await wait_status(host, receipt.commission_id, "done")


async def test_what_was_said_before_a_commission_goes_along_when_its_leader_hands_it_over(hosts, tmp_path):
    from nekopower.host.conversation import ROLE_USER, RecentConversation

    said = RecentConversation()
    leader_llm = ScriptedLLM(_hand_over())
    member_llm = ScriptedLLM([_report("header fixed")])
    host = _host(hosts, tmp_path, _by_role(leader_llm, member_llm), conversation=said)
    workdir = await _project(host, "Shop site", tmp_path)
    _in_git(host, workdir)
    await _allow_members(host)
    said.note("Lan", ROLE_USER, "the logo overlaps the menu on my phone")

    receipt = await _dispatch(host, "fix the header of the shop site")
    await wait_status(host, receipt.commission_id, "done")

    # The member's session holds nothing of the main dialogue: it is told what was said just before, as the leader was.
    opened_leader = "\n".join(_user_messages(leader_llm.requests[0]))
    opened_member = "\n".join(_user_messages(member_llm.requests[0]))
    for opened in (opened_leader, opened_member):
        assert brain_texts.TASK_AGENT_COMMISSION_EXCERPT_OPEN in opened and "the logo overlaps the menu on my phone" in opened


async def _unplaced(host: Any, project: str, title: str) -> Any:
    """A commission of a project that is not sent anywhere by what it says: it names its own directory."""
    commission, _made = await host.ledger.create(
        lanlan_name="Lan", title=title, dispatch_text=title, source="dispatch", project=project, route="task_agent"
    )
    return await host.ledger.update(commission.id, session_dir=f"kept-{commission.id}")


async def test_a_commission_of_a_project_that_is_not_sent_anywhere_goes_to_a_member_and_makes_no_leader(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    await _project(host, "Budget app", tmp_path)

    first = await host.task_agents._give_session(await _unplaced(host, "Budget app", "add a chart"))
    second = await host.task_agents._give_session(await _unplaced(host, "Budget app", "add a legend"))

    # A member that works, not a leader that only hands work on; and the one that is there is gone on with.
    member = await host.ledger.get_session(first.session_id)
    assert (member.kind, member.preset, member.project) == ("line", "general", "Budget app")
    assert second.session_id == first.session_id
    assert await host.ledger.lead_session("Lan", "Budget app") is None
    # One that is over is not gone on with: a new member.
    for commission in (first, second):
        await host.ledger.transition(commission.id, "working", reason="t")
        await host.ledger.transition(commission.id, "done", reason="t", result_summary="done")
    old = host.ledger._clock() - (settings.SESSION_SINGLE_LIFETIME_DAYS + 1) * 86400
    await host.ledger.update_session(member.id, last_active_at=old)
    third = await host.task_agents._give_session(await _unplaced(host, "Budget app", "add a title"))
    assert third.session_id != first.session_id
    assert (await host.ledger.get_session(third.session_id)).kind == "line"
    # Once the project has a leader, it is the leader's.
    leader = (await host.derive_leader("Budget app", "Lan"))["session_id"]
    fourth = await host.task_agents._give_session(await _unplaced(host, "Budget app", "add a footer"))
    assert fourth.session_id == leader


async def test_members_of_two_characters_do_not_write_in_one_project_at_once(hosts, tmp_path):
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    scripts = {
        "CSV export": ScriptedLLM([[wait, *_report("export added")]]),
        "pie chart": ScriptedLLM([_report("chart added")]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    await _project(host, "Budget app", tmp_path)

    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "working")
    second = await _dispatch(host, "draw a pie chart in the budget app", lanlan_name="Mio")
    await wait_until(lambda: host.task_agents.queued == [second.commission_id], what="the other character's member waiting")

    # A member of the other character's own, free: the project and its directory are the same one, so it waits.
    theirs = await _session_of(host, second.commission_id)
    assert (theirs.lanlan_name, theirs.kind, theirs.project) == ("Mio", "line", "Budget app")
    assert theirs.id != (await _session_of(host, first.commission_id)).id
    assert scripts["pie chart"].requests == []
    release.set()
    await wait_status(host, first.commission_id, "done")
    assert (await wait_status(host, second.commission_id, "done")).result_summary == "chart added"


async def test_a_leader_not_in_use_for_long_is_its_projects_leader_all_the_same(hosts, tmp_path, monkeypatch):
    # However few sessions are looked at for what a sentence is about, whether a project has a leader is asked on its own.
    monkeypatch.setattr(settings, "ORCHESTRATOR_SESSIONS_LOOKED_AT", 1)
    scripts = {
        "weather": ScriptedLLM([_report("sunny")]),
        "pie chart": ScriptedLLM([_report("chart added")]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    await _project(host, "Budget app", tmp_path)
    leader = (await host.derive_leader("Budget app", "Lan"))["session_id"]
    # A session in use since: the one that is looked at.
    await wait_status(host, (await _dispatch(host, "what is the weather tomorrow")).commission_id, "done")

    done = await wait_status(host, (await _dispatch(host, "draw a pie chart in the budget app")).commission_id, "done")

    assert done.session_id == leader
    assert await host.ledger.members("Lan", "Budget app") == []
    assert await host.ledger.leaders("Lan") == {"Budget app": leader}
    assert await host.ledger.leaders("Mio") == {}


async def test_the_session_a_project_had_before_leaders_works_on_and_keeps_what_a_template_of_the_users_withholds(
    hosts, tmp_path
):
    scripts = {"pie chart": ScriptedLLM([_report("chart added")])}
    specs: dict[str, Any] = {}
    host = _host(hosts, tmp_path, _recording(scripts_by_commission(scripts), specs))
    await _project(host, "Budget app", tmp_path)
    # As the ledger of an earlier build leaves it: a member, with the leader's preset.
    old = await host.ledger.create_session(
        lanlan_name="Lan", name="Budget app", kind="line", project="Budget app", preset="boss"
    )

    # By the built-in preset it is one that works: it has the tools that write, and the project's commissions.
    template = await host.task_agents._template_of(old)
    assert template.name == "general" and template.writes_files
    done = await wait_status(host, (await _dispatch(host, "draw a pie chart in the budget app")).commission_id, "done")
    assert done.session_id == old.id
    assert not set(LEADER_TOOLS) & set(specs[old.id].disabled_tools)
    assert await host.ledger.members("Lan", "Budget app") == [await host.ledger.get_session(old.id)]

    # A template of the user's in the leader's place, which withholds tools: the session keeps it, and what it withholds.
    folder = tmp_path / "nekopower" / "task_agent_space" / "templates"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "boss.md").write_text(
        "---\nname: boss\ndescription: Looks, and changes nothing.\nread_only: true\nwrites: none\n---\n", encoding="utf-8"
    )
    template = await host.task_agents._template_of(await host.ledger.get_session(old.id))
    assert template.name == "boss" and template.read_only and not template.writes_files
    assert {"write", "edit", "bash", "pwsh"} <= template.withheld()


async def test_a_leader_has_no_tool_that_does_the_work_under_a_template_of_the_users_either(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    await _project(host, "Budget app", tmp_path)
    folder = tmp_path / "nekopower" / "task_agent_space" / "templates"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "boss.md").write_text(
        "---\nname: boss\ndescription: Runs the project.\nwithhold: [web_fetch]\n---\nYou run it.", encoding="utf-8"
    )
    leader, _made = await host.ledger.project_session("Lan", "Budget app")

    template = await host.task_agents._template_of(leader)

    # The user's template, and without the tools that do the work all the same; those stay with whom it hands work to.
    assert template.body == "You run it." and template.withheld() == frozenset({"web_fetch", *LEADER_TOOLS})
    assert template.handed_down() == frozenset({"web_fetch"})


async def test_a_leader_made_between_the_decision_and_the_placing_gets_the_commission(hosts, tmp_path):
    from dataclasses import replace

    host = _host(hosts, tmp_path, ScriptedLLM([]))
    await _project(host, "Budget app", tmp_path)
    member = await host.ledger.create_session(lanlan_name="Lan", name="Budget app", kind="line", project="Budget app")
    families = host.task_agents.families

    async def filed(text: str) -> Any:
        commission, _made = await host.ledger.create(
            lanlan_name="Lan", title=text, dispatch_text=text, source="dispatch", route="task_agent"
        )
        return commission

    first, second = await filed("add a chart to the budget app"), await filed("add a legend to the budget app")
    verdict = await families.decide(first)
    assert (verdict.action, verdict.agent) == (orch.CONTINUE, member.id)
    leader = (await host.derive_leader("Budget app", "Lan"))["session_id"]

    # Decided while the project had no leader, placed once it has one: it is the leader's.
    placed, session = await families.place(first, verdict)
    assert placed.session_id == session.id == leader
    # The same for one that was to get a new member.
    placed, session = await families.place(second, replace(verdict, action=orch.SUMMON_IN_PROJECT, agent=None))
    assert placed.session_id == session.id == leader
    assert await host.ledger.members("Lan", "Budget app") == [await host.ledger.get_session(member.id)]

    for commission, was in ((first, "continue"), (second, "summon_in_project")):
        (decided,) = await _events(host, commission.id, "orchestrated")
        assert (decided["action"], decided["placed"]) == (was, {"action": "to_leader", "session": leader})
        # The leader was there already: none was made for it.
        assert await _events(host, commission.id, "leader_derived") == []
    entries = (await host.timeline(project="Budget app"))["entries"]
    shown = [entry for entry in entries if entry["kind"] == "orchestrated"]
    assert [(entry["detail"]["action"], entry["detail"]["placed"]) for entry in shown] == [
        ("continue", "to_leader"), ("summon_in_project", "to_leader"),
    ] or [(entry["detail"]["action"], entry["detail"]["placed"]) for entry in shown] == [
        ("summon_in_project", "to_leader"), ("continue", "to_leader"),
    ]
    assert all(entry["peer"]["session_id"] == leader for entry in shown)


async def test_what_the_ledger_finds_for_a_family_and_for_a_leader(tmp_path):
    from nekopower.host.ledger import Ledger

    ledger = Ledger(tmp_path / "ledger.db")
    try:

        async def filed(title: str, **values: Any) -> Any:
            commission, _made = await ledger.create(
                lanlan_name="Lan", title=title, dispatch_text=title, source="dispatch", route="task_agent", **values
            )
            return commission

        # What a review suggested for any commission of a session, the latest: not only for the one last given to it.
        reports = await ledger.create_session(lanlan_name="Lan", name="reports", kind="single")
        other = await ledger.create_session(lanlan_name="Lan", name="other", kind="single")
        weekly = await filed("write the weekly report", session_id=reports.id)
        await ledger.append_event(weekly.id, "project_suggested", {"name": "Weekly reports"})
        typo = await filed("fix a typo in the weekly report", session_id=reports.id)
        assert (await ledger.last_commission(reports.id)).id == typo.id
        assert await ledger.last_event_in(reports.id, "project_suggested") == {"name": "Weekly reports"}
        assert await ledger.came_up([reports.id]) == {}
        await ledger.append_event(typo.id, "project_suggested", {"name": "Reports"})
        assert await ledger.last_event_in(reports.id, "project_suggested") == {"name": "Reports"}
        assert await ledger.last_event_in(other.id, "project_suggested") is None
        assert await ledger.last_event_in(reports.id, "family_formed") is None

        # One an earlier build filed under a project stays that project's; the others become members.
        theirs = await ledger.create_session(lanlan_name="Lan", name="alpha job", kind="single", project="Alpha")
        daily = await ledger.create_session(lanlan_name="Lan", name="daily job", kind="single", project="_daily")
        made = await ledger.adopt([theirs.id, reports.id, daily.id], "Beta")
        assert [item.id for item in made] == [reports.id, daily.id]
        kept = await ledger.get_session(theirs.id)
        assert (kept.kind, kept.project) == ("single", "Alpha")
        # What a member did before is the project's with it: taken up again, it is told the project's rules.
        assert [(await ledger.get(item.id)).project for item in (weekly, typo)] == ["Beta", "Beta"]

        # What a leader handed over and the user stopped is among what it is told came of its work.
        leader, _made = await ledger.project_session("Lan", "Beta")
        stopped = await filed("draw a logo", project="Beta", session_id=reports.id, handed_by=leader.id)
        under_way = await filed("write the help page", project="Beta", session_id=reports.id, handed_by=leader.id)
        await ledger.transition(stopped.id, "cancelled", reason="stopped by the user")

        async def handed(limit: int = 5) -> list[Any]:
            return [item for item, _ending in await ledger.handed_work(leader.id, limit=limit)]

        [(_item, cancelled)] = await ledger.handed_work(leader.id, limit=5)
        assert [item.id for item in await handed()] == [stopped.id]
        # That the member's session was told of it (a copy of it had worked on it) is not the leader being told.
        await ledger.append_event(stopped.id, "told_to_session", {"commission_id": under_way.id})
        assert [item.id for item in await handed()] == [stopped.id]
        await ledger.append_event(stopped.id, "told_to_leader", {"commission_id": under_way.id, "ending": cancelled})
        assert await handed() == []
        # Told of how it ended: one that got stuck, was taken up again and ended anew is told once more,
        # whatever the clock says of the two endings (here it stands still: both bear the same time).
        ledger._clock = lambda at=ledger._clock(): at
        await ledger.transition(under_way.id, "working", reason="t")
        await ledger.transition(under_way.id, "blocked", reason="t", result_summary="which page?")
        [(stuck, stuck_at)] = await ledger.handed_work(leader.id, limit=5)
        assert (stuck.id, stuck.status) == (under_way.id, "blocked")
        await ledger.transition(under_way.id, "queued", reason="taken up again")
        await ledger.transition(under_way.id, "working", reason="t")
        await ledger.transition(under_way.id, "done", reason="t", result_summary="help page written")
        # Also where it ended anew before the leader's being told that it was stuck was put on record.
        await ledger.append_event(under_way.id, "told_to_leader", {"commission_id": stopped.id, "ending": stuck_at})
        [(anew, anew_at)] = await ledger.handed_work(leader.id, limit=5)
        assert (anew.result_summary, anew.ended_at == stuck.ended_at, anew_at > stuck_at) == ("help page written", True, True)
        await ledger.append_event(under_way.id, "told_to_leader", {"commission_id": stopped.id, "ending": anew_at})
        assert await handed() == []
        # However many it was told of since, one it was not told of is found; ``limit`` at a time, the latest first.
        # (The clock goes on again, a second at a time: which came later is told by it.)
        import itertools

        ticks = itertools.count(int(ledger._clock()) + 10)
        ledger._clock = lambda: float(next(ticks))
        early = await filed("an early one", project="Beta", session_id=reports.id, handed_by=leader.id)
        await ledger.transition(early.id, "working", reason="t")
        await ledger.transition(early.id, "done", reason="t", result_summary="early one done")
        for index in range(12):
            later = await filed(f"a later one {index}", project="Beta", session_id=reports.id, handed_by=leader.id)
            await ledger.transition(later.id, "working", reason="t")
            await ledger.transition(later.id, "done", reason="t", result_summary="done")
            if index < 10:
                told_of = next(ending for item, ending in await ledger.handed_work(leader.id, limit=50) if item.id == later.id)
                await ledger.append_event(later.id, "told_to_leader", {"commission_id": stopped.id, "ending": told_of})
        assert [item.title for item in await handed(limit=2)] == ["a later one 11", "a later one 10"]
        assert [item.title for item in await handed()] == ["a later one 11", "a later one 10", "an early one"]
        # What was made the project's with its session says so, for whoever reads what happened from where it left off.
        filed_under = [event.payload for event in await ledger.events(weekly.id) if event.type == "filed_under_project"]
        assert filed_under == [{"project": "Beta"}]
        assert under_way.status not in ("done", "blocked", "failed", "cancelled")
    finally:
        await ledger.close()


async def _handing_over(hosts, tmp_path) -> Any:
    """A leader that called ``hand_over`` and whose turn has not ended: the commission is being handed over."""
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    called, ended = _hand_over()
    leader_llm = ScriptedLLM([called, [wait, *ended]])
    member_llm = ScriptedLLM([_report("header fixed")])
    host = _host(hosts, tmp_path, _by_role(leader_llm, member_llm))
    workdir = await _project(host, "Shop site", tmp_path)
    _in_git(host, workdir)
    await _allow_members(host)
    receipt = await _dispatch(host, "fix the header of the shop site")
    await wait_until(lambda: len(leader_llm.requests) == 2, what="the leader having handed it over")
    session = host.task_agents._sessions[receipt.commission_id]
    assert session.handing_over is not None
    return host, receipt, session, leader_llm, member_llm, release


async def test_what_the_user_says_while_a_commission_is_handed_over_is_said_to_the_member(hosts, tmp_path):
    host, receipt, _session, leader_llm, member_llm, release = await _handing_over(hosts, tmp_path)

    assert await host.tell(receipt.commission_id, "and make the logo smaller")
    release.set()
    done = await wait_status(host, receipt.commission_id, "done")

    # After the leader's brief, in the user's own words; the leader's session, being closed, was told nothing more.
    said = _user_messages(member_llm.requests[0])
    brief = next(index for index, text in enumerate(said) if "Do what the commission says" in text)
    words = next(index for index, text in enumerate(said) if "and make the logo smaller" in text)
    assert brief <= words and "and make the logo smaller" not in said[brief].split("Do what the commission says")[0]
    assert "make the logo smaller" not in json.dumps(leader_llm.requests)
    assert await _events(host, receipt.commission_id, "told") == [{"text": "and make the logo smaller", "queued": True}]
    assert done.handed_by == (await host.ledger.lead_session("Lan", "Shop site")).id


async def test_a_leader_whose_session_is_gone_as_it_is_closed_has_handed_its_commission_over(hosts, tmp_path):
    host, receipt, session, _leader_llm, _member_llm, release = await _handing_over(hosts, tmp_path)

    # Its runtime process is gone: the close is answered with an error, and no ``closed`` follows.
    session.closing = True
    await host.task_agents._handle(
        {"type": "error", "session_id": session.session_id, "command": "close", "message": "the runtime process exited"}
    )

    done = await wait_status(host, receipt.commission_id, "done")
    leader = await host.ledger.lead_session("Lan", "Shop site")
    assert (done.result_summary, done.handed_by) == ("header fixed", leader.id) and done.session_id != leader.id
    release.set()
    await _let_go(host)


@pytest.mark.parametrize("failures, outcome", [(1, "done"), (9, "failed")])
async def test_a_leader_that_cannot_be_closed_is_asked_again_and_given_up_on_after_a_few_times(
    hosts, tmp_path, monkeypatch, failures, outcome
):
    monkeypatch.setattr("config.nekopower_settings.REST_LOOK_RETRY_DELAYS", (0.01, 0.01))
    leader_llm = ScriptedLLM(_hand_over())
    member_llm = ScriptedLLM([_report("header fixed")])
    host = _host(hosts, tmp_path, _by_role(leader_llm, member_llm))
    workdir = await _project(host, "Shop site", tmp_path)
    _in_git(host, workdir)
    await _allow_members(host)
    close = host.task_agents._close_session
    failed: list[int] = []

    async def closing(session: Any) -> None:
        if session.handing_over is not None and len(failed) < failures:
            failed.append(1)
            raise OSError("the runtime is busy")
        await close(session)

    monkeypatch.setattr(host.task_agents, "_close_session", closing)

    receipt = await _dispatch(host, "fix the header of the shop site")
    done = await wait_status(host, receipt.commission_id, outcome)

    leader = await host.ledger.lead_session("Lan", "Shop site")
    handed = await _events(host, receipt.commission_id, "handed_over")
    if outcome == "done":
        # Asked once more, it closed: handed over as if nothing had been the matter.
        assert len(failed) == 1 and (done.result_summary, done.handed_by) == ("header fixed", leader.id)
        assert [(item["from"], item["to"], item["new"]) for item in handed] == [(leader.id, done.session_id, True)]
    else:
        # Nothing says it was handed over: it was not.
        assert handed == []
        # Asked as often as it is asked, in vain: the commission fails, and its place is let go.
        assert len(failed) == 3 and member_llm.requests == []
        assert "could not be closed to hand it over" in str(done.status_reason)
        await wait_until(lambda: receipt.commission_id not in host.task_agents._sessions, what="its place being let go")
        assert done.session_id == leader.id and done.handed_by is None
    await _let_go(host)


async def test_a_task_agent_a_member_started_ends_with_the_members_commission(hosts, tmp_path):
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    lead = ScriptedLLM(
        [
            [call("subagent", {"description": "write the export", "prompt": "Write the CSV export."}), finish("tool_calls")],
            _report("export added"),
        ]
    )
    child = ScriptedLLM([[wait, say("written"), finish()]])
    other = ScriptedLLM([_report("chart added")])
    seen: dict[str, ScriptedLLM] = {}

    def pick(spec: Any) -> ScriptedLLM:
        if not spec.top_level:
            return child
        return seen.setdefault(spec.session_id, other if seen else lead)

    host = _host(hosts, tmp_path, pick)
    await _project(host, "Budget app", tmp_path)

    first = await _dispatch(host, "add a CSV export to the budget app")
    done = await wait_status(host, first.commission_id, "done")
    (started,) = await host.ledger.select(parent_id=done.id)

    # The member holds the project's turn at changing files for its commission, and the task agents it
    # started are noted with it: as its run ends they are stopped, and then its session is let go.
    assert host.task_agents._writes_in[started.session_id] == ("Budget app", done.session_id)
    await _let_go(host)
    assert (await host.ledger.get(started.id)).status == "cancelled" and len(child.requests) == 1
    second = await _dispatch(host, "draw a pie chart in the budget app", lanlan_name="Mio")
    assert (await wait_status(host, second.commission_id, "done")).result_summary == "chart added"
    release.set()
    await _let_go(host)


async def test_a_task_agent_an_earlier_build_filed_under_a_project_makes_no_family_of_another(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    theirs = await host.ledger.create_session(lanlan_name="Lan", name="alpha job", kind="single", project="Alpha")
    commission, _made = await host.ledger.create(
        lanlan_name="Lan", title="the alpha job again", dispatch_text="the alpha job again", source="dispatch", route="task_agent"
    )
    verdict = orch.Verdict(action=orch.FORM_FAMILY, members=(theirs.id,), relation=orch.RECURRENCE)

    placed, session = await host.task_agents.families.place(commission, verdict)

    # No project is made of it, and it stays whose it was: the commission gets a task agent of its own.
    assert [item.name for item in await host.space.list_projects() if not item.name.startswith("_")] == []
    kept = await host.ledger.get_session(theirs.id)
    assert (kept.kind, kept.project) == ("single", "Alpha")
    assert (session.kind, session.id, placed.project) == ("single", commission.id, None)
    (decided,) = await _events(host, commission.id, "orchestrated")
    assert (decided["action"], decided["placed"]) == ("form_family", {"action": "summon_stray", "session": commission.id})
    assert await _events(host, commission.id, "family_formed") == []


async def test_a_leader_starts_no_task_agent_that_could_change_files(hosts, tmp_path):
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    def starts(**arguments: Any) -> list[Any]:
        return [call("subagent", {"description": "a part of it", "prompt": "Do this part.", **arguments}), finish("tool_calls")]

    leader_llm = ScriptedLLM(
        [
            starts(), starts(preset="worker"), starts(preset="inspector"), starts(preset="scout"),
            # While the scout is at it the commission is not handed over: what it finds would go to whoever has it then.
            _hand_over()[0],
            [call("wait_agent", {"timeout_ms": 600_000}, "c4"), finish("tool_calls")],
            *_hand_over(),
        ]
    )
    scout_llm = ScriptedLLM([[wait, say("The header is in base.html."), finish()]])
    member_llm = ScriptedLLM([_report("header fixed")])
    specs: list[Any] = []

    def pick(spec: Any) -> ScriptedLLM:
        specs.append(spec)
        if not spec.top_level:
            return scout_llm
        return leader_llm if "hand_over" not in spec.disabled_tools else member_llm

    host = _host(hosts, tmp_path, pick)
    workdir = await _project(host, "Shop site", tmp_path)
    _in_git(host, workdir)
    await _allow_members(host)

    receipt = await _dispatch(host, "fix the header of the shop site")
    await wait_until(lambda: len(leader_llm.requests) == 6, what="the leader waiting for its scout")

    # Neither the worker it gets when it names no preset, nor one it names, nor an inspector, which
    # keeps the shell: only one that reads was started.
    started = await host.ledger.select(parent_id=receipt.commission_id)
    assert [(await host.ledger.get_session(item.session_id)).preset for item in started] == ["scout"]
    (child_spec,) = [spec for spec in specs if not spec.top_level]
    assert {"write", "edit", "bash", "pwsh"} <= set(child_spec.disabled_tools)
    wanted = brain_texts.LEADER_SUBAGENT_WRITES.split("{preset}")[0]
    answers = [str(message.get("content")) for message in leader_llm.requests[-1] if message.get("role") == "tool"]
    refused = [text for text in answers if wanted in text]
    assert len(refused) == 3 and all("scout, planner" in text and "hand_over" in text for text in refused)
    assert [text for text in answers if brain_texts.HAND_OVER_AGENTS_AT_WORK in text] != []
    assert host.task_agents._sessions[receipt.commission_id].handing_over is None
    assert await _events(host, receipt.commission_id, "handed_over") == []
    release.set()
    done = await wait_status(host, receipt.commission_id, "done")
    assert done.result_summary == "header fixed" and len(await _events(host, receipt.commission_id, "handed_over")) == 1


async def test_a_project_in_a_git_repository_has_no_leader_as_shipped(hosts, tmp_path, monkeypatch):
    import importlib

    # As the settings file has it, not as this file's fixture sets it.
    shipped = importlib.util.find_spec("config.nekopower_settings").origin
    assert "\nLEADER_IN_GIT_REPOSITORIES = False\n" in Path(shipped).read_text(encoding="utf-8")
    monkeypatch.setattr(settings, "LEADER_IN_GIT_REPOSITORIES", False)
    scripts = {"the header": ScriptedLLM([_report("header fixed")]), "the footer": ScriptedLLM([_report("footer fixed")])}
    specs: dict[str, Any] = {}
    host = _host(hosts, tmp_path, _recording(scripts_by_commission(scripts), specs))
    workdir = await _project(host, "Shop site", tmp_path)
    looked: list[str] = []

    async def probe(directory: str) -> bool:
        looked.append(directory)
        return True

    host.task_agents.families._probe = probe

    first = await wait_status(host, (await _dispatch(host, "fix the header of the shop site")).commission_id, "done")
    await _let_go(host)
    second = await wait_status(host, (await _dispatch(host, "fix the footer of the shop site")).commission_id, "done")

    # A member that works, in the project's directory, for both: no leader, nothing handed over, and git not asked.
    member = await host.ledger.get_session(first.session_id)
    assert (member.kind, member.preset, member.workdir) == ("line", "general", str(workdir))
    assert second.session_id == first.session_id and (first.handed_by, second.handed_by) == (None, None)
    assert await host.ledger.lead_session("Lan", "Shop site") is None
    assert not set(LEADER_TOOLS) & set(specs[member.id].disabled_tools)
    assert looked == []
    assert await _events(host, first.id, "leader_derived") == []
    # The user asking for one still makes one.
    assert (await host.derive_leader("Shop site", "Lan"))["made"] is True


async def test_new_members_of_a_project_have_names_of_their_own_and_are_handed_work_by_them(hosts, tmp_path):
    leader_llm = ScriptedLLM([*_hand_over(), *_hand_over(), *_hand_over(to="Shop site/worker 2")])
    members = {
        "the header": ScriptedLLM([_report("header fixed")]),
        "the footer": ScriptedLLM([_report("footer fixed")]),
        "the menu": ScriptedLLM([_report("menu folded")]),
    }
    host = _host(hosts, tmp_path, _by_role(leader_llm, members))
    workdir = await _project(host, "Shop site", tmp_path)
    _in_git(host, workdir)
    await _allow_members(host)

    done = []
    for text in ("fix the header of the shop site", "fix the footer of the shop site", "fold the menu of the shop site"):
        done.append(await wait_status(host, (await _dispatch(host, text)).commission_id, "done"))
        await _let_go(host)

    # Two workers opened one after the other are told apart by name, and the one named gets the third.
    first, second = [await host.ledger.get_session(item.session_id) for item in done[:2]]
    assert (first.name, second.name) == ("Shop site/worker", "Shop site/worker 2") and first.id != second.id
    assert (done[2].session_id, done[2].result_summary) == (second.id, "menu folded")
    names = [member.name for member in await host.ledger.members("Lan", "Shop site")]
    assert sorted(names) == ["Shop site/worker", "Shop site/worker 2"]
    # A name is taken within a project, for one character.
    assert await host.ledger.member_name("Lan", "Shop site", "Shop site/worker") == "Shop site/worker 3"
    assert await host.ledger.member_name("Lan", "Shop site", "Shop site") == "Shop site"
    assert await host.ledger.member_name("Mio", "Shop site", "Shop site/worker") == "Shop site/worker"
    # And only there: another project has it free.
    assert await host.ledger.member_name("Lan", "Garden", "Shop site/worker") == "Shop site/worker"


async def test_how_often_a_matter_came_up_is_counted_over_all_a_task_agent_did(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    session = await host.ledger.create_session(lanlan_name="Lan", name="weekly report", kind="single", duty="weekly report")

    async def filed(title: str, relation: str | None, status: str = "done", **values: Any) -> Any:
        commission, _made = await host.ledger.create(
            lanlan_name="Lan", title=title, dispatch_text=title, source="dispatch", route="task_agent",
            session_id=session.id, **({"relation": relation} if relation else {}), **values,
        )
        if status != "queued":
            await host.ledger.transition(commission.id, "working", reason="t")
            await host.ledger.transition(commission.id, status, reason="t", result_summary="ok")
        return commission

    # Written twice, each time followed by three changes: the latest three on record are all changes.
    await filed("write the weekly report", None)
    for index in range(3):
        await filed(f"change line {index} of the weekly report", orch.FOLLOW_UP)
    again = await filed("write the weekly report again", orch.RECURRENCE)
    # One filed before relations were kept that followed another up is no time it came up.
    await filed("one more change to the weekly report", None, continues=again.id)
    for index in range(3):
        await filed(f"change line {index} of the second weekly report", orch.FOLLOW_UP)
    # Neither one under way, nor one a task agent started, nor the one being placed is counted.
    await filed("write the weekly report a third time", orch.RECURRENCE, status="queued")
    now = await filed("write this week's weekly report", None, status="queued")

    standing = await host.task_agents.families.standing(now)
    agent = standing.agent(session.id)

    # (Of the latest three read, two are not over.)
    assert [item.filed for item in agent.done] == [orch.FOLLOW_UP] and agent.came_up == 2
    assert await host.ledger.came_up([session.id, "no-such-session"], skip=now.id) == {session.id: 2}
    assert await host.ledger.came_up([]) == {}
    verdict = orch.settle(orch.AGENT_KEY + session.id, orch.RECURRENCE, standing, orch.Bars())
    assert (verdict.action, verdict.members) == (orch.FORM_FAMILY, (session.id,))
    # Where only what it did lately is told (the scenarios), that is what is counted.
    from dataclasses import replace

    lately = orch.Standing(projects=standing.projects, agents=tuple(replace(item, came_up=None) for item in standing.agents))
    assert orch.settle(orch.AGENT_KEY + session.id, orch.RECURRENCE, lately, orch.Bars()).action == orch.CONTINUE


async def test_a_task_agent_whose_stop_is_not_seen_through_keeps_the_projects_turn_until_it_is_gone(
    hosts, tmp_path, monkeypatch
):
    monkeypatch.setattr("config.nekopower_settings.REST_LOOK_RETRY_DELAYS", (0.01, 0.01))
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    lead = ScriptedLLM(
        [
            [call("subagent", {"description": "write the export", "prompt": "Write the CSV export."}), finish("tool_calls")],
            _report("export added"),
        ]
    )
    child = ScriptedLLM([[wait, say("written"), finish()]])
    other = ScriptedLLM([_report("chart added")])
    seen: dict[str, ScriptedLLM] = {}

    def pick(spec: Any) -> ScriptedLLM:
        if not spec.top_level:
            return child
        return seen.setdefault(spec.session_id, other if seen else lead)

    host = _host(hosts, tmp_path, pick)
    await _project(host, "Budget app", tmp_path)
    abort = host.task_agents.abort
    refused: list[str] = []

    async def failing(commission_id: str) -> None:
        commission = await host.ledger.get(commission_id)
        if commission is not None and commission.parent_id is not None:
            refused.append(commission_id)
            raise OSError("the runtime is busy")
        await abort(commission_id)

    monkeypatch.setattr(host.task_agents, "abort", failing)

    first = await _dispatch(host, "add a CSV export to the budget app")
    done = await wait_status(host, first.commission_id, "done")
    (started,) = await host.ledger.select(parent_id=done.id)
    await wait_until(lambda: done.session_id not in host.task_agents._held_by, what="the member being let go")

    # The member is over and let go; the task agent it started could not be stopped and is still at it.
    assert refused and host.task_agents._held_by.get(started.session_id) == started.id
    second = await _dispatch(host, "draw a pie chart in the budget app", lanlan_name="Mio")
    await wait_until(lambda: host.task_agents.queued == [second.commission_id], what="the other member waiting for it")
    assert other.requests == []
    release.set()
    assert (await wait_status(host, second.commission_id, "done")).result_summary == "chart added"
    await _let_go(host)


async def test_what_is_said_between_a_leader_letting_go_and_the_member_being_queued_reaches_the_member(
    hosts, tmp_path, monkeypatch
):
    host, receipt, _session, leader_llm, member_llm, release = await _handing_over(hosts, tmp_path)
    leader = await host.ledger.lead_session("Lan", "Shop site")
    update = host.ledger.update_session
    reached, go_on = asyncio.Event(), asyncio.Event()

    async def slowly(session_id: str, **values: Any) -> Any:
        if session_id == leader.id and values.get("status") == "closed" and not reached.is_set():
            reached.set()
            await go_on.wait()
        return await update(session_id, **values)

    monkeypatch.setattr(host.ledger, "update_session", slowly)
    release.set()
    await asyncio.wait_for(reached.wait(), 10)

    # The leader's session is let go of, and the commission is not queued for the member yet.
    assert receipt.commission_id not in host.task_agents._sessions
    assert (await host.ledger.get(receipt.commission_id)).status == "working"
    # Nothing else takes it up meanwhile: it would start where it was, in the leader's session. What a
    # wake brings waits; anything else is kept, and goes to the member with what the user said.
    from nekopower.host.task_agents import _Handed

    assert await host.task_agents._wake_working(receipt.commission_id, _Handed("a note", []), reason="woken") is False
    assert await host.task_agents._wake_working(receipt.commission_id, "what the scout found", reason="woken") is True
    assert receipt.commission_id not in host.task_agents._queue
    assert await host.tell(receipt.commission_id, "and the footer too")
    go_on.set()
    await wait_status(host, receipt.commission_id, "done")

    told = "\n".join(_user_messages(member_llm.requests[0]))
    assert "and the footer too" in told and "what the scout found" in told and "a note" not in told
    assert "and the footer too" not in json.dumps(leader_llm.requests)
    assert host.task_agents._said_while_handed == {}


async def test_what_a_leader_had_not_taken_as_it_hands_its_commission_over_waits_for_it_again(hosts, tmp_path):
    from nekopower.host.ledger import MessageLimits

    host, receipt, session, _leader_llm, _member_llm, release = await _handing_over(hosts, tmp_path)
    host.wakes._closed = True
    leader = await host.ledger.lead_session("Lan", "Shop site")
    other = await host.ledger.create_session(lanlan_name="Lan", name="other", kind="single")
    sender, _made = await host.ledger.create(
        lanlan_name="Lan", title="other", dispatch_text="other", source="dispatch", route="task_agent", session_id=other.id
    )
    # (Only a commission at work writes to another session.)
    await host.ledger.transition(sender.id, "working", reason="t")
    message, refusal, _wait = await host.ledger.message_add(
        from_session=other.id,
        from_commission=sender.id,
        to_session=leader.id,
        body="Mind the footer.",
        depth=1,
        notify_when_done=False,
        notice_after=3600.0,
        limits=MessageLimits(target_interval=0.0, per_hour=10, queue_max=5, dedup_seconds=600.0),
    )
    assert message is not None and refusal == "", refusal
    [wake] = await host.ledger.wakes_pending(session_id=leader.id)
    # Handed to the leader's session with a text it has not been seen to take.
    await host.ledger.wakes_fire([wake.id], into=receipt.commission_id)
    session.handed = {session.sent + 1: [wake.id]}
    assert await host.ledger.wakes_pending(session_id=leader.id) == []

    release.set()
    await wait_status(host, receipt.commission_id, "done")

    assert [item.id for item in await host.ledger.wakes_pending(session_id=leader.id)] == [wake.id]


async def test_every_task_agent_that_becomes_a_member_takes_its_turn_and_only_those_are_named(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    one = await host.ledger.create_session(lanlan_name="Lan", name="one", kind="single", title="Weekly sales report")
    two = await host.ledger.create_session(lanlan_name="Lan", name="two", kind="single", title="Sales report of the week")
    theirs = await host.ledger.create_session(lanlan_name="Lan", name="alpha job", kind="single", project="Alpha")
    commission, _made = await host.ledger.create(
        lanlan_name="Lan", title="the weekly sales report", dispatch_text="the weekly sales report", source="dispatch",
        route="task_agent",
    )
    verdict = orch.Verdict(
        action=orch.FORM_FAMILY, agent=one.id, members=(one.id, two.id, theirs.id), relation=orch.RECURRENCE
    )

    async def decided(_commission: Any) -> Any:
        return verdict

    host.task_agents.families.decide = decided

    placed = await host.task_agents._give_session(commission)

    # Both that became members are noted as ones that change files there, not only the one that takes this commission.
    assert placed.session_id == one.id and placed.project
    assert host.task_agents._writes_in == {item.id: (placed.project, item.id) for item in (one, two)}
    # The one that is another project's stays that, and is not said to be a member.
    (formed,) = await _events(host, commission.id, "family_formed")
    assert sorted(formed["members"]) == sorted([one.id, two.id])
    kept = await host.ledger.get_session(theirs.id)
    assert (kept.kind, kept.project) == ("single", "Alpha")


async def test_no_leader_is_made_while_a_commission_is_being_placed(hosts, tmp_path, monkeypatch):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    await _project(host, "Budget app", tmp_path)
    member = await host.ledger.create_session(lanlan_name="Lan", name="Budget app", kind="line", project="Budget app")
    commission, _made = await host.ledger.create(
        lanlan_name="Lan", title="add a chart to the budget app", dispatch_text="add a chart to the budget app",
        source="dispatch", route="task_agent",
    )
    families = host.task_agents.families
    verdict = await families.decide(commission)
    file = host.ledger.file_under_project
    looked: list[str] = []
    lead = host.ledger.lead_session
    reached, go_on = asyncio.Event(), asyncio.Event()

    async def looking(lanlan_name: str, project: str | None) -> Any:
        looked.append(str(project))
        return await lead(lanlan_name, project)

    async def slowly(commission_id: str, project: str, **values: Any) -> Any:
        if not reached.is_set():
            # After the look that found the project without a leader, before the commission is the member's.
            assert looked == ["Budget app"]
            reached.set()
            await go_on.wait()
        return await file(commission_id, project, **values)

    monkeypatch.setattr(host.ledger, "lead_session", looking)
    monkeypatch.setattr(host.ledger, "file_under_project", slowly)
    placing = asyncio.ensure_future(families.place(commission, verdict))
    await asyncio.wait_for(reached.wait(), 10)
    # However it is asked for: by the user for the project, or by opening a session with the leader's preset.
    deriving = asyncio.ensure_future(host.derive_leader("Budget app", "Lan"))
    opening = asyncio.ensure_future(families.make_leader("Lan", "Budget app"))
    await asyncio.sleep(0.05)

    # It waits for the placing: the leader is made after the commission is the member's, not in between.
    assert not deriving.done() and not opening.done() and await lead("Lan", "Budget app") is None
    go_on.set()
    placed, session = await placing
    made, (leader, _made) = await deriving, await opening
    assert (placed.session_id, session.id) == (member.id, member.id)
    assert made["session_id"] == leader.id and (await lead("Lan", "Budget app")).id == leader.id
    (decided,) = await _events(host, commission.id, "orchestrated")
    assert "placed" not in decided


async def test_a_task_agent_with_an_early_commission_still_under_way_is_not_taken_for_one_that_is_over(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    session = await host.ledger.create_session(lanlan_name="Lan", name="photos", kind="single", duty="sorting photos")

    async def filed(title: str, status: str | None) -> Any:
        commission, _made = await host.ledger.create(
            lanlan_name="Lan", title=title, dispatch_text=title, source="dispatch", route="task_agent", session_id=session.id
        )
        if status is not None:
            await host.ledger.transition(commission.id, "working", reason="t")
            if status != "working":
                await host.ledger.transition(commission.id, status, reason="t", result_summary="ok")
        return commission

    # One that is still at work, and after it more than are read for what the session is about.
    await filed("sort the photos of 2019", "working")
    for year in (2020, 2021, 2022, 2023):
        await filed(f"sort the photos of {year}", "done")
    old = host.ledger._clock() - (settings.SESSION_SINGLE_LIFETIME_DAYS + 1) * 86400
    await host.ledger.update_session(session.id, last_active_at=old)
    now = await filed("sort the photos of 2024", None)

    agent = (await host.task_agents.families.standing(now)).agent(session.id)

    assert all(item.title != "sort the photos of 2019" for item in agent.done) and agent.idle_seconds == 0.0
    assert await host.ledger.under_way_in([session.id, "no-such-session"], skip=now.id) == {session.id}
    assert await host.ledger.under_way_in([]) == set()


async def test_the_board_lists_the_members_that_work_and_not_every_one_a_project_ever_had(tmp_path):
    from nekopower.host.ledger import Ledger

    ledger = Ledger(tmp_path / "ledger.db")
    try:
        now = ledger._clock()
        long_ago = now - (settings.SESSION_SINGLE_LIFETIME_DAYS + 3) * 86400

        async def member(name: str, project: str, *, at: float, who: str = "Lan") -> Any:
            session = await ledger.create_session(lanlan_name=who, name=name, kind="line", project=project)
            await ledger.update_session(session.id, last_active_at=at)
            return session

        leader, _made = await ledger.project_session("Lan", "Shop site")
        await ledger.update_session(leader.id, last_active_at=long_ago)
        over = [await member(f"Shop site/worker {index}", "Shop site", at=long_ago - index) for index in range(3)]
        lately = await member("Shop site/scout", "Shop site", at=now - 3600)
        at_work = await member("Shop site/worker", "Shop site", at=long_ago)
        commission, _made = await ledger.create(
            lanlan_name="Lan", title="fix the header", dispatch_text="fix the header", source="dispatch",
            route="task_agent", session_id=at_work.id, project="Shop site",
        )
        await ledger.transition(commission.id, "working", reason="t")
        await ledger.update_session(at_work.id, last_active_at=long_ago)
        # A project without a leader whose one member was last in use long ago, and another character's in the first.
        alone = await member("Garden", "Garden", at=long_ago)
        before = await member("Garden 2", "Garden", at=long_ago - 5)
        theirs = await member("Shop site", "Shop site", at=long_ago, who="Mio")

        async def listed(**asked: Any) -> list[str]:
            rows = await ledger.board_rows(
                recent_since=now - settings.BOARD_RECENT_SECONDS, child_since=now, day_start=now, loose_max=5,
                children_max=3, events_each=5, **asked,
            )
            return [session.id for session in rows.sessions]

        shown = await listed()
        # The leader, however long ago; the member at work, the one in use lately, and of each project and
        # character the one last in use: not the ones a project had before.
        assert set(shown) == {leader.id, at_work.id, lately.id, alone.id, theirs.id}
        assert not {item.id for item in over} & set(shown) and before.id not in shown
        assert shown.index(leader.id) < min(shown.index(at_work.id), shown.index(lately.id))
        assert shown.index(at_work.id) < shown.index(lately.id)
        assert set(await listed(project="Garden")) == {alone.id}
        assert set(await listed(lanlan_name="Mio")) == {theirs.id}
    finally:
        await ledger.close()


async def test_a_new_member_that_could_change_files_through_the_shell_is_asked_about_as_well(hosts, tmp_path):
    host, _leader = await _leader_asks(
        hosts, tmp_path, _hand_over(preset="inspector", description="Checks the pages"), [_report("pages checked")]
    )

    receipt = await _dispatch(host, "check the pages of the shop site")
    waiting = await wait_status(host, receipt.commission_id, "waiting_user")

    # An inspector has no tool that writes a file and keeps the shell: asked about like one that writes.
    question = waiting.question()
    assert (question["kind"], question["category"], question["detail"]["preset"]) == ("permission", "open_session", "inspector")
    assert await host.answer(receipt.commission_id, "yes", choice="once")
    done = await wait_status(host, receipt.commission_id, "done")
    member = await host.ledger.get_session(done.session_id)
    assert (member.preset, done.result_summary) == ("inspector", "pages checked")
    assert host.task_agents._writes_in[member.id] == ("Shop site", member.id)


async def test_what_is_said_as_a_commission_is_handed_over_and_written_down_after_reaches_the_member(
    hosts, tmp_path, monkeypatch
):
    at_first, at_work = asyncio.Event(), asyncio.Event()

    async def first() -> None:
        await at_first.wait()

    async def working() -> None:
        await at_work.wait()

    called, ended = _hand_over()
    leader_llm = ScriptedLLM([[first, *called], ended])
    member_llm = ScriptedLLM([[working, *_report("header fixed")], [say("noted"), finish()]])
    host = _host(hosts, tmp_path, _by_role(leader_llm, member_llm))
    workdir = await _project(host, "Shop site", tmp_path)
    _in_git(host, workdir)
    await _allow_members(host)
    receipt = await _dispatch(host, "fix the header of the shop site")
    await wait_until(lambda: len(leader_llm.requests) == 1, what="the leader being at work")
    append = host.ledger.append_event
    writing, go_on = asyncio.Event(), asyncio.Event()

    async def slowly(commission_id: str, kind: str, payload: dict[str, Any]) -> Any:
        if kind == "told" and not writing.is_set():
            writing.set()
            await go_on.wait()
        return await append(commission_id, kind, payload)

    monkeypatch.setattr(host.ledger, "append_event", slowly)

    # Said while the leader is at work, before it hands the commission over; written down only after it has.
    telling = asyncio.ensure_future(host.tell(receipt.commission_id, "and make the logo smaller"))
    await asyncio.wait_for(writing.wait(), 10)
    at_first.set()
    await wait_until(lambda: len(member_llm.requests) == 1, what="the member being at work on it")
    assert receipt.commission_id not in host.task_agents._said_while_handed
    go_on.set()
    assert await telling is True
    at_work.set()

    await wait_until(
        lambda: "and make the logo smaller" in json.dumps(member_llm.requests), what="the member being told it"
    )
    assert "and make the logo smaller" not in json.dumps(leader_llm.requests)
    await _let_go(host)


async def test_what_is_said_while_it_is_put_on_record_as_handed_over_reaches_the_member(hosts, tmp_path, monkeypatch):
    release, working = asyncio.Event(), asyncio.Event()

    async def wait() -> None:
        await release.wait()

    async def at_work() -> None:
        await working.wait()

    called, ended = _hand_over()
    leader_llm = ScriptedLLM([called, [wait, *ended]])
    member_llm = ScriptedLLM([[at_work, *_report("header fixed")], [say("noted"), finish()]])
    host = _host(hosts, tmp_path, _by_role(leader_llm, member_llm))
    workdir = await _project(host, "Shop site", tmp_path)
    _in_git(host, workdir)
    await _allow_members(host)
    receipt = await _dispatch(host, "fix the header of the shop site")
    await wait_until(lambda: len(leader_llm.requests) == 2, what="the leader having handed it over")
    append = host.ledger.append_event
    writing, go_on = asyncio.Event(), asyncio.Event()

    async def slowly(commission_id: str, kind: str, payload: dict[str, Any]) -> Any:
        if kind == "handed_over" and not writing.is_set():
            writing.set()
            await go_on.wait()
        return await append(commission_id, kind, payload)

    monkeypatch.setattr(host.ledger, "append_event", slowly)
    release.set()
    await asyncio.wait_for(writing.wait(), 10)

    # Queued for the member, the record not written yet: nothing is kept aside any more, and it is said to it there.
    assert receipt.commission_id not in host.task_agents._said_while_handed
    assert await host.tell(receipt.commission_id, "and the footer too")
    go_on.set()
    working.set()

    await wait_until(lambda: "and the footer too" in json.dumps(member_llm.requests), what="the member being told it")
    assert "and the footer too" not in json.dumps(leader_llm.requests)
    assert len(await _events(host, receipt.commission_id, "handed_over")) == 1
    await _let_go(host)


async def test_a_commission_stopped_as_its_leader_lets_go_is_not_put_on_record_as_handed_over(
    hosts, tmp_path, monkeypatch, caplog
):
    host, receipt, _session, _leader_llm, member_llm, release = await _handing_over(hosts, tmp_path)
    leader = await host.ledger.lead_session("Lan", "Shop site")
    update = host.ledger.update_session
    reached, go_on = asyncio.Event(), asyncio.Event()

    async def slowly(session_id: str, **values: Any) -> Any:
        if session_id == leader.id and values.get("status") == "closed" and not reached.is_set():
            reached.set()
            await go_on.wait()
        return await update(session_id, **values)

    monkeypatch.setattr(host.ledger, "update_session", slowly)
    assert await host.tell(receipt.commission_id, "and make the logo smaller")
    release.set()
    await asyncio.wait_for(reached.wait(), 10)
    # Stopped between the leader letting go of it and its being queued for the member.
    await host.ledger.transition(receipt.commission_id, "cancelled", reason="stopped by the user")
    with caplog.at_level("WARNING"):
        go_on.set()
        await wait_until(lambda: receipt.commission_id not in host.task_agents._said_while_handed, what="the hand-over ending")
        await host.task_agents.settle()

    stopped = await host.ledger.get(receipt.commission_id)
    # It stays the leader's, no member is started for it, and nothing says it was handed over.
    assert (stopped.status, stopped.session_id, stopped.handed_by) == ("cancelled", leader.id, None)
    assert await _events(host, receipt.commission_id, "handed_over") == [] and member_llm.requests == []
    # What was said to it meanwhile is on record with it, and that it was not passed on is logged, without the words.
    assert await _events(host, receipt.commission_id, "told") == [{"text": "and make the logo smaller", "queued": True}]
    logged = [record.getMessage() for record in caplog.records if "was not handed over" in record.getMessage()]
    assert len(logged) == 1 and "1 thing(s)" in logged[0] and "logo" not in logged[0]


async def test_two_calls_to_hand_one_commission_over_at_once_hand_it_over_once(hosts, tmp_path, monkeypatch):
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    leader_llm = ScriptedLLM([[wait, say("thinking"), finish()]])
    host = _host(hosts, tmp_path, _by_role(leader_llm, ScriptedLLM([])))
    workdir = await _project(host, "Shop site", tmp_path)
    _in_git(host, workdir)
    await _allow_members(host)
    receipt = await _dispatch(host, "fix the header of the shop site")
    await wait_until(lambda: len(leader_llm.requests) == 1, what="the leader being at work")
    leader = await host.ledger.lead_session("Lan", "Shop site")
    hand_over = host.task_agents._cb_hand_over

    # Two calls of one turn, side by side: both are let in before either has handed anything over.
    outcomes = await asyncio.gather(
        hand_over(leader.id, {"description": "one", "brief": "b"}),
        hand_over(leader.id, {"description": "two", "brief": "b"}),
        return_exceptions=True,
    )

    refused = [item for item in outcomes if isinstance(item, Exception)]
    assert len(refused) == 1 and str(refused[0]) == brain_texts.HAND_OVER_TWICE
    # One member was made, and it is the one the commission is to go to.
    (member,) = await host.ledger.members("Lan", "Shop site")
    assert host.task_agents._sessions[receipt.commission_id].handing_over[0] == member.id
    assert host.task_agents._handing_claims == set()
    host.brain.drop_pending_dispatches()
    release.set()


async def test_a_task_agent_that_became_a_project_s_since_is_gone_on_with_there_and_no_other_project_is_made(
    hosts, tmp_path
):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    await _project(host, "Reports", tmp_path)
    taker = await host.ledger.create_session(lanlan_name="Lan", name="weekly report", kind="single", title="Weekly report")
    other = await host.ledger.create_session(lanlan_name="Lan", name="report again", kind="single", title="Weekly report again")
    commission, _made = await host.ledger.create(
        lanlan_name="Lan", title="the weekly report", dispatch_text="the weekly report", source="dispatch", route="task_agent"
    )
    verdict = orch.Verdict(action=orch.FORM_FAMILY, agent=taker.id, members=(taker.id, other.id), relation=orch.RECURRENCE)
    # Between the decision and the placing another commission made it a member of a project.
    await host.ledger.adopt([taker.id], "Reports")

    placed, session = await host.task_agents.families.place(commission, verdict)

    assert (placed.session_id, session.id, placed.project) == (taker.id, taker.id, "Reports")
    assert [item.name for item in await host.space.list_projects() if not item.name.startswith("_")] == ["Reports"]
    assert (await host.ledger.get_session(other.id)).kind == "single"
    assert await _events(host, commission.id, "family_formed") == []
    (decided,) = await _events(host, commission.id, "orchestrated")
    assert (decided["action"], decided["placed"]) == ("form_family", {"action": "continue", "session": taker.id})


async def test_what_a_task_agent_started_before_it_became_a_member_takes_the_projects_turn_with_it(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    agents = host.task_agents
    one = await host.ledger.create_session(lanlan_name="Lan", name="one", kind="single", title="Weekly sales report")
    started = await host.ledger.create_session(lanlan_name="Lan", name="one/agent-1", kind="child", parent_id=one.id, preset="worker")
    looks = await host.ledger.create_session(lanlan_name="Lan", name="one/agent-2", kind="child", parent_id=one.id, preset="scout")
    reads = await host.ledger.create_session(
        lanlan_name="Lan", name="reads", kind="single", title="Sales report figures", preset="scout"
    )
    for session in (one, started, looks, reads):
        await agents._note_writer(session.id)
        # At work, each for a commission of its own.
        agents._held_by[session.id] = f"at work in {session.id}"
    assert agents._writes_in == {}
    commission, _made = await host.ledger.create(
        lanlan_name="Lan", title="the weekly sales report", dispatch_text="the weekly sales report", source="dispatch",
        route="task_agent",
    )
    verdict = orch.Verdict(action=orch.FORM_FAMILY, agent=one.id, members=(one.id, reads.id), relation=orch.RECURRENCE)

    async def decided(_commission: Any) -> Any:
        return verdict

    agents.families.decide = decided
    looked: list[dict[str, Any]] = []

    async def next_one() -> None:
        looked.append(dict(agents._writes_in))

    agents._start_next = next_one
    agents._queue.append("another member's commission that waits")
    note, noting = agents._note_writer, []

    async def noted(session_id: Any, **more: Any) -> None:
        # While they are being noted, no task agent that changes files starts in the project.
        project = agents._writes_in[one.id][0]
        agents._writes_in["another member"] = (project, "another member")
        agents._session_of["its commission"] = "another member"
        noting.append((set(agents._noting) == {project}, agents._session_free_for("its commission")))
        del agents._writes_in["another member"], agents._session_of["its commission"]
        await note(session_id, **more)

    agents._note_writer = noted
    try:
        placed = await agents._give_session(commission)
        agents._note_writer = note
        await asyncio.sleep(0.01)
        assert noting and set(noting) == {(True, False)} and agents._noting == set()

        # The one it started that can change files is noted with it; the one that reads is not. Nor
        # is the member that reads only, at work though it is: that it changes files was a guess.
        assert (await host.ledger.get_session(reads.id)).kind == "line"
        assert agents._writes_in == {one.id: (placed.project, one.id), started.id: (placed.project, one.id)}
        # What waits is looked at once, when all of them are noted: not as the guess about one is
        # taken back, with the task agent another started not noted yet.
        assert looked == [{one.id: (placed.project, one.id), started.id: (placed.project, one.id)}]
        agents._queue.clear()
        # A run under way stays noted whatever is read of its preset meanwhile: it has the tools it was opened with.
        await host.ledger.update_session(one.id, project="_daily")
        await agents._note_writer(one.id)
        assert agents._writes_in[one.id] == (placed.project, one.id)
        del agents._held_by[one.id]
        await agents._note_writer(one.id)
        assert one.id not in agents._writes_in
    finally:
        agents._held_by.clear()


async def test_a_member_with_an_early_commission_still_under_way_is_not_said_to_be_idle(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    await _project(host, "Budget app", tmp_path)
    member = await host.ledger.create_session(lanlan_name="Lan", name="Budget app/worker", kind="line", project="Budget app")
    leader, _made = await host.ledger.project_session("Lan", "Budget app")

    async def filed(title: str, status: str) -> None:
        commission, _made = await host.ledger.create(
            lanlan_name="Lan", title=title, dispatch_text=title, source="dispatch", route="task_agent",
            session_id=member.id, project="Budget app",
        )
        await host.ledger.transition(commission.id, "working", reason="t")
        if status != "working":
            await host.ledger.transition(commission.id, status, reason="t", result_summary="ok")

    await filed("add the export", "working")
    for index in range(4):
        await filed(f"small fix {index}", "done")

    told, _handed = await host.task_agents.families.leader_notes(leader, "")

    # Of the latest three read for its card none is under way; the one that is was filed before them.
    (line,) = [text for text in told.splitlines() if text.startswith("- Budget app/worker ")]
    assert "at work" in line and "idle" not in line


async def test_the_member_the_user_opened_a_project_with_gets_its_work_whatever_its_preset(hosts, tmp_path):
    scripts = {"which fonts": ScriptedLLM([_report("two fonts")]), "the header": ScriptedLLM([_report("looked at the header")])}
    specs: dict[str, Any] = {}
    host = _host(hosts, tmp_path, _recording(scripts_by_commission(scripts), specs))
    opened = await host.open_session("Lan", "Shop site", preset="scout", duty="looks things up in the shop site")

    first = await wait_status(host, (await _dispatch(host, "find out which fonts the shop site uses")).commission_id, "done")
    await _let_go(host)
    second = await wait_status(host, (await _dispatch(host, "look at the header of the shop site")).commission_id, "done")

    # The one that is there, with what its preset keeps from it: no member that works without those limits is started.
    assert (first.session_id, second.session_id) == (opened["session_id"], opened["session_id"])
    (member,) = await host.ledger.members("Lan", "Shop site")
    assert (member.id, member.preset) == (opened["session_id"], "scout")
    assert {"write", "edit", "bash", "pwsh"} <= set(specs[member.id].disabled_tools)
    assert await host.ledger.lead_session("Lan", "Shop site") is None


async def test_a_member_that_was_not_among_the_sessions_looked_at_is_gone_on_with_and_no_other_is_started(
    hosts, tmp_path, monkeypatch
):
    # However few sessions are looked at for what a sentence is about, a project's members are asked of the ledger.
    monkeypatch.setattr(settings, "ORCHESTRATOR_SESSIONS_LOOKED_AT", 1)
    scripts = {"weather": ScriptedLLM([_report("sunny")]), "the header": ScriptedLLM([_report("looked at the header")])}
    specs: dict[str, Any] = {}
    host = _host(hosts, tmp_path, _recording(scripts_by_commission(scripts), specs))
    opened = await host.open_session("Lan", "Shop site", preset="scout", duty="looks things up in the shop site")
    # A session in use since: the one that is looked at.
    await wait_status(host, (await _dispatch(host, "what is the weather tomorrow")).commission_id, "done")
    await _let_go(host)

    receipt = await _dispatch(host, "look at the header of the shop site")
    done = await wait_status(host, receipt.commission_id, "done")

    assert done.session_id == opened["session_id"]
    assert [member.id for member in await host.ledger.members("Lan", "Shop site")] == [opened["session_id"]]
    (decided,) = await _events(host, receipt.commission_id, "orchestrated")
    assert (decided["action"], decided["placed"]) == ("summon_in_project", {"action": "continue", "session": done.session_id})
    assert {"write", "edit"} <= set(specs[done.session_id].disabled_tools)


async def test_a_task_agent_that_became_a_member_and_takes_no_more_work_is_followed_by_one_of_its_project(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    await _project(host, "Reports", tmp_path)
    taker = await host.ledger.create_session(lanlan_name="Lan", name="weekly report", kind="single", title="Weekly report")
    commission, _made = await host.ledger.create(
        lanlan_name="Lan", title="the weekly report", dispatch_text="the weekly report", source="dispatch", route="task_agent"
    )
    verdict = orch.Verdict(action=orch.FORM_FAMILY, agent=taker.id, members=(taker.id,), relation=orch.RECURRENCE)
    # Between the decision and the placing it became a member of a project, and its time ran out.
    await host.ledger.adopt([taker.id], "Reports")
    old = host.ledger._clock() - (settings.SESSION_SINGLE_LIFETIME_DAYS + 1) * 86400
    await host.ledger.update_session(taker.id, last_active_at=old)

    placed, session = await host.task_agents.families.place(commission, verdict)

    # A new member of the project it had joined: neither a loose task agent nor another project.
    assert (session.kind, session.project, placed.project) == ("line", "Reports", "Reports") and session.id != taker.id
    assert [item.name for item in await host.space.list_projects() if not item.name.startswith("_")] == ["Reports"]


async def test_a_commission_taken_up_again_is_noted_as_one_that_changes_files_though_nothing_was_noted_yet(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    await _project(host, "Budget app", tmp_path)
    agents = host.task_agents
    member = await host.ledger.create_session(lanlan_name="Lan", name="Budget app", kind="line", project="Budget app")
    commission, _made = await host.ledger.create(
        lanlan_name="Lan", title="add a chart", dispatch_text="add a chart", source="dispatch", route="task_agent",
        session_id=member.id, project="Budget app",
    )
    # As after a restart, where a commission that is taken up again is mapped to its session by another way.
    agents._writes_in.clear()
    agents._session_of[commission.id] = member.id

    assert await agents._know_session(commission.id) is True

    assert agents._writes_in == {member.id: ("Budget app", member.id)}


async def test_a_commission_that_went_while_its_member_was_looked_for_is_not_marked_as_handed_over(
    hosts, tmp_path, monkeypatch
):
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    leader_llm = ScriptedLLM([[wait, say("thinking"), finish()]])
    host = _host(hosts, tmp_path, _by_role(leader_llm, ScriptedLLM([])))
    workdir = await _project(host, "Shop site", tmp_path)
    _in_git(host, workdir)
    await _allow_members(host)
    member = await host.ledger.create_session(lanlan_name="Lan", name="Shop site/worker", kind="line", project="Shop site")
    receipt = await _dispatch(host, "fix the header of the shop site")
    await wait_until(lambda: len(leader_llm.requests) == 1, what="the leader being at work")
    leader = await host.ledger.lead_session("Lan", "Shop site")
    live = host.task_agents._sessions[receipt.commission_id]
    alive = host.ledger.session_alive

    async def meanwhile(session: Any, now: float | None = None) -> bool:
        # The commission is stopped while the member named is looked at: its session is closing.
        live.closing = True
        return await alive(session, now)

    monkeypatch.setattr(host.ledger, "session_alive", meanwhile)

    with pytest.raises(RuntimeError, match="not at work any more"):
        await host.task_agents._cb_hand_over(leader.id, {"to": member.name, "description": "d", "brief": "b"})

    assert live.handing_over is None and receipt.commission_id not in host.task_agents._said_while_handed
    assert host.task_agents._handing_claims == set()
    live.closing = False
    host.brain.drop_pending_dispatches()
    release.set()


async def test_a_member_found_as_another_would_be_started_gets_its_project_a_leader_when_it_has_too_much_waiting(
    hosts, tmp_path
):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    await _project(host, "Budget app", tmp_path)
    member = await host.ledger.create_session(lanlan_name="Lan", name="Budget app", kind="line", project="Budget app")
    for index in range(settings.LEADER_WHEN_UNDER_WAY):
        waiting, _made = await host.ledger.create(
            lanlan_name="Lan", title=f"job {index}", dispatch_text=f"job {index}", source="dispatch", route="task_agent",
            session_id=member.id, project="Budget app",
        )
    commission, _made = await host.ledger.create(
        lanlan_name="Lan", title="one more for the budget app", dispatch_text="one more for the budget app",
        source="dispatch", route="task_agent",
    )
    # As where the member was not among the sessions looked at: decided to start another in the project.
    verdict = orch.Verdict(action=orch.SUMMON_IN_PROJECT, project="Budget app", preset="general", relation=orch.FOLLOW_UP)

    placed, session = await host.task_agents.families.place(commission, verdict)

    # Not handed to the member that has enough, nor to another beside it: the project gets a leader.
    assert (session.kind, session.preset, placed.session_id) == ("project", "boss", session.id)
    (derived,) = await _events(host, commission.id, "leader_derived")
    assert derived["reason"] == "busy"
    assert [item.id for item in await host.ledger.members("Lan", "Budget app")] == [member.id]


async def test_task_agents_whose_taker_is_gone_still_become_a_project_and_one_of_them_takes_the_commission(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    other = await host.ledger.create_session(lanlan_name="Lan", name="report again", kind="single", title="Weekly report again")
    commission, _made = await host.ledger.create(
        lanlan_name="Lan", title="the weekly report", dispatch_text="the weekly report", source="dispatch", route="task_agent"
    )
    # The one that was to take it is not on record any more by the time the commission is placed.
    verdict = orch.Verdict(
        action=orch.FORM_FAMILY, agent="no-such-session", members=("no-such-session", other.id), relation=orch.RECURRENCE
    )

    placed, session = await host.task_agents.families.place(commission, verdict)

    (project,) = [item.name for item in await host.space.list_projects() if not item.name.startswith("_")]
    assert (session.id, session.kind, session.project, placed.project) == (other.id, "line", project, project)
    (formed,) = await _events(host, commission.id, "family_formed")
    assert formed == {"project": project, "members": [other.id]}


async def test_a_run_under_way_counts_by_the_tools_it_was_opened_with_whatever_its_preset_says_by_now(hosts, tmp_path):
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    scripts = {"sales report": ScriptedLLM([[wait, *_report("week 45 written")]])}
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    agents = host.task_agents
    receipt = await _dispatch(host, "write the weekly sales report for week 45")
    await wait_until(lambda: len(scripts["sales report"].requests) == 1, what="the task agent being at work")
    live = agents._sessions[receipt.commission_id]
    # Opened with the tools that change files; a loose one, so nothing is noted of it.
    assert live.changes_files is True and agents._writes_in == {}
    # Its template is changed to one that reads only while it works, and it becomes a project's member.
    folder = tmp_path / "nekopower" / "task_agent_space" / "templates"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "general.md").write_text(
        "---\nname: general\ndescription: Looks, and changes nothing.\nread_only: true\nwrites: none\n---\n", encoding="utf-8"
    )
    await _project(host, "Reports", tmp_path)
    await host.ledger.adopt([receipt.commission_id], "Reports")

    await agents._note_writer(receipt.commission_id, guessed=True)

    # The run has the tools it was opened with: it is one that changes files there, guess or no guess.
    assert agents._writes_in == {receipt.commission_id: ("Reports", receipt.commission_id)}
    # And the other way about: a run opened without them is taken off though it is at work.
    live.changes_files = False
    await agents._note_writer(receipt.commission_id)
    assert agents._writes_in == {}
    live.changes_files = True
    release.set()
    await wait_status(host, receipt.commission_id, "done")


async def test_what_waited_for_a_task_agent_noted_on_a_guess_is_looked_at_once_the_note_is_taken_back(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    agents = host.task_agents
    looked: list[int] = []

    async def next_one() -> None:
        looked.append(1)

    agents._start_next = next_one
    agents._writes_in["reads"] = ("Reports", "reads")
    agents._queue.append("waits")
    try:
        agents._unnote_writer("reads", guessed=True)
        await asyncio.sleep(0.01)
        assert agents._writes_in == {} and looked == [1]
        # Nothing was noted of it: nothing changed, and nothing is looked at.
        agents._unnote_writer("reads", guessed=True)
        await asyncio.sleep(0.01)
        assert looked == [1]
        # Nor where nothing waits.
        agents._queue.clear()
        agents._writes_in["reads"] = ("Reports", "reads")
        agents._unnote_writer("reads")
        await asyncio.sleep(0.01)
        assert agents._writes_in == {} and looked == [1]
    finally:
        agents._queue.clear()


async def test_no_task_agent_that_changes_files_starts_in_a_project_while_its_task_agents_are_being_noted(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([]))
    agents = host.task_agents
    agents._writes_in.update({"writer": ("Reports", "writer"), "elsewhere": ("Garden", "elsewhere")})
    agents._session_of.update({"its commission": "writer", "another commission": "elsewhere", "a reader's": "reader"})
    try:
        assert all(agents._session_free_for(item) for item in ("its commission", "another commission", "a reader's"))
        agents._noting.add("Reports")
        # The one that changes files there waits; one of another project, and one that only reads, do not.
        assert agents._session_free_for("its commission") is False
        assert agents._session_free_for("another commission") and agents._session_free_for("a reader's")
        agents._noting.discard("Reports")
        assert agents._session_free_for("its commission")
    finally:
        agents._noting.clear()
        agents._writes_in.clear()
        for item in ("its commission", "another commission", "a reader's"):
            agents._session_of.pop(item, None)
