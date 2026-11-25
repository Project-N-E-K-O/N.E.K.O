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
for the user), a later one that is a side question is worked on in a
read-only copy of it instead of waiting; work waits. One that needs a change
there is handed back to the session itself with what the copy found, and
waits for it as it would have. A copy is not gone on in once its commission
ended: what follows it up goes to the session itself. The session itself is
told, for its information, what copies of it finished or handed back: while
it is at work, before its next step; otherwise with the next commission
opened in it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import config.nekopower_settings as settings
from config.prompts import prompts_nekopower_brain as brain_texts
from nekopower.host.conversation import RecentConversation
from tests.unit.nekopower.fakes import ScriptedLLM, call, finish, say, usage
from tests.unit.nekopower.host_fakes import (
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


async def _has_type(host: Any, commission_id: str, kind: str) -> bool:
    return kind in await _types(host, commission_id)


def _news_sent(host: Any, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Note every text the host sends that tells a session what copies of it did, as it is sent."""
    manager = host.task_agents
    send = manager._send_text
    head = brain_texts.TASK_AGENT_FORKED_WORK.split("{items}")[0]
    sent: list[str] = []

    async def noting(runtime: Any, session: Any, command: str, text: str, **kwargs: Any) -> Any:
        if command == "steer" and head in text:
            sent.append(str(text))
        return await send(runtime, session, command, text, **kwargs)

    monkeypatch.setattr(manager, "_send_text", noting)
    return sent


def _words(commission: Any) -> str:
    """The user's words a commission is quoted with in what its session is told of copies (``_forked_work_text``)."""
    return " ".join((commission.user_text or commission.dispatch_text).split())


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
    decisions = FakeDecisionLLM()
    scripts = {
        EXPORT: ScriptedLLM(
            [[wait, call("glob", {"pattern": "*.py"}, "call-x"), finish("tool_calls")], _report("export added", "call-y")]
        ),
        FILES: ScriptedLLM([[call("glob", {"pattern": "*"}, "call-g"), finish("tool_calls")], _report("tables.py and export.py")]),
        "add a chart": ScriptedLLM([_report("chart added")]),
    }
    host = _host(hosts, tmp_path, scripts, decision_llm=decisions)
    isolated: list[str] = []
    permissions = host.task_agents._permissions
    received = permissions.received_external_input

    def noting(commission_id: str) -> None:
        isolated.append(commission_id)
        received(commission_id)

    permissions.received_external_input = noting
    first = await _export_at_work(host, scripts)
    main = await host.ledger.get_session(first.session_id)
    # The member of a project that has no leader: its commissions come to it one after the other.
    assert main.kind == "line"

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
    # It was asked once whether this is a side question, with what was dispatched.
    [asked] = decisions.side_calls
    assert asked[0]["content"] == brain_texts.TASK_BRAIN_SIDE_QUESTION_PROMPT
    assert done.dispatch_text in asked[1]["content"]
    # The session itself was not disturbed: its one request so far is its first.
    assert len(scripts[EXPORT].requests) == 1

    # Told what the copy did before its next step, for its information: the user's words, what came of it, where.
    await wait_until(
        lambda: host.task_agents._sessions.get(first.id) is not None
        and host.task_agents._sessions[first.id].sent > 1,
        what="the session itself told what the copy did",
    )
    release.set()
    await wait_status(host, first.id, "done")
    item = brain_texts.TASK_AGENT_FORKED_WORK_ITEM.format(
        asked=_words(done), outcome="answered", session=copy.id[:8], result="tables.py and export.py"
    )
    news = brain_texts.TASK_AGENT_FORKED_WORK.format(items=item)
    assert news in "\n".join(_said(scripts[EXPORT].requests[1]))
    # On record once the session is seen to have taken it, which may be said after its run ended.
    await wait_until(lambda: _has_type(host, second.id, "told_to_session"), what="told on record")
    [told] = [event.payload for event in await host.ledger.events(second.id) if event.type == "told_to_session"]
    assert told == {"commission_id": first.id, "steered": True}
    # What the copy wrote stands in its conversation now: what was allowed it for the session or run is asked again.
    assert first.id in isolated
    # On record as no word of the user's (a review reads ``told`` for those).
    assert "told_of_copies" in await _types(host, first.id)
    assert "told" not in await _types(host, first.id)
    # Not as the user's words, and only once: the next commission opened in it is not told again.
    third = await _dispatch(host, "add a chart")
    await wait_status(host, third.id, "done")
    opened = _said(scripts["add a chart"].requests[0])[-1]
    assert opened.startswith(f"Commission {third.id}\n")
    assert brain_texts.TASK_AGENT_FORKED_WORK.split("{items}")[0] not in opened
    assert brain_texts.TASK_AGENT_COMMISSION_IN_COPY not in opened


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
    # Which copy it came back from, for a replay: the commission is the session's now.
    [back] = [event.payload for event in await host.ledger.events(second.id) if event.type == "handed_back"]
    assert (back["copy"], back["to"]) == (copy_id, main.id)

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
    from nekopower.host.ledger import Ledger

    book = Ledger(tmp_path / "nekopower" / "ledger.db")
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
        lanlan_name="Lan", title="by hand", dispatch_text="by hand", user_text="by hand", source="dispatch", session_id=other.id
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
        assert f'The user asked "q" (answered, in session {item.session_id[:8]}): the totals' in part
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
    from nekopower.host.task_agents import _Session

    host = make_host(tmp_path, None)
    manager = host.task_agents
    manager._sessions["c1"] = _Session("c1", session_id="copy-1", carries_on_for="main")
    manager._copying_for["main"] = {"c1", "c2"}
    assert manager._carrying_on("main") == 2
    del manager._sessions["c1"]


@pytest.mark.parametrize("brief", [None, 40], ids=["brief", "brief_cut_short"])
async def test_a_follow_up_that_is_work_goes_to_the_session_itself_and_waits_for_it(hosts, tmp_path, monkeypatch, brief):
    if brief is not None:
        # What the line that tells of the commission it follows up is said
        # past: put on record as told, it is said whole.
        monkeypatch.setattr(settings, "TASK_AGENT_BRIEF_MAX_TOKENS", brief)
    release, wait = _gate()
    decisions = FakeDecisionLLM()
    # The question is a side question; what follows it up is work.
    decisions.side = [{"side_question": True}, {"side_question": False}]
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        FILES: ScriptedLLM([_report("tables.py")]),
        # Said after the answer: change what it found.
        "now rename the table": ScriptedLLM(
            [
                [call("write", {"file_path": "tables.py", "content": "renamed"}, "call-e"), finish("tool_calls")],
                _report("renamed", "call-r"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts, decision_llm=decisions)
    copied = _copies_made(host, monkeypatch)
    first = await _export_at_work(host, scripts)
    main = await host.ledger.get_session(first.session_id)
    answered = await wait_status(host, (await _dispatch(host, FILES)).id, "done")
    # Filed for the copy, as a follow-up is for the session of the one it follows up.
    follow_up, _created = await host.ledger.create(
        lanlan_name="Lan", title="now rename the table", dispatch_text="now rename the table", user_text="now rename the table",
        source="dispatch", session_id=answered.session_id, continues=answered.id, lang="en",
    )
    await host.task_agents.submit(follow_up)
    await _decided(host)
    # The session itself's, and work: it waits for it, in no copy.
    stands = await host.ledger.get(follow_up.id)
    assert (stands.status, stands.session_id) == ("queued", main.id)
    assert len(decisions.side_calls) == 2
    assert len(copied) == 1
    assert not scripts["now rename the table"].requests
    release.set()
    done = await wait_status(host, follow_up.id, "done")
    assert done.session_id == main.id
    assert (Path(main.workdir) / "tables.py").read_text(encoding="utf-8") == "renamed"
    # In the session itself the commission it follows up is not above: it is told what came of it.
    opened = _said(scripts["now rename the table"].requests[0])[-1]
    assert opened.startswith(f"Commission {follow_up.id}\n")
    assert brain_texts.TASK_AGENT_COMMISSION_CONTINUES_IN_COPY.format(title=answered.title, result="tables.py") in opened


async def test_a_follow_up_that_is_a_side_question_is_answered_in_a_new_copy_of_the_session_itself(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        FILES: ScriptedLLM([_report("tables.py")]),
        "and which holds the totals": ScriptedLLM([_report("totals.py")]),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    answered = await wait_status(host, (await _dispatch(host, FILES)).id, "done")
    follow_up, _created = await host.ledger.create(
        lanlan_name="Lan", title="and which holds the totals", dispatch_text="and which holds the totals",
        user_text="and which holds the totals", source="dispatch", session_id=answered.session_id, continues=answered.id,
        lang="en",
    )
    await host.task_agents.submit(follow_up)
    done = await wait_status(host, follow_up.id, "done")
    # Not in the copy that answered the first: in a new one, of the session itself as it stands.
    assert done.session_id not in (answered.session_id, first.session_id)
    copy = await host.ledger.get_session(done.session_id)
    assert (copy.kind, copy.forked_from) == ("fork", first.session_id)
    # The first answer is in neither: it is told what came of it.
    opened = _said(scripts["and which holds the totals"].requests[0])[-1]
    assert brain_texts.TASK_AGENT_COMMISSION_CONTINUES_IN_COPY.format(title=answered.title, result="tables.py") in opened
    release.set()


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


async def test_what_a_copy_finished_after_the_session_went_idle_is_told_with_the_next_opening_only(hosts, tmp_path, monkeypatch):
    release, wait = _gate()
    copy_release, copy_wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        FILES: ScriptedLLM([[copy_wait, *_report("tables.py")]]),
        # Filed for a note another session wrote: opened in the session itself all the same.
        "a note from another session": ScriptedLLM(
            [[call("glob", {"pattern": "*.py"}, "call-n"), finish("tool_calls")], _report("noted")]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    sent = _news_sent(host, monkeypatch)
    append = host.ledger.append_event

    async def slow_told(commission_id: str, kind: str, payload: Any = None) -> Any:
        if kind == "told_to_session" and not (payload or {}).get("steered"):
            # What the opening told is put on record a while after it was
            # taken: meanwhile it is not steered in as well.
            await asyncio.sleep(0.3)
        return await append(commission_id, kind, payload)

    monkeypatch.setattr(host.ledger, "append_event", slow_told)
    forked_work = host.ledger.forked_work

    async def slow_read(*args: Any, **kwargs: Any) -> Any:
        # Read while that is being put on record.
        await asyncio.sleep(0.1)
        return await forked_work(*args, **kwargs)

    monkeypatch.setattr(host.ledger, "forked_work", slow_read)
    first = await _export_at_work(host, scripts)
    second = await _dispatch(host, FILES)
    await wait_until(lambda: scripts[FILES].requests, what="the copy at work")
    release.set()
    await wait_status(host, first.id, "done")
    copy_release.set()
    done = await wait_status(host, second.id, "done")
    await _decided(host)
    # Nothing woke the session itself: it has nothing to be at.
    assert len(scripts[EXPORT].requests) == 1
    assert "told_to_session" not in await _types(host, second.id)
    noted = await _filed(host, "a note from another session", source="session")
    await wait_status(host, noted.id, "done", "failed", "blocked")
    told = _said(scripts["a note from another session"].requests[0])[-1]
    assert told.startswith(f"Commission {noted.id}\n")
    assert brain_texts.TASK_AGENT_FORKED_WORK.split("{items}")[0] in told
    assert f'The user asked "{_words(done)}" (answered' in told
    # On record once the session is seen to have taken it, which may be said after its run ended.
    await wait_until(lambda: _has_type(host, second.id, "told_to_session"), what="told on record")
    # Told by that opening, and not steered in besides while it was on its way.
    await host.task_agents.settle()
    assert sent == []



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


@pytest.mark.parametrize(
    "answer",
    [{"side_question": False}, "not JSON", {"side_question": "yes"}, RuntimeError("the model is down")],
    ids=["work", "unreadable", "not_a_boolean", "failed"],
)
async def test_a_commission_that_is_not_told_to_be_a_side_question_waits_for_its_session(hosts, tmp_path, monkeypatch, answer):
    release, wait = _gate()
    decisions = FakeDecisionLLM()
    decisions.side = [answer]
    scripts = {EXPORT: ScriptedLLM([[wait, *_report("export added")]]), FILES: ScriptedLLM([_report("tables.py")])}
    host = _host(hosts, tmp_path, scripts, decision_llm=decisions)
    copied = _copies_made(host, monkeypatch)
    first = await _export_at_work(host, scripts)
    second = await _dispatch(host, FILES)
    await _decided(host)
    assert len(decisions.side_calls) == 1
    assert copied == []
    assert (await host.ledger.get(second.id)).status == "queued"
    release.set()
    assert (await wait_status(host, second.id, "done")).session_id == first.session_id


@pytest.mark.parametrize("why", ["switched_off", "cold_and_long", "not_dispatched", "no_place"])
async def test_whether_it_is_a_side_question_is_not_asked_where_it_is_not_carried_on_anyway(hosts, tmp_path, monkeypatch, why):
    release, wait = _gate()
    decisions = FakeDecisionLLM()
    scripts = {EXPORT: ScriptedLLM([[wait, *_report("export added")]]), FILES: ScriptedLLM([_report("tables.py")])}
    if why == "switched_off":
        monkeypatch.setattr(settings, "TASK_AGENT_FORK_CONTINUE_ENABLED", False)
    elif why == "cold_and_long":
        monkeypatch.setattr(settings, "TASK_AGENT_FORK_CONTINUE_COLD_MAX_TOKENS", -1)
    host = _host(hosts, tmp_path, scripts, decision_llm=decisions)
    await _export_at_work(host, scripts)
    if why == "no_place":
        # Every place taken: a copy could not be at work.
        host.task_agents._max_concurrent = 1
    second = await (_filed(host, FILES, source="session") if why == "not_dispatched" else _dispatch(host, FILES))
    await _decided(host)
    assert decisions.side_calls == []
    assert (await host.ledger.get(second.id)).status == "queued"
    release.set()


async def test_a_copy_is_told_the_question_its_session_waits_for_the_user_to_answer(hosts, tmp_path):
    decisions = FakeDecisionLLM()
    scripts = {
        EXPORT: ScriptedLLM(
            [[call("report", {"status": "waiting", "summary": "Which columns?"}, "call-w"), finish("tool_calls")]]
        ),
        FILES: ScriptedLLM([_report("tables.py")]),
    }
    host = _host(hosts, tmp_path, scripts, decision_llm=decisions)
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, EXPORT)
    await wait_status(host, first.id, "waiting_user")
    second = await _filed(host, FILES)
    await wait_status(host, second.id, "done")
    # Whether it is a side question was asked with that question: going on with it is work.
    [asked] = decisions.side_calls
    assert 'The agent is waiting for the user to answer: "Which columns?"' in asked[1]["content"]
    opening = _said(scripts[FILES].requests[0])[-1]
    assert brain_texts.TASK_AGENT_COMMISSION_IN_COPY in opening
    assert brain_texts.TASK_AGENT_COPY_SESSION_WAITS.format(question="Which columns?") in opening
    # Still the session's to ask.
    assert (await host.ledger.get(first.id)).status == "waiting_user"


async def test_a_copy_of_a_session_at_work_is_told_of_no_question(hosts, tmp_path):
    release, wait = _gate()
    decisions = FakeDecisionLLM()
    scripts = {EXPORT: ScriptedLLM([[wait, *_report("export added")]]), FILES: ScriptedLLM([_report("tables.py")])}
    host = _host(hosts, tmp_path, scripts, decision_llm=decisions)
    await _export_at_work(host, scripts)
    second = await _dispatch(host, FILES)
    await wait_status(host, second.id, "done")
    opening = _said(scripts[FILES].requests[0])[-1]
    assert brain_texts.TASK_AGENT_COPY_SESSION_WAITS.split("{question}")[0] not in opening
    assert "waiting for the user to answer" not in decisions.side_calls[0][1]["content"]
    release.set()


async def test_what_a_copy_finished_while_the_session_waits_for_its_user_is_told_once_it_goes_on(hosts, tmp_path):
    scripts = {
        EXPORT: ScriptedLLM(
            [
                [call("report", {"status": "waiting", "summary": "Which columns?"}, "call-w"), finish("tool_calls")],
                [call("glob", {"pattern": "*.py"}, "call-x"), finish("tool_calls")],
                _report("export added", "call-y"),
            ]
        ),
        FILES: ScriptedLLM([_report("tables.py")]),
    }
    host = _host(hosts, tmp_path, scripts)
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, EXPORT)
    await wait_status(host, first.id, "waiting_user")
    second = await _filed(host, FILES)
    done = await wait_status(host, second.id, "done")
    await _decided(host)
    # Held while it waits: nothing is sent to it, and it is not told yet.
    assert len(scripts[EXPORT].requests) == 1
    assert "told_to_session" not in await _types(host, second.id)
    assert await host.task_agents.answer(first.id, "all of them")
    await wait_status(host, first.id, "done")
    said = [text for request in scripts[EXPORT].requests[1:] for text in _said(request)]
    news = [text for text in said if brain_texts.TASK_AGENT_FORKED_WORK.split("{items}")[0] in text]
    assert len(news) == 1 and f'The user asked "{_words(done)}" (answered' in news[0]
    # On record once the session is seen to have taken it, which may be said after its run ended.
    await wait_until(lambda: _has_type(host, second.id, "told_to_session"), what="told on record")


async def test_a_session_at_work_is_told_that_a_copy_handed_a_commission_back_to_it(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM(
            [[wait, call("glob", {"pattern": "*.py"}, "call-x"), finish("tool_calls")], _report("export added", "call-y")]
        ),
        RENAME: ScriptedLLM(
            [
                [call("write", {"file_path": "chart.py", "content": "x"}, "call-1"), finish("tool_calls")],
                [say("It is in chart.py."), finish()],
                [call("write", {"file_path": "chart.py", "content": "renamed"}, "call-2"), finish("tool_calls")],
                _report("renamed", "call-3"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    second = await _dispatch(host, RENAME)
    await wait_until(lambda: _handed_back(host, second.id), what="the hand-back")
    copy_id = next(
        event.payload["session_id"] for event in await host.ledger.events(second.id) if event.type == "carried_on_in_copy"
    )
    await wait_until(
        lambda: host.task_agents._sessions[first.id].sent > 1, what="the session itself told of the hand-back"
    )
    release.set()
    await wait_status(host, second.id, "done")
    handed = brain_texts.TASK_AGENT_FORKED_WORK_HANDED_ITEM.format(
        asked=_words(second), session=copy_id[:8]
    )
    assert brain_texts.TASK_AGENT_FORKED_WORK.format(items=handed) in "\n".join(_said(scripts[EXPORT].requests[1]))


@pytest.mark.parametrize(("report", "outcome"), [("blocked", "stuck"), ("failed", "failed")])
async def test_a_commission_stuck_in_its_copy_is_told_of_and_taken_up_in_the_session_itself(hosts, tmp_path, report, outcome):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM(
            [[wait, call("glob", {"pattern": "*.py"}, "call-x"), finish("tool_calls")], _report("export added", "call-y")]
        ),
        FILES: ScriptedLLM(
            [
                [call("report", {"status": report, "summary": "tables.py is not there"}, "call-b"), finish("tool_calls")],
                _report("found it in data/tables.py", "call-r"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    second = await _dispatch(host, FILES)
    stuck = await wait_status(host, second.id, report)
    copy_id = stuck.session_id
    assert (await host.ledger.get_session(copy_id)).kind == "fork"
    await wait_until(lambda: host.task_agents._sessions[first.id].sent > 1, what="the session itself told of it")
    release.set()
    await wait_status(host, first.id, "done")
    # Told of whatever came of it.
    item = brain_texts.TASK_AGENT_FORKED_WORK_ITEM.format(
        asked=_words(stuck), outcome=outcome, session=copy_id[:8], result="tables.py is not there"
    )
    assert item in "\n".join(_said(scripts[EXPORT].requests[1]))
    assert await host.task_agents.tell(second.id, "look under data/")
    done = await wait_status(host, second.id, "done")
    # Not gone on in the copy: in the session itself, told what came of it there.
    assert done.session_id == first.session_id
    [back] = [event.payload for event in await host.ledger.events(second.id) if event.type == "back_from_copy"]
    assert back == {"copy": copy_id, "to": first.session_id}
    said = "\n".join(_said(scripts[FILES].requests[1]))
    head = brain_texts.TASK_AGENT_TAKEN_UP_FROM_COPY.split("{nonce}")[0].format(status=report, session=copy_id[:8])
    assert head in said and "\n    tables.py is not there\n" in said
    assert "look under data/" in said


async def test_a_commission_handed_back_to_a_session_with_nothing_to_do_is_not_told_of_itself(hosts, tmp_path):
    release, wait = _gate()
    copy_release, copy_wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        RENAME: ScriptedLLM(
            [
                [copy_wait, call("write", {"file_path": "chart.py", "content": "x"}, "call-1"), finish("tool_calls")],
                [say("It is in chart.py."), finish()],
                [call("glob", {"pattern": "*.py"}, "call-g"), finish("tool_calls")],
                _report("renamed", "call-3"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    second = await _dispatch(host, RENAME)
    await wait_until(lambda: scripts[RENAME].requests, what="the copy at work")
    release.set()
    await wait_status(host, first.id, "done")
    copy_release.set()
    done = await wait_status(host, second.id, "done")
    assert done.session_id == first.session_id
    # Opened with what the copy found, and not told besides that it waits.
    said = "\n".join(text for request in scripts[RENAME].requests[2:] for text in _said(request))
    assert brain_texts.TASK_AGENT_FORK_HANDED_BACK.split("{tool}")[0] in said
    assert brain_texts.TASK_AGENT_FORKED_WORK.split("{items}")[0] not in said
    assert not host.task_agents._handed_back_news and not host.task_agents._copy_news


def test_what_copies_did_is_said_within_the_limit_with_the_user_words_cut():
    from nekopower.host.task_agents import _forked_work_text
    from utils.tokenize import count_tokens

    class Item:
        def __init__(self, number: int) -> None:
            self.id = f"c{number}"
            self.user_text = "where is the chart " * 200
            self.dispatch_text = self.title = "where is the chart"
            self.status = "done"
            self.session_id = f"copy{number:04d}xxxx"
            self.result_summary = "in chart.py " * 200
            self.status_reason = ""

    said = _forked_work_text([Item(1), Item(2)], [(Item(3), "copy0003xxxx")])
    assert count_tokens(said) <= settings.TASK_AGENT_FORKED_WORK_MAX_TOKENS
    lines = said.splitlines()[-3:]
    # However long what was asked and found, each says how it ended, where, and something of what came of it.
    for number in (1, 2):
        assert f'(answered, in session copy{number:04d}): in chart.py' in lines[number - 1]
    assert '(it needs a change, in session copy0003): handed back' in lines[2]
    assert _forked_work_text([], []) == ""


@pytest.mark.parametrize("judge", ["fails", "none"])
async def test_a_commission_waits_for_its_session_where_nothing_tells_it_is_a_side_question(hosts, tmp_path, monkeypatch, judge):
    release, wait = _gate()
    scripts = {EXPORT: ScriptedLLM([[wait, *_report("export added")]]), FILES: ScriptedLLM([_report("tables.py")])}
    host = _host(hosts, tmp_path, scripts)

    async def failing(dispatch_text: str, user_text: str, waiting_for: str = "") -> bool | None:
        raise RuntimeError("down")

    host.task_agents.set_side_question_judge(failing if judge == "fails" else None)
    copied = _copies_made(host, monkeypatch)
    first = await _export_at_work(host, scripts)
    second = await _dispatch(host, FILES)
    await _decided(host)
    assert copied == []
    assert (await host.ledger.get(second.id)).status == "queued"
    release.set()
    assert (await wait_status(host, second.id, "done")).session_id == first.session_id


async def test_what_a_copy_finished_while_the_session_asks_with_the_question_tool_is_told_once_it_goes_on(hosts, tmp_path):
    question = {"questions": [{"id": "cols", "question": "Which columns?", "options": [{"label": "A"}, {"label": "B"}]}]}
    scripts = {
        EXPORT: ScriptedLLM(
            [
                [call("ask_user_question", question, "call-q"), finish("tool_calls")],
                [call("glob", {"pattern": "*.py"}, "call-x"), finish("tool_calls")],
                _report("export added", "call-y"),
            ]
        ),
        FILES: ScriptedLLM([_report("tables.py")]),
    }
    host = _host(hosts, tmp_path, scripts)
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, EXPORT)
    await wait_status(host, first.id, "waiting_user")
    second = await _filed(host, FILES)
    done = await wait_status(host, second.id, "done")
    await _decided(host)
    # Its run waits in the middle of a tool call: nothing is said to it meanwhile.
    assert "told_of_copies" not in await _types(host, first.id)
    assert await host.task_agents.answer(first.id, "A")
    await wait_status(host, first.id, "done")
    said = [text for request in scripts[EXPORT].requests[1:] for text in _said(request)]
    news = [text for text in said if brain_texts.TASK_AGENT_FORKED_WORK.split("{items}")[0] in text]
    assert len(news) == 1 and f'The user asked "{_words(done)}" (answered' in news[0]


async def test_a_session_whose_run_is_over_while_it_waits_for_its_jobs_is_not_woken_to_be_told(hosts, tmp_path):
    release, wait = _gate()
    copy_release, copy_wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM(
            [[wait, call("glob", {"pattern": "*.py"}, "call-x"), finish("tool_calls")], _report("export added", "call-y")]
        ),
        FILES: ScriptedLLM([[copy_wait, *_report("tables.py")]]),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    second = await _dispatch(host, FILES)
    await wait_until(lambda: scripts[FILES].requests, what="the copy at work")
    # As it stands when its run ended with jobs of its own still running, whose end starts the next.
    host.task_agents._sessions[first.id].idle = True
    copy_release.set()
    done = await wait_status(host, second.id, "done")
    await _decided(host)
    assert "told_of_copies" not in await _types(host, first.id)
    assert host.task_agents._copy_news
    # Told once it is at work again.
    release.set()
    await wait_status(host, first.id, "done")
    assert f'The user asked "{_words(done)}" (answered' in "\n".join(_said(scripts[EXPORT].requests[1]))


async def test_a_copy_finished_while_another_is_put_on_record_after_the_session_came_free_is_not_taken(
    hosts, tmp_path, monkeypatch
):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        FILES: ScriptedLLM([_report("tables.py")]),
        "list the reports": ScriptedLLM([_report("reports")]),
    }
    host = _host(hosts, tmp_path, scripts)
    manager, ledger = host.task_agents, host.ledger
    first = await _export_at_work(host, scripts)
    main = first.session_id
    append = ledger.append_event
    writing, written = asyncio.Event(), asyncio.Event()
    held: list[str] = []

    async def slow(commission_id: str, kind: str, payload: Any = None) -> Any:
        if kind == "carried_on_in_copy" and not held:
            held.append(commission_id)
            writing.set()
            await written.wait()
        return await append(commission_id, kind, payload)

    monkeypatch.setattr(ledger, "append_event", slow)
    fork_of = manager.fork_session
    copying, copied = asyncio.Event(), asyncio.Event()
    forks: list[Any] = []

    async def gated(*args: Any, **kwargs: Any) -> Any:
        forks.append(args)
        if len(forks) == 2:
            copying.set()
            await copied.wait()
        return await fork_of(*args, **kwargs)

    monkeypatch.setattr(manager, "fork_session", gated)
    earlier = await _dispatch(host, FILES)
    await asyncio.wait_for(writing.wait(), 10)
    later = await _dispatch(host, "list the reports")
    await asyncio.wait_for(copying.wait(), 10)
    # The session comes free while the earlier one is being put on record with its copy.
    release.set()
    await wait_status(host, first.id, "done")
    await wait_until(lambda: manager._held_by.get(main) is None, what="the session let go")
    # The later copy is made now: the session is free, so it is not taken.
    copied.set()
    await wait_until(lambda: later.id not in manager._copying_for.get(main, set()), what="the later copy looked at")
    written.set()
    assert (await wait_status(host, earlier.id, "done")).session_id != main
    assert (await wait_status(host, later.id, "done")).session_id == main
    assert "carried_on_in_copy" not in await _types(host, later.id)


async def test_a_session_cleared_while_it_is_copied_is_not_carried_on_in(hosts, tmp_path, monkeypatch):
    release, wait = _gate()
    scripts = {EXPORT: ScriptedLLM([[wait, *_report("export added")]]), FILES: ScriptedLLM([_report("tables.py")])}
    host = _host(hosts, tmp_path, scripts)
    manager = host.task_agents
    first = await _export_at_work(host, scripts)
    fork_of = manager.fork_session

    async def clearing(*args: Any, **kwargs: Any) -> Any:
        made = await fork_of(*args, **kwargs)
        await host.ledger.update_session(first.session_id, status="cleared")
        return made

    monkeypatch.setattr(manager, "fork_session", clearing)
    second = await _dispatch(host, FILES)
    await _decided(host)
    assert (await host.ledger.get(second.id)).status == "queued"
    assert "carried_on_in_copy" not in await _types(host, second.id)
    release.set()
    await wait_status(host, second.id, "done", "failed", "blocked")


async def test_a_copy_that_cannot_be_put_on_record_goes_back_behind_one_handed_back_before_it(hosts, tmp_path, monkeypatch):
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
        FILES: ScriptedLLM([_report("tables.py")]),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    handed = await _dispatch(host, RENAME)
    await wait_until(lambda: _handed_back(host, handed.id), what="the hand-back")
    ledger = host.ledger
    append = ledger.append_event

    refused: list[str] = []

    async def failing(commission_id: str, kind: str, payload: Any = None) -> Any:
        if kind == "carried_on_in_copy":
            refused.append(commission_id)
            raise RuntimeError("the ledger is busy")
        return await append(commission_id, kind, payload)

    monkeypatch.setattr(ledger, "append_event", failing)
    later = await _dispatch(host, FILES)
    await wait_until(lambda: later.id in host.task_agents._queue, what="the later one back in the queue")
    await _decided(host)
    # It was copied, and came back for its record could not be written.
    assert refused == [later.id]
    # Handed over first, the one handed back goes first.
    assert list(host.task_agents._queue) == [handed.id, later.id]
    release.set()
    assert (await wait_status(host, handed.id, "done")).session_id == first.session_id
    await wait_status(host, later.id, "done")


async def test_what_copies_did_is_told_again_where_the_session_never_took_the_text_that_told_it(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM(
            [[wait, call("glob", {"pattern": "*.py"}, "call-x"), finish("tool_calls")], _report("export added", "call-y")]
        ),
        FILES: ScriptedLLM([_report("tables.py")]),
        "add a chart": ScriptedLLM([_report("chart added")]),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    second = await _dispatch(host, FILES)
    done = await wait_status(host, second.id, "done")
    await wait_until(lambda: host.task_agents._sessions[first.id].sent > 1, what="the session itself told")
    # Sent, not yet taken: not on record as told.
    assert "told_to_session" not in await _types(host, second.id)
    # Stopped before its next step: what it was told never got into it.
    await host.brain.abort(first.id, notify=False)
    release.set()
    await wait_until(lambda: first.id not in host.task_agents._sessions, what="the first let go")
    assert "told_to_session" not in await _types(host, second.id)
    assert not host.task_agents._copies_on_the_way
    third = await _dispatch(host, "add a chart")
    await wait_status(host, third.id, "done")
    assert f'The user asked "{_words(done)}" (answered' in _said(scripts["add a chart"].requests[0])[-1]
    # On record once the session is seen to have taken it, which may be said after its run ended.
    await wait_until(lambda: _has_type(host, second.id, "told_to_session"), what="told on record")


async def test_no_more_is_told_in_one_text_than_the_most_there_may_be(hosts, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TASK_AGENT_FORKED_WORK_MAX_ITEMS", 1)
    scripts = {
        EXPORT: ScriptedLLM(
            [
                [call("report", {"status": "waiting", "summary": "Which columns?"}, "call-w"), finish("tool_calls")],
                [call("glob", {"pattern": "*.py"}, "call-x"), finish("tool_calls")],
                [call("glob", {"pattern": "*.md"}, "call-z"), finish("tool_calls")],
                _report("export added", "call-y"),
            ]
        ),
        FILES: ScriptedLLM([_report("tables.py")]),
        RENAME: ScriptedLLM(
            [
                [call("write", {"file_path": "chart.py", "content": "x"}, "call-1"), finish("tool_calls")],
                [say("It is in chart.py."), finish()],
                _report("renamed", "call-2"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    sent = _news_sent(host, monkeypatch)
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, EXPORT)
    await wait_status(host, first.id, "waiting_user")
    answered = await wait_status(host, (await _filed(host, FILES)).id, "done")
    handed = await _filed(host, RENAME)
    await wait_until(lambda: _handed_back(host, handed.id), what="the hand-back")
    await _decided(host)
    # Both held while it waits; told once it goes on, one in each text.
    assert await host.task_agents.answer(first.id, "all of them")
    await wait_status(host, handed.id, "done")
    said = [text for request in scripts[EXPORT].requests[1:] for text in _said(request)]
    head = brain_texts.TASK_AGENT_FORKED_WORK.split("{items}")[0]
    news = list(dict.fromkeys(text[text.index(head):] for text in said if head in text))
    assert len(news) == 2
    assert all(text.count("\n- The user asked") == 1 for text in news)
    assert f'"{_words(answered)}" (answered' in news[0] and f'"{_words(handed)}" (it needs a change' in news[1]
    # Sent so, each once.
    assert len(sent) == 2 and [text.count("\n- The user asked") for text in sent] == [1, 1]


async def test_what_came_of_it_in_the_copy_is_told_when_its_first_taking_up_was_called_off(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        FILES: ScriptedLLM(
            [
                [call("report", {"status": "blocked", "summary": "tables.py is not there"}, "call-b"), finish("tool_calls")],
                _report("found it in data/tables.py", "call-r"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    second = await _dispatch(host, FILES)
    stuck = await wait_status(host, second.id, "blocked")
    release.set()
    await wait_status(host, first.id, "done")
    # Everything stopped while it was moved to the session itself.
    asked: list[bool] = []

    def wanted() -> bool:
        asked.append(True)
        return len(asked) < 2

    assert not await host.task_agents.tell(second.id, "look under data/", still_wanted=wanted)
    assert (await host.ledger.get(second.id)).session_id == first.session_id
    assert await host.task_agents.tell(second.id, "look under data/")
    done = await wait_status(host, second.id, "done")
    assert done.session_id == first.session_id
    said = "\n".join(_said(scripts[FILES].requests[1]))
    head = brain_texts.TASK_AGENT_TAKEN_UP_FROM_COPY.split("{nonce}")[0].format(status="blocked", session=stuck.session_id[:8])
    assert head in said and "\n    tables.py is not there\n" in said


async def test_what_came_of_it_in_the_copy_is_told_while_the_old_copy_is_still_closing(hosts, tmp_path, monkeypatch):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        FILES: ScriptedLLM(
            [
                [call("report", {"status": "blocked", "summary": "tables.py is not there"}, "call-b"), finish("tool_calls")],
                _report("found it in data/tables.py", "call-r"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    manager = host.task_agents
    close_session = manager._close_session
    close_copy = asyncio.Event()
    copy_deferred: list[Any] = []

    async def hold_copy_close(session: Any) -> None:
        if session.carries_on_for is not None and not copy_deferred:
            copy_deferred.append(session)

            async def later() -> None:
                await close_copy.wait()
                await close_session(session)

            manager._spawn(later())
            return
        await close_session(session)

    monkeypatch.setattr(manager, "_close_session", hold_copy_close)
    try:
        first = await _export_at_work(host, scripts)
        second = await _dispatch(host, FILES)
        stuck = await wait_status(host, second.id, "blocked")
        await wait_until(lambda: bool(copy_deferred), what="the copy session reaching close")
        release.set()
        await wait_status(host, first.id, "done")

        asked: list[bool] = []

        def wanted() -> bool:
            asked.append(True)
            return len(asked) < 2

        # First taking up moves the commission to the main session while its old copy is still in _sessions.
        assert not await manager.tell(second.id, "look under data/", still_wanted=wanted)
        assert (await host.ledger.get(second.id)).session_id == first.session_id
        assert second.id in manager._sessions and manager._sessions[second.id].session_id == stuck.session_id

        # Second taking up queues behind the still-closing copy and still carries what came of it in the copy.
        assert await manager.tell(second.id, "look under data/")
        close_copy.set()
        done = await wait_status(host, second.id, "done")
        assert done.session_id == first.session_id
        said = "\n".join(_said(scripts[FILES].requests[1]))
        head = brain_texts.TASK_AGENT_TAKEN_UP_FROM_COPY.split("{nonce}")[0].format(
            status="blocked", session=stuck.session_id[:8]
        )
        assert head in said and "\n    tables.py is not there\n" in said
        await wait_until(lambda: not manager._held_by, what="all session holds settled")
        assert (await host.ledger.get_session(stuck.session_id)).status == "closed"
    finally:
        close_copy.set()


async def test_what_came_of_it_in_the_copy_survives_the_old_copy_releasing_during_take_up(hosts, tmp_path, monkeypatch):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        FILES: ScriptedLLM(
            [
                [call("report", {"status": "blocked", "summary": "tables.py is not there"}, "call-b"), finish("tool_calls")],
                _report("found it in data/tables.py", "call-r"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    manager = host.task_agents
    close_session = manager._close_session
    know_session = manager._know_session
    close_copy = asyncio.Event()
    copy_deferred: list[Any] = []

    async def hold_copy_close(session: Any) -> None:
        if session.carries_on_for is not None and not copy_deferred:
            copy_deferred.append(session)

            async def later() -> None:
                await close_copy.wait()
                await close_session(session)

            manager._spawn(later())
            return
        await close_session(session)

    async def release_copy_during_know_session(commission_id: str) -> bool:
        if copy_deferred and commission_id == copy_deferred[0].commission_id and not close_copy.is_set():
            close_copy.set()
            await wait_until(lambda: commission_id not in manager._sessions, what="the old copy session releasing")
        return await know_session(commission_id)

    monkeypatch.setattr(manager, "_close_session", hold_copy_close)
    monkeypatch.setattr(manager, "_know_session", release_copy_during_know_session)
    try:
        first = await _export_at_work(host, scripts)
        second = await _dispatch(host, FILES)
        stuck = await wait_status(host, second.id, "blocked")
        await wait_until(lambda: bool(copy_deferred), what="the copy session reaching close")
        release.set()
        await wait_status(host, first.id, "done")

        # Even if the old copy releases while _take_up_again is awaiting _know_session,
        # from_copy is not dropped from _notes before the new round claims it.
        assert await manager.tell(second.id, "look under data/")
        done = await wait_status(host, second.id, "done")
        assert done.session_id == first.session_id
        said = "\n".join(_said(scripts[FILES].requests[1]))
        head = brain_texts.TASK_AGENT_TAKEN_UP_FROM_COPY.split("{nonce}")[0].format(
            status="blocked", session=stuck.session_id[:8]
        )
        assert head in said and "\n    tables.py is not there\n" in said
        await wait_until(lambda: not manager._held_by, what="all session holds settled")
        assert (await host.ledger.get_session(stuck.session_id)).status == "closed"
    finally:
        close_copy.set()


async def test_what_copies_did_beyond_the_most_one_text_tells_is_told_in_the_next(hosts, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TASK_AGENT_FORKED_WORK_MAX_ITEMS", 1)
    scripts = {
        EXPORT: ScriptedLLM(
            [
                [call("report", {"status": "waiting", "summary": "Which columns?"}, "call-w"), finish("tool_calls")],
                [call("glob", {"pattern": "*.py"}, "call-x"), finish("tool_calls")],
                [call("glob", {"pattern": "*.md"}, "call-z"), finish("tool_calls")],
                _report("export added", "call-y"),
            ]
        ),
        FILES: ScriptedLLM([_report("tables.py")]),
        "list the reports": ScriptedLLM([_report("reports.py")]),
    }
    host = _host(hosts, tmp_path, scripts)
    sent = _news_sent(host, monkeypatch)
    await host.space.touch_project("Budget app")
    first = await _dispatch(host, EXPORT)
    await wait_status(host, first.id, "waiting_user")
    one = await wait_status(host, (await _filed(host, FILES)).id, "done")
    two = await wait_status(host, (await _filed(host, "list the reports")).id, "done")
    await _decided(host)
    assert await host.task_agents.answer(first.id, "all of them")
    await wait_status(host, first.id, "done")
    said = [text for request in scripts[EXPORT].requests[1:] for text in _said(request)]
    head = brain_texts.TASK_AGENT_FORKED_WORK.split("{items}")[0]
    news = list(dict.fromkeys(text[text.index(head):] for text in said if head in text))
    assert len(news) == 2 and all(text.count("\n- The user asked") == 1 for text in news)
    assert {_words(one), _words(two)} == {text.split('"')[1] for text in news}
    # Each sent once, in a text of its own.
    assert len(sent) == 2 and {text.split('"')[1] for text in sent} == {_words(one), _words(two)}
    for item in (one, two):
        await wait_until(lambda item=item: _has_type(host, item.id, "told_to_session"), what="told on record")


async def test_what_copies_did_is_kept_where_the_ledger_cannot_be_read_and_told_later(hosts, tmp_path, monkeypatch):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM(
            [
                [wait, call("glob", {"pattern": "*.py"}, "call-x"), finish("tool_calls")],
                [call("glob", {"pattern": "*.md"}, "call-z"), finish("tool_calls")],
                _report("export added", "call-y"),
            ]
        ),
        FILES: ScriptedLLM([_report("tables.py")]),
    }
    host = _host(hosts, tmp_path, scripts)
    ledger = host.ledger
    forked_work = ledger.forked_work
    failed: list[bool] = []

    async def failing_once(*args: Any, **kwargs: Any) -> Any:
        if not failed:
            failed.append(True)
            raise RuntimeError("the ledger is busy")
        return await forked_work(*args, **kwargs)

    first = await _export_at_work(host, scripts)
    monkeypatch.setattr(ledger, "forked_work", failing_once)
    done = await wait_status(host, (await _dispatch(host, FILES)).id, "done")
    await _decided(host)
    assert failed and host.task_agents._copy_news
    assert "told_of_copies" not in await _types(host, first.id)
    release.set()
    await wait_status(host, first.id, "done")
    said = "\n".join(text for request in scripts[EXPORT].requests[1:] for text in _said(request))
    assert f'The user asked "{_words(done)}" (answered' in said


def test_a_text_withdrawn_lets_what_copies_did_it_told_of_go_and_the_later_ones_move_up():
    from nekopower.host.task_agents import _Session, _withdraw

    session = _Session(commission_id="c")
    session.sent = 3
    session.numbered = {10: 1, 11: 2, 12: 3}
    session.copies_told = {2: ["a"], 3: ["b"]}
    _withdraw(session, 11)
    assert session.copies_dropped == ["a"]
    assert session.copies_told == {2: ["b"]}


async def test_what_the_user_answered_in_the_copy_goes_with_one_stuck_there_to_the_session_itself(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        FILES: ScriptedLLM(
            [
                [call("report", {"status": "waiting", "summary": "Which columns?"}, "call-w"), finish("tool_calls")],
                [call("report", {"status": "blocked", "summary": "tables.py is not there"}, "call-b"), finish("tool_calls")],
                _report("found it in data/tables.py", "call-r"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    second = await _dispatch(host, FILES)
    await wait_status(host, second.id, "waiting_user")
    assert await host.task_agents.answer(second.id, "only the tax columns")
    await wait_status(host, second.id, "blocked")
    release.set()
    await wait_status(host, first.id, "done")
    assert await host.task_agents.tell(second.id, "look under data/")
    done = await wait_status(host, second.id, "done")
    assert done.session_id == first.session_id
    said = "\n".join(_said(scripts[FILES].requests[2]))
    head = brain_texts.TASK_AGENT_TAKEN_UP_FROM_COPY.split("{session}")[0]
    # With what takes it up: what came of it there, then what the user answered there.
    assert said.index("look under data/") < said.index(head) < said.index("only the tax columns")


@pytest.mark.parametrize("when", ["judged", "copied", "recorded", "recorded_unnoted"])
async def test_more_said_to_it_while_it_is_told_to_be_a_side_question_keeps_it_waiting_for_its_session(
    hosts, tmp_path, monkeypatch, when
):
    release, wait = _gate()
    scripts = {EXPORT: ScriptedLLM([[wait, *_report("export added")]]), FILES: ScriptedLLM([_report("tables.py, renamed")])}
    host = _host(hosts, tmp_path, scripts)
    manager = host.task_agents
    asking, answer = asyncio.Event(), asyncio.Event()

    async def slow(dispatch_text: str, user_text: str, waiting_for: str = "") -> bool | None:
        if when == "judged":
            asking.set()
            await answer.wait()
        return True

    manager.set_side_question_judge(slow)
    fork_of = manager.fork_session
    copies: list[Any] = []

    async def slow_copy(*args: Any, **kwargs: Any) -> Any:
        if when == "copied":
            asking.set()
            await answer.wait()
        copies.append(await fork_of(*args, **kwargs))
        return copies[-1]

    monkeypatch.setattr(manager, "fork_session", slow_copy)
    append = host.ledger.append_event

    async def slow_record(commission_id: str, kind: str, payload: Any = None) -> Any:
        if kind == "carried_on_in_copy" and when.startswith("recorded"):
            asking.set()
            await answer.wait()
        if kind == "not_carried_on" and when == "recorded_unnoted":
            # Taken back on record, and the note of it cannot be written.
            raise RuntimeError("the ledger is busy")
        return await append(commission_id, kind, payload)

    monkeypatch.setattr(host.ledger, "append_event", slow_record)
    first = await _export_at_work(host, scripts)
    second = await _dispatch(host, FILES)
    await asyncio.wait_for(asking.wait(), 10)
    # Said meanwhile: it goes along with it, and is work.
    assert await manager.tell(second.id, "and rename the table")
    answer.set()
    await _decided(host)
    if when == "recorded":
        # On record with its copy a moment, and taken back.
        await wait_until(lambda: _has_type(host, second.id, "not_carried_on"), what="taken back")
    if when.startswith("recorded"):
        assert (await host.ledger.get(second.id)).session_id == first.session_id
        # Waits for the session itself, as the ledger has it.
        await wait_until(lambda: second.id in manager._queue, what="queued again")
        assert manager._session_of.get(second.id) == first.session_id
    else:
        assert "carried_on_in_copy" not in await _types(host, second.id)
    if when == "judged":
        # Not copied at all: that is looked at before a copy is made.
        assert copies == []
    assert (await host.ledger.get(second.id)).status == "queued"
    release.set()
    done = await wait_status(host, second.id, "done")
    assert done.session_id == first.session_id
    assert "and rename the table" in "\n".join(_said(scripts[FILES].requests[0]))


async def test_what_copies_did_is_told_again_where_steering_it_in_fails(hosts, tmp_path, monkeypatch):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM(
            [
                [wait, call("glob", {"pattern": "*.py"}, "call-x"), finish("tool_calls")],
                [call("glob", {"pattern": "*.md"}, "call-z"), finish("tool_calls")],
                _report("export added", "call-y"),
            ]
        ),
        FILES: ScriptedLLM([_report("tables.py")]),
    }
    host = _host(hosts, tmp_path, scripts)
    manager = host.task_agents
    send = manager._send_text
    failed: list[bool] = []

    async def failing_once(runtime: Any, session: Any, command: str, text: str, **kwargs: Any) -> Any:
        if command == "steer" and brain_texts.TASK_AGENT_FORKED_WORK.split("{items}")[0] in text and not failed:
            failed.append(True)
            raise RuntimeError("the runtime is gone")
        return await send(runtime, session, command, text, **kwargs)

    monkeypatch.setattr(manager, "_send_text", failing_once)
    first = await _export_at_work(host, scripts)
    done = await wait_status(host, (await _dispatch(host, FILES)).id, "done")
    await wait_until(lambda: failed, what="the failed steer")
    await _decided(host)
    # Kept, to be told when it is next seen at work.
    assert not manager._copies_on_the_way and first.session_id in manager._copy_news
    release.set()
    await wait_status(host, first.id, "done")
    said = "\n".join(text for request in scripts[EXPORT].requests[1:] for text in _said(request))
    assert f'The user asked "{_words(done)}" (answered' in said


async def test_whether_it_is_a_side_question_is_not_asked_of_one_that_would_not_be_carried_on_by_then(hosts, tmp_path):
    decisions = FakeDecisionLLM()
    scripts = {EXPORT: ScriptedLLM([_report("export added")])}
    host = _host(hosts, tmp_path, scripts, decision_llm=decisions)
    commission, _created = await host.ledger.create(
        lanlan_name="Lan", title="q", dispatch_text="where is it", user_text="where is it", source="dispatch", lang="en"
    )
    assert not await host.task_agents._is_side_question(commission.id, lambda: False)
    assert decisions.side_calls == []
    assert await host.task_agents._is_side_question(commission.id, lambda: True)
    assert len(decisions.side_calls) == 1


def test_a_text_withdrawn_lets_what_was_handed_back_it_told_of_go_too():
    from nekopower.host.task_agents import _Session, _withdraw

    session = _Session(commission_id="c")
    session.sent = 2
    session.numbered = {10: 1, 11: 2}
    session.handed_told = {1: [("h", "copy-1")], 2: [("k", "copy-2")]}
    _withdraw(session, 10)
    assert session.handed_dropped == [("h", "copy-1")]
    assert session.handed_told == {1: [("k", "copy-2")]}


async def test_what_was_handed_back_told_of_in_a_text_the_session_did_not_take_is_told_again(hosts, tmp_path):
    from nekopower.host.task_agents import _Session

    host = _host(hosts, tmp_path, {})
    manager = host.task_agents
    session = _Session(commission_id="c", session_id="main")
    session.copies_dropped = ["done-1"]
    session.handed_dropped = [("handed-1", "copy-1")]
    manager._copies_on_the_way.add("done-1")
    manager._copies_dropped(session)
    assert manager._handed_back_news["main"] == [("handed-1", "copy-1")]
    assert "done-1" not in manager._copies_on_the_way
    assert session.copies_dropped == [] and session.handed_dropped == []


async def test_what_was_said_to_a_taking_up_that_was_called_off_is_not_told_as_said_in_the_copy(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        FILES: ScriptedLLM(
            [
                [call("report", {"status": "blocked", "summary": "tables.py is not there"}, "call-b"), finish("tool_calls")],
                _report("found it in data/tables.py", "call-r"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    first = await _export_at_work(host, scripts)
    second = await _dispatch(host, FILES)
    await wait_status(host, second.id, "blocked")
    release.set()
    await wait_status(host, first.id, "done")
    asked: list[bool] = []

    def wanted() -> bool:
        # Wanted while it is moved and said, and not once it is to be taken up.
        asked.append(True)
        return len(asked) < 3

    assert not await host.task_agents.tell(second.id, "try the docs folder", still_wanted=wanted)
    assert await host.task_agents.tell(second.id, "look under data/")
    await wait_status(host, second.id, "done")
    said = "\n".join(_said(scripts[FILES].requests[1]))
    assert "look under data/" in said and "tables.py is not there" in said
    assert "try the docs folder" not in said


async def test_what_came_of_it_in_the_copy_is_told_once_however_much_is_said_while_it_is_taken_up(hosts, tmp_path, monkeypatch):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        FILES: ScriptedLLM(
            [
                [call("report", {"status": "blocked", "summary": "tables.py is not there"}, "call-b"), finish("tool_calls")],
                _report("found it in data/tables.py", "call-r"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    manager = host.task_agents
    first = await _export_at_work(host, scripts)
    second = await _dispatch(host, FILES)
    await wait_status(host, second.id, "blocked")
    release.set()
    await wait_status(host, first.id, "done")
    # Its taking up in the session itself waits a moment while it is made ready.
    choose = manager._choose_runtime
    choosing, chosen = asyncio.Event(), asyncio.Event()

    async def slow(*args: Any, **kwargs: Any) -> Any:
        choosing.set()
        await chosen.wait()
        return await choose(*args, **kwargs)

    monkeypatch.setattr(manager, "_choose_runtime", slow)
    first_said = asyncio.ensure_future(manager.tell(second.id, "look under data/"))
    await asyncio.wait_for(choosing.wait(), 10)
    # Said while that is under way: it goes along with it, once that is ready.
    more = asyncio.ensure_future(manager.tell(second.id, "and in docs/ as well"))

    async def said_more() -> bool:
        return any(
            event.type == "told" and event.payload.get("text") == "and in docs/ as well"
            for event in await host.ledger.events(second.id)
        )

    await wait_until(said_more, what="the second sentence on record")
    chosen.set()
    assert await first_said and await more
    await wait_status(host, second.id, "done")
    said = "\n".join(text for request in scripts[FILES].requests[1:] for text in dict.fromkeys(_said(request)))
    head = brain_texts.TASK_AGENT_TAKEN_UP_FROM_COPY.split("{session}")[0]
    assert said.count(head) == 1
    assert "look under data/" in said and "and in docs/ as well" in said


async def test_what_came_of_it_in_the_copy_goes_with_what_is_said_after_its_taking_up_was_called_off(hosts, tmp_path, monkeypatch):
    release, wait = _gate()
    scripts = {
        EXPORT: ScriptedLLM([[wait, *_report("export added")]]),
        FILES: ScriptedLLM(
            [
                [call("report", {"status": "blocked", "summary": "tables.py is not there"}, "call-b"), finish("tool_calls")],
                _report("found it in data/tables.py", "call-r"),
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    manager = host.task_agents
    first = await _export_at_work(host, scripts)
    second = await _dispatch(host, FILES)
    stuck = await wait_status(host, second.id, "blocked")
    release.set()
    await wait_status(host, first.id, "done")
    choose = manager._choose_runtime
    choosing, chosen = asyncio.Event(), asyncio.Event()

    async def slow(*args: Any, **kwargs: Any) -> Any:
        choosing.set()
        await chosen.wait()
        return await choose(*args, **kwargs)

    monkeypatch.setattr(manager, "_choose_runtime", slow)
    wanted = [True]
    first_said = asyncio.ensure_future(manager.tell(second.id, "try the docs folder", still_wanted=lambda: wanted[0]))
    await asyncio.wait_for(choosing.wait(), 10)
    # Everything stopped while it is made ready; then the user says more.
    wanted[0] = False
    more = asyncio.ensure_future(manager.tell(second.id, "look under data/"))
    await asyncio.sleep(0.1)
    monkeypatch.setattr(manager, "_choose_runtime", choose)
    chosen.set()
    # (What the first says of itself depends on how far it got before it looked.)
    await first_said
    assert await more
    done = await wait_status(host, second.id, "done")
    assert done.session_id == first.session_id
    said = "\n".join(_said(scripts[FILES].requests[1]))
    head = brain_texts.TASK_AGENT_TAKEN_UP_FROM_COPY.split("{nonce}")[0].format(status="blocked", session=stuck.session_id[:8])
    assert head in said and "\n    tables.py is not there\n" in said
    assert "look under data/" in said

