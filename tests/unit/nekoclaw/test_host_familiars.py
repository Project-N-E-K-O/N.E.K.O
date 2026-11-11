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
from types import SimpleNamespace
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


async def _filed(host: Any, text: str, **values: Any) -> Any:
    values.setdefault("route", "familiar")
    commission, _created = await host.ledger.create(
        lanlan_name="Lan", title=text, dispatch_text=text, source="dispatch", **values
    )
    return commission


def _slow_model() -> tuple[asyncio.Event, asyncio.Event, Any]:
    """A model configuration whose first read waits until released."""
    entered = asyncio.Event()
    release = asyncio.Event()
    reads: list[int] = []

    async def model() -> dict[str, Any]:
        reads.append(1)
        if len(reads) == 1:
            entered.set()
            await release.wait()
        return await fake_model()

    return entered, release, model


async def test_commissions_handed_over_at_once_do_not_overrun_the_limit(hosts, tmp_path):
    release, wait = _gate()
    started: list[str] = []

    def pick(spec: Any) -> ScriptedLLM:
        started.append(spec.session_id)
        return ScriptedLLM([[wait, say("done"), finish()]])

    host = _host(hosts, tmp_path, pick, max_familiars=1)
    first = await _filed(host, "sort the photos")
    second = await _filed(host, "back up the notes")

    await asyncio.gather(host.familiars.submit(first), host.familiars.submit(second))

    assert host.familiars.running == [first.id]
    assert host.familiars.queued == [second.id]
    assert (await host.ledger.get(second.id)).status == "queued"
    release.set()
    await wait_status(host, second.id, "done")
    assert started == [first.id, second.id]


async def test_a_place_freed_by_a_stop_during_preparation_goes_to_the_queue(hosts, tmp_path):
    entered, release, model = _slow_model()
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([[say("done"), finish()]]), familiar_model=model, max_familiars=1)
    first = await _filed(host, "sort the photos")
    second = await _filed(host, "back up the notes")
    handing = asyncio.ensure_future(host.familiars.submit(first))
    await asyncio.wait_for(entered.wait(), 5)
    await host.familiars.submit(second)
    assert host.familiars.queued == [second.id]

    # Stopped while its session is still being prepared.
    await host.ledger.transition(first.id, "cancelled", reason="stopped by the user")
    await host.familiars.abort(first.id)
    release.set()
    _ = await handing

    await wait_status(host, second.id, "done", timeout=5)
    assert (await host.ledger.get(first.id)).status == "cancelled"


async def test_a_place_freed_by_a_cancelled_start_goes_to_the_queue(hosts, tmp_path):
    entered, release, model = _slow_model()
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([[say("done"), finish()]]), familiar_model=model, max_familiars=1)
    first = await _filed(host, "sort the photos")
    await host.ledger.transition(first.id, "working", reason="test")
    await host.ledger.transition(first.id, "blocked", reason="test")
    second = await _filed(host, "back up the notes")
    # A start that is itself cancelled while the session is prepared (callers
    # run starts as shielded tasks, so this is a shutdown cancelling them).
    assert host.familiars._take_place(first.id)
    starting = asyncio.ensure_future(host.familiars._start(first.id, prompt="the drive is E: now"))
    await asyncio.wait_for(entered.wait(), 5)
    await host.familiars.submit(second)
    assert host.familiars.queued == [second.id]

    starting.cancel()
    await asyncio.gather(starting, return_exceptions=True)
    release.set()

    await wait_status(host, second.id, "done", timeout=5)
    assert (await host.ledger.get(first.id)).status == "blocked"


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


async def test_shutting_familiars_down_fails_what_was_queued(hosts, tmp_path):
    release, wait = _gate()
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([[wait, say("done"), finish()]]), max_familiars=1)
    first = await _dispatch(host, "sort the photos")
    await wait_status(host, first.commission_id, "working")
    second = await _dispatch(host, "back up the notes")
    await host.brain.settle()
    assert host.familiars.queued == [second.commission_id]

    await host.familiars.shutdown()
    release.set()

    queued = await host.ledger.get(second.commission_id)
    assert (queued.status, queued.status_reason) == ("failed", "the host was shut down")


async def test_nothing_is_started_after_shutting_down(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([_report("ready", "done")]))
    await host.familiars.shutdown()
    blocked = await _filed(host, "sort the photos")
    await host.ledger.transition(blocked.id, "working", reason="test")
    await host.ledger.transition(blocked.id, "blocked", reason="test")
    fresh = await _filed(host, "back up the notes")

    assert await host.familiars.tell(blocked.id, "try again") is False
    await host.familiars.submit(fresh)

    assert not host.familiars.runtime_started
    assert host.familiars.running == [] and host.familiars.queued == []
    assert (await host.ledger.get(blocked.id)).status == "blocked"
    after = await host.ledger.get(fresh.id)
    assert (after.status, after.status_reason) == ("failed", "the host was shut down")


async def test_stopping_while_the_session_comes_up_gives_it_no_work(hosts, tmp_path):
    llm = ScriptedLLM([_report("ready", "sorted anyway")])
    inner = scripted_runtime(lambda spec: llm)
    may_start = asyncio.Event()
    starting = asyncio.Event()

    class SlowStart:
        """A runtime whose first session waits for its process to come up."""

        def __init__(self, on_event: Any, callbacks: Any) -> None:
            self.inner = inner(on_event, callbacks)

        async def start(self, spec: Any) -> None:
            starting.set()
            await may_start.wait()
            await self.inner.start(spec)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

    host = make_host(tmp_path, None, familiar_model=fake_model, decision_llm=FakeDecisionLLM(), runtime_factory=SlowStart)
    hosts.append(host)
    receipt = await _dispatch(host, "sort the photos")
    await asyncio.wait_for(starting.wait(), 5)

    await host.abort(receipt.commission_id)
    may_start.set()
    await wait_until(lambda: not host.familiars._starting, what="the start being over")
    await wait_until(lambda: host.familiars.running == [], what="the place being freed")
    await wait_until(lambda: not host.familiars._runtime.inner.core._sessions, what="the session being closed")
    await host.familiars.settle()

    assert llm.requests == []
    assert (await host.ledger.get(receipt.commission_id)).status == "cancelled"


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


async def _waiting_and_closed(host: Any, text: str) -> str:
    """Dispatch, wait for the ``waiting`` report, then close the idle session as an idle runtime does."""
    receipt = await _dispatch(host, text)
    await wait_status(host, receipt.commission_id, "waiting_user")
    await wait_until(lambda: _ended(host, receipt.commission_id), what="the asking run ending")
    await host.familiars._runtime.close(receipt.commission_id)
    await host.familiars.settle()
    assert receipt.commission_id not in host.familiars.running
    assert (await host.ledger.get(receipt.commission_id)).status == "waiting_user"
    return receipt.commission_id


async def test_an_answer_kept_for_a_wake_up_that_never_ran_survives_the_session_closing(hosts, tmp_path):
    llm = ScriptedLLM(
        [
            _report("waiting", "Which folder holds the photos?"),
            _report("ready", "sorted D:/Camera"),
        ]
    )
    host = _host(hosts, tmp_path, llm)
    receipt = await _dispatch(host, "sort the photos")
    await wait_status(host, receipt.commission_id, "waiting_user")
    await wait_until(lambda: _ended(host, receipt.commission_id), what="the asking run ending")
    session = host.familiars._sessions[receipt.commission_id]
    # A child's result was steered to wake it, so the answer is kept for the
    # end of that run; the runtime is exiting and the run never comes.
    session.run_ended = False
    assert await host.answer(receipt.commission_id, "the photos are in D:/Camera") is True
    assert session.held_answer == "the photos are in D:/Camera"

    await host.familiars._runtime.close(receipt.commission_id)

    done = await wait_status(host, receipt.commission_id, "done")
    assert done.result_summary == "sorted D:/Camera"
    assert {"role": "user", "content": "the photos are in D:/Camera"} in llm.requests[1]


async def test_a_refused_wake_up_hands_over_the_kept_answer(hosts, tmp_path):
    llm = ScriptedLLM(
        [
            _report("waiting", "Which folder holds the photos?"),
            _report("ready", "sorted D:/Camera"),
        ]
    )
    host = _host(hosts, tmp_path, llm)
    receipt = await _dispatch(host, "sort the photos")
    await wait_status(host, receipt.commission_id, "waiting_user")
    await wait_until(lambda: _ended(host, receipt.commission_id), what="the asking run ending")
    session = host.familiars._sessions[receipt.commission_id]
    session.run_ended = False
    session.sent += 1
    assert await host.answer(receipt.commission_id, "the photos are in D:/Camera") is True

    # The runtime refuses the steer that was to wake it: no run starts.
    await host.familiars._on_error(session, {"type": "error", "command": "steer", "message": "refused"})

    done = await wait_status(host, receipt.commission_id, "done")
    assert done.result_summary == "sorted D:/Camera"


async def test_two_sentences_at_once_for_a_commission_with_no_place_are_queued_together(hosts, tmp_path):
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
    second = await _filed(host, "back up the notes")
    await host.ledger.transition(second.id, "working", reason="test")
    await host.ledger.transition(second.id, "blocked", reason="test")

    told = await asyncio.gather(
        host.familiars.tell(second.id, "put it on drive E:"),
        host.familiars.tell(second.id, "and skip the drafts"),
    )

    assert told == [True, True]
    assert host.familiars.queued == [second.id]
    release.set()
    await wait_status(host, second.id, "done")
    sent = " | ".join(str(message.get("content")) for message in scripts["back up the notes"].requests[0])
    assert "put it on drive E:" in sent
    assert "and skip the drafts" in sent


async def test_an_answer_after_the_idle_session_was_closed_takes_it_up_again(hosts, tmp_path):
    llm = ScriptedLLM(
        [
            _report("waiting", "Which folder holds the photos?"),
            _report("ready", "sorted D:/Camera"),
        ]
    )
    host = _host(hosts, tmp_path, llm)
    commission_id = await _waiting_and_closed(host, "sort the photos")

    assert await host.answer(commission_id, "the photos are in D:/Camera") is True

    done = await wait_status(host, commission_id, "done")
    assert done.result_summary == "sorted D:/Camera"
    # Taken up where its transcript ends: the report that asked is still there.
    assert "Which folder holds the photos?" in json.dumps(llm.requests[1], ensure_ascii=False)
    assert {"role": "user", "content": "the photos are in D:/Camera"} in llm.requests[1]
    (_first, again) = await _events(host, commission_id, "started")
    assert again.payload["resumed"] is True


async def test_taking_up_again_outlives_a_caller_cancelled_meanwhile(hosts, tmp_path):
    entered, release, model = _slow_model()
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([[say("photos sorted"), finish()]]), familiar_model=model)
    commission = await _filed(host, "sort the photos")
    await host.ledger.transition(commission.id, "working", reason="test")
    await host.ledger.transition(commission.id, "blocked", reason="test")

    telling = asyncio.ensure_future(host.familiars.tell(commission.id, "the drive is E: now"))
    await asyncio.wait_for(entered.wait(), 5)
    # Whoever passed it on goes away (a dispatch cancelled by a shutdown, say)
    # while the session is being set up; the start goes on regardless.
    telling.cancel()
    await asyncio.gather(telling, return_exceptions=True)
    release.set()

    await wait_status(host, commission.id, "done", timeout=5)


async def test_a_second_sentence_while_taking_up_again_goes_along_with_that_start(hosts, tmp_path):
    entered, release, model = _slow_model()
    llm = ScriptedLLM([[say("photos sorted"), finish()]])
    host = _host(hosts, tmp_path, lambda spec: llm, familiar_model=model)
    commission = await _filed(host, "sort the photos")
    await host.ledger.transition(commission.id, "working", reason="test")
    await host.ledger.transition(commission.id, "blocked", reason="test")

    first = asyncio.ensure_future(host.familiars.tell(commission.id, "the drive is E: now"))
    await asyncio.wait_for(entered.wait(), 5)
    # Still blocked while its session is set up; a second sentence arrives.
    assert await host.familiars.tell(commission.id, "and skip the screenshots") is True
    assert host.familiars.queued == []
    release.set()
    assert await first is True

    await wait_status(host, commission.id, "done", timeout=5)
    sent = " | ".join(str(message.get("content")) for message in llm.requests[0])
    assert "the drive is E: now" in sent
    assert "and skip the screenshots" in sent


async def test_an_answer_told_after_the_idle_session_was_closed_waits_for_a_place(hosts, tmp_path):
    release, wait = _gate()
    scripts = {
        "sort the photos": ScriptedLLM(
            [_report("waiting", "Which folder holds the photos?"), _report("ready", "sorted D:/Camera")]
        ),
        "back up the notes": ScriptedLLM([[wait, say("notes backed up"), finish()]]),
    }

    def pick(spec: Any) -> ScriptedLLM:
        return next(llm for text, llm in scripts.items() if text in (spec.instructions or ""))

    host = _host(hosts, tmp_path, pick, max_familiars=1)
    commission_id = await _waiting_and_closed(host, "sort the photos")
    other = await _filed(host, "back up the notes")
    assert await host.familiars.submit(other) is True
    await wait_status(host, other.id, "working")

    assert await host.familiars.tell(commission_id, "the photos are in D:/Camera") is True
    assert (await host.ledger.get(commission_id)).status == "queued"
    assert host.familiars.queued == [commission_id]

    release.set()
    done = await wait_status(host, commission_id, "done")
    assert done.result_summary == "sorted D:/Camera"
    assert {"role": "user", "content": "the photos are in D:/Camera"} in scripts["sort the photos"].requests[1]


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
# Web search
# ---------------------------------------------------------------------------


class _SearchRunner:
    """Stands in for the plugin runner and records the entries it is asked to run."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run_entry(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        data = {"results": [{"url": "https://example.com/weather", "title": "Weather"}]}
        return SimpleNamespace(success=True, data=data, error=None)


async def test_web_search_runs_the_search_plugin(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM())
    runner = _SearchRunner()
    host.familiars._runner = runner
    commission = await _filed(host, "look up the weather")

    result = await host.familiars._cb_web_search(commission.id, {"queries": ["weather tomorrow"]})

    assert result == {"results": [{"query": "weather tomorrow", "sources": [{"url": "https://example.com/weather", "title": "Weather"}]}]}
    assert [(call["plugin_id"], call["entry_id"], call["args"]) for call in runner.calls] == [
        ("web_search", "search", {"query": "weather tomorrow"})
    ]


@pytest.mark.parametrize("blocked_by", ["switch", "permission"])
async def test_web_search_obeys_the_plugin_switch_and_permission(hosts, tmp_path, blocked_by):
    llm = ScriptedLLM(
        [
            [call("web_search", {"queries": ["weather tomorrow"]}), finish("tool_calls")],
            _report("blocked", "cannot search"),
        ]
    )
    extra = {"plugins_enabled": lambda: False} if blocked_by == "switch" else {}
    host = _host(hosts, tmp_path, llm, **extra)
    if blocked_by == "permission":
        await host.permission_store.set_mode("plugin_entry", "deny")
    runner = _SearchRunner()
    host.familiars._runner = runner

    receipt = await _dispatch(host, "look up the weather")

    await wait_status(host, receipt.commission_id, "blocked")
    assert runner.calls == []
    told = llm.requests[1][-1]["content"]
    expected = "plugins are switched off" if blocked_by == "switch" else "the permission settings forbid this"
    assert expected in told
    (denied,) = await _events(host, receipt.commission_id, "capability_denied")
    assert denied.payload["capability"] == "plugin:web_search:search"


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


async def test_an_answer_while_a_child_result_keeps_the_parent_busy_waits_for_that_run(hosts, tmp_path):
    child_release, child_wait = _gate()
    parent_release, parent_wait = _gate()
    parent = ScriptedLLM(
        [
            [call("subagent", {"description": "count the photos", "prompt": "Count the photos."}), finish("tool_calls")],
            _report("waiting", "Sort them by month or by place?"),
            # Woken by the child's result while it waits for the answer.
            [parent_wait, say("noted: 120 photos"), finish()],
            _report("ready", "sorted 120 photos by month"),
        ]
    )
    child = ScriptedLLM([[child_wait, say("120 photos"), finish()]])
    host = _host(hosts, tmp_path, lambda spec: parent if spec.top_level else child)

    receipt = await _dispatch(host, "sort the photos")
    await wait_status(host, receipt.commission_id, "waiting_user")
    await wait_until(lambda: _ended(host, receipt.commission_id), what="the asking run ending")
    child_release.set()
    await wait_until(lambda: len(parent.requests) == 3, what="the child's result waking the parent")

    # The user answers while that run is still going.
    assert await host.answer(receipt.commission_id, "by month") is True
    parent_release.set()

    done = await wait_status(host, receipt.commission_id, "done")
    assert done.result_summary == "sorted 120 photos by month"
    assert {"role": "user", "content": "by month"} in parent.requests[3]


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


@pytest.mark.parametrize("where", ["outside", "inside"])
async def test_sub_familiar_works_inside_its_parents_folder(hosts, tmp_path, where):
    outside = tmp_path / "elsewhere"
    cwd = str(outside) if where == "outside" else "part"
    parent = ScriptedLLM(
        [
            [call("subagent", {"description": "count", "prompt": "Count the files.", "cwd": cwd}), finish("tool_calls")],
            [call("wait_agent", {"timeout_ms": 600_000}, "c2"), finish("tool_calls")],
            _report("ready", "counted"),
        ]
    )
    child = ScriptedLLM([[say("3 files"), finish()]])
    host = _host(hosts, tmp_path, lambda spec: parent if spec.top_level else child)

    receipt = await _dispatch(host, "count the files")

    done = await wait_status(host, receipt.commission_id, "done")
    (sub,) = await host.ledger.select(parent_id=receipt.commission_id)
    if where == "outside":
        # Out of bounds: it works where its parent works, so writing elsewhere
        # is asked about just as it is for the parent.
        assert Path(sub.workdir) == Path(done.workdir)
        assert not outside.exists()
    else:
        assert Path(sub.workdir) == Path(done.workdir) / "part"
        assert Path(sub.workdir).is_dir()


async def test_sub_familiars_count_towards_the_limit(hosts, tmp_path):
    parent = ScriptedLLM(
        [
            [call("subagent", {"description": "part one", "prompt": "Do part one."}), finish("tool_calls")],
            _report("ready", "did both parts myself"),
        ]
    )
    child = ScriptedLLM([[say("part one done"), finish()]])
    host = _host(hosts, tmp_path, lambda spec: parent if spec.top_level else child, max_familiars=1)

    receipt = await _dispatch(host, "do the work in two parts")
    await wait_status(host, receipt.commission_id, "done")

    # The only place is the parent's. A child queued behind it would wait for
    # a place its parent keeps while waiting for the child, so it is refused
    # and the parent is told to do that part itself.
    (sub,) = await host.ledger.select(parent_id=receipt.commission_id)
    assert (sub.status, sub.status_reason) == ("failed", "no familiar place was free")
    assert host.familiars.queued == []
    assert child.requests == []
    assert any("do this part yourself" in str(message.get("content")) for message in parent.requests[1])


async def test_no_sub_familiar_starts_for_a_parent_stopped_meanwhile(hosts, tmp_path, monkeypatch):
    parent = ScriptedLLM(
        [
            [call("subagent", {"description": "part one", "prompt": "Do part one."}), finish("tool_calls")],
            _report("ready", "done"),
        ]
    )
    child = ScriptedLLM([[say("part one done"), finish()]])
    host = _host(hosts, tmp_path, lambda spec: parent if spec.top_level else child)
    real_workdir = host.familiars._child_workdir

    async def stopped_meanwhile(parent_commission: Any, cwd: Any) -> Any:
        # The parent is stopped while the child is being prepared, before
        # the child is filed: a stop sets the status first and then finds no
        # child to stop. (The rest of the stop waits for this very call.)
        await host.ledger.transition(parent_commission.id, "cancelled", reason="stopped by the user")
        return await real_workdir(parent_commission, cwd)

    monkeypatch.setattr(host.familiars, "_child_workdir", stopped_meanwhile)
    receipt = await _dispatch(host, "do the work in two parts")
    await wait_until(lambda: _children(host, receipt.commission_id), what="the sub-familiar being filed")
    # The callback that filed it goes on to check the parent; it is not one of
    # the events ``settle`` waits for.
    await wait_until(lambda: _child_status(host, receipt.commission_id, "cancelled"), what="the sub-familiar being stopped")
    await host.brain.settle()
    await host.familiars.settle()

    (sub,) = await host.ledger.select(parent_id=receipt.commission_id)
    assert (sub.status, sub.status_reason) == ("cancelled", "its parent was stopped")
    assert child.requests == []


async def _child_status(host: Any, commission_id: str, status: str) -> bool:
    children = await host.ledger.select(parent_id=commission_id)
    return bool(children) and children[0].status == status


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


async def test_parent_does_not_wait_for_a_child_that_reported_blocked(hosts, tmp_path):
    parent = ScriptedLLM(
        [
            [call("subagent", {"description": "open the vault", "prompt": "Open the vault."}), finish("tool_calls")],
            [call("wait_agent", {"timeout_ms": 600_000}, "c2"), finish("tool_calls")],
            # It ends with plain words rather than a closing report.
            [say("The vault stays shut; everything else is done."), finish()],
        ]
    )
    child = ScriptedLLM([_report("blocked", "the vault needs a key"), [say("waiting"), finish()]])
    host = _host(hosts, tmp_path, lambda spec: parent if spec.top_level else child)

    receipt = await _dispatch(host, "tidy up and open the vault")

    # The blocked child has reported and produces nothing more, so the parent
    # is done rather than left working with its place held.
    done = await wait_status(host, receipt.commission_id, "done")
    assert done.result_summary == "The vault stays shut; everything else is done."
    # With its parent finished, the blocked child is closed like any other.
    (sub,) = await host.ledger.select(parent_id=receipt.commission_id)
    assert (sub.status, sub.status_reason) == ("cancelled", "its parent finished")


async def test_a_parent_woken_by_a_result_is_not_ended_by_its_earlier_run(hosts, tmp_path):
    release, wait = _gate()
    parent = ScriptedLLM(
        [
            [call("subagent", {"description": "count the files", "prompt": "Count the files."}), finish("tool_calls")],
            [wait, say("Counting has started."), finish()],
            _report("ready", "there are 3 files"),
        ]
    )
    child = ScriptedLLM([[say("3 files"), finish()]])
    host = _host(hosts, tmp_path, lambda spec: parent if spec.top_level else child)
    handle = host.familiars._handle

    async def parent_ends_meanwhile(event: dict[str, Any]) -> None:
        if event.get("type") == "ended" and event.get("reason") == "completed":
            commission = await host.ledger.get(str(event.get("session_id")))
            if commission is not None and commission.parent_id is not None:
                # The child's result is handled only once the parent's run
                # has ended too, so that run's "ended" waits behind it.
                lead = host.familiars._runtime.core._sessions[commission.parent_id]
                release.set()
                await wait_until(lambda: not lead.running, what="the parent's run ending")
        await handle(event)

    host.familiars._handle = parent_ends_meanwhile

    receipt = await _dispatch(host, "tell me how many files are in the folder")

    done = await wait_status(host, receipt.commission_id, "done")
    # The result is that of the run the child's result started.
    assert done.result_summary == "there are 3 files"
    assert len(parent.requests) == 3
    assert any("[sub-familiar agent-1 finished: completed]" in str(message.get("content")) for message in parent.requests[2])


async def _blocked_with_closing_session(host: Any, text: str) -> Any:
    """A blocked commission whose old session is still on its way out."""
    from nekoclaw.host.familiars import _Session

    commission = await _filed(host, text)
    await host.ledger.transition(commission.id, "working", reason="test")
    await host.ledger.transition(commission.id, "blocked", reason="test")
    session = _Session(commission.id)
    session.closing = True
    host.familiars._sessions[commission.id] = session
    return commission


async def test_a_retry_whose_old_session_closes_meanwhile_still_starts(hosts, tmp_path, monkeypatch):
    llm = ScriptedLLM([[say("photos sorted"), finish()]])
    host = _host(hosts, tmp_path, lambda spec: llm)
    commission = await _blocked_with_closing_session(host, "sort the photos")
    real_transition = host.ledger.transition

    async def closing_meanwhile(commission_id: str, status: str, **kwargs: Any) -> Any:
        if status == "queued":
            # The old session's "closed" arrives while the status is written:
            # the place comes free while the queue is still empty.
            await host.familiars._release(commission_id)
        return await real_transition(commission_id, status, **kwargs)

    monkeypatch.setattr(host.ledger, "transition", closing_meanwhile)
    host.familiars._candidates[commission.id] = ("weather",)
    start = host.familiars._start
    candidates_at_start: list[Any] = []

    async def recording_start(commission_id: str, **kwargs: Any) -> bool:
        candidates_at_start.append(host.familiars._candidates.get(commission_id))
        return await start(commission_id, **kwargs)

    monkeypatch.setattr(host.familiars, "_start", recording_start)

    assert await host.familiars.tell(commission.id, "the drive is E: now") is True

    await wait_status(host, commission.id, "done", timeout=5)
    assert host.familiars.queued == []
    # Its candidate plugins went along with the retry.
    assert candidates_at_start == [("weather",)]


async def test_a_retry_queued_while_shutting_down_is_failed_not_left_queued(hosts, tmp_path, monkeypatch):
    llm = ScriptedLLM([[say("photos sorted"), finish()]])
    host = _host(hosts, tmp_path, lambda spec: llm)
    commission = await _blocked_with_closing_session(host, "sort the photos")
    real_transition = host.ledger.transition
    shutting: list[Any] = []

    async def shut_down_meanwhile(commission_id: str, status: str, **kwargs: Any) -> Any:
        if status == "queued" and not shutting:
            # Shutting down starts while the status is written and empties
            # the queue before the retry is in it.
            shutting.append(asyncio.ensure_future(host.familiars.shutdown()))
            await wait_until(lambda: host.familiars._closed, what="shutting down starting")
        return await real_transition(commission_id, status, **kwargs)

    monkeypatch.setattr(host.ledger, "transition", shut_down_meanwhile)

    assert await host.familiars.tell(commission.id, "the drive is E: now") is False

    await asyncio.wait_for(shutting[0], 5)
    after = await host.ledger.get(commission.id)
    assert (after.status, after.status_reason) == ("failed", "the host was shut down")
    assert host.familiars.queued == []


async def test_what_is_said_to_a_queued_retry_survives_its_old_session_closing(hosts, tmp_path):
    llm = ScriptedLLM([[say("photos sorted"), finish()]])
    host = _host(hosts, tmp_path, lambda spec: llm)
    commission = await _blocked_with_closing_session(host, "sort the photos")

    assert await host.familiars.tell(commission.id, "the drive is E: now") is True
    assert (await host.ledger.get(commission.id)).status == "queued"
    assert await host.familiars.tell(commission.id, "and skip the screenshots") is True
    # Now the old session is gone and the retry takes its place.
    await host.familiars._release(commission.id)

    await wait_status(host, commission.id, "done", timeout=5)
    sent = "\n".join(str(message.get("content")) for message in llm.requests[0])
    assert "the drive is E: now" in sent
    assert "and skip the screenshots" in sent


async def test_a_retry_waiting_for_a_place_can_be_stopped(hosts, tmp_path):
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
    second = await _filed(host, "back up the notes")
    await host.ledger.transition(second.id, "working", reason="test")
    await host.ledger.transition(second.id, "failed", reason="test")

    # No place is free: the retry waits, and says so.
    assert await host.familiars.tell(second.id, "try drive E: this time") is True
    assert (await host.ledger.get(second.id)).status == "queued"
    assert host.familiars.queued == [second.id]

    stopped = await host.abort(second.id)
    assert stopped is not None and stopped.status == "cancelled"
    release.set()
    await wait_status(host, first.commission_id, "done")
    await host.brain.settle()
    assert (await host.ledger.get(second.id)).status == "cancelled"
    assert scripts["back up the notes"].requests == []


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


async def test_a_start_shutdown_gives_up_on_leaves_no_commission_working(tmp_path, hosts, monkeypatch):
    from nekoclaw.host import familiars as familiars_module

    inner = scripted_runtime(lambda spec: ScriptedLLM([_report("ready", "sorted")]))
    starting = asyncio.Event()

    class NeverStarts:
        """A runtime whose session never comes up."""

        def __init__(self, on_event: Any, callbacks: Any) -> None:
            self._runtime = inner(on_event, callbacks)

        async def start(self, spec: Any) -> None:
            starting.set()
            await asyncio.Event().wait()

        def __getattr__(self, name: str) -> Any:
            return getattr(self._runtime, name)

    monkeypatch.setattr(familiars_module, "_SHUTDOWN_START_WAIT_SECONDS", 0.2)
    host = make_host(tmp_path, None, familiar_model=fake_model, decision_llm=FakeDecisionLLM(), runtime_factory=NeverStarts)
    hosts.append(host)
    receipt = await _dispatch(host, "sort the photos")
    await asyncio.wait_for(starting.wait(), 5)
    # Recorded as working before the runtime was asked to start the session.
    assert (await host.ledger.get(receipt.commission_id)).status == "working"

    await host.familiars.shutdown()

    failed = await host.ledger.get(receipt.commission_id)
    assert (failed.status, failed.status_reason) == ("failed", "the host was shut down")


async def test_starting_a_queued_commission_does_not_hold_up_runtime_events(tmp_path, hosts):
    inner = scripted_runtime(lambda spec: ScriptedLLM([_report("ready", "done")]))
    may_start = asyncio.Event()
    second_starting = asyncio.Event()
    starts: list[str] = []

    class SlowSecondStart:
        """A runtime whose second session takes a while to come up."""

        def __init__(self, on_event: Any, callbacks: Any) -> None:
            self._runtime = inner(on_event, callbacks)

        async def start(self, spec: Any) -> None:
            starts.append(spec.session_id)
            if len(starts) == 2:
                second_starting.set()
                await may_start.wait()
            await self._runtime.start(spec)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._runtime, name)

    host = make_host(
        tmp_path,
        None,
        familiar_model=fake_model,
        decision_llm=FakeDecisionLLM(),
        runtime_factory=SlowSecondStart,
        max_familiars=1,
    )
    hosts.append(host)
    first = await _filed(host, "sort the photos")
    second = await _filed(host, "back up the notes")
    await host.familiars.submit(first)
    await host.familiars.submit(second)
    assert host.familiars.queued == [second.id]

    # The first one ends; its place goes to the second, whose session is slow to come up.
    await asyncio.wait_for(second_starting.wait(), 5)

    # Meanwhile the events that arrived are still handled.
    await asyncio.wait_for(host.familiars.settle(), 2)
    assert (await host.ledger.get(first.id)).status == "done"
    # The start is one that shutting down waits for.
    assert host.familiars._starting

    may_start.set()
    done = await wait_status(host, second.id, "done")
    assert done.result_summary == "done"
