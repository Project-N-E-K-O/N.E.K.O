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

"""The task agent space as task agents meet it: the real runtime in process, a scripted model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from config.prompts.prompts_nekoclaw import REPLY_LANGUAGE_SENTENCE
from nekoclaw.host.paths import DEFAULT_PROJECT
from tests.unit.nekoclaw.fakes import ScriptedLLM, call, finish
from tests.unit.nekoclaw.host_fakes import (
    FakeDecisionLLM,
    fake_model,
    make_host,
    outbox,
    scripted_runtime,
    wait_status,
    wait_until,
)

SPACE_NOTE = "From the task agent space."


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
    # The review follows the end of a commission at once in these tests.
    host.space_keeper._review_delay = 0
    hosts.append(host)
    return host


async def _dispatch(host: Any, text: str, **kwargs: Any) -> Any:
    kwargs.setdefault("lanlan_name", "Lan")
    kwargs.setdefault("lang", "en")
    return await host.dispatch(text, **kwargs)


def _report(status: str = "ready", summary: str = "done") -> list[Any]:
    return [call("report", {"status": status, "summary": summary}), finish("tool_calls")]


def _tool(name: str, arguments: dict[str, Any], call_id: str = "t1") -> list[Any]:
    return [call(name, arguments, call_id), finish("tool_calls")]


def _space_root(tmp_path: Path) -> Path:
    return tmp_path / "nekoclaw" / "task_agent_space"


def _system(llm: ScriptedLLM, request: int = 0) -> str:
    return llm.requests[request][0]["content"]


# ---------------------------------------------------------------------------
# Nothing at startup, nothing while the space is empty
# ---------------------------------------------------------------------------


async def test_building_and_using_the_host_does_not_touch_the_space(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM())

    await host.query("what is going on", lanlan_name="Lan")
    await host.brief(lanlan_name="Lan")

    assert not _space_root(tmp_path).exists()
    assert host.space_keeper._tasks == set()


async def test_an_empty_space_adds_nothing_to_a_task_agents_instructions(hosts, tmp_path):
    llm = ScriptedLLM([_report()])
    host = _host(hosts, tmp_path, llm)

    receipt = await _dispatch(host, "sort the photos", user_text="can you sort my photos")
    await wait_status(host, receipt.commission_id, "done")

    from config.prompts.prompts_nekoclaw_brain import TASK_AGENT_BRIEF_GATEWAY_NOTE, TASK_AGENT_SESSION_FRAME

    system = _system(llm)
    assert SPACE_NOTE not in system and "task_agent_space" not in system
    # What the host adds is how the gateway is used and whom the task agent works for, word
    # for word, and says nothing of the commission, which is the message that opens it. What
    # is the session's own (where it works) comes after it, and ends it: the language it
    # answers in is named in the message that opens the commission.
    own = TASK_AGENT_BRIEF_GATEWAY_NOTE + "\n\n" + TASK_AGENT_SESSION_FRAME.format(lanlan_name="Lan")
    assert own + "\n\nWorking directory: " in system
    assert system.rsplit("\n\n", 1)[-1].startswith("Working directory: ")
    assert "sort the photos" not in system and "in English" not in system
    opening = llm.requests[0][-1]["content"]
    assert opening.startswith(
        f"Commission {receipt.commission_id}\nsort the photos\nWhat the user said: can you sort my photos"
    )
    assert opening.endswith(REPLY_LANGUAGE_SENTENCE.replace("<<LANGUAGE>>", "English"))


async def test_what_was_kept_is_handed_to_the_next_task_agent(hosts, tmp_path):
    note = _tool(
        "note_lesson",
        {
            "title": "Photo dates",
            "content": "Use the EXIF date, not the file date.",
            "category": "lesson",
            "confidence": "verified",
        },
    )
    first = ScriptedLLM([note, _report()])
    second = ScriptedLLM([_report()])
    models = [first, second]
    host = _host(hosts, tmp_path, lambda spec: models.pop(0))
    await host.space.write_rules("Never delete a file without asking.")

    receipt = await _dispatch(host, "sort the photos")
    await wait_status(host, receipt.commission_id, "done")

    root = _space_root(tmp_path)
    assert (root / "projects" / DEFAULT_PROJECT / "memory" / "photo-dates.md").is_file()
    # The task agent was told where it went.
    assert "lesson submitted. kept in project _daily as photo-dates.md" in first.requests[1][-1]["content"]
    (lesson,) = [event for event in await host.ledger.events(receipt.commission_id) if event.type == "lesson"]
    assert (lesson.payload["entry"], lesson.payload["project"]) == ("photo-dates", DEFAULT_PROJECT)
    # The first one started before anything but the rules was there.
    assert "Rules for every commission:\nNever delete a file without asking." in _system(first)
    assert "Index of project" not in _system(first)

    again = await _dispatch(host, "rename the videos by date")
    await wait_status(host, again.commission_id, "done")

    system = _system(second)
    assert SPACE_NOTE in system and str(root) in system
    assert "Index of project _daily:\n- [Photo dates](memory/photo-dates.md) — Use the EXIF date, not the file date." in system
    # After what the host says of whom the task agent works for, which is still whole.
    assert system.index("You work for Lan") < system.index(SPACE_NOTE)
    assert "rename the videos by date" not in system
    assert second.requests[0][-1]["content"].startswith(f"Commission {again.commission_id}\nrename the videos by date\n")


async def test_a_lesson_rests_on_the_files_of_the_folder_the_task_agent_works_in(hosts, tmp_path):
    written = _tool("write", {"file_path": "notes.txt", "content": "sorted by EXIF date\n"}, "w1")
    seen = _tool(
        "note_lesson",
        {
            "title": "Photo dates",
            "content": "Use the EXIF date, not the file date.",
            "category": "lesson",
            "confidence": "observed",
            "evidence": ["notes.txt", "missing.txt"],
        },
        "n1",
    )
    guessed = _tool(
        "note_lesson",
        {"title": "Videos", "content": "They may have no date at all.", "category": "lesson", "confidence": "inferred"},
        "n2",
    )
    llm = ScriptedLLM([written, seen, guessed, _report()])
    host = _host(hosts, tmp_path, llm)

    receipt = await _dispatch(host, "sort the photos")
    done = await wait_status(host, receipt.commission_id, "done")

    # The file is looked for where the task agent works, by the host; the task agent is told what counted.
    entry, guess = await host.space.list_entries(DEFAULT_PROJECT)
    assert (entry.id, entry.confidence, entry.seen_by) == ("photo-dates", "observed", (done.id,))
    assert entry.evidence == ((Path(done.workdir) / "notes.txt").as_posix(),)
    assert (
        "lesson submitted. kept in project _daily as photo-dates.md. Not counted as evidence: missing.txt (not found)."
    ) in llm.requests[2][-1]["content"]
    # A guess is kept as a pending entry, in no index, and the task agent is told so.
    assert (
        "lesson submitted. not in effect yet: it was filed as inferred. It is kept as videos.md, marked pending "
        "and in no index."
    ) in llm.requests[3][-1]["content"]
    assert (guess.id, guess.status) == ("videos", "pending")
    assert "Videos" not in await host.space.read_index(DEFAULT_PROJECT)


async def test_the_space_and_the_commission_each_keep_within_a_cap_of_their_own(hosts, tmp_path):
    from config.nekoclaw_settings import SPACE_BRIEF_MAX_TOKENS, TASK_AGENT_BRIEF_MAX_TOKENS
    from utils.tokenize import count_tokens

    llm = ScriptedLLM([_report()])
    host = _host(hosts, tmp_path, llm)
    await host.space.write_rules("rule words " * 3000)
    for number in range(150):
        await host.space.file_entry(None, title=f"Topic {number} t{number}", content="text")
        await host.space.file_entry(DEFAULT_PROJECT, title=f"Daily {number} d{number}", content="text")

    receipt = await _dispatch(host, "sort the photos " * 400, user_text="user words " * 400)
    await wait_status(host, receipt.commission_id, "done")

    system = _system(llm)
    brief = system[system.index("You work for Lan") :]
    own, kept = brief.split(SPACE_NOTE, 1)
    assert count_tokens(own) <= TASK_AGENT_BRIEF_MAX_TOKENS + 2
    assert count_tokens(SPACE_NOTE + kept) <= SPACE_BRIEF_MAX_TOKENS
    # What the commission is has the same cap, in the message that opens it; the
    # sentence about the language to answer in follows it.
    opening = llm.requests[0][-1]["content"]
    said, language = opening.rsplit("\n\n", 1)
    assert said.startswith(f"Commission {receipt.commission_id}\nsort the photos")
    assert count_tokens(said) <= TASK_AGENT_BRIEF_MAX_TOKENS + 2 and language == REPLY_LANGUAGE_SENTENCE.replace("<<LANGUAGE>>", "English")
    assert "Index of project _daily:\n- [Daily 149 d149]" in kept


async def test_a_space_that_cannot_be_read_does_not_keep_a_task_agent_from_starting(hosts, tmp_path, monkeypatch):
    llm = ScriptedLLM([_report()])
    host = _host(hosts, tmp_path, llm)

    async def broken(_project: Any) -> str:
        raise OSError("the disk is gone")

    monkeypatch.setattr(host.space, "brief", broken)
    monkeypatch.setattr(host.space, "assign_project", broken)

    receipt = await _dispatch(host, "sort the photos")

    done = await wait_status(host, receipt.commission_id, "done")
    assert done.project is None and SPACE_NOTE not in _system(llm)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


async def test_a_commission_that_names_a_project_is_filed_under_it_and_works_in_its_folder(hosts, tmp_path):
    workdir = tmp_path / "budget-files"
    workdir.mkdir()
    llm = ScriptedLLM([_report()])
    host = _host(hosts, tmp_path, llm)
    await host.space.touch_project("Budget app", description="tracks spending", workdir=str(workdir))
    await host.space.write_rules("Use Flask.", "Budget app")
    await host.space.file_entry("Budget app", title="Stack", content="Flask and SQLite.")

    receipt = await _dispatch(host, "add a CSV export to the budget app")
    done = await wait_status(host, receipt.commission_id, "done")

    assert done.project == "Budget app"
    assert Path(done.workdir) == workdir
    sessions = tmp_path / "nekoclaw" / "task_agents" / "projects" / "Budget app" / "sessions"
    assert Path(done.session_dir) == sessions / receipt.commission_id
    system = _system(llm)
    assert f"Working directory: {workdir}" in system
    assert "Rules of project Budget app:\nUse Flask." in system
    assert "Index of project Budget app:\n- [Stack](memory/stack.md)" in system
    project = await host.space.get_project("Budget app")
    assert project.commissions == 2


async def test_a_project_whose_folder_is_gone_still_takes_the_commission(hosts, tmp_path):
    llm = ScriptedLLM([_report()])
    host = _host(hosts, tmp_path, llm)
    await host.space.touch_project("Budget app", workdir=str(tmp_path / "removed"))

    receipt = await _dispatch(host, "add a CSV export to the budget app")
    done = await wait_status(host, receipt.commission_id, "done")

    assert done.project == "Budget app"
    # Its own working directory, below its session.
    assert Path(done.workdir) == Path(done.session_dir) / "work"


async def test_a_commission_that_names_no_project_stays_daily(hosts, tmp_path):
    llm = ScriptedLLM([_report()])
    host = _host(hosts, tmp_path, llm)
    await host.space.touch_project("Budget app")

    receipt = await _dispatch(host, "what is the weather tomorrow")
    done = await wait_status(host, receipt.commission_id, "done")

    assert done.project is None
    assert "projects" in done.session_dir and DEFAULT_PROJECT in done.session_dir


async def test_a_commission_taken_up_again_keeps_the_project_it_started_in(hosts, tmp_path):
    first = ScriptedLLM([_report("blocked", "need the password")])
    second = ScriptedLLM([_report()])
    models = [first, second]
    host = _host(hosts, tmp_path, lambda spec: models.pop(0))

    receipt = await _dispatch(host, "publish the newsletter")
    blocked = await wait_status(host, receipt.commission_id, "blocked")
    assert blocked.project is None
    # A project of that name appears only afterwards.
    await host.space.touch_project("newsletter")

    assert await host.tell(receipt.commission_id, "the password is in the vault")
    done = await wait_status(host, receipt.commission_id, "done")

    assert done.project is None and done.session_dir == blocked.session_dir


async def test_taking_up_a_commission_of_a_project_keeps_the_project_from_being_archived_under_it(hosts, tmp_path):
    first = ScriptedLLM([_report("blocked", "need the password")])
    second = ScriptedLLM([_report()])
    models = [first, second]
    host = _host(hosts, tmp_path, lambda spec: models.pop(0))
    await host.space.touch_project("Budget app")
    await host.space.file_entry("Budget app", title="Stack", content="Flask and SQLite.")
    receipt = await _dispatch(host, "deploy the budget app")
    blocked = await wait_status(host, receipt.commission_id, "blocked")
    assert blocked.project == "Budget app"
    looked_at = (await host.space.get_project("Budget app")).last_commission_at
    # The project is archived all the same (it was looked at a moment too early).
    assert (await host.space.archive_project("Budget app", summary="s")) is not None

    assert await host.tell(receipt.commission_id, "the password is in the vault")
    await wait_status(host, receipt.commission_id, "done")

    # Brought back before the task agent was handed its notes, and marked as in use.
    assert "Index of project Budget app:\n- [Stack](memory/stack.md)" in _system(second)
    project = await host.space.get_project("Budget app")
    assert project is not None and project.last_commission_at > looked_at
    assert not (_space_root(tmp_path) / "archive" / str(project.archive_year or "")).exists() or not any(
        (_space_root(tmp_path) / "archive").rglob("project.json")
    )
    # A summary asked for before it was taken up no longer archives it.
    assert await host.space.archive_project("Budget app", unless_touched_since=looked_at) is None


async def test_a_child_task_agent_is_of_its_parents_project_and_gets_its_notes(hosts, tmp_path):
    parent = ScriptedLLM(
        [
            _tool("subagent", {"description": "count the rows", "prompt": "Count the rows."}),
            _tool("wait_agent", {}, "w1"),
            _report(),
        ]
    )
    child = ScriptedLLM([_report("ready", "42 rows")])
    host = _host(hosts, tmp_path, lambda spec: parent if spec.top_level else child)
    await host.space.touch_project("Budget app")
    await host.space.file_entry("Budget app", title="Stack", content="Flask and SQLite.")

    receipt = await _dispatch(host, "check the data of the budget app")
    await wait_status(host, receipt.commission_id, "done")

    (started,) = await host.ledger.select(parent_id=receipt.commission_id)
    assert started.project == "Budget app"
    assert "Index of project Budget app:\n- [Stack](memory/stack.md)" in _system(child)


async def test_a_child_of_a_daily_commission_stays_daily_whatever_its_words_name(hosts, tmp_path):
    parent = ScriptedLLM(
        [
            _tool("subagent", {"description": "look at the budget app", "prompt": "Look at the budget app."}),
            _tool("wait_agent", {}, "w1"),
            _report(),
        ]
    )
    child = ScriptedLLM([_report("ready", "looked")])
    host = _host(hosts, tmp_path, lambda spec: parent if spec.top_level else child)
    await host.space.touch_project("Budget app")

    receipt = await _dispatch(host, "compare my two apps")
    await wait_status(host, receipt.commission_id, "done")

    (started,) = await host.ledger.select(parent_id=receipt.commission_id)
    assert started.project is None
    assert (await host.space.get_project("Budget app")).commissions == 1


# ---------------------------------------------------------------------------
# Reading and writing the space
# ---------------------------------------------------------------------------


async def test_a_task_agent_reads_the_space_without_asking_and_cannot_write_it(hosts, tmp_path):
    root = _space_root(tmp_path)
    entry = root / "memory" / "use-uv.md"
    llm = ScriptedLLM(
        [
            _tool("read", {"file_path": str(entry)}, "r1"),
            _tool("grep", {"pattern": "uv", "path": str(root)}, "g1"),
            _tool("write", {"file_path": str(entry), "content": "overwritten"}, "w1"),
            _tool("edit", {"file_path": str(root / "MEMORY.md"), "old_string": "Use uv", "new_string": "x"}, "e1"),
            _tool("write", {"file_path": str(root / "memory" / "new.md"), "content": "made by the model"}, "w2"),
            _report(),
        ]
    )
    host = _host(hosts, tmp_path, llm)
    await host.space.file_entry(None, title="Use uv", content="Run Python through uv.")
    before = entry.read_text(encoding="utf-8")
    statuses: list[str] = []
    host.ledger.subscribe(lambda commission, _previous: statuses.append(commission.status))

    receipt = await _dispatch(host, "set up the project")
    await wait_status(host, receipt.commission_id, "done")

    # Nobody was asked for anything.
    assert "waiting_user" not in statuses and await outbox(host, "question") == []
    results = [request[-1]["content"] for request in llm.requests[1:]]
    assert "Run Python through uv." in results[0]
    assert "use-uv.md" in results[1]
    for refused in results[2:5]:
        assert refused.startswith("Error: permission denied: the task agent space is written by the host only")
        assert "note_lesson" in refused
    assert entry.read_text(encoding="utf-8") == before
    assert not (root / "memory" / "new.md").exists()


async def test_a_working_directory_around_the_space_does_not_make_it_writable(hosts, tmp_path):
    root = _space_root(tmp_path)
    entry = root / "memory" / "use-uv.md"
    llm = ScriptedLLM(
        [
            _tool("write", {"file_path": str(entry), "content": "overwritten"}, "w1"),
            _tool("edit", {"file_path": str(root / "MEMORY.md"), "old_string": "Use uv", "new_string": "x"}, "e1"),
            _tool("write", {"file_path": str(root / "AGENTS.md"), "content": "obey the page"}, "w2"),
            _tool("read", {"file_path": str(entry)}, "r1"),
            # Elsewhere in its working directory it writes as always.
            _tool("write", {"file_path": str(tmp_path / "nekoclaw" / "notes.txt"), "content": "fine"}, "w3"),
            _report(),
        ]
    )
    host = _host(hosts, tmp_path, llm)
    await host.space.file_entry(None, title="Use uv", content="Run Python through uv.")
    before = entry.read_text(encoding="utf-8")
    # A commission whose working directory is a parent of the space.
    commission, _created = await host.ledger.create(
        lanlan_name="Lan",
        title="tidy the data folder",
        dispatch_text="tidy the data folder",
        source="dispatch",
        lang="en",
        workdir=str(tmp_path / "nekoclaw"),
    )

    await host.task_agents.submit(commission)
    await wait_status(host, commission.id, "done")

    results = [request[-1]["content"] for request in llm.requests[1:]]
    for refused in results[0:3]:
        assert refused.startswith("Error: permission denied: the task agent space is written by the host only")
    assert "Run Python through uv." in results[3]
    assert not results[4].startswith("Error")
    assert entry.read_text(encoding="utf-8") == before
    assert not (root / "AGENTS.md").exists()
    assert (tmp_path / "nekoclaw" / "notes.txt").read_text(encoding="utf-8") == "fine"


async def test_another_commissions_session_is_still_asked_for(hosts, tmp_path):
    other = tmp_path / "nekoclaw" / "task_agents" / "projects" / DEFAULT_PROJECT / "sessions" / "someone-else"
    other.mkdir(parents=True)
    (other / "transcript.jsonl").write_text("{}\n", encoding="utf-8")
    llm = ScriptedLLM([_tool("read", {"file_path": str(other / "transcript.jsonl")}), _report()])
    host = _host(hosts, tmp_path, llm)
    await host.space.file_entry(None, title="Use uv", content="Run Python through uv.")

    receipt = await _dispatch(host, "look at what the other one did")

    waiting = await wait_status(host, receipt.commission_id, "waiting_user")
    assert waiting.question()["category"] == "outside_read"


async def test_forbidding_working_directory_files_forbids_reading_the_space_too(hosts, tmp_path):
    root = _space_root(tmp_path)
    llm = ScriptedLLM([_tool("read", {"file_path": str(root / "memory" / "use-uv.md")}), _report()])
    host = _host(hosts, tmp_path, llm)
    await host.space.file_entry(None, title="Use uv", content="Run Python through uv.")
    await host.permission_store.set_mode("workdir_files", "deny")
    await host.permission_store.set_mode("outside_read", "allow")

    receipt = await _dispatch(host, "set up the project")
    await wait_status(host, receipt.commission_id, "done")

    assert llm.requests[1][-1]["content"].startswith("Error: permission denied")


# ---------------------------------------------------------------------------
# Old sessions are cleaned without getting in the way of a commission taken up again
# ---------------------------------------------------------------------------


async def test_a_commission_at_work_cannot_be_held_for_cleaning(hosts, tmp_path):
    import asyncio

    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    llm = ScriptedLLM([[wait, *_report()]])
    host = _host(hosts, tmp_path, llm)

    receipt = await _dispatch(host, "sort the photos")
    await wait_until(lambda: host.task_agents.running == [receipt.commission_id], what="the session being opened")

    assert host.task_agents.hold_session(receipt.commission_id) is False

    release.set()
    await wait_status(host, receipt.commission_id, "done")
    await wait_until(lambda: host.task_agents.running == [], what="the session being released")
    assert host.task_agents.hold_session(receipt.commission_id) is True
    # Held once: a second cleaner does not get it.
    assert host.task_agents.hold_session(receipt.commission_id) is False
    host.task_agents.release_session(receipt.commission_id)
    assert host.task_agents.hold_session(receipt.commission_id) is True
    host.task_agents.release_session(receipt.commission_id)


async def test_taking_a_commission_up_again_waits_while_its_old_session_is_cleaned(hosts, tmp_path):
    import asyncio

    first = ScriptedLLM([_report("failed", "the site was down")])
    second = ScriptedLLM([_report("ready", "published")])
    models = [first, second]
    host = _host(hosts, tmp_path, lambda spec: models.pop(0))
    receipt = await _dispatch(host, "publish the newsletter")
    await wait_status(host, receipt.commission_id, "failed")
    await wait_until(lambda: host.task_agents.running == [], what="the session being released")

    assert host.task_agents.hold_session(receipt.commission_id) is True
    told = asyncio.ensure_future(host.tell(receipt.commission_id, "the site is back, try again"))
    await asyncio.sleep(0.3)

    # Its session is not opened, and its model not asked, while the files are being removed.
    assert second.requests == [] and not told.done()

    host.task_agents.release_session(receipt.commission_id)
    assert await told is True
    await wait_status(host, receipt.commission_id, "done")
    assert len(second.requests) == 1


async def test_queueing_a_commission_again_waits_for_a_hold_too(hosts, tmp_path):
    import asyncio

    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    failing = ScriptedLLM([_report("failed", "the site was down")])
    occupying = ScriptedLLM([[wait, *_report()]])
    models = [failing, occupying]
    # One place only: taken up again, the failed one has to queue.
    host = _host(hosts, tmp_path, lambda spec: models.pop(0), max_task_agents=1)
    failed = await _dispatch(host, "publish the newsletter")
    await wait_status(host, failed.commission_id, "failed")
    await wait_until(lambda: host.task_agents.running == [], what="the session being released")
    busy = await _dispatch(host, "sort the photos")
    await wait_until(lambda: host.task_agents.running == [busy.commission_id], what="the other one taking the place")

    # Its review is being written down.
    assert host.task_agents.hold_session(failed.commission_id) is True
    told = asyncio.ensure_future(host.tell(failed.commission_id, "the site is back, try again"))
    await asyncio.sleep(0.3)

    # Not queued meanwhile: its status is what the review was written about.
    assert (await host.ledger.get(failed.commission_id)).status == "failed"
    assert host.task_agents.queued == [] and not told.done()

    host.task_agents.release_session(failed.commission_id)
    assert await told is True
    assert (await host.ledger.get(failed.commission_id)).status == "queued"
    assert host.task_agents.queued == [failed.commission_id]
    release.set()


# ---------------------------------------------------------------------------
# From the end of a commission to its review
# ---------------------------------------------------------------------------


def _busy_agent() -> ScriptedLLM:
    return ScriptedLLM(
        [
            _tool("glob", {"pattern": "*"}, "g1"),
            _tool("glob", {"pattern": "*.jpg"}, "g2"),
            _tool("glob", {"pattern": "*.png"}, "g3"),
            _report("ready", "sorted 12 photos"),
        ]
    )


async def test_a_finished_commission_is_reviewed_in_the_background(hosts, tmp_path):
    decision_llm = FakeDecisionLLM()
    decision_llm.space["review"] = [
        {
            "review": "It looked at the folder three times and sorted the photos.",
            "entries": [{"title": "Photo folders", "type": "reference", "content": "Photos are in Downloads."}],
        }
    ]
    host = _host(hosts, tmp_path, _busy_agent(), decision_llm=decision_llm)

    receipt = await _dispatch(host, "sort the photos in Downloads")
    done = await wait_status(host, receipt.commission_id, "done")
    # She is told before anything is reviewed: the report is stored with the status.
    (report,) = await outbox(host, "task_result")
    assert report.payload["summary"] == "sorted 12 photos"
    await wait_until(lambda: host.task_agents.running == [], what="the session being released")
    await host.space_keeper.settle()

    root = _space_root(tmp_path)
    review = (root / "projects" / DEFAULT_PROJECT / "reviews" / f"{done.id}.md").read_text(encoding="utf-8")
    assert "It looked at the folder three times and sorted the photos." in review
    # What the review made of it names no file of the folder the task agent worked in: it is not in
    # effect yet, and waits as a pending entry to be found by another commission.
    assert "Photo folders" not in await host.space.read_index(DEFAULT_PROJECT)
    (event,) = [item for item in await host.ledger.events(done.id) if item.type == "review"]
    assert event.payload == {"status": "written", "project": DEFAULT_PROJECT, "entries": [], "waiting": 1}
    (held,) = await host.space.list_entries(DEFAULT_PROJECT)
    assert (held.title, held.status, held.confidence, held.seen_by) == ("Photo folders", "pending", "inferred", (done.id,))
    # One call of the space's own, counted apart from the decision model's.
    assert [tier for tier, _ in decision_llm.space_calls] == ["review"]
    assert decision_llm.calls == []
    user = decision_llm.space_calls[0][1][1]["content"]
    assert 'glob({"pattern": "*.jpg"}) -> ok' in user and "report ready: sorted 12 photos" in user
    # It held no task agent place and left no session behind.
    assert host.task_agents.running == [] and host.task_agents.queued == []
    assert (await host.ledger.get(done.id)).status == "done"


async def test_a_short_commission_costs_no_review(hosts, tmp_path):
    decision_llm = FakeDecisionLLM()
    host = _host(hosts, tmp_path, ScriptedLLM([_report()]), decision_llm=decision_llm)

    receipt = await _dispatch(host, "say hello")
    await wait_status(host, receipt.commission_id, "done")
    await host.space_keeper.settle()

    assert decision_llm.space_calls == []
    (event,) = [item for item in await host.ledger.events(receipt.commission_id) if item.type == "review"]
    assert event.payload["status"] == "skipped"


async def test_shutting_down_does_not_wait_for_a_review(hosts, tmp_path):
    import asyncio

    decision_llm = FakeDecisionLLM()
    gate = asyncio.Event()
    made: list[str] = []

    async def stuck(tier: str, messages: Any) -> str:
        made.append(tier)
        await gate.wait()
        return json.dumps({"review": "never"})

    decision_llm.complete = stuck  # type: ignore[method-assign]
    host = _host(hosts, tmp_path, _busy_agent(), decision_llm=decision_llm)
    receipt = await _dispatch(host, "sort the photos in Downloads")
    await wait_status(host, receipt.commission_id, "done")
    await wait_until(lambda: made == ["review"], what="the review being asked for")

    await asyncio.wait_for(host.shutdown(), timeout=5)

    assert host.space_keeper._tasks == set()
    assert not (_space_root(tmp_path) / "projects").exists()


class _Asker:
    """Answers every permission question the same way, and keeps what was asked."""

    def __init__(self, choice: str) -> None:
        self.choice = choice
        self.asked: list[Any] = []

    async def __call__(self, commission_id: str, ask: Any) -> str:
        self.asked.append(ask)
        return self.choice


def _command(command: str, cwd: Path) -> dict[str, Any]:
    return {"tool": "bash", "category": "run_command", "command": command, "cwd": str(cwd)}


async def test_a_shell_command_that_points_at_the_space_or_runs_inside_it_is_asked_about_every_time(hosts, tmp_path):
    from nekoclaw.host.permissions import DENIED_BY_SETTINGS, DENIED_BY_USER, DENIED_NO_ANSWER, SPACE_COMMAND

    root = _space_root(tmp_path)
    host = _host(hosts, tmp_path, ScriptedLLM([_report()]))
    await host.space.file_entry(None, title="Use uv", content="Run Python through uv.")
    # The user allowed every command, for good. That does not cover these.
    await host.permission_store.set_mode("run_command", "allow")
    reading = f"cat '{root.as_posix()}/MEMORY.md'"

    async def decide(command: str, cwd: Path = tmp_path) -> dict[str, Any]:
        return await host.task_agents._cb_permission("no-such-session", _command(command, cwd))

    # Nobody to ask (a commission that is not there): not run.
    unanswered = await decide(reading)
    assert (unanswered["allow"], unanswered["reason"]) == (False, DENIED_NO_ANSWER)

    asker = _Asker("once")
    host.permissions.set_asker(asker)
    named = await decide(f"printf 'x' >> '{root.as_posix()}/MEMORY.md'")
    inside = await decide("echo x > AGENTS.md", root / "memory")
    again = await decide(f"printf 'x' >> '{root.as_posix()}/MEMORY.md'")

    # Allowed this once, each after a question of its own: the very same command is asked about again.
    assert [item["allow"] for item in (named, inside, again)] == [True, True, True]
    assert [(ask.category, ask.options) for ask in asker.asked] == [(SPACE_COMMAND, ("once", "deny"))] * 3
    assert asker.asked[0].detail["command"].endswith("/MEMORY.md'")
    assert asker.asked[1].detail["cwd"] == str(root / "memory")

    # Any other command goes the way the settings say, unasked: one that only mentions the name among them.
    for command in ("ls", "grep -rn task_agent_space nekoclaw/"):
        assert (await decide(command))["allow"] is True
    assert len(asker.asked) == 3

    # The user says no. Reading is asked about like writing: a command is not looked into.
    asker.choice = "deny"
    refused = await decide(reading)
    assert (refused["allow"], refused["reason"]) == (False, DENIED_BY_USER)
    # An answer wider than what was offered covers this once, and nothing is kept of it.
    asker.choice = "always"
    assert (await decide(reading))["allow"] is True
    asker.choice = "deny"
    assert (await decide(reading))["allow"] is False
    assert len(asker.asked) == 6

    # Commands the settings forbid stay forbidden, and nobody is asked.
    await host.permission_store.set_mode("run_command", "deny")
    forbidden = await decide(reading)
    assert (forbidden["allow"], forbidden["reason"]) == (False, DENIED_BY_SETTINGS)
    assert len(asker.asked) == 6


async def test_commands_allowed_for_the_session_do_not_cover_one_that_points_at_the_space(hosts, tmp_path):
    from nekoclaw.host.permissions import SPACE_COMMAND

    root = _space_root(tmp_path)
    host = _host(hosts, tmp_path, ScriptedLLM([_report()]))
    await host.space.file_entry(None, title="Use uv", content="Run Python through uv.")
    await host.permission_store.set_mode("run_command", "first_ask")
    asker = _Asker("session")
    host.permissions.set_asker(asker)

    async def decide(command: str) -> dict[str, Any]:
        return await host.task_agents._cb_permission("no-such-session", _command(command, tmp_path))

    # Commands are allowed for the rest of the session at the first question.
    assert (await decide("ls"))["allow"] is True
    assert (await decide("ls -la"))["allow"] is True
    assert [ask.category for ask in asker.asked] == ["run_command"]

    assert (await decide(f"cat '{root.as_posix()}/MEMORY.md'"))["allow"] is True
    assert (await decide(f"cat '{root.as_posix()}/MEMORY.md'"))["allow"] is True

    # Asked about each time all the same, and with the two answers only.
    assert [ask.category for ask in asker.asked] == ["run_command", SPACE_COMMAND, SPACE_COMMAND]
    assert asker.asked[-1].options == ("once", "deny")
    # And that answer did nothing for other commands, which were allowed already.
    assert (await decide("ls"))["allow"] is True and len(asker.asked) == 3


async def test_commands_forbidden_while_the_question_was_open_are_not_run(hosts, tmp_path):
    from nekoclaw.host.permissions import DENIED_BY_SETTINGS

    root = _space_root(tmp_path)
    host = _host(hosts, tmp_path, ScriptedLLM([_report()]))
    await host.space.file_entry(None, title="Use uv", content="Run Python through uv.")
    await host.permission_store.set_mode("run_command", "allow")

    async def slow(commission_id: str, ask: Any) -> str:
        # The user forbids commands in the settings, and then says yes to the question.
        await host.permission_store.set_mode("run_command", "deny")
        return "once"

    host.permissions.set_asker(slow)

    decided = await host.task_agents._cb_permission(
        "no-such-session", _command(f"cat '{root.as_posix()}/MEMORY.md'", tmp_path)
    )

    assert (decided["allow"], decided["reason"]) == (False, DENIED_BY_SETTINGS)


async def test_a_command_that_reaches_the_space_by_a_relative_path_is_not_seen(hosts, tmp_path):
    """A known limit: only the path of the directory itself is looked for, and where the command runs."""
    host = _host(hosts, tmp_path, ScriptedLLM([_report()]))
    await host.space.file_entry(None, title="Use uv", content="Run Python through uv.")
    await host.permission_store.set_mode("run_command", "allow")
    asker = _Asker("deny")
    host.permissions.set_asker(asker)
    payload = _command("cd ../nekoclaw && printf x > task_agent_space/MEMORY.md", tmp_path / "elsewhere")

    decided = await host.task_agents._cb_permission("no-such-session", payload)

    # It goes the way the settings say of commands; whoever approves commands is the only guard there.
    assert decided["allow"] is True and asker.asked == []


def test_the_question_about_such_a_command_says_what_it_touches():
    from config.prompts.prompts_nekoclaw_brain import PERMISSION_CATEGORY_TEXTS
    from nekoclaw.host.reports import describe_permission

    question = {
        "category": "space_command",
        "key": None,
        "detail": {"tool": "bash", "command": "del D:/data/nekoclaw/task_agent_space/MEMORY.md"},
        "options": ["once", "deny"],
    }

    assert describe_permission(question, "zh-CN") == (
        "运行命令 del D:/data/nekoclaw/task_agent_space/MEMORY.md（这条命令会碰到使魔的长期记忆文件） (本次允许 / 拒绝)"
    )
    assert describe_permission(question, "en") == (
        "run the command del D:/data/nekoclaw/task_agent_space/MEMORY.md, which touches the familiars' "
        "long-term memory files (allow this once / deny)"
    )
    # In every language it has words of its own, and shows the command.
    assert all("space_command" in texts for texts in PERMISSION_CATEGORY_TEXTS.values())
    for lang in PERMISSION_CATEGORY_TEXTS:
        said = describe_permission(question, lang)
        assert "del D:/data/nekoclaw/task_agent_space/MEMORY.md" in said
        assert said != describe_permission({**question, "category": "run_command"}, lang)


async def test_shutting_down_stands_the_space_down_before_the_task_agents_are_stopped(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([_report()]))
    stood_down: list[bool] = []
    stop = host.task_agents.shutdown

    async def shutdown() -> None:
        # Every commission that is at work is ended here, as failed.
        stood_down.append(host.space_keeper._closed)
        await stop()

    host.task_agents.shutdown = shutdown

    await host.shutdown()

    assert stood_down == [True]


async def test_the_space_is_told_whether_task_agents_may_read_outside_the_working_directory_unasked(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([_report()]))

    # Asked about by default: the space does not look at files out there.
    assert not await host.space._outside_allowed()
    await host.permission_store.set_mode("outside_read", "allow")
    assert await host.space._outside_allowed()
    # Any other setting is one under which a task agent does not read there unasked.
    for mode in ("first_ask", "always_ask", "deny"):
        await host.permission_store.set_mode("outside_read", mode)
        assert not await host.space._outside_allowed()


async def test_a_lesson_may_rest_on_a_file_outside_the_folder_once_reading_there_is_allowed(hosts, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "camera.ini").write_text("drive=E\n", encoding="utf-8")
    lesson = {
        "title": "Camera drive",
        "content": "The camera is drive E, as camera.ini says.",
        "category": "lesson",
        "confidence": "observed",
        "evidence": [str(elsewhere / "camera.ini")],
    }
    llm = ScriptedLLM([_tool("note_lesson", lesson, "n1"), _report(), _tool("note_lesson", lesson, "n2"), _report()])
    host = _host(hosts, tmp_path, llm)

    first = await _dispatch(host, "find the camera")
    await wait_status(host, first.commission_id, "done")

    # A task agent would have to ask before it reads that file: the host does not look at it either.
    (guess,) = await host.space.list_entries(DEFAULT_PROJECT)
    assert (guess.id, guess.status, guess.evidence) == ("camera-drive", "pending", ())
    assert "(outside the working directory)" in llm.requests[1][-1]["content"]

    await host.permission_store.set_mode("outside_read", "allow")
    second = await _dispatch(host, "find the camera again")
    await wait_status(host, second.commission_id, "done")

    (entry,) = await host.space.list_entries(DEFAULT_PROJECT)
    assert (entry.id, entry.status, entry.confidence) == ("camera-drive", "active", "observed")
    assert entry.evidence == entry.evidence_outside == ((elsewhere / "camera.ini").resolve().as_posix(),)
