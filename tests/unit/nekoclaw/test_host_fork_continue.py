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

"""A later commission of a project carried on in a copy of the project's session.

While another commission holds the project's session (at work, or waiting
for the user), a later one is worked on in a read-only copy of it instead of
waiting. One that needs a change there is handed back to the session itself
with what the copy found, and waits for it as it would have. The session
itself is told, with the next commission opened in it, what copies of it
finished meanwhile.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import config.nekoclaw_settings as settings
from config.prompts import prompts_nekoclaw_brain as brain_texts
from nekoclaw.host.conversation import RecentConversation
from tests.unit.nekoclaw.fakes import ScriptedLLM, call, finish, say, usage
from tests.unit.nekoclaw.host_fakes import (
    FakeDecisionLLM,
    fake_model,
    make_host,
    scripted_runtime,
    scripts_by_commission,
    wait_status,
    wait_until,
)

EXPORT = "add a CSV export"
FILES = "which files hold the tables"
RENAME = "rename the chart"


@pytest.fixture
async def hosts() -> Any:
    made: list[Any] = []
    yield made
    for host in made:
        await host.shutdown()


def _host(hosts: list[Any], tmp_path: Path, scripts: dict[str, ScriptedLLM], **kwargs: Any) -> Any:
    kwargs.setdefault("task_agent_model", fake_model)
    kwargs.setdefault("decision_llm", FakeDecisionLLM())
    kwargs.setdefault("conversation", RecentConversation())
    host = make_host(tmp_path, None, runtime_factory=scripted_runtime(scripts_by_commission(scripts)), **kwargs)
    hosts.append(host)
    return host


async def _dispatch(host: Any, text: str) -> Any:
    return (await host.dispatch(f"{text} in the budget app", lanlan_name="Lan", lang="en")).commissions[0]


async def _filed(host: Any, text: str, source: str = "dispatch") -> Any:
    """A commission of the project handed to a task agent without her: as she would, with ``source``."""
    commission, _created = await host.ledger.create(
        lanlan_name="Lan", title=text, dispatch_text=text, user_text=text, source=source, project="Budget app", lang="en"
    )
    await host.task_agents.submit(commission)
    return commission


def _report(summary: str, call_id: str = "call-r") -> list[Any]:
    return [call("report", {"status": "ready", "summary": summary}, call_id), finish("tool_calls")]


def _gate() -> tuple[asyncio.Event, Any]:
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    return release, wait


def _said(request: list[dict[str, Any]]) -> list[str]:
    said = [str(message["content"]) for message in request if message.get("role") == "user"]
    return [text for text in said if not text.startswith("<runtime_context>")]


async def _types(host: Any, commission_id: str) -> list[str]:
    await host.task_agents.settle()
    return [event.type for event in await host.ledger.events(commission_id)]


async def _export_at_work(host: Any, scripts: dict[str, ScriptedLLM]) -> Any:
    """The project's first commission, at work on its first request until the test lets it go on."""
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, EXPORT)
    await wait_until(lambda: scripts[EXPORT].requests, what="the export at work")
    return await wait_status(host, first.id, "working")


async def test_a_later_commission_is_carried_on_in_a_copy_while_the_session_is_at_work(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        FILES: ScriptedLLM([[call("glob", {"pattern": "*"}, "call-g"), finish("tool_calls")], _report("tables.py and export.py")]),
        "add a chart": ScriptedLLM([_report("chart added")]),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    main = await host.ledger.get_session(first.session_id)
    assert main.kind == "project"

    second = await _dispatch(host, FILES)
    done = await wait_status(host, second.id, "done", "failed", "blocked")
    # Done while the first is still at work, in a copy of the project's session.
    assert (done.status, done.result_summary) == ("done", "tables.py and export.py")
    assert (await host.ledger.get(first.id)).status == "working"
    copy = await host.ledger.get_session(done.session_id)
    assert (copy.kind, copy.forked_from, copy.workdir) == ("fork", main.id, main.workdir)
    types = await _types(host, second.id)
    assert types.index("queued") < types.index("carried_on_in_copy")
    [carried] = [event.payload for event in await host.ledger.events(second.id) if event.type == "carried_on_in_copy"]
    assert carried == {"session_id": copy.id, "of": main.id, "behind": first.id}
    # The copy begins as the session's requests do, holds what was said in it, and is told what it is.
    copied = scripts[FILES].requests[0]
    assert copied[0] == scripts[EXPORT].requests[0][0]
    opening = _said(copied)[-1]
    assert opening.startswith(f"Commission {second.id}\n") and brain_texts.TASK_AGENT_COMMISSION_IN_COPY in opening
    assert any(f"Commission {first.id}" in text for text in _said(copied)[:-1])
    # The session itself was not disturbed: its one request so far is its first.
    assert len(scripts[EXPORT].requests) == 1

    release.set()
    await wait_status(host, first.id, "done")
    # The next commission opened in the session itself is told what the copy did, once.
    third = await _dispatch(host, "add a chart")
    await wait_status(host, third.id, "done")
    told = _said(scripts["add a chart"].requests[0])[-1]
    assert told.startswith(f"Commission {third.id}\n")
    item = brain_texts.TASK_AGENT_FORKED_WORK_ITEM.format(
        title=done.title, status="done", session=copy.id[:8], result="tables.py and export.py"
    )
    assert brain_texts.TASK_AGENT_FORKED_WORK.format(items=item) in told
    assert brain_texts.TASK_AGENT_COMMISSION_IN_COPY not in told
    fourth_scripts = ScriptedLLM([_report("nothing more")])
    scripts["list the reports"] = fourth_scripts
    fourth = await _dispatch(host, "list the reports")
    await wait_status(host, fourth.id, "done")
    assert "read-only copies of it worked on" not in _said(fourth_scripts.requests[0])[-1]


async def test_a_commission_that_needs_a_change_in_the_copy_is_handed_back_with_what_it_found(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        RENAME: ScriptedLLM(
            [
                [call("grep", {"pattern": "chart"}, "call-1"), finish("tool_calls")],
                [call("write", {"file_path": "chart.py", "content": "in the copy"}, "call-2"), finish("tool_calls")],
                [say("The chart is drawn in chart.py, in draw(); I was about to give it the new title."), finish()],
                # In the session itself.
                [call("write", {"file_path": "chart.py", "content": "renamed"}, "call-3"), finish("tool_calls")],
                _report("renamed", "call-4"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    main = await host.ledger.get_session(first.session_id)
    second = await _dispatch(host, RENAME)
    # Handed back: it waits for the session itself, which the first still holds.
    await wait_until(lambda: len(scripts[RENAME].requests) == 3, what="the copy writing down what it found")
    await wait_status(host, second.id, "queued")
    await wait_until(lambda: host.task_agents._session_of.get(second.id) == main.id, what="the hand-back")
    stands = await host.ledger.get(second.id)
    assert stands.session_id == main.id
    copy_id = next(
        event.payload["session_id"] for event in await host.ledger.events(second.id) if event.type == "carried_on_in_copy"
    )
    assert copy_id not in host.task_agents._held_by
    assert (await host.ledger.get_session(copy_id)).status == "closed"
    # Nothing was written in the copy; it was told to write down what it found, once.
    assert not (Path(main.workdir) / "chart.py").exists()
    told = json.dumps(scripts[RENAME].requests[2], ensure_ascii=False)
    assert "`write` was not carried out" in told
    assert "`write` was not carried out" not in json.dumps(scripts[RENAME].requests[1], ensure_ascii=False)
    types = await _types(host, second.id)
    assert types.index("hand_back_told") < types.index("handed_back")

    release.set()
    await wait_status(host, first.id, "done")
    done = await wait_status(host, second.id, "done", "failed", "blocked")
    assert (done.status, done.session_id) == ("done", main.id)
    assert (Path(main.workdir) / "chart.py").read_text(encoding="utf-8") == "renamed"
    # In the session itself: opened there anew, then what the copy found, framed as no word of the user's.
    said = _said(scripts[RENAME].requests[3])[-1]
    assert said.startswith(f"Commission {second.id}\n") and brain_texts.TASK_AGENT_COMMISSION_IN_COPY not in said
    # Not as one it was at before: it never was, here.
    assert brain_texts.TASK_AGENT_COMMISSION_BACK not in said
    handed = said[said.index(brain_texts.TASK_AGENT_FORK_HANDED_BACK.split("{tool}")[0]) :]
    assert handed.startswith(brain_texts.TASK_AGENT_FORK_HANDED_BACK.split("{tool}")[0] + "write`")
    assert "\n    The chart is drawn in chart.py, in draw(); I was about to give it the new title.\n" in handed
    # What the copy said is not in the session itself.
    request = json.dumps(scripts[RENAME].requests[3], ensure_ascii=False)
    assert "in the copy" not in request


async def test_a_copy_that_goes_on_calling_tools_is_handed_back_after_its_request_for_it(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        RENAME: ScriptedLLM(
            [
                [call("bash", {"command": "sed -i s/a/b/ chart.py"}, "call-1"), finish("tool_calls")],
                [call("glob", {"pattern": "*.py"}, "call-2"), finish("tool_calls")],
                _report("renamed", "call-3"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    second = await _dispatch(host, RENAME)
    await wait_until(lambda: _handed_back(host, second.id), what="the hand-back")
    # Its request for writing down what it found is the only one it got.
    assert len(scripts[RENAME].requests) == 2
    release.set()
    assert (await wait_status(host, second.id, "done")).session_id == first.session_id
    handed = _said(scripts[RENAME].requests[2])[-1]
    assert "`bash`" in handed and f"\n    {brain_texts.TASK_AGENT_FORK_FOUND_NOTHING}\n" in handed


async def test_what_the_user_says_to_it_in_the_copy_goes_back_with_it(hosts, tmp_path):
    release, wait = _gate()
    copy_release, copy_wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        RENAME: ScriptedLLM(
            [
                [copy_wait, call("write", {"file_path": "chart.py", "content": "x"}, "call-1"), finish("tool_calls")],
                [say("Found it in chart.py."), finish()],
                _report("renamed", "call-2"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    second = await _dispatch(host, RENAME)
    await wait_until(lambda: scripts[RENAME].requests, what="the copy at work")
    assert await host.task_agents.tell(second.id, "call it Monthly totals")
    copy_release.set()
    await wait_until(lambda: _handed_back(host, second.id), what="the hand-back")
    release.set()
    await wait_status(host, second.id, "done")
    said = _said(scripts[RENAME].requests[2])[-1]
    # After what the copy found, what the user said to it there, which the session itself never saw.
    assert said.endswith("\n\ncall it Monthly totals")
    assert said.index(brain_texts.TASK_AGENT_FORK_HANDED_BACK.split("{tool}")[0]) < said.index("call it Monthly totals")
    assert (await host.ledger.get(first.id)).status == "done"


async def test_a_session_waiting_for_its_user_is_copied_too(hosts, tmp_path):
    scripts = {
        EXPORT: ScriptedLLM(
            [[call("report", {"status": "waiting", "summary": "Which columns?"}, "call-w"), finish("tool_calls")]]
        ),
        FILES: ScriptedLLM([_report("tables.py")]),
    }
    host = _host(hosts, tmp_path, scripts)
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, EXPORT)
    await wait_status(host, first.id, "waiting_user")
    # Filed as a dispatch is (said here, she would take it for the answer).
    second = await _filed(host, FILES)
    done = await wait_status(host, second.id, "done")
    assert (await host.ledger.get_session(done.session_id)).kind == "fork"
    assert (await host.ledger.get(first.id)).status == "waiting_user"


@pytest.mark.parametrize("why", ["switched_off", "cold_and_long", "too_many", "not_dispatched"])
async def test_a_commission_waits_for_its_session_where_it_is_not_carried_on(hosts, tmp_path, monkeypatch, why):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        FILES: ScriptedLLM([_report("tables.py")]),
        # At work in its copy until the first lets the session go.
        "list the reports": ScriptedLLM([[wait, *_report("reports")]]),
    }
    if why == "switched_off":
        monkeypatch.setattr(settings, "TASK_AGENT_FORK_CONTINUE_ENABLED", False)
    elif why == "cold_and_long":
        # No request of the session lately, and any length is too long.
        monkeypatch.setattr(settings, "TASK_AGENT_FORK_CONTINUE_COLD_MAX_TOKENS", -1)
    elif why == "too_many":
        monkeypatch.setattr(settings, "TASK_AGENT_FORK_CONTINUE_MAX_PER_SESSION", 1)
    host = _host(hosts, tmp_path, scripts)
    copied = _copies_made(host, monkeypatch)
    first = await _export_at_work(host, scripts)
    if why == "too_many":
        carried = await _dispatch(host, "list the reports")
        await wait_until(
            lambda: any(item.carries_on_for for item in host.task_agents._sessions.values()), what="the first copy"
        )
    if why == "not_dispatched":
        # What another session wrote is at home in the session itself.
        second = await _filed(host, FILES, source="session")
    else:
        second = await _dispatch(host, FILES)
    await _decided(host)
    assert len(copied) == (1 if why == "too_many" else 0)
    assert (await host.ledger.get(second.id)).status == "queued"
    assert "carried_on_in_copy" not in await _types(host, second.id)
    assert not scripts[FILES].requests
    release.set()
    done = await wait_status(host, second.id, "done")
    assert done.session_id == first.session_id
    if why == "too_many":
        assert (await host.ledger.get_session((await host.ledger.get(carried.id)).session_id)).kind == "fork"


async def test_as_many_copies_as_may_be_at_work_at_once_are(hosts, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TASK_AGENT_FORK_CONTINUE_MAX_PER_SESSION", 2)
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        # Both at work in their copies until the first lets the session go.
        FILES: ScriptedLLM([[wait, *_report("tables.py")]]),
        "list the reports": ScriptedLLM([[wait, *_report("reports")]]),
    }
    host = _host(hosts, tmp_path, scripts)
    await _export_at_work(host, scripts)
    carried = [await _dispatch(host, FILES)]
    # Handed over the moment the first is being started in its copy: counted once.
    await wait_until(
        lambda: any(item.carries_on_for for item in host.task_agents._sessions.values()), what="the first copy"
    )
    carried.append(await _dispatch(host, "list the reports"))
    await wait_until(lambda: scripts["list the reports"].requests, what="the second copy at work")
    release.set()
    for item in carried:
        done = await wait_status(host, item.id, "done")
        assert (await host.ledger.get_session(done.session_id)).kind == "fork"


async def test_a_session_that_made_a_request_lately_is_copied_however_long(hosts, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TASK_AGENT_FORK_CONTINUE_COLD_MAX_TOKENS", -1)
    release, wait = _gate()
    scripts = {
        # A request that is answered, then one that waits: the session made a request a moment ago.
        EXPORT: ScriptedLLM(
            [[call("glob", {"pattern": "*"}, "call-g"), usage(1_200), finish("tool_calls")], [wait, *_report("export added")]]
        ),
        FILES: ScriptedLLM([[usage(1_300), *_report("tables.py")]]),
    }
    host = _host(hosts, tmp_path, scripts)
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, EXPORT)
    await wait_until(lambda: len(scripts[EXPORT].requests) == 2, what="the export's second request")
    first = await wait_status(host, first.id, "working")
    await wait_until(lambda: first.session_id in host.task_agents._last_request_at, what="the request on record")
    made_at = host.task_agents._last_request_at[first.session_id]
    second = await _dispatch(host, FILES)
    done = await wait_status(host, second.id, "done")
    assert (await host.ledger.get_session(done.session_id)).kind == "fork"
    # What the copy sent begins as the session's requests do: it keeps the session's head warm too.
    assert host.task_agents._last_request_at[first.session_id] > made_at
    # Past the time a provider keeps it, it is not.
    host.task_agents._last_request_at[first.session_id] -= settings.TASK_AGENT_FORK_CONTINUE_HOT_SECONDS + 1
    scripts["list the reports"] = ScriptedLLM([_report("reports")])
    copied = _copies_made(host, monkeypatch)
    third = await _dispatch(host, "list the reports")
    await _decided(host)
    assert copied == []
    assert (await host.ledger.get(third.id)).status == "queued"
    release.set()
    assert (await wait_status(host, third.id, "done")).session_id == first.session_id


async def test_a_commission_whose_session_comes_free_while_it_is_copied_goes_on_there(hosts, tmp_path, monkeypatch):
    release, wait = _gate()
    scripts = {EXPORT: ScriptedLLM([[wait, *_report("export added")]]), FILES: ScriptedLLM([_report("tables.py")])}
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    manager = host.task_agents
    fork_of = manager.fork_session
    copying, copied = asyncio.Event(), asyncio.Event()
    made: list[Any] = []

    async def slow(*args: Any, **kwargs: Any) -> Any:
        copying.set()
        await copied.wait()
        copy = await fork_of(*args, **kwargs)
        made.append(copy)
        return copy

    monkeypatch.setattr(manager, "fork_session", slow)
    second = await _dispatch(host, FILES)
    await asyncio.wait_for(copying.wait(), 10)
    release.set()
    done = await wait_status(host, second.id, "done")
    assert done.session_id == first.session_id
    copied.set()
    await wait_until(lambda: made, what="the copy")
    await wait_until(
        lambda: _status_of(host, made[0].id, "cleared"), what="the copy that came to nothing being removed"
    )


def _copies_made(host: Any, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Note every copy the host makes of a session from here on."""
    manager = host.task_agents
    fork_of = manager.fork_session
    made: list[Any] = []

    async def noting(*args: Any, **kwargs: Any) -> Any:
        made.append(args)
        return await fork_of(*args, **kwargs)

    monkeypatch.setattr(manager, "fork_session", noting)
    return made


async def _decided(host: Any) -> None:
    """Wait until what was handed over is queued or started, and every attempt to carry it on is through."""
    await host.brain.settle()
    await host.task_agents.settle()
    await asyncio.gather(*list(host.task_agents._starting), return_exceptions=True)


async def _handed_back(host: Any, commission_id: str) -> bool:
    manager = host.task_agents
    return commission_id in manager._queue and "handed_back" in await _types(host, commission_id)


async def _status_of(host: Any, session_id: str, status: str) -> bool:
    kept = await host.ledger.get_session(session_id)
    return kept is not None and kept.status == status


@pytest.fixture
async def ledger(tmp_path: Any) -> Any:
    from nekoclaw.host.ledger import Ledger

    book = Ledger(tmp_path / "nekoclaw" / "ledger.db")
    yield book
    await book.close()


async def test_a_session_counts_only_the_opening_messages_it_took(ledger):
    first, _ = await ledger.create(lanlan_name="Lan", title="a", dispatch_text="a", user_text="a", source="dispatch")
    second, _ = await ledger.create(lanlan_name="Lan", title="b", dispatch_text="b", user_text="b", source="dispatch")
    first, session = await ledger.give_session(first.id, kind="project", name="p")
    await ledger.update(second.id, session_id=session.id)
    await ledger.append_event(first.id, "opened", {"session_id": session.id})
    # The second took its opening message in another session (a copy), and was handed back since.
    await ledger.append_event(second.id, "opened", {"session_id": "copy"})
    assert await ledger.opened_last(session.id) == first.id
    assert await ledger.opened_in(second.id, session.id) is None
    assert await ledger.opened_in(second.id, "copy") is not None
    assert await ledger.opened_in(first.id, session.id) is not None


async def test_every_finished_copy_listed_is_told_whole_within_the_limit(hosts, tmp_path):
    scripts = {EXPORT: ScriptedLLM([_report("export added")]), "add a chart": ScriptedLLM([_report("chart added")])}
    host = _host(hosts, tmp_path, scripts)
    await host.space.touch_project("Budget app")
    first = await wait_status(host, (await _dispatch(host, EXPORT)).id, "done")
    long = " ".join(["the totals are computed month by month in tables.py"] * 40)
    finished = []
    for number in range(settings.TASK_AGENT_FORKED_WORK_MAX_ITEMS):
        copy = await host.ledger.create_session(
            lanlan_name="Lan", kind="fork", forked_from=first.session_id, name=f"copy-{number}", project="Budget app"
        )
        item, _created = await host.ledger.create(
            lanlan_name="Lan", title=f"question {number}", dispatch_text="q", user_text="q", source="dispatch",
            session_id=copy.id,
        )
        await host.ledger.transition(item.id, "working")
        # (Made to carry a commission on in, as the host marks it.)
        await host.ledger.append_event(item.id, "carried_on_in_copy", {"session_id": copy.id, "of": first.session_id})
        await host.ledger.transition(item.id, "done", result_summary=long)
        finished.append(item)
    # One made by other means is not among them.
    other = await host.ledger.create_session(
        lanlan_name="Lan", kind="fork", forked_from=first.session_id, name="by-hand", project="Budget app"
    )
    stray, _created = await host.ledger.create(
        lanlan_name="Lan", title="by hand", dispatch_text="q", user_text="q", source="dispatch", session_id=other.id
    )
    await host.ledger.transition(stray.id, "working")
    await host.ledger.transition(stray.id, "done", result_summary="by hand")
    third = await wait_status(host, (await _dispatch(host, "add a chart")).id, "done")
    told = _said(scripts["add a chart"].requests[0])[-1]
    assert '"by hand"' not in told
    assert told.startswith(f"Commission {third.id}\n")
    head = told.index(brain_texts.TASK_AGENT_FORKED_WORK.split("{items}")[0])
    part = told[head:].split("\n\n")[0]
    from utils.tokenize import count_tokens

    assert count_tokens(part) <= settings.TASK_AGENT_FORKED_WORK_MAX_TOKENS
    # Each one put on record as told was said.
    for item in finished:
        assert f'"{item.title}" (done, in session {item.session_id[:8]}): the totals' in part
        assert "told_to_session" in await _types(host, item.id)


async def test_what_the_user_answered_in_the_copy_goes_back_with_it(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        RENAME: ScriptedLLM(
            [
                [call("report", {"status": "waiting", "summary": "Which title?"}, "call-w"), finish("tool_calls")],
                [call("write", {"file_path": "chart.py", "content": "x"}, "call-1"), finish("tool_calls")],
                [say("The title is set in chart.py."), finish()],
                _report("renamed", "call-2"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    await _export_at_work(host, scripts)
    second = await _dispatch(host, RENAME)
    await wait_status(host, second.id, "waiting_user")
    assert await host.task_agents.answer(second.id, "Monthly totals")
    await wait_until(lambda: _handed_back(host, second.id), what="the hand-back")
    release.set()
    await wait_status(host, second.id, "done")
    said = _said(scripts[RENAME].requests[3])[-1]
    assert said.endswith("\n\nMonthly totals")


async def test_a_copy_whose_close_cannot_be_asked_for_is_closed_by_the_host_without_more_requests(hosts, tmp_path, monkeypatch):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        RENAME: ScriptedLLM(
            [
                [call("write", {"file_path": "chart.py", "content": "x"}, "call-1"), finish("tool_calls")],
                [call("glob", {"pattern": "*.py"}, "call-2"), finish("tool_calls")],
                [call("glob", {"pattern": "*.md"}, "call-3"), finish("tool_calls")],
                _report("renamed", "call-4"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    runtime = host.task_agents._runtimes.builtin()
    close = runtime.close
    failed: list[str] = []

    async def failing_once(session_id: str) -> None:
        if session_id != first.session_id and not failed:
            failed.append(session_id)
            raise RuntimeError("the runtime is busy")
        await close(session_id)

    monkeypatch.setattr(runtime, "close", failing_once)
    second = await _dispatch(host, RENAME)
    await wait_until(lambda: _handed_back(host, second.id), what="the hand-back")
    # The close that failed was asked again by the host: no more requests of the copy.
    assert failed and len(scripts[RENAME].requests) == 2
    release.set()
    assert (await wait_status(host, second.id, "done")).session_id == first.session_id


async def test_a_copy_that_is_gone_when_it_is_closed_is_handed_back_all_the_same(hosts, tmp_path, monkeypatch):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        RENAME: ScriptedLLM(
            [
                [call("write", {"file_path": "chart.py", "content": "x"}, "call-1"), finish("tool_calls")],
                [say("It is in chart.py."), finish()],
                _report("renamed", "call-2"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    runtime = host.task_agents._runtimes.builtin()
    close = runtime.close

    async def gone(session_id: str) -> None:
        if session_id == first.session_id:
            await close(session_id)
            return
        # As a runtime whose session went away meanwhile answers.
        runtime.core._reject(session_id, "close", f'unknown session "{session_id}"')

    monkeypatch.setattr(runtime, "close", gone)
    second = await _dispatch(host, RENAME)
    await wait_until(lambda: _handed_back(host, second.id), what="the hand-back")
    release.set()
    done = await wait_status(host, second.id, "done")
    assert done.session_id == first.session_id
    assert "\n    It is in chart.py.\n" in _said(scripts[RENAME].requests[2])[-1]


async def test_one_handed_back_goes_before_those_handed_over_after_it(hosts, tmp_path, monkeypatch):
    # One copy at a time: the second waits for the session itself.
    monkeypatch.setattr(settings, "TASK_AGENT_FORK_CONTINUE_MAX_PER_SESSION", 1)
    release, wait = _gate()
    copy_release, copy_wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        RENAME: ScriptedLLM(
            [
                [copy_wait, call("write", {"file_path": "chart.py", "content": "x"}, "call-1"), finish("tool_calls")],
                [say("load() is in budget.py."), finish()],
                _report("renamed", "call-2"),
            ]
        ),
        "add a chart": ScriptedLLM([_report("chart added")]),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    renaming = await _dispatch(host, RENAME)
    await wait_until(lambda: scripts[RENAME].requests, what="the copy at work")
    later = await _dispatch(host, "add a chart")
    await host.brain.settle()
    assert list(host.task_agents._queue) == [later.id]
    copy_release.set()
    await wait_until(lambda: _handed_back(host, renaming.id), what="the hand-back")
    # Handed over first, it is worked on first in the session itself.
    assert list(host.task_agents._queue) == [renaming.id, later.id]
    release.set()
    renamed = await wait_status(host, renaming.id, "done")
    added = await wait_status(host, later.id, "done")
    assert renamed.session_id == added.session_id == first.session_id
    said = (Path((await host.ledger.get_session(first.session_id)).session_dir) / "transcript.jsonl").read_text(
        encoding="utf-8"
    )
    assert said.index(f"Commission {renaming.id}") < said.index(f"Commission {later.id}")


async def test_a_commission_put_in_a_copy_by_other_means_is_not_carried_on(hosts, tmp_path):
    scripts = {
        EXPORT: ScriptedLLM([_report("export added")]),
        # A call that changes something is answered as in any copy, and hands nothing back.
        FILES: ScriptedLLM(
            [[call("write", {"file_path": "x.py", "content": "x"}, "call-w"), finish("tool_calls")], _report("tables.py")]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    await host.space.touch_project("Budget app")
    first = await wait_status(host, (await _dispatch(host, EXPORT)).id, "done")
    await wait_until(lambda: not host.task_agents._held_by, what="the session let go")
    copy = await host.task_agents.fork_session(first.session_id)
    item, _created = await host.ledger.create(
        lanlan_name="Lan", title=FILES, dispatch_text=FILES, user_text=FILES, source="dispatch", session_id=copy.id,
        lang="en",
    )
    await host.task_agents.submit(item)
    done = await wait_status(host, item.id, "done")
    assert brain_texts.TASK_AGENT_COMMISSION_IN_COPY not in _said(scripts[FILES].requests[0])[-1]
    assert done.session_id == copy.id and "handed_back" not in await _types(host, item.id)
    assert "was not carried out" not in json.dumps(scripts[FILES].requests[1], ensure_ascii=False)


def test_a_commission_is_counted_once_while_it_is_both_copied_for_and_at_work_in_its_copy(tmp_path):
    from nekoclaw.host.task_agents import _Session

    host = make_host(tmp_path, None)
    manager = host.task_agents
    manager._sessions["c1"] = _Session("c1", session_id="copy-1", carries_on_for="main")
    manager._copying_for["main"] = {"c1", "c2"}
    assert manager._carrying_on("main") == 2
    del manager._sessions["c1"]


async def test_a_follow_up_in_the_copy_that_needs_a_change_is_handed_back_too(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        FILES: ScriptedLLM([_report("tables.py")]),
        # Said after the answer: change what it found.
        "now rename the table": ScriptedLLM(
            [
                [call("edit", {"file_path": "tables.py", "old_string": "a", "new_string": "b"}, "call-e"), finish("tool_calls")],
                [say("The table is in tables.py."), finish()],
                _report("renamed", "call-r"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    answered = await wait_status(host, (await _dispatch(host, FILES)).id, "done")
    follow_up, _created = await host.ledger.create(
        lanlan_name="Lan", title="now rename the table", dispatch_text="now rename the table", user_text="now rename the table",
        source="dispatch", session_id=answered.session_id, continues=answered.id, lang="en",
    )
    await host.task_agents.submit(follow_up)
    await wait_until(lambda: _handed_back(host, follow_up.id), what="the hand-back of the follow-up")
    release.set()
    done = await wait_status(host, follow_up.id, "done")
    assert done.session_id == first.session_id
    # In the session itself the commission it follows up is not above: it is told what came of it.
    opened = next(text for text in _said(scripts["now rename the table"].requests[2]) if text.startswith(f"Commission {follow_up.id}"))
    assert brain_texts.TASK_AGENT_COMMISSION_CONTINUES_IN_COPY.format(title=answered.title, result="tables.py") in opened


async def test_a_copy_that_asks_its_user_instead_goes_on_as_the_answer_says(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        RENAME: ScriptedLLM(
            [
                [call("write", {"file_path": "chart.py", "content": "x"}, "call-1"), finish("tool_calls")],
                # Told to write down what it found, it asks the user instead.
                [call("report", {"status": "waiting", "summary": "Change it, or only tell you?"}, "call-w"), finish("tool_calls")],
                _report("the chart is titled Totals", "call-2"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    second = await _dispatch(host, RENAME)
    await wait_status(host, second.id, "waiting_user")
    assert await host.task_agents.answer(second.id, "only tell me the title")
    done = await wait_status(host, second.id, "done")
    # Done in the copy, the first still at work: nothing handed back.
    assert (await host.ledger.get_session(done.session_id)).kind == "fork"
    assert "handed_back" not in await _types(host, second.id)
    assert (await host.ledger.get(first.id)).status == "working"
    release.set()


async def test_a_copy_that_cannot_be_put_on_record_leaves_the_commission_waiting_for_its_session(hosts, tmp_path, monkeypatch):
    release, wait = _gate()
    scripts = {EXPORT: ScriptedLLM([[wait, *_report("export added")]]), FILES: ScriptedLLM([_report("tables.py")])}
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    ledger = host.ledger
    append = ledger.append_event

    async def failing(commission_id: str, kind: str, payload: Any) -> Any:
        if kind == "carried_on_in_copy":
            raise RuntimeError("the ledger is busy")
        return await append(commission_id, kind, payload)

    monkeypatch.setattr(ledger, "append_event", failing)
    made: list[Any] = []
    fork_of = host.task_agents.fork_session

    async def noting(*args: Any, **kwargs: Any) -> Any:
        copy = await fork_of(*args, **kwargs)
        made.append(copy)
        return copy

    monkeypatch.setattr(host.task_agents, "fork_session", noting)
    second = await _dispatch(host, FILES)
    await wait_until(lambda: made, what="the copy")
    await wait_until(lambda: _status_of(host, made[0].id, "cleared"), what="the copy being removed")
    assert list(host.task_agents._queue) == [second.id]
    assert (await host.ledger.get(second.id)).status == "queued"
    release.set()
    assert (await wait_status(host, second.id, "done")).session_id == first.session_id


async def test_a_copy_whose_session_went_cold_while_it_was_made_is_not_taken(hosts, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TASK_AGENT_FORK_CONTINUE_COLD_MAX_TOKENS", -1)
    release, wait = _gate()
    scripts = {EXPORT: ScriptedLLM([[wait, *_report("export added")]]), FILES: ScriptedLLM([_report("tables.py")])}
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    manager = host.task_agents
    manager._last_request_at[first.session_id] = asyncio.get_running_loop().time()
    fork_of = manager.fork_session
    made: list[Any] = []

    async def slow(*args: Any, **kwargs: Any) -> Any:
        copy = await fork_of(*args, **kwargs)
        # The provider's cache went cold while the copy was made.
        manager._last_request_at[first.session_id] -= settings.TASK_AGENT_FORK_CONTINUE_HOT_SECONDS + 1
        made.append(copy)
        return copy

    monkeypatch.setattr(manager, "fork_session", slow)
    second = await _dispatch(host, FILES)
    await wait_until(lambda: made, what="the copy")
    await wait_until(lambda: _status_of(host, made[0].id, "cleared"), what="the copy being removed")
    assert (await host.ledger.get(second.id)).status == "queued"
    release.set()
    assert (await wait_status(host, second.id, "done")).session_id == first.session_id


async def test_what_copies_finished_is_told_to_a_commission_filed_for_a_note_too(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        FILES: ScriptedLLM([_report("tables.py")]),
        "a note from another session": ScriptedLLM([_report("noted")]),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    done = await wait_status(host, (await _dispatch(host, FILES)).id, "done")
    release.set()
    await wait_status(host, first.id, "done")
    noted = await _filed(host, "a note from another session", source="session")
    await wait_status(host, noted.id, "done", "failed", "blocked")
    told = json.dumps(scripts["a note from another session"].requests[0], ensure_ascii=False)
    assert f"{done.title}" in told and "read-only copies of it worked on" in told



@pytest.mark.parametrize("when", ["with_the_call", "after_being_told"])
async def test_a_copy_that_reports_after_a_call_that_changes_something_is_handed_back(hosts, tmp_path, when):
    release, wait = _gate()
    if when == "with_the_call":
        steps = [
            [
                call("write", {"file_path": "chart.py", "content": "renamed"}, "call-w"),
                call("report", {"status": "ready", "summary": "renamed it in chart.py"}, "call-r", index=1),
                finish("tool_calls"),
            ]
        ]
    else:
        steps = [
            [call("write", {"file_path": "chart.py", "content": "renamed"}, "call-w"), finish("tool_calls")],
            [call("report", {"status": "blocked", "summary": "cannot write here; it is chart.py"}, "call-b"), finish("tool_calls")],
        ]
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        RENAME: ScriptedLLM(
            [
                *steps,
                [call("write", {"file_path": "chart.py", "content": "renamed"}, "call-m"), finish("tool_calls")],
                _report("renamed", "call-d"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    main = await host.ledger.get_session(first.session_id)
    second = await _dispatch(host, RENAME)
    await wait_until(lambda: _handed_back(host, second.id), what="the hand-back")
    assert (await host.ledger.get(second.id)).status == "queued"
    release.set()
    done = await wait_status(host, second.id, "done", "failed", "blocked")
    assert (done.status, done.session_id) == ("done", first.session_id)
    assert (Path(main.workdir) / "chart.py").read_text(encoding="utf-8") == "renamed"
    handed = _said(scripts[RENAME].requests[len(steps)])[-1]
    assert "chart.py" in handed[handed.index(brain_texts.TASK_AGENT_FORK_HANDED_BACK.split("{tool}")[0]) :]


async def test_what_the_user_answered_to_the_question_tool_of_a_copy_goes_back_with_it(hosts, tmp_path):
    release, wait = _gate()
    question = {"questions": [{"id": "title", "question": "Which title?", "options": [{"label": "A"}, {"label": "B"}]}]}
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        RENAME: ScriptedLLM(
            [
                [call("ask_user_question", question, "call-q"), finish("tool_calls")],
                [call("write", {"file_path": "chart.py", "content": "x"}, "call-1"), finish("tool_calls")],
                [say("The title is set in chart.py."), finish()],
                _report("renamed", "call-2"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    await _export_at_work(host, scripts)
    second = await _dispatch(host, RENAME)
    await wait_status(host, second.id, "waiting_user")
    assert await host.task_agents.answer(second.id, "Monthly totals")
    await wait_until(lambda: _handed_back(host, second.id), what="the hand-back")
    release.set()
    await wait_status(host, second.id, "done")
    assert _said(scripts[RENAME].requests[3])[-1].endswith("\n\nMonthly totals")


async def test_a_copy_no_runtime_holds_any_more_is_handed_back_at_once(hosts, tmp_path, monkeypatch):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        RENAME: ScriptedLLM(
            [
                [call("write", {"file_path": "chart.py", "content": "x"}, "call-1"), finish("tool_calls")],
                [say("It is in chart.py."), finish()],
                _report("renamed", "call-2"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    manager = host.task_agents
    runtime_of = manager._runtime_of

    def gone(session: Any) -> Any:
        # As where its runtime went away by the time it is handed back.
        return None if session.handing_back is not None else runtime_of(session)

    monkeypatch.setattr(manager, "_runtime_of", gone)
    second = await _dispatch(host, RENAME)
    await wait_until(lambda: _handed_back(host, second.id), what="the hand-back")
    monkeypatch.setattr(manager, "_runtime_of", runtime_of)
    release.set()
    assert (await wait_status(host, second.id, "done")).session_id == first.session_id



async def test_a_copy_still_knows_which_commission_it_opened_last_once_that_one_was_handed_back(ledger):
    first, _ = await ledger.create(lanlan_name="Lan", title="a", dispatch_text="a", user_text="a", source="dispatch")
    stuck, _ = await ledger.create(lanlan_name="Lan", title="b", dispatch_text="b", user_text="b", source="dispatch")
    later, _ = await ledger.create(lanlan_name="Lan", title="c", dispatch_text="c", user_text="c", source="dispatch")
    first, main = await ledger.give_session(first.id, kind="project", name="p")
    copy = await ledger.create_session(lanlan_name="Lan", kind="fork", forked_from=main.id, name="p/fork", project="p")
    await ledger.update(stuck.id, session_id=copy.id)
    await ledger.update(later.id, session_id=copy.id)
    await ledger.append_event(stuck.id, "opened", {"session_id": copy.id})
    await ledger.append_event(later.id, "opened", {"session_id": copy.id})
    # The later one was handed back to the session itself: the copy was at it last all the same.
    await ledger.update(later.id, session_id=main.id)
    assert await ledger.opened_last(copy.id) == later.id
    assert await ledger.opened_last(main.id) is None


async def test_a_copy_whose_run_is_over_and_cannot_be_closed_at_once_is_closed_by_the_host(hosts, tmp_path, monkeypatch):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        RENAME: ScriptedLLM(
            [
                [call("write", {"file_path": "chart.py", "content": "x"}, "call-1"), finish("tool_calls")],
                # Writes down what it found and ends its run.
                [say("It is in chart.py."), finish()],
                _report("renamed", "call-2"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    runtime = host.task_agents._runtimes.builtin()
    close = runtime.close
    failed: list[str] = []

    async def failing_once(session_id: str) -> None:
        if session_id != first.session_id and not failed:
            failed.append(session_id)
            raise RuntimeError("the runtime is busy")
        await close(session_id)

    monkeypatch.setattr(runtime, "close", failing_once)
    second = await _dispatch(host, RENAME)
    await wait_until(lambda: _handed_back(host, second.id), what="the hand-back")
    assert failed and len(scripts[RENAME].requests) == 2
    release.set()
    done = await wait_status(host, second.id, "done")
    assert done.session_id == first.session_id
    # Handed back once: queued for the session itself once.
    queued = [event for event in await host.ledger.events(second.id) if event.type == "status" and event.payload.get("to") == "queued"]
    assert len([event for event in queued if "handed back" in str(event.payload.get("reason"))]) == 1



async def test_a_hand_back_whose_look_at_the_ledger_fails_goes_on_once_the_copy_is_closed(hosts, tmp_path, monkeypatch):
    import sys

    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        RENAME: ScriptedLLM(
            [
                [call("write", {"file_path": "chart.py", "content": "x"}, "call-1"), finish("tool_calls")],
                [say("It is in chart.py."), finish()],
                _report("renamed", "call-2"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    ledger = host.ledger
    get = ledger.get
    failed: list[str] = []

    def failing(commission_id: str) -> Any:
        if sys._getframe(1).f_code.co_name == "_hand_back" and not failed:
            failed.append(commission_id)
            raise RuntimeError("the ledger is busy")
        return get(commission_id)

    monkeypatch.setattr(ledger, "get", failing)
    second = await _dispatch(host, RENAME)

    async def back() -> bool:
        # (Not on record as handed back: that note comes after the look that failed.)
        manager = host.task_agents
        return bool(failed) and second.id in manager._queue and manager._session_of.get(second.id) == first.session_id

    await wait_until(back, what="the hand-back")
    assert failed == [second.id] and (await host.ledger.get(second.id)).session_id == first.session_id
    release.set()
    done = await wait_status(host, second.id, "done")
    assert done.session_id == first.session_id
    assert "\n    It is in chart.py.\n" in _said(scripts[RENAME].requests[2])[-1]



async def test_what_a_copy_streams_before_it_reports_goes_back_as_it_was_written(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        RENAME: ScriptedLLM(
            [
                [call("write", {"file_path": "chart.py", "content": "x"}, "call-1"), finish("tool_calls")],
                [
                    say("The title is set in "),
                    say("chart"),
                    say(".py"),
                    say(", line "),
                    say("12."),
                    call("report", {"status": "blocked", "summary": "cannot write here"}, "call-b"),
                    finish("tool_calls"),
                ],
                _report("renamed", "call-2"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    await _export_at_work(host, scripts)
    second = await _dispatch(host, RENAME)
    await wait_until(lambda: _handed_back(host, second.id), what="the hand-back")
    release.set()
    await wait_status(host, second.id, "done")
    handed = _said(scripts[RENAME].requests[2])[-1]
    assert "\n    The title is set in chart.py, line 12.\n    cannot write here\n" in handed


async def test_a_copy_that_cannot_be_put_on_record_goes_back_ahead_of_those_queued_since(hosts, tmp_path, monkeypatch):
    # One copy at a time: what is handed over while the first is copied waits for the session.
    monkeypatch.setattr(settings, "TASK_AGENT_FORK_CONTINUE_MAX_PER_SESSION", 1)
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        FILES: ScriptedLLM([_report("tables.py")]),
        "list the reports": ScriptedLLM([_report("reports")]),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    ledger = host.ledger
    append = ledger.append_event
    writing, go_on = asyncio.Event(), asyncio.Event()

    async def failing(commission_id: str, kind: str, payload: Any) -> Any:
        if kind == "carried_on_in_copy":
            writing.set()
            await go_on.wait()
            raise RuntimeError("the ledger is busy")
        return await append(commission_id, kind, payload)

    monkeypatch.setattr(ledger, "append_event", failing)
    second = await _dispatch(host, FILES)
    await asyncio.wait_for(writing.wait(), 10)
    # Handed over while the first is being put on record: later than it.
    later = await _dispatch(host, "list the reports")
    await wait_until(lambda: later.id in host.task_agents._queue, what="the later one queued")
    go_on.set()
    await wait_until(lambda: second.id in host.task_agents._queue, what="the first back in the queue")
    assert list(host.task_agents._queue) == [second.id, later.id]
    release.set()
    assert (await wait_status(host, second.id, "done")).session_id == first.session_id
    await wait_status(host, later.id, "done")



async def test_two_copies_that_cannot_be_put_on_record_go_back_in_the_order_they_were_handed_over(
    hosts, tmp_path, monkeypatch
):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        FILES: ScriptedLLM([_report("tables.py")]),
        "list the reports": ScriptedLLM([_report("reports")]),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    ledger = host.ledger
    append = ledger.append_event
    writing: list[str] = []
    go_on: dict[str, asyncio.Event] = {}

    async def failing(commission_id: str, kind: str, payload: Any) -> Any:
        if kind == "carried_on_in_copy":
            writing.append(commission_id)
            go_on[commission_id] = asyncio.Event()
            await go_on[commission_id].wait()
            raise RuntimeError("the ledger is busy")
        return await append(commission_id, kind, payload)

    monkeypatch.setattr(ledger, "append_event", failing)
    earlier = await _dispatch(host, FILES)
    later = await _dispatch(host, "list the reports")
    await wait_until(lambda: len(writing) == 2, what="both being put on record")
    # The earlier one fails first and goes back; then the later one.
    go_on[earlier.id].set()
    await wait_until(lambda: earlier.id in host.task_agents._queue, what="the earlier one back in the queue")
    go_on[later.id].set()
    await wait_until(lambda: later.id in host.task_agents._queue, what="the later one back in the queue")
    assert list(host.task_agents._queue) == [earlier.id, later.id]
    release.set()
    assert (await wait_status(host, earlier.id, "done")).session_id == first.session_id
    await wait_status(host, later.id, "done")
