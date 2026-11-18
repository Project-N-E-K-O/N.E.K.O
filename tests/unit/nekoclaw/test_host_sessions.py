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

from config.prompts import prompts_nekoclaw_brain as brain_texts
from tests.unit.nekoclaw.fakes import ScriptedLLM, call, finish, say
from tests.unit.nekoclaw.host_fakes import (
    FakeDecisionLLM,
    fake_model,
    make_host,
    scripted_runtime,
    scripts_by_commission,
    wait_status,
    wait_until,
)


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
    # It went on from the session as it is by now, told what the user added and not, again, what it is.
    said = _user_messages(llm.requests[-1])
    assert said[-1] == "you have access now"
    assert any(text.startswith(_opens(second.commission_id, "add a chart")) for text in said)
    assert sum(1 for text in said if text.startswith(_opens(first.commission_id, "add a CSV export"))) == 1


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
    assert specs[0].model.context_window == 256_000

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
    assert [spec.model.context_window for spec in specs] == [256_000, 32_768]


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
    # No model was asked where it goes.
    assert host.decision._llm.calls == []
    (decision,) = await _events(host, receipt.commission_id, "decision")
    assert (decision.payload["route"], decision.payload["continues"]) == ("task_agent", first.commission_id)
    said = _user_messages(scripts["rename the photos"].requests[0])
    assert said[0].startswith(_opens(first.commission_id, "sort the photos in my downloads folder"))
    assert said[-1].startswith(
        _opens(receipt.commission_id, "also rename the photos by date\nWhat the user said: oh and rename the photos too")
    )
    assert "follows up the earlier commission" in said[-1]


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

    # The system prompt of the session names the language of the commission it was first
    # opened for, and stays as it is; the message that opens a commission names its own.
    said = _user_messages(scripts["add a chart"].requests[0])
    opened_first = next(text for text in said if text.startswith(f"Commission {first.commission_id}\n"))
    opened_second = next(text for text in said if text.startswith(f"Commission {second.commission_id}\n"))
    assert opened_first.endswith("in English.")
    assert opened_second.endswith("in Japanese.")
