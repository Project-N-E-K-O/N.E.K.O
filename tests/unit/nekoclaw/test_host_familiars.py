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

"""Familiars driven by the host: the real runtime in process, a scripted model."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from tests.unit.nekoclaw.fakes import ScriptedLLM, call, finish, say
from tests.unit.nekoclaw.host_fakes import (
    FakeDecisionLLM,
    fake_model,
    make_host,
    outbox,
    scripted_runtime,
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
    """A host with no plugins, so every dispatch goes to a familiar."""
    pick = llm if callable(llm) else (lambda spec: llm)
    kwargs.setdefault("familiar_model", fake_model)
    kwargs.setdefault("decision_llm", FakeDecisionLLM())
    host = make_host(tmp_path, None, runtime_factory=scripted_runtime(pick), **kwargs)
    hosts.append(host)
    return host


async def _dispatch(host: Any, text: str, **kwargs: Any) -> Any:
    kwargs.setdefault("lanlan_name", "Lan")
    kwargs.setdefault("lang", "en")
    receipt = await host.dispatch(text, **kwargs)
    return receipt


def _report(status: str, summary: str, **extra: Any) -> list[Any]:
    return [call("report", {"status": status, "summary": summary, **extra}), finish("tool_calls")]


async def _events(host: Any, commission_id: str, kind: str | None = None) -> list[Any]:
    await host.familiars.settle()
    return [event for event in await host.ledger.events(commission_id) if kind is None or event.type == kind]


def _gate() -> tuple[asyncio.Event, Any]:
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    return release, wait


# ---------------------------------------------------------------------------
# From dispatch to result
# ---------------------------------------------------------------------------


async def test_nothing_runs_until_a_familiar_is_needed(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM())

    await host.query("what is going on", lanlan_name="Lan")

    assert not host.familiars.runtime_started
    assert not host.gateway.started
    assert host.familiars.running == []


async def test_commission_goes_to_a_familiar_and_its_report_ends_it(hosts, tmp_path):
    llm = ScriptedLLM([_report("ready", "sorted 12 photos", details="in D:/Photos/by-month")])
    host = _host(hosts, tmp_path, llm)

    receipt = await _dispatch(host, "sort the photos in my downloads folder", user_text="can you sort my photos")

    assert receipt.kind == "accepted"
    # No plugin exists, so no model was asked how to route it.
    assert host.decision._llm.calls == []
    done = await wait_status(host, receipt.commission_id, "done")
    assert (done.route, done.runtime) == ("familiar", "builtin")
    assert done.result_summary == "sorted 12 photos\nin D:/Photos/by-month"
    assert Path(done.workdir).is_dir()
    # The familiar was given her sentence as its work and the user's words in its brief.
    request = llm.requests[0]
    assert request[-1]["content"].startswith("sort the photos in my downloads folder")
    assert "can you sort my photos" in request[0]["content"]
    assert "commission for Lan" in request[0]["content"]
    # The session is closed again and its place is free.
    await wait_until(lambda: host.familiars.running == [], what="the session being released")
    (report,) = await outbox(host, "task_result")
    assert report.payload["channel"] == "familiar"
    assert (report.payload["status"], report.payload["success"]) == ("completed", True)
    assert report.payload["summary"] == "sorted 12 photos\nin D:/Photos/by-month"
    assert report.payload["delivery_mode"] == "proactive"
    kinds = [event.type for event in await _events(host, receipt.commission_id)]
    assert kinds[:3] == ["created", "decision", "status"]
    assert {"started", "tool_start", "tool_end", "report", "ended"} <= set(kinds)


async def test_the_session_is_the_commission_and_lives_below_the_data_root(hosts, tmp_path):
    llm = ScriptedLLM([_report("ready", "done")])
    host = _host(hosts, tmp_path, llm)

    receipt = await _dispatch(host, "tidy the desktop")
    await wait_status(host, receipt.commission_id, "done")

    session_dir = tmp_path / "nekoclaw" / "familiar" / "projects" / "_daily" / "sessions" / receipt.commission_id
    assert (session_dir / "transcript.jsonl").is_file()
    assert (session_dir / "work").is_dir()


async def test_run_that_ends_without_a_report_is_done_with_its_last_words(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([[say("All twelve photos are sorted."), finish()]]))

    receipt = await _dispatch(host, "sort the photos")

    done = await wait_status(host, receipt.commission_id, "done")
    assert done.result_summary == "All twelve photos are sorted."
    (report,) = await outbox(host, "task_result")
    assert report.payload["summary"] == "All twelve photos are sorted."


@pytest.mark.parametrize("status, expected", [("blocked", "blocked"), ("failed", "failed")])
async def test_blocked_and_failed_reports(hosts, tmp_path, status, expected):
    host = _host(hosts, tmp_path, ScriptedLLM([_report(status, "the folder is on a drive that is not connected")]))

    receipt = await _dispatch(host, "sort the photos")

    ended = await wait_status(host, receipt.commission_id, expected)
    assert ended.result_summary == "the folder is on a drive that is not connected"
    (report,) = await outbox(host, "task_result")
    assert (report.payload["status"], report.payload["success"]) == (expected, False)
    await wait_until(lambda: host.familiars.running == [], what="the session being released")


async def test_delivery_follows_the_commission_setting_then_the_familiars_wish(hosts, tmp_path):
    llm = ScriptedLLM([_report("ready", "one", delivery="passive"), _report("ready", "two", delivery="passive")])
    host = _host(hosts, tmp_path, llm)

    first = await _dispatch(host, "sort the photos")
    await wait_status(host, first.commission_id, "done")
    second, _created = await host.ledger.create(
        lanlan_name="Lan", title="backup", dispatch_text="back up the notes", source="dispatch", delivery="silent"
    )
    await host.familiars.submit(second)
    await wait_status(host, second.id, "done")

    reports = await outbox(host, "task_result")
    # The familiar asked for "passive": used where the commission says nothing,
    # overruled where the commission is set to silent (nothing is reported).
    assert [report.payload["delivery_mode"] for report in reports] == ["passive"]


async def test_progress_reports_are_recorded_and_change_nothing(hosts, tmp_path):
    llm = ScriptedLLM(
        [
            [call("report", {"status": "progress", "summary": "half way"}), finish("tool_calls")],
            _report("ready", "all done"),
        ]
    )
    host = _host(hosts, tmp_path, llm)

    receipt = await _dispatch(host, "sort the photos")
    await wait_status(host, receipt.commission_id, "done")

    reports = [event.payload["status"] for event in await _events(host, receipt.commission_id, "report")]
    assert reports == ["progress", "ready"]
    assert len(await outbox(host, "task_result")) == 1


async def test_text_increments_are_stored_as_one_row(hosts, tmp_path):
    words = [say(f"word{n} ") for n in range(50)]
    host = _host(hosts, tmp_path, ScriptedLLM([[*words, finish()]]))

    receipt = await _dispatch(host, "say fifty words")
    await wait_status(host, receipt.commission_id, "done")

    texts = await _events(host, receipt.commission_id, "text")
    assert len(texts) == 1
    assert texts[0].payload["text"] == "".join(f"word{n} " for n in range(50))


async def test_familiar_that_cannot_be_started_fails_the_commission(hosts, tmp_path):
    def broken(on_event: Any, callbacks: Any) -> Any:
        raise RuntimeError("the runtime process could not be created")

    host = make_host(tmp_path, None, familiar_model=fake_model, decision_llm=FakeDecisionLLM(), runtime_factory=broken)
    hosts.append(host)

    receipt = await _dispatch(host, "sort the photos")

    failed = await wait_status(host, receipt.commission_id, "failed")
    assert "could not be started" in failed.status_reason
    assert host.familiars.running == []
    (report,) = await outbox(host, "task_result")
    assert report.payload["status"] == "failed"


async def test_without_a_model_no_familiar_is_available(hosts, tmp_path):
    from tests.unit.nekoclaw.host_fakes import no_model

    host = _host(hosts, tmp_path, ScriptedLLM(), familiar_model=no_model)

    receipt = await _dispatch(host, "sort the photos")

    assert (receipt.kind, receipt.reason) == ("no_capability", "not_installed")
    assert not host.familiars.runtime_started


async def test_switched_off_familiars_are_not_available(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM(), familiars_enabled=lambda: False)

    assert await host.familiars.available() == (False, "not_enabled")
    assert (await _dispatch(host, "sort the photos")).kind == "no_capability"


# ---------------------------------------------------------------------------
# Places
# ---------------------------------------------------------------------------


async def test_default_limit_is_ten(hosts, tmp_path):
    from config.nekoclaw_settings import FAMILIAR_MAX_CONCURRENT

    assert FAMILIAR_MAX_CONCURRENT == 10
    assert _host(hosts, tmp_path, ScriptedLLM()).familiars.limit == 10


async def test_commissions_beyond_the_limit_wait_in_the_queue(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        "sort the photos": ScriptedLLM([[wait, say("photos sorted"), finish()]]),
        "back up the notes": ScriptedLLM([[say("notes backed up"), finish()]]),
        "empty the recycle bin": ScriptedLLM([[say("bin emptied"), finish()]]),
    }
    seen: list[str] = []

    def pick(spec: Any) -> ScriptedLLM:
        text = next(text for text in scripts if text in (spec.instructions or ""))
        seen.append(text)
        return scripts[text]

    host = _host(hosts, tmp_path, pick, max_familiars=1)

    first = await _dispatch(host, "sort the photos")
    await wait_status(host, first.commission_id, "working")
    second = await _dispatch(host, "back up the notes")
    third = await _dispatch(host, "empty the recycle bin")
    await host.brain.settle()

    # One place: the other two are registered and wait.
    assert host.familiars.running == [first.commission_id]
    assert host.familiars.queued == [second.commission_id, third.commission_id]
    assert (await host.ledger.get(second.commission_id)).status == "queued"
    assert seen == ["sort the photos"]

    release.set()
    await wait_status(host, first.commission_id, "done")
    await wait_status(host, second.commission_id, "done")
    await wait_status(host, third.commission_id, "done")
    # Started in the order they were queued, one at a time.
    assert seen == ["sort the photos", "back up the notes", "empty the recycle bin"]
    assert host.familiars.queued == []


async def test_a_queued_commission_can_be_stopped_before_it_starts(hosts, tmp_path):
    release, wait = _gate()
    started: list[str] = []

    def pick(spec: Any) -> ScriptedLLM:
        started.append(spec.session_id)
        return ScriptedLLM([[wait, say("done"), finish()]])

    host = _host(hosts, tmp_path, pick, max_familiars=1)
    first = await _dispatch(host, "sort the photos")
    await wait_status(host, first.commission_id, "working")
    second = await _dispatch(host, "back up the notes")
    await host.brain.settle()

    await host.abort(second.commission_id)
    release.set()
    await wait_status(host, first.commission_id, "done")
    await wait_until(lambda: host.familiars.running == [], what="the session being released")

    assert (await host.ledger.get(second.commission_id)).status == "cancelled"
    assert started == [first.commission_id]


# ---------------------------------------------------------------------------
# Stopping
# ---------------------------------------------------------------------------


async def test_stopping_closes_the_session_and_nothing_overwrites_cancelled(hosts, tmp_path):
    release, wait = _gate()
    llm = ScriptedLLM([[wait, *_report("ready", "sorted anyway")]])
    host = _host(hosts, tmp_path, llm)
    receipt = await _dispatch(host, "sort the photos")
    await wait_status(host, receipt.commission_id, "working")
    await wait_until(lambda: len(llm.requests) == 1, what="the model request")

    stopped = await _dispatch(host, "stop sorting the photos")

    assert stopped.kind == "stopped"
    assert stopped.commission_id == receipt.commission_id
    await wait_until(lambda: host.familiars.running == [], what="the session being closed")
    release.set()
    await host.brain.settle()
    after = await host.ledger.get(receipt.commission_id)
    assert (after.status, after.result_summary) == ("cancelled", None)
    assert await outbox(host, "task_result") == []
    ended = [event.payload["reason"] for event in await _events(host, receipt.commission_id, "ended")]
    assert ended[-1] == "closed"


async def test_shutting_the_host_down_fails_what_was_under_way(tmp_path):
    release, wait = _gate()
    host = make_host(
        tmp_path,
        None,
        familiar_model=fake_model,
        decision_llm=FakeDecisionLLM(),
        runtime_factory=scripted_runtime(lambda spec: ScriptedLLM([[wait, say("done"), finish()]])),
    )
    receipt = await _dispatch(host, "sort the photos")
    await wait_status(host, receipt.commission_id, "working")
    path = host.ledger.path

    await host.shutdown()
    release.set()

    from nekoclaw.host.ledger import Ledger

    again = Ledger(path)
    try:
        commission = await again.get(receipt.commission_id)
    finally:
        await again.close()
    # Not "cancelled": nobody stopped it.
    assert (commission.status, commission.status_reason) == ("failed", "the host was shut down")


# ---------------------------------------------------------------------------
# Questions and permissions
# ---------------------------------------------------------------------------


async def test_question_for_the_user_waits_and_the_answer_goes_back(hosts, tmp_path):
    questions = [
        {"id": "how", "question": "Sort by month or by place?", "options": [{"label": "month"}, {"label": "place"}]}
    ]
    llm = ScriptedLLM(
        [
            [call("ask_user_question", {"questions": questions}), finish("tool_calls")],
            _report("ready", "sorted by month"),
        ]
    )
    host = _host(hosts, tmp_path, llm)
    receipt = await _dispatch(host, "sort the photos")

    waiting = await wait_status(host, receipt.commission_id, "waiting_user")
    assert waiting.question() == {"kind": "questions", "questions": questions}
    (asked,) = await outbox(host, "question")
    assert asked.payload["task_id"] == receipt.commission_id
    assert asked.payload["question"]["questions"] == questions
    view = await host.query("", lanlan_name="Lan", lang="en")
    assert "waiting for the user to answer: Sort by month or by place?" in view.text

    answered = await _dispatch(host, "answer: by month please")

    assert answered.kind == "answered"
    assert answered.commission_id == receipt.commission_id
    await wait_status(host, receipt.commission_id, "done")
    tool_result = llm.requests[1][-1]["content"]
    assert json.loads(tool_result) == {
        "answers": [{"id": "how", "selected": ["month"], "custom": "answer: by month please"}]
    }
    statuses = [event.payload["to"] for event in await _events(host, receipt.commission_id, "status")]
    assert statuses == ["working", "waiting_user", "working", "done"]


async def test_waiting_report_keeps_the_session_for_the_answer(hosts, tmp_path):
    llm = ScriptedLLM(
        [
            _report("waiting", "Which folder holds the photos?"),
            _report("ready", "sorted D:/Camera"),
        ]
    )
    host = _host(hosts, tmp_path, llm)
    receipt = await _dispatch(host, "sort the photos")

    waiting = await wait_status(host, receipt.commission_id, "waiting_user")
    assert waiting.question() == {"kind": "report", "question": "Which folder holds the photos?", "details": ""}
    assert host.familiars.running == [receipt.commission_id]
    assert len(await outbox(host, "question")) == 1

    answered = await _dispatch(host, "answer: the photos are in D:/Camera")

    assert answered.kind == "answered"
    await wait_status(host, receipt.commission_id, "done")
    assert {"role": "user", "content": "answer: the photos are in D:/Camera"} in llm.requests[1]


async def test_answer_arriving_before_the_asking_run_has_ended_is_not_lost(hosts, tmp_path):
    llm = ScriptedLLM(
        [
            _report("waiting", "Which folder holds the photos?"),
            _report("ready", "sorted D:/Camera"),
        ]
    )
    host = _host(hosts, tmp_path, llm)
    handle_report = host.familiars._on_report
    early: list[bool] = []

    async def answer_at_once(session: Any, event: dict[str, Any]) -> None:
        await handle_report(session, event)
        if event.get("status") == "waiting":
            # The report is being handled while its run is still winding down.
            running = host.familiars._runtime.core._sessions[session.commission_id].running
            early.append(running and await host.familiars.answer(session.commission_id, "it is D:/Camera"))

    host.familiars._on_report = answer_at_once

    receipt = await _dispatch(host, "sort the photos")

    done = await wait_status(host, receipt.commission_id, "done")
    assert early == [True]
    assert done.result_summary == "sorted D:/Camera"
    # The model got the answer with its next request, not one request later.
    assert len(llm.requests) == 2
    assert {"role": "user", "content": "it is D:/Camera"} in llm.requests[1]
    statuses = [event.payload["to"] for event in await _events(host, receipt.commission_id, "status")]
    assert statuses == ["working", "waiting_user", "working", "done"]


def _write_outside(tmp_path: Path, name: str = "outside.txt") -> list[Any]:
    return [call("write", {"file_path": str(tmp_path / name), "content": "hello"}), finish("tool_calls")]


async def test_permission_once_allows_the_request_and_asks_again_next_time(hosts, tmp_path):
    llm = ScriptedLLM(
        [
            _write_outside(tmp_path, "one.txt"),
            [call("write", {"file_path": str(tmp_path / "two.txt"), "content": "hi"}, "c2"), finish("tool_calls")],
            _report("ready", "written"),
        ]
    )
    host = _host(hosts, tmp_path, llm)
    receipt = await _dispatch(host, "write two files outside the work folder")

    waiting = await wait_status(host, receipt.commission_id, "waiting_user")
    question = waiting.question()
    assert question["kind"] == "permission"
    assert question["category"] == "outside_write"
    assert question["options"] == ["once", "always", "deny"]
    assert question["detail"]["path"] == str(tmp_path / "one.txt")
    assert not (tmp_path / "one.txt").exists()

    assert (await _dispatch(host, "yes, go ahead")).kind == "answered"
    await wait_until(lambda: (tmp_path / "one.txt").exists(), what="the first file")
    # The second write asks again.
    await wait_until(lambda: _waiting_for(host, receipt.commission_id, "two.txt"), what="the second question")
    assert not (tmp_path / "two.txt").exists()
    assert (await _dispatch(host, "yes")).kind == "answered"

    await wait_status(host, receipt.commission_id, "done")
    assert (tmp_path / "two.txt").read_text(encoding="utf-8") == "hi"
    assert await host.permission_store.mode("outside_write") == "first_ask"


async def _waiting_for(host: Any, commission_id: str, name: str) -> bool:
    commission = await host.ledger.get(commission_id)
    question = commission.question() if commission else None
    return bool(question and commission.status == "waiting_user" and name in str(question.get("detail")))


async def test_permission_always_is_remembered(hosts, tmp_path):
    llm = ScriptedLLM(
        [
            _write_outside(tmp_path, "one.txt"),
            [call("write", {"file_path": str(tmp_path / "two.txt"), "content": "hi"}, "c2"), finish("tool_calls")],
            _report("ready", "written"),
        ]
    )
    host = _host(hosts, tmp_path, llm)
    receipt = await _dispatch(host, "write two files outside the work folder")
    await wait_status(host, receipt.commission_id, "waiting_user")

    assert (await _dispatch(host, "always, from now on")).kind == "answered"

    await wait_status(host, receipt.commission_id, "done")
    assert (tmp_path / "one.txt").exists() and (tmp_path / "two.txt").exists()
    assert await host.permission_store.mode("outside_write") == "allow"
    assert len(await outbox(host, "question")) == 1


async def test_permission_denied_is_told_to_the_familiar(hosts, tmp_path):
    llm = ScriptedLLM([_write_outside(tmp_path), _report("blocked", "not allowed to write there")])
    host = _host(hosts, tmp_path, llm)
    receipt = await _dispatch(host, "write a file outside the work folder")
    await wait_status(host, receipt.commission_id, "waiting_user")

    assert (await _dispatch(host, "no")).kind == "answered"

    await wait_status(host, receipt.commission_id, "blocked")
    assert not (tmp_path / "outside.txt").exists()
    assert "permission denied: the user declined" in llm.requests[1][-1]["content"]


async def test_an_answer_that_says_neither_yes_nor_no_keeps_waiting(hosts, tmp_path):
    llm = ScriptedLLM([_write_outside(tmp_path), _report("ready", "written")])
    host = _host(hosts, tmp_path, llm)
    receipt = await _dispatch(host, "write a file outside the work folder")
    await wait_status(host, receipt.commission_id, "waiting_user")

    unclear = await _dispatch(host, "answer: hmm, what is it for")

    assert unclear.kind == "unclear_answer"
    assert (await host.ledger.get(receipt.commission_id)).status == "waiting_user"
    assert not (tmp_path / "outside.txt").exists()
    await host.abort(receipt.commission_id)


async def test_forbidden_category_is_refused_without_asking(hosts, tmp_path):
    llm = ScriptedLLM([_write_outside(tmp_path), _report("blocked", "writing there is forbidden")])
    host = _host(hosts, tmp_path, llm)
    await host.permission_store.set_mode("outside_write", "deny")

    receipt = await _dispatch(host, "write a file outside the work folder")

    await wait_status(host, receipt.commission_id, "blocked")
    assert "the permission settings forbid this" in llm.requests[1][-1]["content"]
    assert await outbox(host, "question") == []
    statuses = [event.payload["to"] for event in await _events(host, receipt.commission_id, "status")]
    assert "waiting_user" not in statuses


async def test_stopping_while_it_waits_for_an_answer(hosts, tmp_path):
    llm = ScriptedLLM([_write_outside(tmp_path), _report("ready", "written")])
    host = _host(hosts, tmp_path, llm)
    receipt = await _dispatch(host, "write a file outside the work folder")
    await wait_status(host, receipt.commission_id, "waiting_user")

    await host.abort(receipt.commission_id)
    await wait_until(lambda: host.familiars.running == [], what="the session being closed")
    await host.brain.settle()

    assert (await host.ledger.get(receipt.commission_id)).status == "cancelled"
    assert not (tmp_path / "outside.txt").exists()


# ---------------------------------------------------------------------------
# Lessons and feedback
# ---------------------------------------------------------------------------


def _note(category: str, title: str, content: str, call_id: str = "n1") -> list[Any]:
    arguments = {"title": title, "content": content, "category": category}
    return [call("note_lesson", arguments, call_id), finish("tool_calls")]


async def test_feedback_becomes_a_task_preference_after_the_second_time(hosts, tmp_path):
    feedback = ("Reports in bullet points", "The user wants results as short bullet points, not paragraphs")

    def pick(spec: Any) -> ScriptedLLM:
        return ScriptedLLM([_note("feedback", *feedback), _report("ready", "done")])

    host = _host(hosts, tmp_path, pick)

    first = await _dispatch(host, "summarise the meeting notes")
    await wait_status(host, first.commission_id, "done")
    (entry,) = await host.preferences.list()
    assert (entry.origin, entry.active, entry.observations) == ("inferred", False, 1)
    assert entry.source_commission == first.commission_id

    second = await _dispatch(host, "summarise the project plan")
    await wait_status(host, second.commission_id, "done")

    (entry,) = await host.preferences.list()
    assert (entry.origin, entry.active, entry.observations) == ("inferred", True, 2)
    # Not stored as a lesson: feedback belongs to the task preferences.
    assert await _events(host, first.commission_id, "lesson") == []
    assert len(await _events(host, first.commission_id, "feedback")) == 1


@pytest.mark.parametrize("category", ["lesson", "reflection", "reference", "progress"])
async def test_other_lessons_go_to_the_lesson_sink(hosts, tmp_path, category):
    llm = ScriptedLLM([_note(category, "Photo dates", "EXIF dates are more reliable than file dates"), _report("ready", "done")])
    host = _host(hosts, tmp_path, llm)

    receipt = await _dispatch(host, "sort the photos")
    await wait_status(host, receipt.commission_id, "done")

    (lesson,) = await _events(host, receipt.commission_id, "lesson")
    assert lesson.payload["category"] == category
    assert lesson.payload["title"] == "Photo dates"
    assert await host.preferences.list() == []
    assert "lesson submitted. recorded" in llm.requests[1][-1]["content"]


async def test_lesson_sink_can_be_replaced(hosts, tmp_path):
    received: list[tuple[str, dict[str, Any]]] = []

    class Sink:
        async def submit(self, commission: Any, lesson: dict[str, Any]) -> str:
            received.append((commission.id, lesson))
            return "kept in the familiar space"

    llm = ScriptedLLM([_note("lesson", "Photo dates", "Use EXIF dates"), _report("ready", "done")])
    host = _host(hosts, tmp_path, llm, lessons=Sink())

    receipt = await _dispatch(host, "sort the photos")
    await wait_status(host, receipt.commission_id, "done")

    assert received == [
        (receipt.commission_id, {"title": "Photo dates", "content": "Use EXIF dates", "category": "lesson", "scope": "project"})
    ]


# ---------------------------------------------------------------------------
# Passing things on, and taking a commission up again
# ---------------------------------------------------------------------------


async def test_addition_is_passed_on_to_the_running_familiar(hosts, tmp_path):
    release, wait = _gate()
    llm = ScriptedLLM(
        [
            [call("todo_write", {"todos": []}), wait, finish("tool_calls")],
            _report("ready", "sorted by month"),
        ]
    )
    host = _host(hosts, tmp_path, llm)
    receipt = await _dispatch(host, "sort the photos in the downloads folder")
    await wait_status(host, receipt.commission_id, "working")
    await wait_until(lambda: len(llm.requests) == 1, what="the model request")

    told = await _dispatch(host, "also sort the photos by month")

    assert told.kind == "told"
    assert told.commission_id == receipt.commission_id
    assert len(await host.ledger.select()) == 1
    release.set()
    await wait_status(host, receipt.commission_id, "done")
    assert {"role": "user", "content": "also sort the photos by month"} in llm.requests[1]


async def test_a_blocked_commission_is_taken_up_again_where_it_stopped(hosts, tmp_path):
    scripts = [
        ScriptedLLM([_report("blocked", "the camera drive is not connected")]),
        ScriptedLLM([_report("ready", "sorted after all")]),
    ]
    host = _host(hosts, tmp_path, lambda spec: scripts.pop(0))
    receipt = await _dispatch(host, "sort the photos on the camera drive")
    await wait_status(host, receipt.commission_id, "blocked")
    await wait_until(lambda: host.familiars.running == [], what="the session being released")

    told = await _dispatch(host, "also the camera drive photos are on E: now")

    assert told.kind == "told"
    done = await wait_status(host, receipt.commission_id, "done")
    assert done.result_summary == "sorted after all"
    started = await _events(host, receipt.commission_id, "started")
    assert [event.payload["resumed"] for event in started] == [False, True]
    assert len(await host.ledger.select()) == 1


async def test_addition_arriving_while_the_blocked_session_is_still_closing(hosts, tmp_path):
    scripts = [
        ScriptedLLM([_report("blocked", "the camera drive is not connected")]),
        ScriptedLLM([_report("ready", "sorted after all")]),
    ]
    host = _host(hosts, tmp_path, lambda spec: scripts.pop(0))
    handle_report = host.familiars._on_report
    told: list[bool] = []

    async def tell_at_once(session: Any, event: dict[str, Any]) -> None:
        await handle_report(session, event)
        if event.get("status") == "blocked":
            # Its session is still there: neither ended nor released yet.
            assert session.commission_id in host.familiars.running
            told.append(await host.familiars.tell(session.commission_id, "the drive is E: now"))

    host.familiars._on_report = tell_at_once

    receipt = await _dispatch(host, "sort the photos on the camera drive")

    done = await wait_status(host, receipt.commission_id, "done")
    assert told == [True]
    assert done.result_summary == "sorted after all"
    started = await _events(host, receipt.commission_id, "started")
    assert [event.payload["resumed"] for event in started] == [False, True]


async def test_addition_for_a_queued_commission_goes_along_when_it_starts(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        "sort the photos": ScriptedLLM([[wait, say("photos sorted"), finish()]]),
        "back up the notes": ScriptedLLM([[say("notes backed up"), finish()]]),
    }

    def pick(spec: Any) -> ScriptedLLM:
        return next(llm for text, llm in scripts.items() if text in (spec.instructions or ""))

    host = _host(hosts, tmp_path, pick, max_familiars=1)
    first = await _dispatch(host, "sort the photos")
    await wait_status(host, first.commission_id, "working")
    second = await _dispatch(host, "back up the notes")
    await host.brain.settle()

    assert await host.tell(second.commission_id, "put the backup on drive E:") is True
    release.set()
    await wait_status(host, second.commission_id, "done")

    request = scripts["back up the notes"].requests[0]
    assert request[-1]["content"].startswith("back up the notes")
    assert "put the backup on drive E:" in request[-1]["content"]


# ---------------------------------------------------------------------------
# Familiars started by familiars
# ---------------------------------------------------------------------------


async def test_sub_familiar_runs_under_its_parent_and_reports_back_to_it(hosts, tmp_path):
    parent = ScriptedLLM(
        [
            [call("subagent", {"description": "count the files", "prompt": "Count the files in the folder."}), finish("tool_calls")],
            [call("list_agents", {}, "c2"), call("wait_agent", {"timeout_ms": 600_000}, "c3", index=1), finish("tool_calls")],
            _report("ready", "there are 3 files"),
        ]
    )
    child = ScriptedLLM([[say("3 files"), finish()]])
    specs: list[Any] = []

    def pick(spec: Any) -> ScriptedLLM:
        specs.append(spec)
        return parent if spec.top_level else child

    host = _host(hosts, tmp_path, pick)

    receipt = await _dispatch(host, "tell me how many files are in the folder")

    done = await wait_status(host, receipt.commission_id, "done")
    assert done.result_summary == "there are 3 files"
    (sub,) = await host.ledger.select(parent_id=receipt.commission_id)
    assert (sub.source, sub.route, sub.name, sub.status) == ("familiar", "familiar", "agent-1", "done")
    assert (sub.title, sub.dispatch_text) == ("count the files", "Count the files in the folder.")
    assert sub.lanlan_name == "Lan"
    # The child got its part as its work and may not start familiars itself.
    child_spec = next(spec for spec in specs if not spec.top_level)
    assert child_spec.disabled_tools == ["subagent"]
    assert child.requests[0][-1]["content"] == "Count the files in the folder."
    assert "subagent" not in [tool["function"]["name"] for tool in child.overrides[0].get("tools", [])]
    # Its result came back to the parent as a message, and the parent listed it.
    assert "[sub-familiar agent-1 finished: completed]\n3 files" in json.dumps(parent.requests[2], ensure_ascii=False).replace("\\n", "\n")
    listing = next(message["content"] for message in parent.requests[2] if message.get("role") == "tool" and "lead" in str(message.get("content")))
    assert listing.startswith("lead [working]")
    assert "agent-1 [" in listing
    # Only the commission she gave is reported to her.
    reports = await outbox(host, "task_result")
    assert [report.payload["task_id"] for report in reports] == [receipt.commission_id]
    # And a query lists her commission, not its sub-familiar.
    view = await host.query("", lanlan_name="Lan", lang="en")
    assert [item.id for item in view.commissions] == [receipt.commission_id]


async def test_sub_familiars_count_towards_the_limit(hosts, tmp_path):
    release, wait = _gate()
    parent = ScriptedLLM(
        [
            [call("subagent", {"description": "part one", "prompt": "Do part one."}), finish("tool_calls")],
            [call("wait_agent", {"timeout_ms": 600_000}, "c2"), finish("tool_calls")],
            _report("ready", "both parts done"),
        ]
    )
    child = ScriptedLLM([[wait, say("part one done"), finish()]])
    host = _host(hosts, tmp_path, lambda spec: parent if spec.top_level else child, max_familiars=1)

    receipt = await _dispatch(host, "do the work in two parts")
    await wait_until(lambda: _children(host, receipt.commission_id), what="the sub-familiar being registered")

    (sub,) = await host.ledger.select(parent_id=receipt.commission_id)
    # The only place is the parent's, so the child waits.
    assert sub.status == "queued"
    assert host.familiars.queued == [sub.id]
    await host.abort(receipt.commission_id)
    release.set()
    await host.brain.settle()
    assert (await host.ledger.get(sub.id)).status == "cancelled"


async def _children(host: Any, commission_id: str) -> bool:
    return bool(await host.ledger.select(parent_id=commission_id))


async def test_stopping_a_commission_stops_its_sub_familiars(hosts, tmp_path):
    release, wait = _gate()
    parent = ScriptedLLM(
        [
            [call("subagent", {"description": "part one", "prompt": "Do part one."}), finish("tool_calls")],
            [call("wait_agent", {"timeout_ms": 600_000}, "c2"), finish("tool_calls")],
            _report("ready", "done"),
        ]
    )
    child = ScriptedLLM([[wait, say("part one done"), finish()]])
    host = _host(hosts, tmp_path, lambda spec: parent if spec.top_level else child)
    receipt = await _dispatch(host, "do the work in two parts")
    await wait_until(lambda: _child_working(host, receipt.commission_id), what="the sub-familiar working")
    (sub,) = await host.ledger.select(parent_id=receipt.commission_id)

    await host.abort(receipt.commission_id)

    # Stopped together with its parent, by the time the stop returns.
    stopped = await host.ledger.get(sub.id)
    assert (stopped.status, stopped.status_reason) == ("cancelled", "its parent was stopped")
    await wait_until(lambda: host.familiars.running == [], what="both sessions being closed")
    release.set()
    await host.brain.settle()

    assert (await host.ledger.get(receipt.commission_id)).status == "cancelled"
    assert (await host.ledger.get(sub.id)).status == "cancelled"


async def test_parent_is_not_done_while_a_familiar_it_started_still_works(hosts, tmp_path):
    release, wait = _gate()
    parent = ScriptedLLM(
        [
            [call("subagent", {"description": "count the files", "prompt": "Count the files."}), finish("tool_calls")],
            # It ends its turn without waiting for the familiar it started.
            [say("Counting has started."), finish()],
            _report("ready", "there are 3 files"),
        ]
    )
    child = ScriptedLLM([[wait, say("3 files"), finish()]])
    host = _host(hosts, tmp_path, lambda spec: parent if spec.top_level else child)

    receipt = await _dispatch(host, "tell me how many files are in the folder")
    await wait_until(lambda: len(parent.requests) == 2, what="the parent's second request")
    await wait_until(lambda: _child_working(host, receipt.commission_id), what="the sub-familiar working")
    await wait_until(lambda: _ended(host, receipt.commission_id), what="the parent's run ending")

    # Its run is over, yet the commission is not: the result is still to come.
    assert (await host.ledger.get(receipt.commission_id)).status == "working"
    assert receipt.commission_id in host.familiars.running
    assert await outbox(host, "task_result") == []

    release.set()
    done = await wait_status(host, receipt.commission_id, "done")
    assert done.result_summary == "there are 3 files"
    # The familiar's result woke it up again.
    assert any("[sub-familiar agent-1 finished: completed]" in str(message.get("content")) for message in parent.requests[2])


async def _ended(host: Any, commission_id: str) -> bool:
    return any(event.payload.get("reason") == "completed" for event in await _events(host, commission_id, "ended"))


async def _child_working(host: Any, commission_id: str) -> bool:
    children = await host.ledger.select(parent_id=commission_id)
    return bool(children) and children[0].status == "working"


async def test_a_familiar_being_started_outlives_whoever_handed_it_over(tmp_path, hosts):
    llm = ScriptedLLM([_report("ready", "sorted")])
    inner = scripted_runtime(lambda spec: llm)
    may_start = asyncio.Event()
    starting = asyncio.Event()

    class SlowStart:
        """A runtime whose sessions take a while to come up."""

        def __init__(self, on_event: Any, callbacks: Any) -> None:
            self._runtime = inner(on_event, callbacks)

        async def start(self, spec: Any) -> None:
            starting.set()
            await may_start.wait()
            await self._runtime.start(spec)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._runtime, name)

    host = make_host(tmp_path, None, familiar_model=fake_model, decision_llm=FakeDecisionLLM(), runtime_factory=SlowStart)
    hosts.append(host)
    receipt = await _dispatch(host, "sort the photos")
    await asyncio.wait_for(starting.wait(), 5)

    # The second round, which handed the commission over, is cancelled while
    # the session is still coming up.
    host.brain._work[receipt.commission_id].cancel()
    await asyncio.sleep(0.05)
    assert host.familiars.running == [receipt.commission_id]
    may_start.set()

    # The session is neither lost nor left half set up: the work gets done.
    done = await wait_status(host, receipt.commission_id, "done")
    assert done.result_summary == "sorted"
