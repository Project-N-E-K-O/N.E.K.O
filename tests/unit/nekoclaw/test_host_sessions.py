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

"""Sessions that outlast a commission: which session a commission is worked on in, and one at a time.

A project has one session, in which its commissions are worked on one after
the other; a commission that follows another up goes on in that one's
session; what a commission is, is said once, in the message that opens it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from config.prompts import prompts_nekoclaw as runtime_texts
from config.prompts import prompts_nekoclaw_brain as brain_texts
from tests.unit.nekoclaw.fakes import ScriptedLLM, call, finish, say
from tests.unit.nekoclaw.host_fakes import (
    FakeDecisionLLM,
    fake_model,
    make_host,
    pick_plugin,
    scripted_runtime,
    scripts_by_commission,
    wait_status,
    wait_until,
)
from tests.unit.nekoclaw.plugin_server_fake import FakePluginServer, entry, plugin
from tests.unit.nekoclaw.plugin_server_fake import finish as plugin_result


@pytest.fixture
async def hosts() -> Any:
    made: list[Any] = []
    yield made
    for host in made:
        await host.shutdown()


def _host(hosts: list[Any], tmp_path: Path, llm: Any, **kwargs: Any) -> Any:
    """A host with no plugins, so every dispatch goes to a task agent."""
    pick = llm if callable(llm) else (lambda spec: llm)
    kwargs.setdefault("task_agent_model", fake_model)
    kwargs.setdefault("decision_llm", FakeDecisionLLM())
    host = make_host(tmp_path, None, runtime_factory=scripted_runtime(pick), **kwargs)
    hosts.append(host)
    return host


async def _dispatch(host: Any, text: str, **kwargs: Any) -> Any:
    kwargs.setdefault("lanlan_name", "Lan")
    kwargs.setdefault("lang", "en")
    return await host.dispatch(text, **kwargs)


def _report(status: str = "ready", summary: str = "done") -> list[Any]:
    return [call("report", {"status": status, "summary": summary}), finish("tool_calls")]


def _gate() -> tuple[asyncio.Event, Any]:
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    return release, wait


async def _events(host: Any, commission_id: str, kind: str | None = None) -> list[Any]:
    await host.task_agents.settle()
    return [event for event in await host.ledger.events(commission_id) if kind is None or event.type == kind]


async def _released(host: Any, commission_id: str) -> None:
    await wait_until(lambda: commission_id not in host.task_agents.running, what="the session being released")
    await host.task_agents.settle()


async def _let_go(host: Any) -> None:
    """Wait until no commission holds a session any more."""
    await wait_until(lambda: host.task_agents._held_by == {}, what="every session being let go")


def _user_messages(request: list[dict[str, Any]]) -> list[str]:
    """What was said to the session, without the notices the runtime adds about the time."""
    said = [str(message["content"]) for message in request if message.get("role") == "user"]
    return [text for text in said if not text.startswith("<runtime_context>")]


def _opens(commission_id: str, text: str) -> str:
    return f"Commission {commission_id}\n{text}"


# ---------------------------------------------------------------------------
# A project has one session
# ---------------------------------------------------------------------------


async def test_commissions_of_a_project_are_worked_on_in_its_one_session_one_after_the_other(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        "add a CSV export": ScriptedLLM([[wait, *_report("ready", "export added")]]),
        "add a chart": ScriptedLLM([_report("ready", "chart added")]),
        "what is the weather": ScriptedLLM([_report("ready", "sunny")]),
    }
    seen: list[str] = []
    workdir = tmp_path / "budget-files"
    workdir.mkdir()
    host = _host(hosts, tmp_path, scripts_by_commission(scripts, seen))
    await host.space.touch_project("Budget app", workdir=str(workdir))

    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "working")
    second = await _dispatch(host, "add a chart to the budget app")
    other = await _dispatch(host, "what is the weather tomorrow")
    await host.brain.settle()

    # Places are free (ten of them), yet the second waits: its session is at work on the first.
    assert host.task_agents.queued == [second.commission_id]
    queued = await host.ledger.get(second.commission_id)
    assert (queued.status, queued.project, queued.session_id) == ("queued", "Budget app", first.commission_id)
    assert [event.payload for event in await _events(host, second.commission_id, "queued")] == [
        {"ahead": 0, "behind": first.commission_id}
    ]
    # A commission of no project has a session of its own and does not wait for either.
    unrelated = await wait_status(host, other.commission_id, "done")
    assert unrelated.session_id == other.commission_id
    assert (await host.ledger.get(second.commission_id)).status == "queued"
    assert (await host.ledger.get_session(first.commission_id)).status == "running"

    release.set()
    await wait_status(host, first.commission_id, "done")
    done = await wait_status(host, second.commission_id, "done")
    await _released(host, second.commission_id)
    await _let_go(host)

    assert seen == ["add a CSV export", "what is the weather", "add a chart"]
    former = await host.ledger.get(first.commission_id)
    session = await host.ledger.get_session(done.session_id)
    # One session, named after the project, kept where the first commission opened it.
    assert done.session_id == former.session_id == first.commission_id
    assert (session.kind, session.name, session.project, session.status) == ("project", "Budget app", "Budget app", "closed")
    assert done.session_dir == former.session_dir == session.session_dir
    assert Path(done.workdir) == Path(former.workdir) == Path(session.workdir) == workdir
    # The second went on from what the first left: it was asked with the first's
    # conversation in front, under the very system prompt the session was opened with.
    earlier, later = scripts["add a CSV export"].requests[0], scripts["add a chart"].requests[0]
    assert later[0] == earlier[0]
    said = _user_messages(later)
    assert said[0].startswith(_opens(first.commission_id, "add a CSV export to the budget app"))
    assert said[-1].startswith(_opens(second.commission_id, "add a chart to the budget app"))
    assert any(message.get("role") == "assistant" for message in later[1:-1])
    # What happened is on record with the commission it happened for.
    for commission_id, summary in ((first.commission_id, "export added"), (second.commission_id, "chart added")):
        reports = [event.payload["summary"] for event in await _events(host, commission_id, "report")]
        assert reports == [summary]
        assert len(await _events(host, commission_id, "opened")) == 1
    await _let_go(host)


async def test_another_characters_commission_of_the_project_has_a_session_of_its_own(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        "add a CSV export": ScriptedLLM([[wait, *_report()]]),
        "add a chart": ScriptedLLM([_report()]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    await host.space.touch_project("Budget app")

    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "working")
    second = await _dispatch(host, "add a chart to the budget app", lanlan_name="Mio")

    done = await wait_status(host, second.commission_id, "done")
    assert done.session_id == second.commission_id != first.commission_id
    release.set()
    await wait_status(host, first.commission_id, "done")


async def test_a_commission_waiting_for_the_user_keeps_its_session_and_the_next_one_waits(hosts, tmp_path):
    scripts = {
        "add a CSV export": ScriptedLLM(
            [_report("waiting", "Which separator?"), _report("ready", "export added with commas")]
        ),
        "add a chart": ScriptedLLM([_report("ready", "chart added")]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    await host.space.touch_project("Budget app")

    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "waiting_user")
    second = await _dispatch(host, "add a chart to the budget app")
    await host.brain.settle()

    assert (await host.ledger.get(second.commission_id)).status == "queued"
    assert (await host.ledger.get_session(first.commission_id)).status == "waiting"
    # Also when the runtime closes the idle session meanwhile, as one with nothing to do does.
    await wait_until(
        lambda: host.task_agents._sessions[first.commission_id].run_ended, what="the asking run ending"
    )
    await host.task_agents._runtime.close(first.commission_id)
    await _released(host, first.commission_id)
    assert host.task_agents._held_by == {first.commission_id: first.commission_id}
    assert (await host.ledger.get(second.commission_id)).status == "queued"
    assert (await host.ledger.get_session(first.commission_id)).status == "waiting"

    # The answer takes the first up again where it was, and only then is the second worked on.
    assert await host.answer(first.commission_id, "commas")
    await wait_status(host, first.commission_id, "done")
    done = await wait_status(host, second.commission_id, "done")

    assert done.session_id == first.commission_id
    later = _user_messages(scripts["add a chart"].requests[0])
    # A question and its answer stand together, before the next commission begins.
    assert later.index("commas") < len(later) - 1 and later[-1].startswith(_opens(second.commission_id, "add a chart"))


async def test_stopping_a_commission_that_waits_without_a_place_lets_the_next_one_of_its_session_go_on(
    hosts, tmp_path
):
    scripts = {
        "add a CSV export": ScriptedLLM([_report("waiting", "Which separator?")]),
        "add a chart": ScriptedLLM([_report("ready", "chart added")]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "waiting_user")
    second = await _dispatch(host, "add a chart to the budget app")
    await host.brain.settle()
    await wait_until(
        lambda: host.task_agents._sessions[first.commission_id].run_ended, what="the asking run ending"
    )
    # The runtime closes the idle session, as one with nothing to do does: the commission
    # goes on waiting, and holding its session, with no place in a runtime.
    await host.task_agents._runtime.close(first.commission_id)
    await _released(host, first.commission_id)
    assert host.task_agents._held_by == {first.commission_id: first.commission_id}
    assert (await host.ledger.get(second.commission_id)).status == "queued"

    # No runtime has its session open, so none will say that it is over: the ledger does.
    await host.abort(first.commission_id)

    done = await wait_status(host, second.commission_id, "done")
    assert done.session_id == first.commission_id
    assert (await host.ledger.get(first.commission_id)).status == "cancelled"
    await _released(host, second.commission_id)
    await _let_go(host)


@pytest.mark.parametrize("ending", ["blocked", "failed", "stopped"])
async def test_a_commission_that_is_over_without_being_done_lets_the_next_one_of_its_session_go_on(
    hosts, tmp_path, ending
):
    release, wait = _gate()
    scripts = {
        "add a CSV export": ScriptedLLM(
            [[wait, *_report("blocked" if ending == "blocked" else "failed", "no access to the files")]]
        ),
        "add a chart": ScriptedLLM([_report("ready", "chart added")]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "working")
    second = await _dispatch(host, "add a chart to the budget app")
    await host.brain.settle()
    assert (await host.ledger.get(second.commission_id)).status == "queued"

    if ending == "stopped":
        await host.abort(first.commission_id)
        release.set()
        await wait_status(host, first.commission_id, "cancelled")
    else:
        release.set()
        await wait_status(host, first.commission_id, ending)

    done = await wait_status(host, second.commission_id, "done")
    assert done.session_id == first.commission_id
    await _released(host, second.commission_id)
    await _let_go(host)


async def test_a_blocked_commission_taken_up_again_waits_for_its_session_like_any_other(hosts, tmp_path):
    release, wait = _gate()
    # One model for the session, asked in this order: the first commission, the second, the first again.
    llm = ScriptedLLM(
        [
            _report("blocked", "no access"),
            [wait, *_report("ready", "chart added")],
            _report("ready", "export added"),
        ]
    )
    host = _host(hosts, tmp_path, llm)
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "blocked")
    second = await _dispatch(host, "add a chart to the budget app")
    await wait_status(host, second.commission_id, "working")

    # The session is at work on the second by now: what the user adds to the first queues it.
    assert await host.tell(first.commission_id, "you have access now")
    assert (await host.ledger.get(first.commission_id)).status == "queued"
    assert host.task_agents.queued == [first.commission_id]

    release.set()
    await wait_status(host, second.commission_id, "done")
    done = await wait_status(host, first.commission_id, "done")
    assert done.result_summary == "export added"
    # The session was at the second commission since: it is told which one it is back at, and
    # that it is back at it, in front of what the user added. Told nothing but what the user
    # added, it would read that as part of the commission it worked on last.
    said = _user_messages(llm.requests[-1])
    assert any(text.startswith(_opens(second.commission_id, "add a chart")) for text in said[:-1])
    assert said[-1].startswith(_opens(first.commission_id, "add a CSV export"))
    assert brain_texts.TASK_AGENT_COMMISSION_BACK in said[-1]
    assert said[-1].endswith("you have access now")
    assert said[-1].index(brain_texts.TASK_AGENT_COMMISSION_BACK) < said[-1].index("you have access now")
    assert sum(1 for text in said if text.startswith(_opens(first.commission_id, "add a CSV export"))) == 2
    # Opened anew, so the runtime keeps this message through a summary, not the second one's.
    opened = [event.payload for event in await _events(host, first.commission_id, "opened")]
    assert opened == [{"session_id": first.commission_id}] * 2
    assert await host.ledger.opened_last(first.commission_id) == first.commission_id


async def test_stopping_a_commission_that_waits_for_a_place_to_be_answered_at_lets_its_session_go(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        "add a CSV export": ScriptedLLM([_report("waiting", "Which separator?")]),
        "add a chart": ScriptedLLM([_report("ready", "chart added")]),
        "sort the photos": ScriptedLLM([[wait, *_report("ready", "sorted")]]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts), max_task_agents=1)
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "waiting_user")
    await wait_until(
        lambda: host.task_agents._sessions[first.commission_id].run_ended, what="the asking run ending"
    )
    # The runtime closes the idle session; the one place goes to something else.
    await host.task_agents._runtime.close(first.commission_id)
    await _released(host, first.commission_id)
    other = await _dispatch(host, "sort the photos")
    await wait_status(host, other.commission_id, "working")
    second = await _dispatch(host, "add a chart to the budget app")
    await host.brain.settle()

    # Answered with no place free: it waits for one, still holding its session.
    assert await host.answer(first.commission_id, "a semicolon")
    assert (await host.ledger.get(first.commission_id)).status == "queued"
    assert host.task_agents._held_by == {
        first.commission_id: first.commission_id,
        other.commission_id: other.commission_id,
    }

    # Stopped there. No runtime has its session open, so none will say that it is over.
    await host.abort(first.commission_id)
    await wait_until(
        lambda: first.commission_id not in host.task_agents._held_by, what="the session of the stopped one being let go"
    )

    release.set()
    await wait_status(host, other.commission_id, "done")
    done = await wait_status(host, second.commission_id, "done")
    assert done.session_id == first.commission_id
    assert (await host.ledger.get(first.commission_id)).status == "cancelled"


async def test_a_commission_stopped_while_it_is_being_queued_again_lets_its_session_go(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        "add a CSV export": ScriptedLLM([_report("waiting", "Which separator?")]),
        "add a chart": ScriptedLLM([_report("ready", "chart added")]),
        "sort the photos": ScriptedLLM([[wait, *_report("ready", "sorted")]]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts), max_task_agents=1)
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "waiting_user")
    await wait_until(
        lambda: host.task_agents._sessions[first.commission_id].run_ended, what="the asking run ending"
    )
    await host.task_agents._runtime.close(first.commission_id)
    await _released(host, first.commission_id)
    other = await _dispatch(host, "sort the photos")
    await wait_status(host, other.commission_id, "working")
    second = await _dispatch(host, "add a chart to the budget app")
    await host.brain.settle()

    # The answer finds no place free and queues the commission; it is stopped at the very
    # moment its new status is written, so that is refused. It was in hand when it ended
    # (being queued), and is in nobody's hands after: its session has to be let go here.
    transition = host.ledger.transition

    async def stopped_first(commission_id: str, status: str, **kwargs: Any) -> Any:
        if commission_id == first.commission_id and status == "queued":
            assert (await transition(commission_id, "cancelled", reason="stopped by the user")).applied
        return await transition(commission_id, status, **kwargs)

    host.ledger.transition = stopped_first
    assert await host.answer(first.commission_id, "a semicolon") is False
    host.ledger.transition = transition
    await wait_until(
        lambda: first.commission_id not in host.task_agents._held_by, what="the session of the stopped one being let go"
    )

    release.set()
    await wait_status(host, other.commission_id, "done")
    done = await wait_status(host, second.commission_id, "done")
    assert done.session_id == first.commission_id
    assert (await host.ledger.get(first.commission_id)).status == "cancelled"


async def test_a_commission_that_cannot_be_started_again_lets_its_session_go(hosts, tmp_path):
    there = {"yes": True}

    async def model() -> Any:
        return {"model": "fake-model", "api_key": "test-key"} if there["yes"] else None

    scripts = {
        "add a CSV export": ScriptedLLM([_report("blocked", "no access")]),
        "add a chart": ScriptedLLM([_report("ready", "chart added")]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts), task_agent_model=model)
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "blocked")
    await _released(host, first.commission_id)
    await _let_go(host)

    # Taken up again while no model is set: it takes its session, and the start fails.
    there["yes"] = False
    await host.tell(first.commission_id, "you have access now")
    await wait_status(host, first.commission_id, "failed")
    await host.task_agents.settle()
    # Over, and never in a runtime: nothing held on to the session for it.
    await _let_go(host)

    there["yes"] = True
    second = await _dispatch(host, "add a chart to the budget app")
    done = await wait_status(host, second.commission_id, "done")
    assert done.session_id == first.commission_id


async def test_a_queued_commission_of_a_session_that_is_stopped_never_starts(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        "add a CSV export": ScriptedLLM([[wait, *_report()]]),
        "add a chart": ScriptedLLM([_report()]),
        "translate the help pages": ScriptedLLM([_report("ready", "translated")]),
    }
    seen: list[str] = []
    host = _host(hosts, tmp_path, scripts_by_commission(scripts, seen))
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "working")
    second = await _dispatch(host, "add a chart to the budget app")
    third = await _dispatch(host, "translate the help pages of the budget app into French")
    await host.brain.settle()
    assert host.task_agents.queued == [second.commission_id, third.commission_id]
    assert (await _events(host, third.commission_id, "queued"))[0].payload == {"ahead": 1, "behind": first.commission_id}

    await host.abort(second.commission_id)

    assert (await host.ledger.get(second.commission_id)).status == "cancelled"
    assert (await host.ledger.get(first.commission_id)).status == "working"
    release.set()
    await wait_status(host, first.commission_id, "done")
    await wait_status(host, third.commission_id, "done")
    assert seen == ["add a CSV export", "translate the help pages"]


async def test_the_limit_counts_sessions_at_work_and_a_commission_behind_its_session_takes_no_place(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        "add a CSV export": ScriptedLLM([[wait, *_report()]]),
        "add a chart": ScriptedLLM([_report()]),
        "sort the photos": ScriptedLLM([_report("ready", "sorted")]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts), max_task_agents=2)
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "working")
    second = await _dispatch(host, "add a chart to the budget app")
    await host.brain.settle()
    assert host.task_agents.queued == [second.commission_id]

    # The second place is still free for a commission of another session, though one is queued before it.
    other = await _dispatch(host, "sort the photos")
    await wait_status(host, other.commission_id, "done")

    assert (await host.ledger.get(second.commission_id)).status == "queued"
    release.set()
    await wait_status(host, second.commission_id, "done")


# ---------------------------------------------------------------------------
# Going on with what is over
# ---------------------------------------------------------------------------


async def _follow_up(host: Any, earlier: Any, text: str) -> Any:
    """File and hand over a commission that follows another up, as the task brain does."""
    commission, _created = await host.ledger.create(
        lanlan_name=earlier.lanlan_name,
        title=text,
        dispatch_text=text,
        source="dispatch",
        route="task_agent",
        lang="en",
        session_id=earlier.session_id,
        continues=earlier.id,
    )
    assert await host.task_agents.submit(commission)
    return commission


@pytest.mark.parametrize("ending", ["done", "cancelled"])
async def test_a_commission_that_follows_one_up_goes_on_in_that_ones_session(hosts, tmp_path, ending):
    release, wait = _gate()
    if ending == "done":
        release.set()
    scripts = {
        "sort the photos": ScriptedLLM([[say("looking"), wait, *_report("ready", "sorted 12 photos")]]),
        "also rename them": ScriptedLLM([_report("ready", "renamed")]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    first = await _dispatch(host, "sort the photos")
    if ending == "cancelled":
        await wait_until(lambda: bool(scripts["sort the photos"].requests), what="the first request")
        await host.abort(first.commission_id)
        release.set()
    earlier = await wait_status(host, first.commission_id, ending)
    await _released(host, first.commission_id)
    # Closed, as far as the record goes, once the commission has let its session go.
    await _let_go(host)
    assert (await host.ledger.get_session(earlier.session_id)).status == "closed"

    follow = await _follow_up(host, earlier, "also rename them by date")
    done = await wait_status(host, follow.id, "done")

    # A commission of its own, in the session of the one it follows up; that one stays as it ended.
    assert (done.session_id, done.continues) == (first.commission_id, first.commission_id)
    assert (done.workdir, done.session_dir) == (earlier.workdir, earlier.session_dir)
    assert (await host.ledger.get(first.commission_id)).status == ending
    request = scripts["also rename them"].requests[0]
    assert request[0] == scripts["sort the photos"].requests[0][0]
    said = _user_messages(request)
    assert said[0].startswith(_opens(first.commission_id, "sort the photos"))
    opening = said[-1]
    assert opening.startswith(_opens(follow.id, "also rename them by date"))
    assert brain_texts.TASK_AGENT_COMMISSION_CONTINUES.format(title="sort the photos") in opening
    assert (await host.ledger.get_session(first.commission_id)).kind == "single"


async def test_a_follow_up_in_a_session_whose_record_was_removed_is_told_what_came_of_the_earlier_one(
    hosts, tmp_path
):
    scripts = {
        "sort the photos": ScriptedLLM([_report("ready", "sorted 12 photos into D:/Photos/by-month")]),
        "also rename them": ScriptedLLM([_report("ready", "renamed")]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    first = await _dispatch(host, "sort the photos")
    earlier = await wait_status(host, first.commission_id, "done")
    await _released(host, first.commission_id)
    # What was said was removed since, being old; the working directory is still there.
    for name in ("transcript.jsonl", "system.json"):
        Path(earlier.session_dir, name).unlink()

    follow = await _follow_up(host, earlier, "also rename them by date")
    await wait_status(host, follow.id, "done")

    said = _user_messages(scripts["also rename them"].requests[0])
    assert len(said) == 1 and said[0].startswith(_opens(follow.id, "also rename them by date"))
    assert (
        brain_texts.TASK_AGENT_COMMISSION_CONTINUES_CLEARED.format(
            title="sort the photos", result="sorted 12 photos into D:/Photos/by-month"
        )
        in said[0]
    )


async def test_a_commission_taken_up_again_is_not_told_a_second_time_what_it_is(hosts, tmp_path):
    llm = ScriptedLLM([_report("blocked", "need the password"), _report("ready", "published")])
    host = _host(hosts, tmp_path, llm)
    receipt = await _dispatch(host, "publish the newsletter")
    await wait_status(host, receipt.commission_id, "blocked")
    await _released(host, receipt.commission_id)

    assert await host.tell(receipt.commission_id, "the password is in the vault")
    await wait_status(host, receipt.commission_id, "done")

    said = _user_messages(llm.requests[-1])
    assert said[0].startswith(_opens(receipt.commission_id, "publish the newsletter"))
    assert said[-1] == "the password is in the vault"
    assert sum(1 for text in said if text.startswith("Commission ")) == 1
    assert len(await _events(host, receipt.commission_id, "opened")) == 1
    # Under the system prompt it began with.
    assert llm.requests[-1][0] == llm.requests[0][0]


async def test_a_commission_whose_start_failed_before_its_session_took_it_is_told_what_it_is_when_taken_up(
    hosts, tmp_path
):
    scripts = {
        "add a CSV export": ScriptedLLM([_report("ready", "export added")]),
        "add a chart": ScriptedLLM([_report("ready", "chart added")]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "done")
    await _released(host, first.commission_id)
    refuse = [True]

    async def meter(commission_id: str) -> bool:
        return not (refuse and refuse.pop())

    host.task_agents._meter = meter
    # Its session holds the first commission's conversation, and nothing of this one:
    # the quota ran out before its opening message was sent.
    second = await _dispatch(host, "add a chart to the budget app")
    await wait_status(host, second.commission_id, "failed")
    await _released(host, second.commission_id)
    assert await _events(host, second.commission_id, "opened") == []

    assert await host.tell(second.commission_id, "try again")
    done = await wait_status(host, second.commission_id, "done")

    assert done.session_id == first.commission_id
    said = _user_messages(scripts["add a chart"].requests[0])
    assert said[-1].startswith(_opens(second.commission_id, "add a chart to the budget app"))
    assert said[-1].endswith("\n\ntry again")
    assert len(await _events(host, second.commission_id, "opened")) == 1


async def test_a_commission_of_an_earlier_build_goes_on_in_the_session_that_has_its_id(hosts, tmp_path):
    llm = ScriptedLLM([_report("ready", "done")])
    host = _host(hosts, tmp_path, llm)
    # As the ledger of an earlier build has it once it is opened: a session of the commission's id.
    commission, _created = await host.ledger.create(
        lanlan_name="Lan", title="sort the photos", dispatch_text="sort the photos", source="dispatch", route="task_agent"
    )
    await host.ledger.transition(commission.id, "working", reason="test")
    await host.ledger.transition(commission.id, "blocked", reason="test")
    assert commission.session_id is None

    assert await host.tell(commission.id, "the photos are on drive E:")
    done = await wait_status(host, commission.id, "done")

    session = await host.ledger.get_session(done.session_id)
    assert (done.session_id, session.kind) == (commission.id, "single")
    assert Path(done.session_dir).name == commission.id
    # Nothing of it was said in this session yet: it is told what the commission is, then what the user said.
    said = _user_messages(llm.requests[0])
    assert said[-1].startswith(_opens(commission.id, "sort the photos")) and said[-1].endswith(
        "\n\nthe photos are on drive E:"
    )


# ---------------------------------------------------------------------------
# What the session is opened with
# ---------------------------------------------------------------------------


async def test_a_session_is_managed_for_the_window_of_the_setting_unless_another_is_known_for_its_model(
    hosts, tmp_path
):
    specs: list[Any] = []

    def pick(spec: Any) -> ScriptedLLM:
        specs.append(spec)
        return ScriptedLLM([_report()])

    host = _host(hosts, tmp_path, pick)
    first = await _dispatch(host, "sort the photos")
    await wait_status(host, first.commission_id, "done")
    # The owner's numbers (2026-11-18): managed for 200,000 tokens, summed up from 170,000 on.
    assert (specs[0].model.context_window, specs[0].model.compact_at) == (200_000, 170_000)

    await host.model_capabilities.set_context_window(None, "fake-model", 64_000)
    second = await _dispatch(host, "back up the notes")
    await wait_status(host, second.commission_id, "done")
    assert specs[1].model.context_window == 64_000


async def test_a_window_the_model_configuration_names_is_taken_as_it_is(hosts, tmp_path):
    specs: list[Any] = []

    def pick(spec: Any) -> ScriptedLLM:
        specs.append(spec)
        return ScriptedLLM([_report()])

    async def model() -> dict[str, Any]:
        return {**await fake_model(), "context_window": 32_000}

    host = _host(hosts, tmp_path, pick, task_agent_model=model)
    await host.model_capabilities.set_context_window(None, "fake-model", 64_000)
    receipt = await _dispatch(host, "sort the photos")
    await wait_status(host, receipt.commission_id, "done")
    assert specs[0].model.context_window == 32_000
    # Summed up from the same part of the window the session is started with, not of the
    # one that is on record for the model.
    assert specs[0].model.compact_at == 27_200


async def test_what_a_session_learns_of_its_models_window_is_used_for_the_sessions_started_later(hosts, tmp_path):
    release, wait = _gate()
    specs: list[Any] = []

    def pick(spec: Any) -> ScriptedLLM:
        specs.append(spec)
        return ScriptedLLM([[wait, *_report()]])

    host = _host(hosts, tmp_path, pick)
    first = await _dispatch(host, "sort the photos")
    await wait_status(host, first.commission_id, "working")

    # As the runtime says when the provider refused a request as too long and stated its window.
    await host.task_agents._handle(
        {"type": "model_capability", "session_id": first.commission_id, "context_window": 32_768, "reason": "too long"}
    )

    assert await host.model_capabilities.context_window(None, "fake-model") == 32_768
    # Nothing was said about pictures.
    assert await host.model_capabilities.sees_images(None, "fake-model") is True
    release.set()
    await wait_status(host, first.commission_id, "done")
    second = await _dispatch(host, "back up the notes")
    await wait_status(host, second.commission_id, "done")
    assert [spec.model.context_window for spec in specs] == [200_000, 32_768]
    # Summed up from the same part of the smaller window.
    assert [spec.model.compact_at for spec in specs] == [170_000, 27_853]


async def test_the_user_is_told_once_that_a_model_takes_less_context_than_was_assumed(hosts, tmp_path):
    from tests.unit.nekoclaw.host_fakes import outbox

    release, wait = _gate()
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([[wait, *_report()]]))
    first = await _dispatch(host, "sort the photos", lang="zh-CN")
    await wait_status(host, first.commission_id, "working")
    before = len(await outbox(host))

    # As the runtime says when the provider refused a request as too long.
    learned = {"type": "model_capability", "session_id": first.commission_id, "context_window": 131_072, "reason": "too long"}
    await host.task_agents._handle(learned)

    (note,) = (await outbox(host))[before:]
    # Shown to the user on the HUD as it is written, and said to no model: it is about how
    # NekoClaw is set, which she has no use for. Nothing is put into her context.
    assert note.kind == "receipt"
    assert note.payload["delivery_mode"] == "silent" and note.payload["visibility"] == ["hud"]
    assert note.payload["receipt"] == "context_window"
    assert note.payload["lanlan_name"] == "Lan" and note.payload["task_id"] is None
    # In the language of the commission, with the model and both numbers; and it names no
    # place to set the number in, there being none yet.
    assert note.payload["text"] == brain_texts.MODEL_WINDOW_REMINDER_TEXT["zh"].format(
        model="fake-model", was=200_000, window=131_072
    )
    assert "fake-model" in note.payload["text"] and "131072" in note.payload["text"]
    described = await host.model_capabilities.describe_window(None, "fake-model")
    assert described["reminded"] == 131_072

    # Once for the model: not when the same is learned again, nor when it turns out smaller still.
    await host.task_agents._handle(learned)
    await host.task_agents._handle({**learned, "context_window": 64_000})
    assert len(await outbox(host)) == before + 1
    assert await host.model_capabilities.context_window(None, "fake-model") == 64_000
    release.set()
    await wait_status(host, first.commission_id, "done")


async def test_a_reminder_that_could_not_be_written_is_made_the_next_time(hosts, tmp_path):
    from tests.unit.nekoclaw.host_fakes import outbox

    release, wait = _gate()
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([[wait, *_report()]]))
    first = await _dispatch(host, "sort the photos")
    await wait_status(host, first.commission_id, "working")
    before = len(await outbox(host))
    note = host.task_agents._reporter.note

    async def broken(*args: Any, **kwargs: Any) -> int:
        raise OSError("the ledger is busy")

    host.task_agents._reporter.note = broken
    learned = {"type": "model_capability", "session_id": first.commission_id, "context_window": 131_072, "reason": "too long"}
    await host.task_agents._handle(learned)
    # The window is used all the same; that the user was told is not on record.
    assert await host.model_capabilities.context_window(None, "fake-model") == 131_072
    assert "reminded" not in await host.model_capabilities.describe_window(None, "fake-model")

    host.task_agents._reporter.note = note
    await host.task_agents._handle(learned)
    (made,) = (await outbox(host))[before:]
    # What its sessions were managed for before anything was learned, not what was learned meanwhile.
    assert made.payload["text"] == brain_texts.MODEL_WINDOW_REMINDER_TEXT["en"].format(
        model="fake-model", was=200_000, window=131_072
    )
    release.set()
    await wait_status(host, first.commission_id, "done")


async def test_a_reminder_cut_short_by_a_shutdown_is_not_on_record_as_made(hosts, tmp_path):
    from tests.unit.nekoclaw.host_fakes import outbox

    release, wait = _gate()
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([[wait, *_report()]]))
    first = await _dispatch(host, "sort the photos")
    await wait_status(host, first.commission_id, "working")
    before = len(await outbox(host))
    note = host.task_agents._reporter.note

    async def cancelled(*args: Any, **kwargs: Any) -> int:
        raise asyncio.CancelledError

    host.task_agents._reporter.note = cancelled
    learned = {"type": "model_capability", "session_id": first.commission_id, "context_window": 131_072, "reason": "too long"}
    with pytest.raises(asyncio.CancelledError):
        await host.task_agents._handle(learned)
    # Neither written down as told, nor held back as being told.
    assert "reminded" not in await host.model_capabilities.describe_window(None, "fake-model")
    assert await host.model_capabilities.claim_window_reminder(None, "fake-model") == 131_072
    host.model_capabilities.unclaim_window_reminder(None, "fake-model")

    host.task_agents._reporter.note = note
    await host.task_agents._handle(learned)
    assert len(await outbox(host)) == before + 1
    assert (await host.model_capabilities.describe_window(None, "fake-model"))["reminded"] == 131_072
    release.set()
    await wait_status(host, first.commission_id, "done")


def test_the_reminder_is_there_in_every_language_and_is_short() -> None:
    from utils.tokenize import count_tokens

    texts = brain_texts.MODEL_WINDOW_REMINDER_TEXT
    assert set(texts) == {"zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}
    for lang, text in texts.items():
        said = text.format(model="some-model", was=200_000, window=131_072)
        assert "some-model" in said and "200000" in said and "131072" in said, lang
        # Short enough to be read on the HUD. (It is in no model's context.)
        assert count_tokens(said) <= 130, (lang, count_tokens(said))
        # Written for the user to read as it stands, not for someone to pass on.
        assert "用户" not in said and "使用者" not in said and "the user" not in said.lower(), lang


async def test_that_a_sessions_conversation_was_summed_up_is_on_record_with_the_session(hosts, tmp_path):
    release, wait = _gate()
    host = _host(hosts, tmp_path, ScriptedLLM([[wait, *_report()]]))
    receipt = await _dispatch(host, "sort the photos")
    await wait_status(host, receipt.commission_id, "working")
    assert (await host.ledger.get_session(receipt.commission_id)).compacted_at is None

    await host.task_agents._handle(
        {"type": "compacted", "session_id": receipt.commission_id, "count": 1, "tokens_before": 9000}
    )

    assert (await host.ledger.get_session(receipt.commission_id)).compacted_at is not None
    assert [event.payload["count"] for event in await _events(host, receipt.commission_id, "compacted")] == [1]
    release.set()
    await wait_status(host, receipt.commission_id, "done")


async def test_what_a_runtime_says_of_a_session_is_about_the_commission_at_work_in_it(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        "add a CSV export": ScriptedLLM([_report("ready", "export added")]),
        "add a chart": ScriptedLLM([[wait, *_report("ready", "chart added")]]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "done")
    second = await _dispatch(host, "add a chart to the budget app")
    await wait_status(host, second.commission_id, "working")
    before = len(await _events(host, first.commission_id))

    # The runtime names the session, which has the first commission's id; the second is at work in it.
    assert host.task_agents._at_work_in(first.commission_id) == second.commission_id
    lesson = await host.task_agents._cb_note_lesson(
        first.commission_id, {"title": "Charts", "content": "Use the house palette.", "category": "lesson"}
    )

    assert lesson is not None
    assert len(await _events(host, first.commission_id)) == before
    assert [event.payload["title"] for event in await _events(host, second.commission_id, "lesson")] == ["Charts"]
    release.set()
    await wait_status(host, second.commission_id, "done")


# ---------------------------------------------------------------------------
# From a dispatched sentence to the session it goes on in
# ---------------------------------------------------------------------------


async def test_a_dispatched_sentence_that_adds_to_a_commission_that_is_done_goes_on_in_its_session(hosts, tmp_path):
    scripts = {
        "sort the photos": ScriptedLLM([_report("ready", "sorted 12 photos by month")]),
        "rename the photos": ScriptedLLM([_report("ready", "renamed")]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    first = await _dispatch(host, "sort the photos in my downloads folder")
    earlier = await wait_status(host, first.commission_id, "done")
    await _released(host, first.commission_id)

    receipt = await _dispatch(host, "also rename the photos by date", user_text="oh and rename the photos too")

    # A commission of its own, which she is told of like any other; the one it follows up stays done.
    assert receipt.kind == "accepted" and receipt.commission_id != first.commission_id
    done = await wait_status(host, receipt.commission_id, "done")
    assert (done.session_id, done.continues, done.route) == (first.commission_id, first.commission_id, "task_agent")
    assert (done.workdir, done.session_dir) == (earlier.workdir, earlier.session_dir)
    assert (await host.ledger.get(first.commission_id)).status == "done"
    # No plugin is installed: there is nothing to choose between, and no model is asked.
    assert host.decision._llm.calls == []
    (decision,) = await _events(host, receipt.commission_id, "decision")
    assert (decision.payload["route"], decision.payload["continues"]) == ("task_agent", first.commission_id)
    said = _user_messages(scripts["rename the photos"].requests[0])
    assert said[0].startswith(_opens(first.commission_id, "sort the photos in my downloads folder"))
    assert said[-1].startswith(
        _opens(receipt.commission_id, "also rename the photos by date\nWhat the user said: oh and rename the photos too")
    )
    assert "follows up the earlier commission" in said[-1]


def _renamer(server: FakePluginServer, calls: list[Any]) -> None:
    async def rename(args: dict[str, Any]) -> dict[str, Any]:
        calls.append(args)
        return plugin_result({"reply": "renamed 12 photos"})

    server.add_plugin(
        plugin(
            "renamer",
            "Renames files by a pattern",
            [entry("rename", "Rename the files of a folder", llm_result_fields=[])],
            keywords=["rename"],
        ),
        rename=rename,
    )


async def test_a_sentence_that_adds_to_a_commission_is_chosen_for_like_any_other_before_it_goes_on_there(
    hosts, tmp_path, monkeypatch
):
    server, calls = FakePluginServer.install(monkeypatch), []
    _renamer(server, calls)
    decision = FakeDecisionLLM(
        fine=[{"route": "task_agent"}, {"route": "task_agent", "preference": "name photos by the date they were taken"}]
    )
    scripts = {
        "sort the photos": ScriptedLLM([_report("ready", "sorted 12 photos by month")]),
        "rename the photos": ScriptedLLM([_report("ready", "renamed")]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts), client_factory=server.client, decision_llm=decision)
    first = await _dispatch(host, "sort the photos in my downloads folder")
    await wait_status(host, first.commission_id, "done")
    await _released(host, first.commission_id)
    assert decision.tiers() == ["fine"]
    # A commission that adds to none says nothing of one it goes on with.
    (plain,) = await _events(host, first.commission_id, "decision")
    assert plain.payload["route"] == "task_agent" and "continues" not in plain.payload

    receipt = await _dispatch(host, "also rename the photos by date")

    assert receipt.kind == "accepted"
    done = await wait_status(host, receipt.commission_id, "done")
    # With a plugin that could do it installed, the model is asked, as for any sentence.
    assert decision.tiers() == ["fine", "fine"] and calls == []
    # Handed to a task agent, it goes on where the other was done.
    assert (done.session_id, done.continues, done.route) == (first.commission_id, first.commission_id, "task_agent")
    (chosen,) = await _events(host, receipt.commission_id, "decision")
    assert (chosen.payload["route"], chosen.payload["continues"]) == ("task_agent", first.commission_id)
    assert chosen.payload["model_calls"] == 1
    # What the sentence states for good is kept, which that request reads.
    (kept,) = await host.preferences.list()
    assert kept.text == "name photos by the date they were taken"
    # The plugins it was shown go along, to be called through the gateway.
    said = _user_messages(scripts["rename the photos"].requests[0])
    assert "follows up the earlier commission" in said[-1] and '"name": "plugin__renamer__rename"' in said[-1]
    # And what was just stated is among how the user wants such things done.
    assert "- name photos by the date they were taken" in said[-1]


@pytest.mark.parametrize("chosen", ["a plugin", "nothing"])
async def test_a_sentence_that_adds_to_a_commission_and_is_not_for_a_task_agent_has_no_part_in_its_session(
    hosts, tmp_path, monkeypatch, chosen
):
    server, calls = FakePluginServer.install(monkeypatch), []
    _renamer(server, calls)
    second = (
        pick_plugin("renamer", "rename", {"pattern": "date"})
        if chosen == "a plugin"
        else {"route": "none", "reason": "nothing here renames photos"}
    )
    decision = FakeDecisionLLM(fine=[{"route": "task_agent"}, second])
    scripts = {"sort the photos": ScriptedLLM([_report("ready", "sorted 12 photos by month")])}
    host = _host(hosts, tmp_path, scripts_by_commission(scripts), client_factory=server.client, decision_llm=decision)
    first = await _dispatch(host, "sort the photos in my downloads folder")
    earlier = await wait_status(host, first.commission_id, "done")
    await _released(host, first.commission_id)
    await _let_go(host)

    receipt = await _dispatch(host, "also rename the photos by date")

    assert receipt.kind == "accepted"
    ended = await wait_status(host, receipt.commission_id, "done", "failed")
    await host.brain.settle()
    if chosen == "a plugin":
        # The plugin does it by itself.
        assert (ended.status, ended.route, ended.plugin_id) == ("done", "plugin", "renamer")
        assert [args["pattern"] for args in calls] == ["date"]
    else:
        assert ended.status == "failed" and "nothing here renames photos" in ended.status_reason
        assert calls == []
    # It is no part of the session of the commission it adds to, and holds nothing of it:
    # the next commission of that session does not wait for it.
    assert (ended.session_id, ended.continues, ended.project) == (None, None, earlier.project)
    (written,) = await _events(host, receipt.commission_id, "decision")
    assert "continues" not in written.payload
    assert [item.id for item in await host.ledger.select(session_id=earlier.session_id)] == [first.commission_id]
    assert host.task_agents._held_by == {} and host.task_agents.running == [] and host.task_agents.queued == []
    assert len(scripts["sort the photos"].requests) == 1
    assert host.task_agents.hold_files(earlier.session_id) is True
    host.task_agents.release_files(earlier.session_id)


async def test_a_dispatched_sentence_about_something_else_is_a_commission_in_a_session_of_its_own(hosts, tmp_path):
    scripts = {
        "sort the photos": ScriptedLLM([_report("ready", "sorted")]),
        "back up the notes": ScriptedLLM([_report("ready", "backed up")]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    first = await _dispatch(host, "sort the photos in my downloads folder")
    await wait_status(host, first.commission_id, "done")

    # About the same thing without a word that says it adds something, and about something else with such a word.
    for text in ("sort the photos on drive E: as well", "also back up the notes to drive F:"):
        receipt = await _dispatch(host, text)
        done = await wait_status(host, receipt.commission_id, "done")
        assert (done.session_id, done.continues) == (receipt.commission_id, None)
        scripts["sort the photos"].add(*_report("ready", "sorted again"))


async def test_a_dispatched_sentence_that_adds_to_a_commission_that_failed_takes_that_one_up_again(hosts, tmp_path):
    llm = ScriptedLLM([_report("failed", "the folder is missing"), _report("ready", "sorted")])
    host = _host(hosts, tmp_path, llm)
    first = await _dispatch(host, "sort the photos in my downloads folder")
    await wait_status(host, first.commission_id, "failed")
    await _released(host, first.commission_id)

    receipt = await _dispatch(host, "also the photos are on drive E: now")

    assert (receipt.kind, receipt.commission_id) == ("told", first.commission_id)
    done = await wait_status(host, first.commission_id, "done")
    assert done.result_summary == "sorted"
    assert len(await host.ledger.select(lanlan_name="Lan")) == 1
    assert _user_messages(llm.requests[-1])[-1] == "also the photos are on drive E: now"


async def test_a_follow_up_to_a_commission_of_a_project_waits_for_the_projects_session(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        "add a CSV export": ScriptedLLM([_report("ready", "export added")]),
        "add a chart": ScriptedLLM([[wait, *_report("ready", "chart added")]]),
        "semicolons": ScriptedLLM([_report("ready", "now with semicolons")]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    workdir = tmp_path / "budget-files"
    workdir.mkdir()
    await host.space.touch_project("Budget app", workdir=str(workdir))
    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "done")
    second = await _dispatch(host, "add a chart to the budget app")
    await wait_status(host, second.commission_id, "working")

    follow = await _dispatch(host, "also the CSV export should use semicolons")
    await host.brain.settle()

    queued = await host.ledger.get(follow.commission_id)
    assert (queued.status, queued.session_id, queued.continues, queued.project) == (
        "queued",
        first.commission_id,
        first.commission_id,
        "Budget app",
    )
    release.set()
    await wait_status(host, second.commission_id, "done")
    done = await wait_status(host, follow.commission_id, "done")
    assert done.result_summary == "now with semicolons"
    # It works where its session works: in the project's folder, which its own words do not name.
    assert Path(done.workdir) == workdir


# ---------------------------------------------------------------------------
# Removing what was said in an old session
# ---------------------------------------------------------------------------


async def test_a_sessions_files_are_not_claimed_while_it_is_in_use_and_nothing_starts_in_it_while_they_are(
    hosts, tmp_path
):
    release, wait = _gate()
    scripts = {
        "add a CSV export": ScriptedLLM([[wait, *_report()]]),
        "add a chart": ScriptedLLM([_report()]),
        "translate the help pages": ScriptedLLM([_report("ready", "translated")]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "working")
    second = await _dispatch(host, "add a chart to the budget app")
    await host.brain.settle()
    session_id = first.commission_id

    # At work on one commission, with another waiting for it.
    assert host.task_agents.hold_files(session_id) is False
    release.set()
    await wait_status(host, first.commission_id, "done")
    await wait_status(host, second.commission_id, "done")
    await _released(host, second.commission_id)
    await _let_go(host)

    assert host.task_agents.hold_files(session_id) is True
    # Claimed once.
    assert host.task_agents.hold_files(session_id) is False
    third = await _dispatch(host, "translate the help pages of the budget app into French")
    await wait_until(lambda: third.commission_id in host.task_agents.running, what="the third taking its place")
    await asyncio.sleep(0.05)
    # It has its place, and waits there: the session is not opened under the hands that remove its files.
    assert third.commission_id in host.task_agents.running
    assert (await host.ledger.get(third.commission_id)).status == "queued"
    assert scripts["translate the help pages"].requests == []

    host.task_agents.release_files(session_id)
    done = await wait_status(host, third.commission_id, "done")
    assert done.session_id == session_id
    # Given back twice, nothing happens.
    host.task_agents.release_files(session_id)


async def test_a_failed_commission_is_not_taken_up_again_by_a_sentence_caught_by_a_stop(hosts, tmp_path):
    llm = ScriptedLLM([_report("failed", "the folder is missing"), _report("ready", "sorted")])
    host = _host(hosts, tmp_path, llm)
    first = await _dispatch(host, "sort the photos in my downloads folder")
    await wait_status(host, first.commission_id, "failed")
    await _released(host, first.commission_id)

    # Everything is stopped while the sentence is still being looked at.
    looked = host.brain._recently_over

    async def stopped_meanwhile(lanlan_name: str) -> Any:
        found = await looked(lanlan_name)
        host.brain.drop_pending_dispatches()
        return found

    host.brain._recently_over = stopped_meanwhile
    receipt = await _dispatch(host, "also the photos are on drive E: now")
    host.brain._recently_over = looked

    # A stop does not end a commission that is over already; taken up again, it would start
    # its work after the stop.
    assert (receipt.kind, receipt.reason) == ("no_capability", "stopped")
    await host.brain.settle()
    await host.task_agents.settle()
    assert (await host.ledger.get(first.commission_id)).status == "failed"
    assert host.task_agents.running == [] and len(llm.requests) == 1


@pytest.mark.parametrize("what", ["switched off", "stopped"])
async def test_a_failed_commission_is_not_taken_up_again_once_that_was_called_off_while_it_waited(hosts, tmp_path, what):
    llm = ScriptedLLM([_report("failed", "the folder is missing"), _report("ready", "sorted")])
    switch = {"on": True}
    host = _host(hosts, tmp_path, llm, enabled=lambda: switch["on"])
    first = await _dispatch(host, "sort the photos in my downloads folder")
    await wait_status(host, first.commission_id, "failed")
    await _released(host, first.commission_id)
    await _let_go(host)

    # Its review is being written down: taking it up again waits for that, and meanwhile
    # NekoClaw is switched off (which stops nothing, and changes nothing a stop changes), or
    # everything is stopped (which does not end a commission that is over).
    assert host.task_agents.hold_session(first.commission_id) is True
    dispatching = asyncio.ensure_future(_dispatch(host, "also the photos are on drive E: now"))
    await wait_until(
        lambda: _events(host, first.commission_id, "told"), what="the sentence being passed on to the commission"
    )
    assert not dispatching.done()
    if what == "switched off":
        switch["on"] = False
    else:
        host.brain.drop_pending_dispatches()
    host.task_agents.release_session(first.commission_id)
    receipt = await dispatching

    assert (receipt.kind, receipt.reason) == ("no_capability", "disabled" if what == "switched off" else "stopped")
    await host.brain.settle()
    await host.task_agents.settle()
    assert (await host.ledger.get(first.commission_id)).status == "failed"
    assert host.task_agents.running == [] and len(llm.requests) == 1
    assert len(await host.ledger.select(lanlan_name="Lan")) == 1


@pytest.mark.parametrize("waits", [False, True])
async def test_a_commission_that_is_over_is_not_taken_up_again_when_that_is_no_longer_wanted(hosts, tmp_path, waits):
    llm = ScriptedLLM([_report("failed", "the folder is missing"), _report("ready", "sorted")])
    host = _host(hosts, tmp_path, llm)
    first = await _dispatch(host, "sort the photos in my downloads folder")
    await wait_status(host, first.commission_id, "failed")
    await _released(host, first.commission_id)
    await _let_go(host)
    wanted = {"yes": True}

    if waits:
        # Its review is being written down: taking it up again waits for that, and everything
        # is stopped while it waits.
        assert host.task_agents.hold_session(first.commission_id) is True
        telling = asyncio.ensure_future(
            host.task_agents.tell(first.commission_id, "the photos are on drive E: now", still_wanted=lambda: wanted["yes"])
        )
        await asyncio.sleep(0.05)
        assert not telling.done()
        wanted["yes"] = False
        host.task_agents.release_session(first.commission_id)
        told = await telling
    else:
        wanted["yes"] = False
        told = await host.task_agents.tell(
            first.commission_id, "the photos are on drive E: now", still_wanted=lambda: wanted["yes"]
        )

    # A stop does not end a commission that is over; taken up now, it would start after the stop.
    assert told is False
    await host.task_agents.settle()
    assert (await host.ledger.get(first.commission_id)).status == "failed"
    assert host.task_agents.running == [] and host.task_agents.queued == [] and len(llm.requests) == 1
    if not waits:
        # Known before anything was done: nothing is on record of words that were not passed on.
        assert await _events(host, first.commission_id, "told") == []

    # Still wanted, it is taken up as before.
    assert await host.task_agents.tell(first.commission_id, "the photos are on drive E: now", still_wanted=lambda: True)
    await wait_status(host, first.commission_id, "done")


async def test_a_commission_being_set_up_again_gets_no_work_once_everything_was_stopped(hosts, tmp_path):
    llm = ScriptedLLM([_report("failed", "the folder is missing"), _report("ready", "sorted")])
    host = _host(hosts, tmp_path, llm)
    agents = host.task_agents
    first = await _dispatch(host, "sort the photos in my downloads folder")
    await wait_status(host, first.commission_id, "failed")
    await _released(host, first.commission_id)
    await _let_go(host)
    wanted = {"yes": True}

    # Setting it up again takes a while (the model's configuration is read, its directory made
    # ready), and everything is stopped meanwhile. A stop ends what is under way; this
    # commission is over until its status is written, so the stop does not find it.
    reached, go_on = asyncio.Event(), asyncio.Event()
    configured = agents._model_config

    async def slowly() -> Any:
        reached.set()
        await go_on.wait()
        return await configured()

    agents._model_config = slowly
    telling = asyncio.ensure_future(
        agents.tell(first.commission_id, "look on drive D:", still_wanted=lambda: wanted["yes"])
    )
    await asyncio.wait_for(reached.wait(), 10)
    assert first.commission_id in agents.running
    # A second sentence joins the one that is taking it up.
    assert await asyncio.wait_for(agents.tell(first.commission_id, "and the videos too"), 5) is True
    wanted["yes"] = False
    go_on.set()
    assert await telling is False
    agents._model_config = configured
    await agents.settle()

    # As it was: it got no work after the stop, and can be taken up later.
    assert (await host.ledger.get(first.commission_id)).status == "failed"
    assert agents.running == [] and agents.queued == [] and len(llm.requests) == 1
    assert len(await _events(host, first.commission_id, "opened")) == 1
    # What it was to be told went with the stop.
    assert first.commission_id not in agents._notes
    await _let_go(host)

    assert await agents.tell(first.commission_id, "the photos are on drive E: now", still_wanted=lambda: True)
    await wait_status(host, first.commission_id, "done")
    said = "\n".join(_user_messages(llm.requests[-1]))
    assert "the photos are on drive E: now" in said
    assert "look on drive D:" not in said and "and the videos too" not in said


async def test_a_sentence_said_after_a_stop_does_not_go_down_with_what_the_stop_called_off(hosts, tmp_path):
    llm = ScriptedLLM([_report("failed", "the folder is missing"), _report("ready", "sorted")])
    host = _host(hosts, tmp_path, llm)
    agents = host.task_agents
    first = await _dispatch(host, "sort the photos in my downloads folder")
    await wait_status(host, first.commission_id, "failed")
    await _released(host, first.commission_id)
    await _let_go(host)
    wanted = {"yes": True}
    reached, go_on = asyncio.Event(), asyncio.Event()
    configured = agents._model_config

    async def slowly() -> Any:
        reached.set()
        await go_on.wait()
        return await configured()

    agents._model_config = slowly
    telling = asyncio.ensure_future(
        agents.tell(first.commission_id, "look on drive D:", still_wanted=lambda: wanted["yes"])
    )
    await asyncio.wait_for(reached.wait(), 10)
    # Everything is stopped while it is being set up, and right after that the user says more
    # of it. Added to what is being set up, that would be given up with it a moment later,
    # and said to have been passed on.
    wanted["yes"] = False
    later = asyncio.ensure_future(
        agents.tell(first.commission_id, "the photos are on drive E: now", still_wanted=lambda: True)
    )
    await asyncio.sleep(0.05)
    assert not later.done() and first.commission_id not in agents._notes
    go_on.set()
    assert await telling is False
    # It waited for the other to be out of the way, and took the commission up by itself.
    assert await asyncio.wait_for(later, 10) is True
    agents._model_config = configured
    await wait_status(host, first.commission_id, "done")
    said = "\n".join(_user_messages(llm.requests[-1]))
    assert "the photos are on drive E: now" in said and "look on drive D:" not in said
    assert agents._under_way == {}


async def test_a_sentence_said_after_a_stop_is_not_added_to_a_commission_the_stop_is_about_to_end(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        "sort the photos": ScriptedLLM([_report("failed", "the folder is missing"), _report("ready", "sorted")]),
        "translate the manual": ScriptedLLM([[wait, *_report()]]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts), max_task_agents=1)
    agents = host.task_agents
    first = await _dispatch(host, "sort the photos in my downloads folder")
    await wait_status(host, first.commission_id, "failed")
    await _released(host, first.commission_id)
    await _let_go(host)
    # The one place is taken: taken up again, the first one is queued.
    other, _created = await host.ledger.create(
        lanlan_name="Lan",
        title="translate the manual",
        dispatch_text="translate the manual",
        source="dispatch",
        route="task_agent",
        lang="en",
    )
    assert await agents.submit(other)
    await wait_status(host, other.id, "working")
    wanted = {"yes": True}
    reached, go_on = asyncio.Event(), asyncio.Event()
    moving = host.ledger.transition

    async def stopped_as_it_is_written(commission_id: str, status: str, **kwargs: Any) -> Any:
        moved = await moving(commission_id, status, **kwargs)
        if status == "queued":
            # The stop looked before this was written; whoever queued it ends it on looking again.
            wanted["yes"] = False
            reached.set()
            await go_on.wait()
        return moved

    host.ledger.transition = stopped_as_it_is_written
    try:
        telling = asyncio.ensure_future(
            agents.tell(first.commission_id, "look on drive D:", still_wanted=lambda: wanted["yes"])
        )
        await asyncio.wait_for(reached.wait(), 10)
        # Said after the stop. The commission reads as queued, and takes along what it is
        # told while it is: this would be stopped with it, and said to have been passed on.
        later = asyncio.ensure_future(
            agents.tell(first.commission_id, "the photos are on drive E: now", still_wanted=lambda: True)
        )
        await asyncio.sleep(0.05)
        assert not later.done() and first.commission_id not in agents._notes
        go_on.set()
        assert await telling is False
        # The commission was stopped: nothing took the sentence, and that is what is said of it.
        assert await asyncio.wait_for(later, 10) is False
    finally:
        host.ledger.transition = moving
    await agents.settle()
    assert (await host.ledger.get(first.commission_id)).status == "cancelled"
    assert agents.queued == [] and first.commission_id not in agents._notes and agents._under_way == {}
    release.set()
    await wait_status(host, other.id, "done")


async def test_a_host_that_shuts_down_does_not_stop_a_commission_that_is_being_taken_up_again(hosts, tmp_path):
    llm = ScriptedLLM([_report("failed", "the folder is missing"), _report("ready", "sorted")])
    host = _host(hosts, tmp_path, llm)
    first = await _dispatch(host, "sort the photos in my downloads folder")
    await wait_status(host, first.commission_id, "failed")
    await _released(host, first.commission_id)
    await _let_go(host)

    assert host.task_agents.hold_session(first.commission_id) is True
    dispatching = asyncio.ensure_future(_dispatch(host, "also the photos are on drive E: now"))
    await wait_until(
        lambda: _events(host, first.commission_id, "told"), what="the sentence being passed on to the commission"
    )
    assert not dispatching.done()
    # A host that shuts down closes its brain before it ends its task agents. Those end a
    # start they are caught at themselves, as failed because the host shut down, which can be
    # taken up again. That the brain is closed is not a stop, which would end it for good.
    host.brain._closed = True
    try:
        host.task_agents.release_session(first.commission_id)
        receipt = await dispatching
    finally:
        host.brain._closed = False

    assert (receipt.kind, receipt.commission_id) == ("told", first.commission_id)
    assert (await wait_status(host, first.commission_id, "done", "cancelled")).status == "done"


async def test_a_stop_that_lands_as_a_commission_taken_up_again_is_set_working_stops_it(hosts, tmp_path):
    llm = ScriptedLLM([_report("failed", "the folder is missing"), _report("ready", "sorted")])
    host = _host(hosts, tmp_path, llm)
    agents = host.task_agents
    first = await _dispatch(host, "sort the photos in my downloads folder")
    await wait_status(host, first.commission_id, "failed")
    await _released(host, first.commission_id)
    await _let_go(host)
    wanted = {"yes": True}
    moving = host.ledger.transition

    async def stopped_as_it_is_written(commission_id: str, status: str, **kwargs: Any) -> Any:
        moved = await moving(commission_id, status, **kwargs)
        if status == "working":
            # The stop looked a moment before this was written, and saw a commission that was over.
            wanted["yes"] = False
        return moved

    host.ledger.transition = stopped_as_it_is_written
    try:
        told = await agents.tell(first.commission_id, "look on drive D:", still_wanted=lambda: wanted["yes"])
    finally:
        host.ledger.transition = moving

    # At work with nothing to end it, it would start after the stop: it is stopped as the stop
    # would have stopped it, before it is given anything.
    assert told is False
    await _released(host, first.commission_id)
    assert (await host.ledger.get(first.commission_id)).status == "cancelled"
    assert len(llm.requests) == 1
    await _let_go(host)
    assert agents.running == [] and agents.queued == []
    # Nothing said is kept for it: the session's end saw to that, as after any stop.
    kept = (agents._notes, agents._carried, agents._resume, agents._under_way)
    assert all(first.commission_id not in where for where in kept)


@pytest.mark.parametrize("lands", ["as the ledger is read", "as it is queued"])
async def test_a_commission_waiting_for_a_place_to_be_taken_up_again_is_not_queued_past_a_stop(hosts, tmp_path, lands):
    release, wait = _gate()
    scripts = {
        "sort the photos": ScriptedLLM([_report("failed", "the folder is missing"), _report("ready", "sorted")]),
        "translate the manual": ScriptedLLM([[wait, *_report()]]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts), max_task_agents=1)
    agents = host.task_agents
    first = await _dispatch(host, "sort the photos in my downloads folder")
    await wait_status(host, first.commission_id, "failed")
    await _released(host, first.commission_id)
    await _let_go(host)
    # The one place is taken by another commission: taken up again, the first one is queued.
    other, _created = await host.ledger.create(
        lanlan_name="Lan",
        title="translate the manual",
        dispatch_text="translate the manual",
        source="dispatch",
        route="task_agent",
        lang="en",
    )
    assert await agents.submit(other)
    await wait_status(host, other.id, "working")
    wanted = {"yes": True}
    # Said earlier, and not yet in its session.
    agents._carried[first.commission_id] = ["look in the attic folder"]
    known, moving = agents._know_session, host.ledger.transition

    async def stopped_as_it_is_read(commission_id: str) -> bool:
        found = await known(commission_id)
        wanted["yes"] = False
        return found

    async def stopped_as_it_is_written(commission_id: str, status: str, **kwargs: Any) -> Any:
        moved = await moving(commission_id, status, **kwargs)
        if status == "queued":
            wanted["yes"] = False
        return moved

    if lands == "as the ledger is read":
        agents._know_session = stopped_as_it_is_read
    else:
        host.ledger.transition = stopped_as_it_is_written
    try:
        told = await agents.tell(first.commission_id, "look on drive D:", still_wanted=lambda: wanted["yes"])
    finally:
        agents._know_session, host.ledger.transition = known, moving

    assert told is False
    await agents.settle()
    after = (await host.ledger.get(first.commission_id)).status
    if lands == "as the ledger is read":
        # Nothing of it was touched yet: it stays as it was, with what was kept for it.
        assert after == "failed"
        assert agents._carried[first.commission_id] == ["look in the attic folder"]
    else:
        # Queued behind the stop's back, it would wait for a place with nothing to end it: it
        # is stopped as the stop would have stopped it, and nothing is kept for it.
        assert after == "cancelled"
        assert first.commission_id not in agents._carried
    assert agents.queued == [] and first.commission_id not in agents._resume
    assert first.commission_id not in agents._notes

    # The place that comes free starts nothing of it.
    release.set()
    await wait_status(host, other.id, "done")
    await _released(host, other.id)
    await agents.settle()
    assert (await host.ledger.get(first.commission_id)).status == after
    assert len(scripts["sort the photos"].requests) == 1 and agents.running == []


@pytest.mark.parametrize("fails", ["before it is set working", "as a runtime is asked to open it"])
async def test_a_session_that_was_cleared_stays_cleared_until_a_commission_gets_to_work_in_it(hosts, tmp_path, fails):
    from nekoclaw.host.task_agents import _Session

    llm = ScriptedLLM([_report(), _report("ready", "by year too")])
    host = _host(hosts, tmp_path, llm)
    agents = host.task_agents
    first = await _dispatch(host, "sort the photos")
    done = await wait_status(host, first.commission_id, "done")
    await _released(host, first.commission_id)
    await _let_go(host)
    session_id = done.session_id
    configured, runtime = agents._model_config, agents._runtimes.get("builtin")
    start = runtime.start

    async def no_model() -> None:
        return None

    async def not_coming_up(spec: Any) -> None:
        raise RuntimeError("the runtime did not come up")

    async def failing(text: str) -> None:
        """Have a commission that follows up fail before a runtime has the session open."""
        if fails == "before it is set working":
            agents._model_config = no_model
        else:
            # The commission is set working by then, which says of the session that it is at work.
            runtime.start = not_coming_up
        try:
            commission = await _follow_up(host, done, text)
            failed = await wait_status(host, commission.id, "failed")
            assert "could not be started" in failed.status_reason
            await _let_go(host)
        finally:
            agents._model_config, runtime.start = configured, start

    # In a session with a conversation in it, that changes nothing: it is closed, as it was.
    assert (await host.ledger.get_session(session_id)).status == "closed"
    await failing("sort them by month as well")
    assert (await host.ledger.get_session(session_id)).status == "closed"

    # What was said in it was removed since, being old.
    await host.ledger.mark_session_cleared(session_id)
    assert (await host.ledger.get_session(session_id)).status == "cleared"
    assert (await _events(host, done.id, "session_cleaned"))[-1].payload == {"session_id": session_id}

    await failing("sort them by year as well")
    # Nothing is in the session still. Written down as closed, it would read as one with a
    # conversation that is there to be removed.
    assert (await host.ledger.get_session(session_id)).status == "cleared"

    # One that gets to work in it leaves it closed: there is a conversation in it again.
    third = await _follow_up(host, done, "sort them by year after all")
    await wait_status(host, third.id, "done")
    await _released(host, third.id)
    await _let_go(host)
    assert (await host.ledger.get_session(session_id)).status == "closed"
    # And so does a start that fails after a runtime had it open: that one may have written to it.
    await agents._leave_cleared(
        _Session(commission_id=third.id, session_id=session_id, cleared_before=True, opened_in_runtime=True)
    )
    assert (await host.ledger.get_session(session_id)).status == "closed"
    await agents._leave_cleared(_Session(commission_id=third.id, session_id=session_id, cleared_before=True))
    assert (await host.ledger.get_session(session_id)).status == "cleared"


async def test_a_session_is_let_go_also_when_where_it_stands_cannot_be_written(hosts, tmp_path):
    import sqlite3

    release, wait = _gate()
    llm = ScriptedLLM([[wait, *_report()], _report("ready", "the videos too")])
    host = _host(hosts, tmp_path, llm)
    first = await _dispatch(host, "sort the photos")
    await wait_status(host, first.commission_id, "working")
    settling = host.ledger.settle_session

    async def locked(session_id: str, commission_id: str) -> None:
        raise sqlite3.OperationalError("database is locked")

    host.ledger.settle_session = locked
    try:
        release.set()
        done = await wait_status(host, first.commission_id, "done")
        await _released(host, first.commission_id)
        # What the session's status says is for showing. Held on because that could not be
        # written, nothing else would ever start in the session.
        await _let_go(host)
    finally:
        host.ledger.settle_session = settling

    second = await _follow_up(host, done, "sort the videos as well")
    await wait_status(host, second.id, "done")
    await _released(host, second.id)
    await _let_go(host)
    assert (await host.ledger.get_session(done.session_id)).status == "closed"


async def test_the_files_of_a_session_nobody_has_in_hand_are_claimed_whatever_its_commissions_were(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([_report()]))
    receipt = await _dispatch(host, "sort the photos")
    await wait_status(host, receipt.commission_id, "done")
    await _released(host, receipt.commission_id)
    await _let_go(host)

    assert host.task_agents.hold_files(receipt.commission_id) is True
    host.task_agents.release_files(receipt.commission_id)
    # A session this process has never heard of is in nobody's hands either.
    assert host.task_agents.hold_files("some-old-session") is True
    host.task_agents.release_files("some-old-session")


async def test_the_files_of_a_session_whose_commission_waits_for_an_answer_without_a_place_are_not_claimed(
    hosts, tmp_path
):
    host = _host(hosts, tmp_path, ScriptedLLM([_report("waiting", "Which separator?")]))
    receipt = await _dispatch(host, "add a CSV export")
    await wait_status(host, receipt.commission_id, "waiting_user")
    await wait_until(
        lambda: host.task_agents._sessions[receipt.commission_id].run_ended, what="the asking run ending"
    )
    await host.task_agents._runtime.close(receipt.commission_id)
    await _released(host, receipt.commission_id)

    # Nothing of it is in a runtime or waits for a place: that it holds the session is all
    # that says the session is in use.
    assert host.task_agents._held_by == {receipt.commission_id: receipt.commission_id}
    assert host.task_agents.hold_files(receipt.commission_id) is False


async def test_the_files_of_a_session_whose_commission_waits_for_a_place_are_not_claimed(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        "sort the photos": ScriptedLLM([[wait, *_report()]]),
        "rename the notes": ScriptedLLM([_report()]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts), max_task_agents=1)
    first = await _dispatch(host, "sort the photos")
    await wait_status(host, first.commission_id, "working")
    second = await _dispatch(host, "rename the notes")
    await host.brain.settle()
    assert (await host.ledger.get(second.commission_id)).status == "queued"

    # No commission holds its session yet (the one place is taken by another session): that
    # one waits to be started in it is all that says the session is in use.
    assert second.commission_id not in host.task_agents._held_by
    assert host.task_agents.hold_files(second.commission_id) is False

    release.set()
    await wait_status(host, second.commission_id, "done")


async def test_commissions_filed_under_the_project_of_those_that_belong_to_none_do_not_share_a_session(
    hosts, tmp_path
):
    from nekoclaw.host.paths import DEFAULT_PROJECT

    host = _host(hosts, tmp_path, ScriptedLLM([_report(), _report()]))
    kinds = []
    for text in ("sort the photos", "rename the notes"):
        commission, _created = await host.ledger.create(
            lanlan_name="Lan",
            title=text,
            dispatch_text=text,
            user_text=text,
            source="dispatch",
            lang="en",
            project=DEFAULT_PROJECT,
        )
        await host.task_agents.submit(commission)
        done = await wait_status(host, commission.id, "done")
        await _released(host, commission.id)
        await _let_go(host)
        # A session of its own, under its own id.
        assert done.session_id == commission.id
        kinds.append((await host.ledger.get_session(done.session_id)).kind)

    assert kinds == ["single", "single"]


async def test_every_commission_says_in_which_language_to_answer(hosts, tmp_path):
    scripts = {
        "add a CSV export": ScriptedLLM([_report("ready", "export added")]),
        "add a chart": ScriptedLLM([_report("ready", "chart added")]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, "add a CSV export to the budget app")
    await wait_status(host, first.commission_id, "done")
    await _released(host, first.commission_id)
    await _let_go(host)
    second = await _dispatch(host, "add a chart to the budget app", lang="ja")
    done = await wait_status(host, second.commission_id, "done")
    assert done.session_id == first.commission_id

    # The message that opens a commission names its language. The system prompt of the
    # session names none: it stays as the session was opened, so a language named there
    # would be the first commission's and contradict the second's.
    request = scripts["add a chart"].requests[0]
    said = _user_messages(request)
    opened_first = next(text for text in said if text.startswith(f"Commission {first.commission_id}\n"))
    opened_second = next(text for text in said if text.startswith(f"Commission {second.commission_id}\n"))
    assert opened_first.endswith(runtime_texts.REPLY_LANGUAGE_SENTENCE.replace("<<LANGUAGE>>", "English"))
    assert opened_second.endswith(runtime_texts.REPLY_LANGUAGE_SENTENCE.replace("<<LANGUAGE>>", "Japanese"))
    # Named there whatever the sentence says around it.
    assert "English" in opened_first.rsplit("\n\n", 1)[-1]
    assert "Japanese" in opened_second.rsplit("\n\n", 1)[-1]
    (system,) = [str(message["content"]) for message in request if message.get("role") == "system"]
    assert runtime_texts.USER_LANGUAGE_GUIDANCE in system
    assert "English" not in system and "Japanese" not in system
