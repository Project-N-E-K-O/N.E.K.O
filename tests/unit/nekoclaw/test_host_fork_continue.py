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
        title=done.title, status="done", result="tables.py and export.py"
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
    await wait_status(host, second.id, "done")
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
    await host.brain.settle()
    await host.task_agents.settle()
    await asyncio.sleep(0.2)
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
    third = await _dispatch(host, "list the reports")
    await host.brain.settle()
    await host.task_agents.settle()
    await asyncio.sleep(0.2)
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
