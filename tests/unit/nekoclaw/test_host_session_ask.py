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

"""One session asking another a question (``ask_session``).

A copy of the other session's conversation is made and answers in a
commission under the one that asked; the answer comes back to the asker
framed, and the other session is not disturbed. What a session may ask is
counted with the notes it writes and on its own.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from config.prompts import prompts_nekoclaw_sessions as words
from nekoclaw.host.task_agents import ForkRefused
from tests.unit.nekoclaw.fakes import ScriptedLLM, call, finish, say, usage
from tests.unit.nekoclaw.host_fakes import (
    FakeDecisionLLM,
    fake_model,
    make_host,
    scripted_runtime,
    wait_status,
    wait_until,
)

PHOTOS = "sort the photos"
BUILD = "check the build log"
QUESTION = "Which year folders did you make, and where?"


@pytest.fixture
async def hosts() -> Any:
    made: list[Any] = []
    yield made
    for host in made:
        await host.shutdown()


class _ByWhatWasSaid:
    """A session's model that answers from the script of what was said to it.

    The last thing said to the session (not what its model wrote, nor what
    tools gave back) that names one of the keys decides, the first key in
    ``scripts`` before the others: the message that opens a copy names the
    session that asks as well as the question.
    """

    def __init__(self, scripts: dict[str, ScriptedLLM]) -> None:
        self._scripts = scripts

    def _key(self, messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            text = json.dumps(message.get("content"), ensure_ascii=False)
            for key in self._scripts:
                if key in text:
                    return key
        raise AssertionError(f"no script for a session asked: {messages[-1].get('content')!r}")

    async def astream(self, messages: list[dict[str, Any]], **overrides: Any):  # noqa: ANN201
        async for chunk in self._scripts[self._key(messages)].astream(messages, **overrides):
            yield chunk

    async def aclose(self) -> None:
        return None


def _host(hosts: list[Any], tmp_path: Path, scripts: dict[str, ScriptedLLM], **kwargs: Any) -> Any:
    kwargs.setdefault("task_agent_model", fake_model)
    kwargs.setdefault("decision_llm", FakeDecisionLLM())
    host = make_host(tmp_path, None, runtime_factory=scripted_runtime(lambda spec: _ByWhatWasSaid(scripts)), **kwargs)
    hosts.append(host)
    return host


async def _dispatch(host: Any, text: str) -> Any:
    return await host.dispatch(text, lanlan_name="Lan", lang="en")


def _rests(note: str = "look at the upload again") -> list[Any]:
    return [call("schedule_wake", {"delay_seconds": 3600, "note": note}), finish("tool_calls")]


def _photos() -> ScriptedLLM:
    """The photos are sorted, and the commission rests: it waits for an upload."""
    return ScriptedLLM([[say("Sorted 214 photos by year."), *_rests()], [say("Waiting for the upload."), finish()]])


async def _resting(host: Any, commission_id: str) -> bool:
    commission = await host.ledger.get(commission_id)
    return commission is not None and commission.resting


def _said(request: list[dict[str, Any]]) -> list[str]:
    return [str(message["content"]) for message in request if message.get("role") == "user"]


async def _at_rest(host: Any, scripts: dict[str, ScriptedLLM]) -> Any:
    """Dispatch the photos and wait until that commission rests: it is under way, with nobody at work on it."""
    photos = (await _dispatch(host, PHOTOS)).commission_id
    await wait_until(lambda: _resting(host, photos), what="the photos resting")
    await wait_until(lambda: photos not in host.task_agents._held_by.values(), what="the photos letting go")
    return await host.ledger.get(photos)


async def test_a_session_is_asked_a_question_that_a_copy_of_it_answers_and_it_is_not_disturbed(hosts, tmp_path):
    scripts = {
        # The copy's first: its opening message names the session that asks as well.
        QUESTION: ScriptedLLM([[say("2019, 2020 and 2021, under photos/sorted."), usage(1_200, 30), finish()]]),
        PHOTOS: _photos(),
        BUILD: ScriptedLLM(
            [
                [call("ask_session", {"session": "photos", "question": QUESTION}), finish("tool_calls")],
                [say("Asked; waiting for the answer."), finish()],
                [call("report", {"status": "ready", "summary": "the folders are 2019 to 2021"}), finish("tool_calls")],
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    photos = await _at_rest(host, scripts)
    await host.ledger.update_session(photos.session_id, name="photos")
    before = (Path((await host.ledger.get_session(photos.session_id)).session_dir) / "transcript.jsonl").read_bytes()
    # What the runtime is handed, and whether as the user's words.
    runtime = host.task_agents._runtimes.builtin()
    handed: list[tuple[str, bool]] = []
    for command in ("prompt", "steer"):

        def noting(original: Any) -> Any:
            async def send(session_id: str, text: str, **options: Any) -> Any:
                handed.append((text, options.get("human", True)))
                return await original(session_id, text, **options)

            return send

        setattr(runtime, command, noting(getattr(runtime, command)))
    requests_before = len(scripts[PHOTOS].requests)

    asker = (await _dispatch(host, BUILD)).commission_id
    done = await wait_status(host, asker, "done", "failed", "blocked")
    assert (done.status, done.result_summary) == ("done", "the folders are 2019 to 2021")

    # The question was answered in a commission under the one that asked, worked on in a copy.
    [child] = await host.ledger.select(parent_id=asker)
    assert (child.source, child.status, child.name) == ("ask", "done", "ask-1")
    assert child.title == 'Question to "photos"' and child.dispatch_text == QUESTION
    copy = await host.ledger.get_session(child.session_id)
    assert (copy.kind, copy.forked_from, copy.workdir) == ("fork", photos.session_id, photos.workdir)

    # The copy begins as the other session's requests did, and is told who asks and what, framed.
    copied = scripts[QUESTION].requests[0]
    assert copied[0] == scripts[PHOTOS].requests[0][0]
    opening = _said(copied)[-1]
    assert opening.startswith('Another session asks you a question: "') and "at most 8 model requests" in opening
    assert f"\n    {QUESTION}\n" in opening + "\n"
    assert any("Sorted 214 photos by year." in str(message.get("content")) for message in copied)
    # Not as the user's words: the copy is given no goal on its account (see the runtime).

    # The answer reached the asker framed, named by the session that was asked.
    answered = _said(scripts[BUILD].requests[2])[-1]
    assert answered.startswith('[answer from "photos": completed]')
    assert "\n    2019, 2020 and 2021, under photos/sorted." in answered
    assert "that session itself was not told" in answered
    # Handed to the asker as no word of the user's: it entitles its run to nothing the user's
    # words would (setting a goal, say). So is the question handed to the copy.
    [answer_was_human] = [human for text, human in handed if text.startswith('[answer from "photos"')]
    [question_was_human] = [human for text, human in handed if text.startswith("Another session asks you a question")]
    assert answer_was_human is False and question_was_human is False

    # The other session is as it was: still resting, nothing said to it, its record untouched.
    assert (await host.ledger.get(photos.id)).resting
    assert len(scripts[PHOTOS].requests) == requests_before
    after = (Path((await host.ledger.get_session(photos.session_id)).session_dir) / "transcript.jsonl").read_bytes()
    assert after == before
    # On record, to be counted: a question, never handed over.
    asked = await host.ledger.run(
        lambda conn: [dict(row) for row in conn.execute("SELECT kind, status, to_session FROM messages")]
    )
    assert asked == [{"kind": "ask", "status": "delivered", "to_session": photos.session_id}]
    # What the copy used counts with it, with the commission that asked, and not with the other session.
    child = await host.ledger.get(child.id)
    assert (child.requests, child.tokens) == (1, 1_230)
    assert (await host.ledger.get(asker)).tokens >= 1_230
    assert (await host.ledger.get_session(photos.session_id)).tokens == (await host.ledger.get(photos.id)).tokens


async def test_the_copy_that_answers_has_nothing_that_writes_or_reaches_other_sessions(hosts, tmp_path, monkeypatch):
    specs: list[Any] = []
    scripts = {
        QUESTION: ScriptedLLM(
            [
                [
                    call("write", {"file_path": "notes.txt", "content": "x"}, "call-w"),
                    call("message_session", {"to": "photos", "message": "hi"}, "call-m", index=1),
                    call("ask_session", {"session": "photos", "question": "again?"}, "call-a", index=2),
                    finish("tool_calls"),
                ],
                [say("I can only read."), finish()],
            ]
        ),
        PHOTOS: _photos(),
        BUILD: ScriptedLLM(
            [
                [call("ask_session", {"session": "photos", "question": QUESTION}), finish("tool_calls")],
                [say("Waiting."), finish()],
                [call("report", {"status": "ready", "summary": "asked"}), finish("tool_calls")],
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    spec_of = host.task_agents._spec

    async def noting(*args: Any, **kwargs: Any) -> Any:
        spec = await spec_of(*args, **kwargs)
        specs.append(spec)
        return spec

    monkeypatch.setattr(host.task_agents, "_spec", noting)
    photos = await _at_rest(host, scripts)
    await host.ledger.update_session(photos.session_id, name="photos")
    requests_before = len(scripts[PHOTOS].requests)
    asker = (await _dispatch(host, BUILD)).commission_id
    await wait_status(host, asker, "done", "failed", "blocked")
    [child] = await host.ledger.select(parent_id=asker)
    [spec] = [item for item in specs if item.session_id == child.session_id]
    assert {"write", "edit", "subagent", "message_session", "schedule_wake", "ask_session"} <= set(spec.disabled_tools)
    assert spec.limits.max_turns == 8 and spec.top_level is False
    # Every other session is opened as before.
    assert all(item.limits.max_turns is None for item in specs if item.session_id != child.session_id)
    events = [event.payload for event in await host.ledger.events(child.id) if event.type == "tool_end"]
    assert all(event["is_error"] for event in events) and len(events) == 3
    assert not (Path(photos.workdir) / "notes.txt").exists()
    # Nothing reached the other session.
    assert len(scripts[PHOTOS].requests) == requests_before


async def test_a_copy_that_uses_up_its_requests_ends_and_the_asker_is_told(hosts, tmp_path, monkeypatch):
    monkeypatch.setattr("config.nekoclaw_settings.SESSION_ASK_MAX_REQUESTS", 2)
    reading = [[call("glob", {"pattern": "*"}, f"call-{index}"), finish("tool_calls")] for index in range(5)]
    scripts = {
        QUESTION: ScriptedLLM(reading),
        PHOTOS: _photos(),
        BUILD: ScriptedLLM(
            [
                [call("ask_session", {"session": "photos", "question": QUESTION}), finish("tool_calls")],
                [say("Waiting."), finish()],
                [call("report", {"status": "blocked", "summary": "no answer"}), finish("tool_calls")],
            ]
        ),
    }
    host = _host(hosts, tmp_path, scripts)
    photos = await _at_rest(host, scripts)
    await host.ledger.update_session(photos.session_id, name="photos")
    asker = (await _dispatch(host, BUILD)).commission_id
    await wait_status(host, asker, "done", "failed", "blocked")
    [child] = await host.ledger.select(parent_id=asker)
    assert child.status in ("failed", "blocked", "done")
    # No more requests than it may make.
    assert len(scripts[QUESTION].requests) == 2
    told = _said(scripts[BUILD].requests[2])[-1]
    assert told.startswith('[answer from "photos": ')


def _held() -> tuple[asyncio.Event, ScriptedLLM]:
    """The build's model, whose first request is answered once the event is set: until then the build is at work."""
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    return release, ScriptedLLM([[wait, say("Done."), finish()]])


async def _asker_at_work(host: Any, scripts: dict[str, ScriptedLLM]) -> Any:
    """A commission of the build that is at work and asks nothing by itself: the callback is called by the test."""
    asker = (await _dispatch(host, BUILD)).commission_id
    def ready() -> bool:
        session = host.task_agents._sessions.get(asker)
        return session is not None and session.ready

    await wait_until(ready, what="the build at work")
    return await wait_status(host, asker, "working")


def _ask(host: Any, asker: Any, session: str = "photos", question: str = QUESTION) -> Any:
    return host.task_agents._cb_session_ask(asker.session_id, {"session": session, "question": question})


async def test_what_cannot_be_asked_is_refused_with_what_to_do_instead(hosts, tmp_path, monkeypatch):
    from config import nekoclaw_settings

    release, held = _held()
    scripts = {
        PHOTOS: _photos(),
        BUILD: held,
        "paint the fence": ScriptedLLM([[call("report", {"status": "ready", "summary": "painted"}), finish("tool_calls")]]),
    }
    host = _host(hosts, tmp_path, scripts)
    photos = await _at_rest(host, scripts)
    await host.ledger.update_session(photos.session_id, name="photos")
    fence = await wait_status(host, (await _dispatch(host, "paint the fence")).commission_id, "done")
    await host.ledger.update_session(fence.session_id, name="fence")
    asker = await _asker_at_work(host, scripts)
    refusals = words.ASK_REFUSALS

    async def refused(*args: Any, **kwargs: Any) -> str:
        with pytest.raises(RuntimeError) as raised:
            await _ask(host, asker, *args, **kwargs)
        return str(raised.value)

    # A task agent another one started asks nobody: the one that started it does.
    part, _created = await host.ledger.create(
        lanlan_name="Lan", title="part", dispatch_text="part", source="task_agent", route="task_agent",
        parent_id=asker.id, name="part",
    )
    part, _session = await host.ledger.give_session(part.id, kind="child", name="build/part")
    with pytest.raises(RuntimeError) as raised:
        await host.task_agents._cb_session_ask(part.session_id, {"session": "photos", "question": QUESTION})
    assert str(raised.value) == refusals["from_child"]
    # Itself; nobody of that name; a session with nothing under way.
    assert await refused((await host.ledger.get_session(asker.session_id)).name) == refusals["self"]
    assert "No session is called" in await refused("nobody")
    assert await refused("fence") == refusals["not_at_work"].format(name="fence")
    # Too long a question.
    long = " ".join(["word"] * (nekoclaw_settings.SESSION_MESSAGE_MAX_TOKENS + 10))
    assert (await refused("photos", long)).startswith("Not asked: the question is ")
    # A session of dsh; a session whose record was removed; one that is a copy or a helper.
    await host.ledger.update_session(photos.session_id, runtime="dsh")
    assert await refused("photos") == refusals["dsh"].format(name="photos")
    await host.ledger.update_session(photos.session_id, runtime="builtin", status="cleared")
    assert await refused("photos") == refusals["cleared"].format(name="photos")
    def kind_of(kind: str) -> Any:
        return host.ledger.run(
            lambda conn: conn.execute("UPDATE sessions SET kind = ? WHERE id = ?", (kind, photos.session_id))
        )

    await host.ledger.update_session(photos.session_id, status="closed")
    await kind_of("fork")
    assert await refused("photos") == refusals["child"].format(name="photos")
    # Nor is a copy written to: it answers one question and takes no notes.
    with pytest.raises(RuntimeError) as raised:
        await host.task_agents._cb_session_send(asker.session_id, {"to": "photos", "message": "hi"})
    assert str(raised.value) == words.MESSAGE_REFUSALS["fork"].format(name="photos")
    await kind_of("single")
    # A copy that cannot be made: said why.
    async def no_copy(session_id: str, **kwargs: Any) -> Any:
        raise ForkRefused("the runtime did not answer")

    monkeypatch.setattr(host.task_agents, "fork_session", no_copy)
    assert await refused("photos") == refusals["no_copy"].format(name="photos", reason="the runtime did not answer")
    # Switched off.
    monkeypatch.setattr(host.task_agents, "available", lambda: _answer((False, "off")))
    assert await refused("photos") == refusals["switched_off"]
    monkeypatch.undo()
    # Nor where the user forbade reading outside the working directory: the copy answers from
    # the other session's record, which ``read_session`` would not read either.
    from nekoclaw.host.permissions import DENIED_BY_SETTINGS

    await host.permission_store.set_mode("outside_read", "deny")
    copies = len(await host.ledger.find_sessions(kind="fork"))
    assert await refused("photos") == DENIED_BY_SETTINGS
    assert len(await host.ledger.find_sessions(kind="fork")) == copies
    release.set()


async def _answer(value: Any) -> Any:
    return value


async def test_a_session_asks_no_more_than_so_many_questions_an_hour_counted_with_its_notes(hosts, tmp_path, monkeypatch):
    monkeypatch.setattr("config.nekoclaw_settings.SESSION_ASKS_PER_HOUR", 2)
    monkeypatch.setattr("config.nekoclaw_settings.SESSION_MESSAGES_PER_HOUR", 3)
    monkeypatch.setattr("config.nekoclaw_settings.SESSION_MESSAGE_TARGET_INTERVAL_SECONDS", 0.0)
    release, held = _held()
    scripts = {
        QUESTION: ScriptedLLM([[say("An answer."), finish()]] * 4),
        PHOTOS: _photos(),
        BUILD: held,
        "paint the fence": ScriptedLLM([[say("Painting."), *_rests()]]),
    }
    host = _host(hosts, tmp_path, scripts, max_task_agents=6)
    photos = await _at_rest(host, scripts)
    await host.ledger.update_session(photos.session_id, name="photos")
    fence = (await _dispatch(host, "paint the fence")).commission_id
    await wait_until(lambda: _resting(host, fence), what="the fence resting")
    await host.ledger.update_session((await host.ledger.get(fence)).session_id, name="fence")
    asker = await _asker_at_work(host, scripts)

    forks: list[str] = []
    fork = host.task_agents.fork_session

    async def counted(session_id: str, **kwargs: Any) -> Any:
        forks.append(session_id)
        if len(forks) == 1:
            raise ForkRefused("not made in this test")
        return await fork(session_id, **kwargs)

    monkeypatch.setattr(host.task_agents, "fork_session", counted)
    # One that is not asked after all (no copy could be made) counts for nothing.
    with pytest.raises(RuntimeError, match="no copy"):
        await _ask(host, asker)
    assert (await _ask(host, asker))["message"].startswith('Asked "photos"')
    assert (await _ask(host, asker, "fence"))["message"].startswith('Asked "fence"')
    # Two questions within the hour: the third is not asked, and no copy is made for it.
    with pytest.raises(RuntimeError) as raised:
        await _ask(host, asker, "fence")
    assert str(raised.value).startswith("Not asked: this session has asked 2 questions within the hour.")
    assert len(forks) == 3
    # The questions count with the notes: one note more, and the hour's three are used up.
    await host.task_agents._cb_session_send(asker.session_id, {"to": "fence", "message": "the build is green"})
    with pytest.raises(RuntimeError) as raised:
        await host.task_agents._cb_session_send(asker.session_id, {"to": "fence", "message": "and deployed"})
    assert str(raised.value).startswith("Not sent: this session has written 3 notes and questions within the hour.")
    release.set()


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def ledger(tmp_path: Any) -> Any:
    from nekoclaw.host.ledger import Ledger

    clock = _Clock()
    book = Ledger(tmp_path / "nekoclaw" / "ledger.db", clock=clock)
    book.clock = clock  # type: ignore[attr-defined]
    yield book
    await book.close()


async def _at_work(ledger: Any, title: str) -> Any:
    commission, _created = await ledger.create(
        lanlan_name="Lan", title=title, dispatch_text=title, source="dispatch", route="task_agent"
    )
    commission, _session = await ledger.give_session(commission.id, kind="single", name=title)
    await ledger.transition(commission.id, "working")
    return await ledger.get(commission.id)


def _asking(ledger: Any, asker: Any, target: Any, body: str = "which port?", **limits: Any) -> Any:
    values = {"per_hour": 3, "asks_per_hour": 2, **limits}
    return ledger.ask_add(
        from_session=asker.session_id, from_commission=asker.id, to_session=target.session_id, body=body, **values
    )


async def test_a_question_is_on_record_as_delivered_and_nothing_hands_it_over(ledger):
    asker, target = await _at_work(ledger, "asker"), await _at_work(ledger, "target")

    asked, refusal, wait = await _asking(ledger, asker, target)

    assert (refusal, wait) == ("", 0.0)
    assert (asked.kind, asked.status, asked.delivered_at, asked.from_commission) == ("ask", "delivered", 1000.0, asker.id)
    assert await ledger.wakes_pending(session_id=target.session_id) == []
    assert [event.type for event in await ledger.events(asker.id)][-1] == "session_asked"


async def test_what_a_session_may_ask_is_counted_by_the_hour_with_its_notes(ledger):
    from nekoclaw.host.ledger import MessageLimits

    asker, target = await _at_work(ledger, "asker"), await _at_work(ledger, "target")
    limits = MessageLimits(target_interval=0.0, per_hour=3, queue_max=10, dedup_seconds=0.0)

    for _ in range(2):
        assert (await _asking(ledger, asker, target))[1] == ""
    ledger.clock.now += 600
    # A third question within the hour is not asked: ``wait`` is until the first is an hour old.
    assert await _asking(ledger, asker, target) == (None, "too_many_asks", 3000.0)
    # A note still goes, and with it the three of the hour are used up: no question and no note more.
    note, refusal, _wait = await ledger.message_add(
        from_session=asker.session_id, from_commission=asker.id, to_session=target.session_id, body="hello",
        depth=1, notify_when_done=False, notice_after=3600.0, limits=limits,
    )
    assert note is not None and refusal == ""
    assert (await _asking(ledger, asker, target, asks_per_hour=10))[1] == "too_many"
    refused = await ledger.message_add(
        from_session=asker.session_id, from_commission=asker.id, to_session=target.session_id, body="again",
        depth=1, notify_when_done=False, notice_after=3600.0, limits=limits,
    )
    assert refused[1] == "too_many"
    # Once the first is more than an hour old, it may ask again.
    ledger.clock.now += 3001
    assert (await _asking(ledger, asker, target))[1] == ""


async def test_a_question_that_was_not_asked_after_all_counts_for_nothing(ledger):
    asker, target = await _at_work(ledger, "asker"), await _at_work(ledger, "target")
    first, _refusal, _wait = await _asking(ledger, asker, target)
    await ledger.message_void(first.id, "not asked after all")
    # Two may be asked in the hour: the one that was not asked leaves both.
    assert (await _asking(ledger, asker, target))[1] == ""
    assert (await _asking(ledger, asker, target))[1] == ""
    assert (await _asking(ledger, asker, target))[1] == "too_many_asks"
    assert (await ledger.message_get(first.id)).status == "void"


async def test_a_commission_that_is_over_asks_nothing(ledger):
    asker, target = await _at_work(ledger, "asker"), await _at_work(ledger, "target")
    await ledger.transition(asker.id, "done")

    assert await _asking(ledger, asker, target) == (None, "over", 0.0)
    rows = await ledger.run(lambda conn: conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
    assert rows == 0


async def test_an_asker_stopped_while_the_copy_is_made_has_nothing_started_for_it(hosts, tmp_path, monkeypatch):
    release, held = _held()
    scripts = {QUESTION: ScriptedLLM([[say("An answer."), finish()]]), PHOTOS: _photos(), BUILD: held}
    host = _host(hosts, tmp_path, scripts)
    photos = await _at_rest(host, scripts)
    await host.ledger.update_session(photos.session_id, name="photos")
    asker = await _asker_at_work(host, scripts)
    fork = host.task_agents.fork_session

    async def stopped_meanwhile(session_id: str, **kwargs: Any) -> Any:
        copy = await fork(session_id, **kwargs)
        await host.ledger.transition(asker.id, "cancelled", reason="stopped by the user")
        return copy

    monkeypatch.setattr(host.task_agents, "fork_session", stopped_meanwhile)
    with pytest.raises(RuntimeError) as raised:
        await _ask(host, asker)
    assert str(raised.value) == words.ASK_REFUSALS["over"]
    [child] = await host.ledger.select(parent_id=asker.id)
    assert (child.status, child.source) == ("cancelled", "ask")
    assert child.id not in host.task_agents._sessions and scripts[QUESTION].requests == []
    # Nor is the copy made for it kept.
    copy = await host.ledger.get_session(child.session_id)
    assert copy.status == "cleared" and not Path(copy.session_dir).exists()
    release.set()


async def test_a_question_whose_asker_is_cancelled_while_it_is_filed_is_answered_all_the_same(hosts, tmp_path):
    # A tool call of dsh that is given up cancels the callback. The question is filed, given
    # the copy and handed over all the same: left queued, nobody would start it, and the asker
    # would wait for its answer for ever.
    release, held = _held()
    scripts = {QUESTION: ScriptedLLM([[say("An answer."), finish()]]), PHOTOS: _photos(), BUILD: held}
    host = _host(hosts, tmp_path, scripts)
    photos = await _at_rest(host, scripts)
    await host.ledger.update_session(photos.session_id, name="photos")
    asker = await _asker_at_work(host, scripts)
    append, entered, go_on = host.ledger.append_event, asyncio.Event(), asyncio.Event()

    async def slow(commission_id: str, kind: str, payload: Any = None) -> Any:
        if kind == "subagent_started":
            entered.set()
            await go_on.wait()
        return await append(commission_id, kind, payload)

    host.ledger.append_event = slow  # type: ignore[method-assign]
    told = _steered_into(host, asker.id)
    asking = asyncio.ensure_future(_ask(host, asker))
    await asyncio.wait_for(entered.wait(), 5)
    asking.cancel()
    await asyncio.gather(asking, return_exceptions=True)
    go_on.set()
    await wait_until(lambda: _children_of(host, asker.id), what="the question being filed")
    [child] = await host.ledger.select(parent_id=asker.id)
    child = await wait_status(host, child.id, "done", "failed", "cancelled", "blocked")
    assert child.status == "done" and child.session_id
    host.ledger.append_event = append  # type: ignore[method-assign]
    # And the answer reached the asker.
    await wait_until(lambda: any("An answer." in text for text in told), what="the answer reaching the asker")
    assert sum('[answer from "photos": completed]' in text for text in told) == 1
    release.set()


async def test_a_question_that_cannot_be_handed_over_is_ended_and_not_left_waiting(hosts, tmp_path):
    release, held = _held()
    scripts = {QUESTION: ScriptedLLM([[say("An answer."), finish()]]), PHOTOS: _photos(), BUILD: held}
    host = _host(hosts, tmp_path, scripts)
    photos = await _at_rest(host, scripts)
    await host.ledger.update_session(photos.session_id, name="photos")
    asker = await _asker_at_work(host, scripts)
    append = host.ledger.append_event

    async def failing(commission_id: str, kind: str, payload: Any = None) -> Any:
        if kind == "subagent_started":
            raise OSError("the disk is full")
        return await append(commission_id, kind, payload)

    host.ledger.append_event = failing  # type: ignore[method-assign]
    told = _steered_into(host, asker.id)
    with pytest.raises(OSError):
        await _ask(host, asker)
    host.ledger.append_event = append  # type: ignore[method-assign]
    [child] = await host.ledger.select(parent_id=asker.id)
    assert (child.status, child.status_reason) == (
        "failed",
        "the question could not be handed to the copy that was to answer it",
    )
    assert scripts[QUESTION].requests == []
    # The asker is told, as of any task agent of its that ends: one that ended its turn to wait
    # for the answer is not left waiting.
    await wait_until(lambda: told, what="the asker being told")
    [text] = told
    assert text.startswith('[answer from "photos": failed]') and "could not be handed to the copy" in text
    # Nor is the copy made for it kept: nothing will be said in it.
    copy = await host.ledger.get_session(child.session_id)
    assert copy.status == "cleared" and not Path(copy.session_dir).exists()
    release.set()


def _steered_into(host: Any, commission_id: str) -> list[str]:
    """Note what is steered into a commission's session from now on."""
    session = host.task_agents._sessions[commission_id]
    steer = session.runtime.steer
    told: list[str] = []

    async def steering(session_id: str, text: str, **options: Any) -> Any:
        if session_id == session.session_id:
            told.append(text)
        return await steer(session_id, text, **options)

    session.runtime.steer = steering
    return told


async def _children_of(host: Any, commission_id: str) -> bool:
    return bool(await host.ledger.select(parent_id=commission_id))


async def test_an_answer_that_takes_its_asker_up_again_is_handed_over_as_no_word_of_the_users(hosts, tmp_path):
    # The asker's session was closed while it waited (an idle runtime closes idle sessions):
    # the answer takes it up again, and opens its next turn as no word of the user's either.
    from nekoclaw.host.task_agents import _Unsaid

    scripts = {PHOTOS: _photos()}
    host = _host(hosts, tmp_path, scripts)
    photos = await _at_rest(host, scripts)
    await wait_until(lambda: photos.id not in host.task_agents._sessions, what="its session closing")
    runtime = host.task_agents._runtimes.builtin()
    handed: list[tuple[str, bool]] = []
    prompt = runtime.prompt

    async def noting(session_id: str, text: str, **options: Any) -> Any:
        handed.append((text, options.get("human", True)))
        return await prompt(session_id, text, **options)

    runtime.prompt = noting
    answer = _Unsaid('[answer from "build": completed] It is under dist/.')
    assert await host.task_agents._wake_working(photos.id, answer, reason="an answer takes it up again") is True
    await wait_until(lambda: handed, what="it being taken up again")
    [(text, human)] = handed
    assert answer in text and human is False


async def test_with_the_clock_set_back_the_wait_is_until_enough_of_the_hour_is_old(ledger):
    # Written, the clock moved on, written again, and set back: more than the hour allows are
    # in it. Asked again once the first of them is an hour old, as many as allowed would still
    # be there: the wait is until enough of them are.
    from nekoclaw.host.ledger import MessageLimits

    asker, target = await _at_work(ledger, "asker"), await _at_work(ledger, "target")
    limits = MessageLimits(target_interval=0.0, per_hour=3, queue_max=100, dedup_seconds=0.0)

    async def note(body: str) -> Any:
        return await ledger.message_add(
            from_session=asker.session_id, from_commission=asker.id, to_session=target.session_id, body=body,
            depth=1, notify_when_done=False, notice_after=3600.0, limits=limits,
        )

    for index in range(3):
        assert (await note(f"early {index}"))[1] == ""
    ledger.clock.now = 5000.0
    for index in range(3):
        assert (await note(f"late {index}"))[1] == ""
    ledger.clock.now = 2000.0
    # Six in the hour around now. Once the three at 1000 are an hour old, the three at 5000
    # still are: the wait is until the first of those is.
    message, refusal, wait = await note("again")
    assert (message, refusal) == (None, "too_many")
    assert wait == 3600.0  # (5000 + 3600 - 2000, at most an hour)
    # Exactly as many as allowed: until the first of them is an hour old.
    ledger.clock.now = 5100.0
    assert await note("once more") == (None, "too_many", 3500.0)


async def test_no_copy_is_made_and_nothing_counted_where_no_place_is_free_for_it(hosts, tmp_path):
    release, held = _held()
    scripts = {QUESTION: ScriptedLLM([[say("An answer."), finish()]]), PHOTOS: _photos(), BUILD: held}
    host = _host(hosts, tmp_path, scripts, max_task_agents=1)
    photos = await _at_rest(host, scripts)
    await host.ledger.update_session(photos.session_id, name="photos")
    asker = await _asker_at_work(host, scripts)
    # The asker holds the one place there is.
    with pytest.raises(RuntimeError) as raised:
        await _ask(host, asker)
    assert str(raised.value) == words.ASK_REFUSALS["no_place"]
    assert await host.ledger.find_sessions(kind="fork") == []
    asked = await host.ledger.run(lambda conn: conn.execute("SELECT COUNT(*) FROM messages WHERE kind = 'ask'").fetchone()[0])
    assert asked == 0
    release.set()


async def test_a_copy_whose_place_was_taken_while_it_was_made_is_removed(hosts, tmp_path):
    # A place was free when the question was asked, and was taken while the copy was made: the
    # question is not handed over, and the copy made for it is removed at once.
    release, held = _held()
    scripts = {QUESTION: ScriptedLLM([[say("An answer."), finish()]]), PHOTOS: _photos(), BUILD: held}
    host = _host(hosts, tmp_path, scripts, max_task_agents=2)
    photos = await _at_rest(host, scripts)
    await host.ledger.update_session(photos.session_id, name="photos")
    asker = await _asker_at_work(host, scripts)
    manager = host.task_agents
    fork = manager.fork_session

    async def and_the_place_is_taken(session_id: str, **kwargs: Any) -> Any:
        copy = await fork(session_id, **kwargs)
        manager._max_concurrent = 1
        return copy

    manager.fork_session = and_the_place_is_taken
    told = _steered_into(host, asker.id)
    with pytest.raises(RuntimeError) as raised:
        await _ask(host, asker)
    assert str(raised.value) == words.ASK_REFUSALS["no_place"]
    # Told so by the call alone: no answer of a copy comes besides.
    await asyncio.sleep(0.1)
    assert told == []
    # Nor does it count against what may be asked.
    asked = await host.ledger.run(
        lambda conn: [tuple(row) for row in conn.execute("SELECT kind, status FROM messages WHERE kind = 'ask'")]
    )
    assert asked == [("ask", "void")]
    [child] = await host.ledger.select(parent_id=asker.id)
    assert child.status == "failed"
    copy = await host.ledger.get_session(child.session_id)
    assert copy.status == "cleared" and not Path(copy.session_dir).exists()
    # The session it was copied from is as it was.
    assert (await host.ledger.get_session(photos.session_id)).status != "cleared"
    assert scripts[QUESTION].requests == []
    release.set()


async def test_a_copy_whose_files_cannot_be_removed_is_not_put_on_record_as_cleared(hosts, tmp_path, monkeypatch):
    # Put on record as cleared, a copy whose files are still there would be passed over by the
    # removal of old sessions for good. It stays as it is, for that removal.
    import shutil

    release, held = _held()
    scripts = {QUESTION: ScriptedLLM([[say("An answer."), finish()]]), PHOTOS: _photos(), BUILD: held}
    host = _host(hosts, tmp_path, scripts, max_task_agents=2)
    photos = await _at_rest(host, scripts)
    await host.ledger.update_session(photos.session_id, name="photos")
    asker = await _asker_at_work(host, scripts)
    manager = host.task_agents
    fork = manager.fork_session

    async def and_the_place_is_taken(session_id: str, **kwargs: Any) -> Any:
        copy = await fork(session_id, **kwargs)
        manager._max_concurrent = 1
        return copy

    manager.fork_session = and_the_place_is_taken
    monkeypatch.setattr(shutil, "rmtree", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError):
        await _ask(host, asker)
    [child] = await host.ledger.select(parent_id=asker.id)
    copy = await host.ledger.get_session(child.session_id)
    assert copy.status != "cleared" and Path(copy.session_dir).exists()
    release.set()


async def test_a_copy_that_cannot_be_started_is_said_so_and_removed(hosts, tmp_path):
    # Not for want of a place: the call says why, in the words the asker is told by the answer
    # ``submit`` hands back, and the copy goes.
    release, held = _held()
    scripts = {QUESTION: ScriptedLLM([[say("An answer."), finish()]]), PHOTOS: _photos(), BUILD: held}
    host = _host(hosts, tmp_path, scripts)
    photos = await _at_rest(host, scripts)
    await host.ledger.update_session(photos.session_id, name="photos")
    asker = await _asker_at_work(host, scripts)
    manager = host.task_agents
    give = manager._give_session

    async def cannot_be_given(commission: Any) -> Any:
        if commission.source == "ask":
            raise OSError("the runtime is gone")
        return await give(commission)

    manager._give_session = cannot_be_given
    told = _steered_into(host, asker.id)
    with pytest.raises(RuntimeError) as raised:
        await _ask(host, asker)
    said = str(raised.value)
    assert said.startswith('Not asked: the copy of "photos" could not be started') and "the runtime is gone" in said
    await wait_until(lambda: told, what="the asker being told")
    [text] = told
    assert text.startswith('[answer from "photos": failed]') and "the runtime is gone" in text
    asked = await host.ledger.run(
        lambda conn: [tuple(row) for row in conn.execute("SELECT kind, status FROM messages WHERE kind = 'ask'")]
    )
    assert asked == [("ask", "void")]
    [child] = await host.ledger.select(parent_id=asker.id)
    copy = await host.ledger.get_session(child.session_id)
    assert copy.status == "cleared" and not Path(copy.session_dir).exists()
    release.set()


async def test_two_questions_asked_at_once_are_not_named_alike(hosts, tmp_path):
    release, held = _held()
    answers = [[say("An answer."), finish()], [say("Another answer."), finish()]]
    scripts = {QUESTION: ScriptedLLM(answers), PHOTOS: _photos(), BUILD: held}
    host = _host(hosts, tmp_path, scripts, max_task_agents=4)
    photos = await _at_rest(host, scripts)
    await host.ledger.update_session(photos.session_id, name="photos")
    asker = await _asker_at_work(host, scripts)
    manager = host.task_agents
    fork, team, made, both = manager.fork_session, manager._team, [], asyncio.Event()

    async def made_together(session_id: str, **kwargs: Any) -> Any:
        # Both copies are made before either question goes on, so that both are named at once.
        copy = await fork(session_id, **kwargs)
        made.append(copy)
        if len(made) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 10)
        return copy

    async def read_a_while_ago(commission_id: str) -> Any:
        # What was read is a while old by the time a name is chosen from it.
        found = await team(commission_id)
        await asyncio.sleep(0.2)
        return found

    manager.fork_session = made_together
    manager._team = read_a_while_ago
    await asyncio.gather(_ask(host, asker), _ask(host, asker))
    children = await host.ledger.select(parent_id=asker.id)
    assert sorted(child.name for child in children) == ["ask-1", "ask-2"]
    release.set()


async def test_a_question_and_a_task_agent_started_at_once_are_not_named_alike(hosts, tmp_path):
    # A task agent its lead starts may be given any name, that of a question too: it is named
    # under the same hold as a question, and the two are not filed under one name.
    release, held = _held()
    part = "Make the little part."
    scripts = {
        part: ScriptedLLM([[say("Done."), finish()]]),
        QUESTION: ScriptedLLM([[say("An answer."), finish()]]),
        PHOTOS: _photos(),
        BUILD: held,
    }
    host = _host(hosts, tmp_path, scripts, max_task_agents=4)
    photos = await _at_rest(host, scripts)
    await host.ledger.update_session(photos.session_id, name="photos")
    asker = await _asker_at_work(host, scripts)
    manager = host.task_agents
    fork, team = manager.fork_session, manager._team
    forked, reading, go_on = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def copied_then_held(session_id: str, **kwargs: Any) -> Any:
        copy = await fork(session_id, **kwargs)
        forked.set()
        await asyncio.wait_for(go_on.wait(), 10)
        return copy

    async def read_a_while_ago(commission_id: str) -> Any:
        # What was read is a while old by the time a name is chosen from it.
        found = await team(commission_id)
        reading.set()
        await asyncio.sleep(0.2)
        return found

    manager.fork_session = copied_then_held
    manager._team = read_a_while_ago
    asking = asyncio.ensure_future(_ask(host, asker))
    await asyncio.wait_for(forked.wait(), 10)
    # The task agent's names are read first, and the question's while that reading is not filed yet.
    starting = asyncio.ensure_future(
        manager._cb_subagent_start(asker.session_id, {"name": "ask-1", "prompt": part, "description": "a part"})
    )
    await asyncio.wait_for(reading.wait(), 10)
    go_on.set()
    await asyncio.gather(asking, starting)
    names = [child.name for child in await host.ledger.select(parent_id=asker.id)]
    assert len(names) == 2 and len(set(names)) == 2
    release.set()


async def test_what_a_task_agent_hands_back_is_not_steered_into_an_asker_that_was_stopped(hosts, tmp_path):
    # The asker was stopped, and its session is not closed yet: what one of its task agents
    # hands back (a question that failed, say) starts no turn there.
    release, held = _held()
    scripts = {PHOTOS: _photos(), BUILD: held}
    host = _host(hosts, tmp_path, scripts)
    await _at_rest(host, scripts)
    asker = await _asker_at_work(host, scripts)
    child, _created = await host.ledger.create(
        lanlan_name="Lan", title="part", dispatch_text="part", source="task_agent", route="task_agent",
        parent_id=asker.id, name="part",
    )
    told = _steered_into(host, asker.id)
    # At work, it is told.
    await host.task_agents._tell_parent(child, "failed", "it went wrong")
    assert len(told) == 1
    # Stopped, with its session still open: not told.
    await host.ledger.transition(asker.id, "cancelled", reason="stopped by the user")
    assert asker.id in host.task_agents._sessions
    await host.task_agents._tell_parent(child, "failed", "it went wrong again")
    assert len(told) == 1
    release.set()


@pytest.mark.parametrize("ended", ["done", "failed", "blocked"])
async def test_what_a_task_agent_hands_back_is_not_steered_into_an_asker_whose_run_is_over(hosts, tmp_path, ended):
    # Its run ended, and its session is not closed yet: the task agents it started are being
    # stopped, and what one of them hands back starts no turn there either.
    release, held = _held()
    scripts = {PHOTOS: _photos(), BUILD: held}
    host = _host(hosts, tmp_path, scripts)
    await _at_rest(host, scripts)
    asker = await _asker_at_work(host, scripts)
    child, _created = await host.ledger.create(
        lanlan_name="Lan", title="part", dispatch_text="part", source="task_agent", route="task_agent",
        parent_id=asker.id, name="part",
    )
    told = _steered_into(host, asker.id)
    await host.ledger.transition(asker.id, ended, reason="its run ended")
    assert asker.id in host.task_agents._sessions
    await host.task_agents._tell_parent(child, "completed", "it is done")
    assert told == []
    release.set()


async def test_a_question_whose_asker_is_cancelled_while_the_copy_is_made_is_answered_all_the_same(hosts, tmp_path):
    release, held = _held()
    scripts = {QUESTION: ScriptedLLM([[say("An answer."), finish()]]), PHOTOS: _photos(), BUILD: held}
    host = _host(hosts, tmp_path, scripts)
    photos = await _at_rest(host, scripts)
    await host.ledger.update_session(photos.session_id, name="photos")
    asker = await _asker_at_work(host, scripts)
    manager = host.task_agents
    fork, entered, go_on = manager.fork_session, asyncio.Event(), asyncio.Event()

    async def slow(session_id: str, **kwargs: Any) -> Any:
        entered.set()
        await go_on.wait()
        return await fork(session_id, **kwargs)

    manager.fork_session = slow
    told = _steered_into(host, asker.id)
    asking = asyncio.ensure_future(_ask(host, asker))
    await asyncio.wait_for(entered.wait(), 5)
    asking.cancel()
    await asyncio.gather(asking, return_exceptions=True)
    go_on.set()
    await wait_until(lambda: _children_of(host, asker.id), what="the question being filed")
    [child] = await host.ledger.select(parent_id=asker.id)
    child = await wait_status(host, child.id, "done", "failed", "cancelled", "blocked")
    assert child.status == "done"
    await wait_until(lambda: any("An answer." in text for text in told), what="the answer reaching the asker")
    release.set()


async def test_a_question_whose_asker_is_cancelled_while_it_is_counted_is_asked_all_the_same(hosts, tmp_path):
    # The ledger writes in a thread of its own, which a cancelled caller does not stop: a
    # question counted for an asker that gave up the call is still asked (or not counted),
    # and is not left counting against what may be asked with nothing asked.
    release, held = _held()
    scripts = {QUESTION: ScriptedLLM([[say("An answer."), finish()]]), PHOTOS: _photos(), BUILD: held}
    host = _host(hosts, tmp_path, scripts)
    photos = await _at_rest(host, scripts)
    await host.ledger.update_session(photos.session_id, name="photos")
    asker = await _asker_at_work(host, scripts)
    ask_add, entered, go_on = host.ledger.ask_add, asyncio.Event(), asyncio.Event()

    async def written_behind_the_caller(**kwargs: Any) -> Any:
        writing = asyncio.ensure_future(ask_add(**kwargs))
        entered.set()
        await go_on.wait()
        return await writing

    host.ledger.ask_add = written_behind_the_caller  # type: ignore[method-assign]
    told = _steered_into(host, asker.id)
    asking = asyncio.ensure_future(_ask(host, asker))
    await asyncio.wait_for(entered.wait(), 5)
    asking.cancel()
    await asyncio.gather(asking, return_exceptions=True)
    go_on.set()
    await wait_until(lambda: _children_of(host, asker.id), what="the question being filed")
    [child] = await host.ledger.select(parent_id=asker.id)
    child = await wait_status(host, child.id, "done", "failed", "cancelled", "blocked")
    assert child.status == "done"
    await wait_until(lambda: any("An answer." in text for text in told), what="the answer reaching the asker")
    asked = await host.ledger.run(
        lambda conn: [tuple(row) for row in conn.execute("SELECT kind, status FROM messages WHERE kind = 'ask'")]
    )
    assert asked == [("ask", "delivered")]
    release.set()


async def test_whether_the_asker_was_stopped_is_asked_again_where_the_ledger_cannot_be_read_at_first(hosts, tmp_path):
    release, held = _held()
    scripts = {PHOTOS: _photos(), BUILD: held}
    host = _host(hosts, tmp_path, scripts)
    await _at_rest(host, scripts)
    asker = await _asker_at_work(host, scripts)
    child, _created = await host.ledger.create(
        lanlan_name="Lan", title="part", dispatch_text="part", source="task_agent", route="task_agent",
        parent_id=asker.id, name="part",
    )
    told = _steered_into(host, asker.id)
    await host.ledger.transition(asker.id, "cancelled", reason="stopped by the user")
    get, failed = host.ledger.get, []

    async def unreadable_once(commission_id: str) -> Any:
        if commission_id == asker.id and not failed:
            failed.append(commission_id)
            raise OSError("the ledger is locked")
        return await get(commission_id)

    host.ledger.get = unreadable_once  # type: ignore[method-assign]
    await host.task_agents._tell_parent(child, "failed", "it went wrong")
    host.ledger.get = get  # type: ignore[method-assign]
    # Read again, it is known to be stopped: not told.
    assert failed == [asker.id] and told == []
    release.set()
