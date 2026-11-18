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

"""What a session can be told besides its work: to be copied, to sum itself up, and to ask before it spends.

A session is copied into a new one that goes on from where it stood and
begins its requests the same way (``fork.py``); an idle one sums its
conversation up when told to; and a host that keeps count is asked before
every model request.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import types
from pathlib import Path
from typing import Any

import pytest

from config.prompts import prompts_nekoclaw as texts
from nekoclaw.runtime import protocol
from nekoclaw.runtime.context import SUMMARY_CUT_SHORT_NOTE
from nekoclaw.runtime.core import InProcessRuntime
from nekoclaw.runtime.fork import FORK_NAMESPACE, REASON_FORKED, ForkError, fork_record, whole_prefix
from nekoclaw.runtime.host import HostCallbacks
from nekoclaw.runtime.loop.messages import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from nekoclaw.runtime.loop.session import MessageEntry, SessionState
from nekoclaw.runtime.loop.session.jsonl import entries_from_json_lines
from nekoclaw.runtime.model import LLMClientProvider
from nekoclaw.runtime.protocol import ModelConfig
from nekoclaw.runtime.session import TRANSCRIPT_FILE, SessionError, TaskAgentSession
from nekoclaw.runtime.snapshot import SYSTEM_FILE, read_snapshot, write_snapshot
from tests.unit.nekoclaw.fakes import Recorder, ScriptedLLM, allow_all, call, finish, make_spec, open_session, say, usage


def _words(count: int, prefix: str) -> str:
    return " ".join(f"{prefix}{index}" for index in range(count))


def _small_model(window: int = 32_000) -> ModelConfig:
    return ModelConfig(model="fake-model", api_key="k", context_window=window, max_output_tokens=2_000)


def _system_of(request: list[dict[str, Any]]) -> str:
    assert request[0]["role"] == "system"
    return str(request[0]["content"])


def _said(request: list[dict[str, Any]]) -> list[str]:
    """What a request carries besides the system prompt and the notices about the time, as text."""
    out = []
    for message in request[1:]:
        text = message.get("content")
        text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
        if not str(text).startswith("<runtime_context>"):
            out.append(f"{message['role']}: {text}")
    return out


async def _ask(session: Any, events: Any, text: str, **options: Any) -> dict[str, Any]:
    mark = len(events.events)
    session.prompt(text, **options)
    return await events.wait_ended(start=mark)


def _entries(session_dir: Path) -> list[Any]:
    return entries_from_json_lines((session_dir / TRANSCRIPT_FILE).read_text(encoding="utf-8").split("\n"))


def _copy_of(spec: Any, tmp_path: Path, session_id: str = "copy", **overrides: Any) -> Any:
    """The spec of a session that is opened from a copy: the same working directory, a directory of its own."""
    return make_spec(tmp_path, session_id, **overrides)


# ---------------------------------------------------------------------------
# A copy
# ---------------------------------------------------------------------------


async def test_a_copy_goes_on_from_where_the_other_stood_and_begins_its_requests_the_same(tmp_path: Path) -> None:
    spec = make_spec(tmp_path, instructions="You work for Lan.")
    llm = ScriptedLLM(
        [
            [call("todo_write", {"todos": [{"content": "count the photos", "status": "in_progress"}]}), finish("tool_calls")],
            [say("There are 214 photos, in three folders."), finish()],
        ]
    )
    session, events = await open_session(spec, llm, HostCallbacks(decide_permission=allow_all()))
    await _ask(session, events, "Commission c1\nSort the photos by year.", opening=True)
    await session.close()
    source_dir = Path(spec.session_dir)
    before = (source_dir / TRANSCRIPT_FILE).read_bytes()

    target = _copy_of(spec, tmp_path, instructions="Told anew, and not used: the copy keeps what the other was told.")
    result = fork_record(source_dir, Path(target.session_dir))

    # The other session is as it was.
    assert (source_dir / TRANSCRIPT_FILE).read_bytes() == before
    copied = _entries(Path(target.session_dir))
    originals = _entries(source_dir)
    # Everything of the other's conversation, entry for entry, and then the entry that says it is a copy.
    assert [entry.id for entry in copied[:-1]] == [entry.id for entry in originals]
    assert result.entries == len(originals) and result.upto == originals[-1].id
    marker = copied[-1]
    assert marker.type == "custom" and marker.namespace == FORK_NAMESPACE and marker.parent_id == result.upto
    assert marker.data["upto"] == result.upto and marker.data["source_dir"] == str(source_dir)
    # The snapshot went along.
    snapshot = read_snapshot(Path(target.session_dir))
    assert snapshot is not None and snapshot.reason == REASON_FORKED
    assert snapshot.system == read_snapshot(source_dir).system and snapshot.compactions == 0

    asked = ScriptedLLM([[say("About two thirds are from 2019."), finish()]])
    copy, copy_events = await open_session(target, asked, HostCallbacks(decide_permission=allow_all()))
    try:
        assert copy_events.of("started")[0]["resumed"] is True
        # What the other session had still to do is not the copy's: no to-do list, no goal.
        assert copy.todos.open_items() == [] and copy.goals.current is None
        await _ask(copy, copy_events, "How many of them are from 2019?")
        request = asked.requests[0]
        # It begins as the other's requests did: the same system prompt, the same tools, the same conversation.
        assert _system_of(request) == _system_of(llm.requests[0])
        assert asked.overrides[0]["tools"] == llm.overrides[0]["tools"]
        shared = len(llm.requests[-1])
        assert _said(request)[: len(_said(llm.requests[-1]))] == _said(llm.requests[-1])
        assert shared and _said(request)[-1] == "user: How many of them are from 2019?"
        assert "assistant: There are 214 photos, in three folders." in _said(request)
        # No to-do list of the other's is told to it either.
        assert "count the photos" not in json.dumps([m for m in request if "runtime_context" in str(m.get("content"))])
    finally:
        await copy.close()

    # Opened again, the copy is still without the other's list, and has what it said itself.
    again, again_events = await open_session(target, ScriptedLLM(), HostCallbacks(decide_permission=allow_all()))
    try:
        assert again.todos.open_items() == [] and again_events.of("started")[0]["resumed"] is True
        texts_said = [message.text for message in again.harness.message_list if isinstance(message, AssistantMessage)]
        assert texts_said[-1] == "About two thirds are from 2019."
    finally:
        await again.close()


def _reply(text: str = "", calls: tuple[str, ...] = (), stop: str = "stop") -> MessageEntry:
    content: list[Any] = [TextContent(text=text)] if text else []
    content.extend(ToolCall(id=call_id, name="read", arguments={}) for call_id in calls)
    return MessageEntry(message=AssistantMessage(model="m", content=content, stop_reason=stop))  # type: ignore[arg-type]


def _result(call_id: str) -> MessageEntry:
    return MessageEntry(
        message=ToolResultMessage(tool_call_id=call_id, tool_name="read", content=[TextContent(text="ok")], is_error=False)
    )


def _user(text: str) -> MessageEntry:
    return MessageEntry(message=UserMessage(content=text))


def test_a_copy_ends_where_the_conversation_is_whole() -> None:
    # A reply all of whose calls are answered is whole, and so is what follows it.
    whole = [_user("go"), _reply(calls=("a", "b")), _result("a"), _result("b"), _reply("done")]
    assert whole_prefix(whole) == 5
    # A reply some of whose calls have no result yet is where the other session is at work: the copy ends before it.
    assert whole_prefix([_user("go"), _reply(calls=("a", "b")), _result("a")]) == 1
    assert whole_prefix([_user("go"), _reply(calls=("a",))]) == 1
    # Only the last such reply is looked at for that: what was whole before it stays.
    earlier = [_user("go"), _reply(calls=("a",)), _result("a"), _reply(calls=("b",))]
    assert whole_prefix(earlier) == 3
    # Replies at the very end that failed or were stopped are left out, however many.
    assert whole_prefix([_user("go"), _reply("ok"), _user("more"), _reply(stop="error"), _reply(stop="aborted")]) == 3
    # One in the middle that was gone on from stays.
    assert whole_prefix([_user("go"), _reply(stop="error"), _user("again"), _reply("ok")]) == 4
    # Both at once: the stopped reply goes, and then the reply in front of it that is still waiting for a result.
    assert whole_prefix([_user("go"), _reply(calls=("a",)), _reply(stop="aborted")]) == 1
    assert whole_prefix([]) == 0


async def test_a_copy_can_end_at_an_earlier_entry_and_never_in_the_middle_of_something(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    llm = ScriptedLLM([[say("First answer."), finish()], [say("Second answer."), finish()]])
    session, events = await open_session(spec, llm)
    await _ask(session, events, "First question.")
    await _ask(session, events, "Second question.")
    await session.close()
    source_dir = Path(spec.session_dir)
    originals = _entries(source_dir)
    first_answer = next(
        entry.id for entry in originals if entry.type == "message" and getattr(entry.message, "text", "") == "First answer."
    )

    result = fork_record(source_dir, tmp_path / "sessions" / "early", upto=first_answer)

    assert result.upto == first_answer
    state = SessionState.from_entries(_entries(tmp_path / "sessions" / "early"))
    said = [getattr(message, "text", "") for message in state.messages]
    assert "First answer." in said and "Second question." not in said and "Second answer." not in said

    # An entry that is not of the conversation, a session with nothing said, a place that is taken.
    with pytest.raises(ForkError, match="no entry"):
        fork_record(source_dir, tmp_path / "sessions" / "nowhere", upto="not-an-entry")
    assert not (tmp_path / "sessions" / "nowhere" / TRANSCRIPT_FILE).exists()
    with pytest.raises(ForkError, match="nothing was said"):
        fork_record(tmp_path / "sessions" / "never-opened", tmp_path / "sessions" / "of-nothing")
    # (Looking for a record that is not there makes no directory for it.)
    assert not (tmp_path / "sessions" / "never-opened").exists()
    with pytest.raises(ForkError, match="has a record already"):
        fork_record(source_dir, tmp_path / "sessions" / "early")


async def test_a_record_that_ends_in_a_line_cut_short_is_copied_without_it_and_left_as_it_is(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    session, events = await open_session(spec, ScriptedLLM([[say("An answer."), finish()]]))
    await _ask(session, events, "A question.")
    await session.close()
    path = Path(spec.session_dir) / TRANSCRIPT_FILE
    whole = path.read_bytes()
    # As a process that died while it wrote leaves it: half an entry, no newline.
    path.write_bytes(whole + b'{"type":"message","id":"torn","parent_')
    torn = path.read_bytes()

    result = fork_record(Path(spec.session_dir), tmp_path / "sessions" / "copy")

    assert path.read_bytes() == torn
    assert result.entries == len(entries_from_json_lines(whole.decode("utf-8").split("\n")))
    assert "torn" not in (tmp_path / "sessions" / "copy" / TRANSCRIPT_FILE).read_text(encoding="utf-8")


def _runtime(scripts: dict[str, ScriptedLLM], recorder: Recorder, host: Any = None) -> InProcessRuntime:
    async def opener(spec: Any, bridge: Any, emit: Any) -> Any:
        llm = scripts[spec.session_id]

        async def factory() -> ScriptedLLM:
            return llm

        return await TaskAgentSession.open(spec, bridge, emit, model=LLMClientProvider(spec.model, client_factory=factory))

    return InProcessRuntime(recorder, host, open_session=opener)


async def test_a_session_at_work_is_copied_up_to_where_it_is_whole_and_goes_on_undisturbed(tmp_path: Path) -> None:
    # The session being copied is in the middle of a run: it has called a tool and
    # waits for the model's next reply. What it has said so far is on the disk
    # before it is copied, and the copy ends with the last answered call.
    release = asyncio.Event()

    async def hold() -> None:
        await release.wait()

    work = ScriptedLLM(
        [
            [call("glob", {"pattern": "*.none"}), finish("tool_calls")],
            [hold, say("Nothing to sort."), finish()],
        ]
    )
    asked = ScriptedLLM([[say("It has looked for files and found none."), finish()]])
    recorder = Recorder()
    runtime = _runtime({"s1": work, "copy": asked}, recorder, HostCallbacks(decide_permission=allow_all()))
    spec = make_spec(tmp_path)
    try:
        await runtime.start(spec)
        await runtime.prompt("s1", "Commission c1\nSort the photos.", opening=True)
        await recorder.wait_for(lambda event: event.get("type") == "tool_end")
        # Its second request is under way, and held.
        for _attempt in range(200):
            if len(work.requests) == 2:
                break
            await asyncio.sleep(0.01)
        assert len(work.requests) == 2

        target = make_spec(tmp_path, "copy")
        await runtime.fork(
            "copy", session_dir=target.session_dir, source_session_id="s1", source_dir=spec.session_dir
        )

        forked = recorder.of("forked")
        assert [(event["session_id"], event["source_session_id"]) for event in forked] == [("copy", "s1")]
        copied = _entries(Path(target.session_dir))
        # The call and its result are both there; the reply that is still being written is not.
        kinds = [type(entry.message).__name__ for entry in copied if entry.type == "message"]
        assert kinds == ["UserMessage", "AssistantMessage", "ToolResultMessage"]
        assert copied[-2].id == forked[0]["upto"]

        # The copy is opened and asked while the other is still at work.
        await runtime.start(target)
        await runtime.prompt("copy", "Where does this stand?")
        answered = await recorder.wait_ended("copy")
        assert answered["text"] == "It has looked for files and found none."
        assert _system_of(asked.requests[0]) == _system_of(work.requests[0])
        assert not recorder.of("ended", "s1")

        # The other one finishes as if nothing had happened, and its record holds nothing of the copy's.
        release.set()
        assert (await recorder.wait_ended("s1"))["text"] == "Nothing to sort."
        await runtime.close("s1")
        said = (Path(spec.session_dir) / TRANSCRIPT_FILE).read_text(encoding="utf-8")
        assert "Where does this stand?" not in said and FORK_NAMESPACE not in said

        # A session that is there already is not made a second time; one of an unknown place is refused.
        await runtime.fork("copy", session_dir=target.session_dir, source_session_id="s1", source_dir=spec.session_dir)
        await runtime.fork(
            "other", session_dir=str(tmp_path / "sessions" / "other"), source_session_id="gone",
            source_dir=str(tmp_path / "sessions" / "gone"),
        )
        refused = [(event["session_id"], event["command"]) for event in recorder.of("error")]
        assert refused == [("copy", "fork"), ("other", "fork")]
        assert "already exists" in recorder.of("error")[0]["message"]
        assert "nothing was said" in recorder.of("error")[1]["message"]
    finally:
        release.set()
        await runtime.shutdown()


async def test_a_copy_reads_what_the_other_kept_of_long_tool_output_without_asking(tmp_path: Path) -> None:
    # A long result is cut in the conversation and kept whole in a file of the
    # session; what was said names that file. The copy reads it where it is.
    spec = make_spec(tmp_path)
    session, events = await open_session(spec, ScriptedLLM([[say("Read."), finish()]]))
    await _ask(session, events, "Read the long file.")
    await session.close()
    kept = Path(spec.session_dir) / "outputs" / "pwsh-call-1.txt"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("the whole of a long result\n", encoding="utf-8")
    asks: list[dict[str, Any]] = []

    async def decide(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        # As a host does: what is in the working directory goes ahead, the rest is the user's to say.
        asks.append({"session": session_id, **payload})
        if payload["category"] == "workdir_files":
            return {"allow": True}
        return {"allow": False, "reason": "the user said no"}

    target = make_spec(tmp_path, "copy")
    fork_record(Path(spec.session_dir), Path(target.session_dir))
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("not the session's\n", encoding="utf-8")
    reader = ScriptedLLM(
        [
            [call("read", {"file_path": str(kept)}), finish("tool_calls")],
            [call("read", {"file_path": str(elsewhere)}, "call-2"), finish("tool_calls")],
            [say("Seen."), finish()],
        ]
    )
    copy, copy_events = await open_session(target, reader, HostCallbacks(decide_permission=decide))
    try:
        await _ask(copy, copy_events, "Look at what was kept.")
        first, second = [event for event in copy_events.of("tool_end") if event["tool_name"] == "read"]
        # What the other kept is read without anybody being asked.
        assert first["is_error"] is False and "the whole of a long result" in first["text"]
        # Any other file outside the working directory is asked about, as ever.
        assert second["is_error"] is True
        assert [ask["path"] for ask in asks if ask.get("category") == "outside_read"] == [str(elsewhere)]
    finally:
        await copy.close()


# ---------------------------------------------------------------------------
# Summing up when told to
# ---------------------------------------------------------------------------


async def _seed(session: Any, events: Any, llm: ScriptedLLM, turns: int = 4) -> None:
    for index in range(turns):
        llm.add(say(f"Finished step {index}. " + _words(300, f"s{index}w")), usage(1_000 * (index + 1), 320), finish())
        await _ask(session, events, f"Do step {index}.")


async def test_an_idle_session_sums_its_conversation_up_when_it_is_told_to(tmp_path: Path) -> None:
    spec = make_spec(tmp_path, model=_small_model(), instructions="Index of then.")
    llm = ScriptedLLM()
    session, events = await open_session(spec, llm)
    try:
        await _seed(session, events, llm)
        requests_before, usage_before = len(llm.requests), len(events.of("usage"))
        length_before = len(session.harness.message_list)
        # Far below where it would be summed up before a request.
        assert await session._size() < session.context.policy.threshold
        llm.add(say("## Current Work\n- the four steps are done"), usage(2_500, 40), finish())
        mark = len(events.events)

        session.compact()
        ended = await events.wait_ended(start=mark)

        assert ended["reason"] == "completed" and not ended.get("error")
        assert len(llm.requests) == requests_before + 1
        summary = llm.requests[-1]
        # The request that sums it up begins as every request of the session did, and asks for the summary.
        assert _system_of(summary) == _system_of(llm.requests[0])
        assert llm.overrides[-1]["tools"] == llm.overrides[0]["tools"]
        assert summary[-1] == {"role": "user", "content": texts.COMPACTION_INSTRUCTION}
        # What it used is told apart from the usage of the runs, and says nothing of the conversation's length.
        told = events.of("usage")[usage_before:]
        assert [(event["summary"], event["input"], event["output"], event["context_tokens"]) for event in told] == [
            (True, 2_500, 40, 0)
        ]
        assert all(event["summary"] is False for event in events.of("usage")[:usage_before])
        compacted = events.of("compacted")
        assert [event["count"] for event in compacted] == [1]
        assert events.events.index(compacted[0]) < events.events.index(ended)
        # The conversation is the summary and its newest part; as much is kept word
        # for word as before a request (the last answer is, whole).
        messages = session.harness.message_list
        assert len(messages) < length_before
        assert any("the four steps are done" in getattr(message, "text", "") for message in messages)
        assert any(getattr(message, "text", "").startswith("Finished step 3.") for message in messages)
        # (More than the last answer: nothing was wrong with the length of this conversation,
        # so it is not cut down as one that was refused for its length is.)
        assert any(getattr(message, "text", "").startswith("Finished step 2.") for message in messages)
        assert not any(getattr(message, "text", "").startswith("Finished step 0.") for message in messages)
        # The snapshot was put together anew, as after any summary.
        saved = json.loads((Path(spec.session_dir) / SYSTEM_FILE).read_text(encoding="utf-8"))
        assert saved["reason"] == "compacted" and saved["compactions"] == 1

        # A second time right away there is nothing left to sum up: it ends, and says nothing was.
        mark = len(events.events)
        session.compact()
        again = await events.wait_ended(start=mark)
        assert again["reason"] == "completed" and len(events.of("compacted")) == 1
        assert len(llm.requests) == requests_before + 1
    finally:
        await session.close()

    # Opened again, the session goes on from the summary.
    llm = ScriptedLLM([[say("On it."), finish()]])
    session, events = await open_session(make_spec(tmp_path, model=_small_model()), llm)
    try:
        await _ask(session, events, "Go on.")
        assert any("the four steps are done" in line for line in _said(llm.requests[0]))
        assert not any("s0w10 " in line for line in _said(llm.requests[0]))
    finally:
        await session.close()


async def test_summing_up_is_refused_while_a_run_is_under_way_and_stops_at_once_when_told_to(tmp_path: Path) -> None:
    release = asyncio.Event()

    async def hold() -> None:
        await release.wait()

    recorder = Recorder()
    llm = ScriptedLLM()
    runtime = _runtime({"s1": llm}, recorder)
    spec = make_spec(tmp_path, model=_small_model())
    try:
        await runtime.start(spec)
        for index in range(4):
            llm.add(say(f"Finished step {index}. " + _words(300, f"s{index}w")), finish())
            mark = len(recorder.events)
            await runtime.prompt("s1", f"Do step {index}.")
            await recorder.wait_ended("s1", start=mark)

        # While a run is under way.
        llm.add(hold, say("Late."), finish())
        mark = len(recorder.events)
        await runtime.prompt("s1", "One more.")
        await runtime.compact("s1")
        refused = await recorder.wait_for(lambda event: event.get("type") == "error", start=mark)
        assert refused["command"] == "compact" and "a run is in progress" in refused["message"]
        release.set()
        await recorder.wait_ended("s1", start=mark)

        # Told to stop while the summary is being written: stopped at once, and nothing of the conversation changed.
        length = len(runtime.core._sessions["s1"].harness.message_list)
        release.clear()
        llm.add(hold, say("## Current Work\n- never arrives"), finish())
        mark = len(recorder.events)
        await runtime.compact("s1")
        for _attempt in range(500):
            if llm.requests and llm.requests[-1][-1].get("content") == texts.COMPACTION_INSTRUCTION:
                break
            await asyncio.sleep(0.01)
        # The request that sums it up is under way: it is that one the stop cuts short.
        assert llm.requests[-1][-1].get("content") == texts.COMPACTION_INSTRUCTION
        await runtime.abort("s1")
        stopped = await asyncio.wait_for(recorder.wait_ended("s1", start=mark), 5)
        assert stopped["reason"] == "aborted"
        assert not recorder.of("compacted")
        assert len(runtime.core._sessions["s1"].harness.message_list) == length
        assert not any(entry.type == "compaction" for entry in _entries(Path(spec.session_dir)))

        # A session nobody knows.
        refusals = len(recorder.of("error"))
        await runtime.compact("nobody")
        assert [(event["session_id"], event["command"]) for event in recorder.of("error")[refusals:]] == [
            ("nobody", "compact")
        ]
    finally:
        release.set()
        await runtime.shutdown()


# ---------------------------------------------------------------------------
# Asking before a request
# ---------------------------------------------------------------------------


async def test_a_host_that_keeps_count_is_asked_before_every_model_request(tmp_path: Path) -> None:
    asked: list[int] = []
    answers: list[Any] = []

    async def budget(session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        assert session_id == "s1" and payload == {}
        asked.append(len(llm.requests))
        answer = answers.pop(0) if answers else None
        if isinstance(answer, BaseException):
            raise answer
        return answer

    llm = ScriptedLLM(
        [
            [call("glob", {"pattern": "*.none"}), finish("tool_calls")],
            [say("Nothing there."), finish()],
            [say("Handing over: nothing was found."), finish()],
            [say("Still here."), finish()],
        ]
    )
    session, events = await open_session(make_spec(tmp_path), llm, HostCallbacks(budget=budget, decide_permission=allow_all()))
    try:
        # Asked before each of the two requests of the run, when the one before it was answered.
        await _ask(session, events, "Look for files.")
        assert asked == [0, 1]

        # What the host has to say is said to the model in front of the request.
        answers.append({"notice": "No more may be used. Hand over what you have."})
        ended = await _ask(session, events, "Go on.")
        assert ended["text"] == "Handing over: nothing was found."
        told = _said(llm.requests[-1])
        assert told[-1] == "user: No more may be used. Hand over what you have."
        assert told[-2] == "user: Go on."

        # A host that cannot answer does not hold the request up.
        answers.append(RuntimeError("the ledger is locked"))
        assert (await _ask(session, events, "And now?"))["text"] == "Still here."

        # Told to stop, the run ends without the request.
        answers.append({"stop": True})
        requests = len(llm.requests)
        stopped = await _ask(session, events, "Once more.")
        assert stopped["reason"] == "aborted" and len(llm.requests) == requests
    finally:
        await session.close()


async def test_a_request_waits_for_as_long_as_the_host_takes_to_answer_and_a_stop_ends_the_wait(tmp_path: Path) -> None:
    answer: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    waiting = asyncio.Event()

    async def budget(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        del session_id, payload
        waiting.set()
        return await asyncio.shield(answer)

    llm = ScriptedLLM([[say("Went on."), finish()]])
    session, events = await open_session(make_spec(tmp_path), llm, HostCallbacks(budget=budget))
    try:
        session.prompt("Work.")
        await asyncio.wait_for(waiting.wait(), 5)
        await asyncio.sleep(0.3)
        # The user is being asked: nothing is sent meanwhile.
        assert llm.requests == [] and not events.of("ended")
        answer.set_result({})
        assert (await events.wait_ended())["text"] == "Went on."

        # A run that is stopped while it waits ends as stopped, and sends nothing. Nor is the session
        # told the time, or its conversation looked at for summing up (a request as long as all of
        # it), after the stop: both are due here, and both come after the wait.
        answer = asyncio.get_running_loop().create_future()
        waiting.clear()
        fitted: list[dict[str, Any]] = []
        fit = session._fit

        async def watched(**kwargs: Any) -> None:
            fitted.append(kwargs)
            await fit(**kwargs)

        session._fit = watched

        def told_the_time() -> int:
            return sum(
                1 for message in session.harness.message_list
                if getattr(message, "custom_type", None) == "notice:runtime_context"
            )

        mark = len(events.events)
        session.prompt("More work.")
        await asyncio.wait_for(waiting.wait(), 5)
        # (The run was told the time when it began. It is due again, as when a job ended meanwhile.)
        before, session._context_due = told_the_time(), True
        # (And what follows a summary is due: the session was closed before it was done.)
        session._compaction_due = True
        compacted = len(events.of("compacted"))
        session.abort()
        assert (await asyncio.wait_for(events.wait_ended(start=mark), 5))["reason"] == "aborted"
        assert len(llm.requests) == 1
        assert fitted == [] and told_the_time() == before
        # Left to the next run that is not stopped.
        assert session._compaction_due is True and len(events.of("compacted")) == compacted
        session._compaction_due = False
        # One that is not stopped while it waits is told the time and fitted as before.
        answer.set_result({})
        answer = asyncio.get_running_loop().create_future()
        waiting.clear()
        llm.add(say("And on."), finish())
        mark = len(events.events)
        session.prompt("Still more.")
        await asyncio.wait_for(waiting.wait(), 5)
        before, session._context_due = told_the_time(), True
        answer.set_result({})
        assert (await events.wait_ended(start=mark))["text"] == "And on."
        assert fitted == [{"force": False}] and told_the_time() == before + 1
    finally:
        if not answer.done():
            answer.set_result({})
        await session.close()


def test_a_fork_and_a_compact_command_are_read_as_what_they_are() -> None:
    fork = protocol.parse_command(
        {"type": "fork", "session_id": "b", "session_dir": "/s/b", "source_session_id": "a", "source_dir": "/s/a"}
    )
    assert isinstance(fork, protocol.ForkCommand) and fork.upto is None
    assert isinstance(protocol.parse_command({"type": "compact", "session_id": "a"}), protocol.CompactCommand)
    with pytest.raises(Exception):
        # The session that is made is named like any session.
        protocol.parse_command(
            {"type": "fork", "session_id": "../b", "session_dir": "/s/b", "source_session_id": "a", "source_dir": "/s/a"}
        )
    event = protocol.parse_event({"type": "forked", "session_id": "b", "source_session_id": "a", "upto": "e1", "entries": 3})
    assert isinstance(event, protocol.ForkedEvent)
    assert protocol.UsageEvent(session_id="a").summary is False


async def test_a_copy_that_ends_before_a_summary_holds_the_conversation_as_it_was_then(tmp_path: Path) -> None:
    spec = make_spec(tmp_path, model=_small_model())
    llm = ScriptedLLM()
    session, events = await open_session(spec, llm)
    await _seed(session, events, llm)
    before_summary = _entries(Path(spec.session_dir))[-1].id
    llm.add(say("## Current Work\n- the four steps are done"), finish())
    mark = len(events.events)
    session.compact()
    await events.wait_ended(start=mark)
    await session.close()
    source_dir = Path(spec.session_dir)
    assert read_snapshot(source_dir).compactions == 1

    # All of it: the summary is part of the copy, and the copy is counted as summed up once.
    whole = make_spec(tmp_path, "whole", model=_small_model())
    fork_record(source_dir, Path(whole.session_dir))
    assert read_snapshot(Path(whole.session_dir)).compactions == 1
    # Up to where it stood before it was summed up: every word of then, and no summary.
    early = make_spec(tmp_path, "early", model=_small_model())
    result = fork_record(source_dir, Path(early.session_dir), upto=before_summary)
    assert result.upto == before_summary
    assert read_snapshot(Path(early.session_dir)).compactions == 0
    assert not any(entry.type == "compaction" for entry in _entries(Path(early.session_dir)))

    for target, summed in ((whole, True), (early, False)):
        asked = ScriptedLLM([[say("Here."), finish()]])
        copy, copy_events = await open_session(target, asked)
        try:
            await _ask(copy, copy_events, "Where does it stand?")
            said = " ".join(_said(asked.requests[0]))
            assert ("the four steps are done" in said) is summed
            assert ("s0w10 s0w11" in said) is not summed
            # Nothing is put together anew for it: it begins as the session it was copied from does.
            assert not copy_events.of("compacted")
            assert _system_of(asked.requests[0]) == read_snapshot(source_dir).system
        finally:
            await copy.close()


# ---------------------------------------------------------------------------
# What the first round of review found
# ---------------------------------------------------------------------------


async def test_without_a_whole_summary_a_conversation_told_to_sum_itself_up_stays_as_it_is(tmp_path: Path) -> None:
    spec = make_spec(tmp_path, model=_small_model())
    llm = ScriptedLLM()
    session, events = await open_session(spec, llm)
    try:
        await _seed(session, events, llm)
        length = len(session.harness.message_list)
        record = (Path(spec.session_dir) / TRANSCRIPT_FILE).read_text(encoding="utf-8")

        async def fails() -> None:
            raise RuntimeError("the provider is away")

        # The request fails; it comes back empty; it ends at its length limit; the model calls a tool
        # (the request offers them, as every request of the session does) instead of going on.
        cut_short = [say("## Primary Request and Intent\n- the four st"), usage(2_400, 900), finish("length")]
        a_call = [
            say("## Primary Request and Intent\n- let me look"), call("glob", {"pattern": "*"}), usage(2_000, 50),
            finish("tool_calls"),
        ]
        for answer in ([fails], [finish()], cut_short, a_call):
            llm.add(*answer)
            mark, told = len(events.events), len(events.of("usage"))
            session.compact()
            ended = await events.wait_ended(start=mark)
            assert ended["reason"] == "completed" and not ended.get("error")
            # Nothing was summed up, and nothing was dropped for it.
            assert not events.of("compacted")
            assert len(session.harness.message_list) == length
            assert (Path(spec.session_dir) / TRANSCRIPT_FILE).read_text(encoding="utf-8") == record
            # What a request that came back used is told all the same: the request was made, and
            # its summary was not taken.
            assert [(event["summary"], event["input"], event["output"]) for event in events.of("usage")[told:]] == (
                [(True, 2_400, 900)] if answer is cut_short else [(True, 2_000, 50)] if answer is a_call else []
            )
        assert any(getattr(message, "text", "").startswith("Finished step 0.") for message in session.harness.message_list)
    finally:
        await session.close()


async def test_a_summary_cut_short_says_so_where_the_conversation_has_to_get_shorter(tmp_path: Path) -> None:
    spec = make_spec(tmp_path, model=_small_model())
    llm = ScriptedLLM()
    session, events = await open_session(spec, llm)
    try:
        await _seed(session, events, llm)
        llm.add(say("## Critical Context\n- never touch the vault\n\n## Next Step\n- go on with st"), usage(2_400, 900), finish("length"))

        # As after a request that was refused as too long: the older part goes either way.
        await session._fit(force=True)

        said = "\n".join(getattr(message, "text", "") for message in session.harness.message_list)
        assert "never touch the vault" in said and SUMMARY_CUT_SHORT_NOTE in said
        assert said.index("go on with st") < said.index(SUMMARY_CUT_SHORT_NOTE)
        assert [event["summary"] for event in events.of("usage")][-1] is True
        assert session._compactions == 1
    finally:
        await session.close()


def test_what_limits_the_work_comes_before_the_long_quotes_in_a_summary() -> None:
    # A summary that reaches its length limit loses its end: that is to be the quotes, not the constraints.
    text = texts.COMPACTION_INSTRUCTION
    assert text.index("## Critical Context") < text.index("## Next Step") < text.index("Rules:")
    assert text.index("## Current Work") < text.index("## Critical Context")


async def test_a_summing_up_that_is_stopped_before_it_took_a_step_is_over_all_the_same(tmp_path: Path) -> None:
    llm = ScriptedLLM()
    session, events = await open_session(make_spec(tmp_path, model=_small_model()), llm)
    await _seed(session, events, llm)
    requests = len(llm.requests)

    # Told to sum up and to stop in one go: the task has not run when it is cancelled.
    mark = len(events.events)
    session.compact()
    assert session.abort() is True
    ended = await asyncio.wait_for(events.wait_ended(start=mark), 5)
    assert ended["reason"] == "aborted" and not session.running and len(llm.requests) == requests
    # The session takes work again.
    llm.add(say("Still here."), finish())
    assert (await _ask(session, events, "And now?"))["text"] == "Still here."

    # Told to sum up and closed, the summing up cancelled before it took a step: the closing is
    # carried through (what was said is written out, the model's client closed), and says so.
    mark = len(events.events)
    session.compact()
    session._run_task.cancel()
    await asyncio.wait_for(session.close(), 5)
    reasons = [event["reason"] for event in events.events[mark:] if event["type"] == "ended"]
    assert reasons == ["closed"] and len(llm.requests) == requests + 1


async def test_nothing_is_added_to_a_conversation_while_it_is_summed_up(tmp_path: Path) -> None:
    release = asyncio.Event()

    async def hold() -> None:
        await release.wait()

    llm = ScriptedLLM()
    session, events = await open_session(make_spec(tmp_path, model=_small_model()), llm)
    try:
        await _seed(session, events, llm)
        inputs = session._inputs
        llm.add(hold, say("## Current Work\n- the four steps are done"), finish())
        mark = len(events.events)
        session.compact()
        for _attempt in range(200):
            if llm.requests[-1][-1].get("content") == texts.COMPACTION_INSTRUCTION:
                break
            await asyncio.sleep(0.01)
        assert llm.requests[-1][-1].get("content") == texts.COMPACTION_INSTRUCTION

        for add in (session.steer, session.follow_up):
            with pytest.raises(SessionError, match="is being summed up"):
                add("one more thing")
        # Not counted as taken: the end of the summing up says how many inputs the session took.
        release.set()
        ended = await events.wait_ended(start=mark)
        assert ended["reason"] == "completed" and ended["inputs"] == inputs == session._inputs
    finally:
        release.set()
        await session.close()


async def test_what_the_host_said_about_usage_holds_for_the_run_it_was_said_in(tmp_path: Path) -> None:
    answers: list[Any] = [{"notice": "No more may be used. Hand over what you have."}]

    async def budget(session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        del session_id, payload
        return answers.pop(0) if answers else None

    llm = ScriptedLLM([[say("Handing over: half is done."), finish()], [say("Going on."), finish()]])
    spec = make_spec(tmp_path)
    session, events = await open_session(spec, llm, HostCallbacks(budget=budget))
    try:
        await _ask(session, events, "Sort the photos.", opening=True)
        assert _said(llm.requests[0])[-1] == "user: No more may be used. Hand over what you have."

        def notices() -> int:
            return sum(1 for message in session.harness.message_list if getattr(message, "custom_type", None) == "notice:usage")

        # Told to sum itself up before another run came: what was said about usage goes first, so
        # that it is not written into a summary that later commissions read.
        assert notices() == 1
        mark = len(events.events)
        session.compact()
        await events.wait_ended(start=mark)
        assert notices() == 0

        # The next run, of this commission (the user allowed more) or of another: the host is asked
        # anew, has nothing to say, and what it said before is not in front of the model any more.
        await _ask(session, events, "Go on then.")
        told = _said(llm.requests[1])
        assert not any("No more may be used" in line for line in told)
        assert told[-1] == "user: Go on then." and "assistant: Handing over: half is done." in told
    finally:
        await session.close()
    # The record keeps it; a session opened from it leaves it out the same way.
    assert "No more may be used" in (Path(spec.session_dir) / TRANSCRIPT_FILE).read_text(encoding="utf-8")
    llm = ScriptedLLM([[say("Here again."), finish()]])
    session, events = await open_session(make_spec(tmp_path), llm)
    try:
        await _ask(session, events, "And again.")
        assert not any("No more may be used" in line for line in _said(llm.requests[0]))
    finally:
        await session.close()


async def test_a_session_is_not_started_while_its_record_is_being_copied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from nekoclaw.runtime import fork as fork_module

    recorder = Recorder()
    llm = ScriptedLLM([[say("Done."), finish()]])
    runtime = _runtime({"s1": llm, "copy": ScriptedLLM([[say("From the copy."), finish()]])}, recorder)
    spec = make_spec(tmp_path)
    go, entered = threading.Event(), threading.Event()
    real = fork_module.fork_record

    def slow(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert go.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(fork_module, "fork_record", slow)
    try:
        await runtime.start(spec)
        await runtime.prompt("s1", "Do it.")
        await recorder.wait_ended("s1")
        copy = make_spec(tmp_path, "copy")
        copying = asyncio.ensure_future(
            runtime.fork("copy", session_dir=copy.session_dir, source_session_id="s1", source_dir=spec.session_dir)
        )
        await asyncio.to_thread(entered.wait, 10)

        # While the copy is being written: not started from nothing, or from half of it.
        mark = len(recorder.events)
        await runtime.start(copy)
        refused = await recorder.wait_for(lambda event: event.get("type") == "error", start=mark)
        assert (refused["session_id"], refused["command"]) == ("copy", "start") and "being copied" in refused["message"]

        # Whoever waits for the copy is cancelled: the thread still writes, and the id stays taken until it is through.
        copying.cancel()
        await asyncio.gather(copying, return_exceptions=True)
        assert "copy" in runtime.core._forking
        go.set()
        for _attempt in range(200):
            if "copy" not in runtime.core._forking:
                break
            await asyncio.sleep(0.01)
        assert "copy" not in runtime.core._forking
        # What became of it is said all the same, for whoever still has to know that a record was written.
        made = [event for event in recorder.of("forked") if event["session_id"] == "copy"]
        assert len(made) == 1 and made[0]["source_session_id"] == "s1" and made[0]["entries"] > 0
        # Written whole, it is started like any.
        mark = len(recorder.events)
        await runtime.start(copy)
        started = await recorder.wait_for(lambda event: event.get("type") == "started", start=mark)
        assert started["resumed"] is True
    finally:
        go.set()
        await runtime.shutdown()


async def test_a_copy_is_made_whole_or_not_at_all_and_does_not_pass_a_stale_snapshot_for_a_fresh_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nekoclaw.runtime import fork as fork_module

    spec = make_spec(tmp_path, model=_small_model())
    llm = ScriptedLLM()
    session, events = await open_session(spec, llm)
    try:
        await _seed(session, events, llm)
        llm.add(say("## Current Work\n- the four steps are done"), finish())
        session.compact()
        await events.wait_ended(start=len(events.events))
    finally:
        await session.close()
    source = Path(spec.session_dir)
    assert read_snapshot(source).compactions == 1

    # The snapshot cannot be written: nothing of the copy is left, and it can be made again.
    def unwritable(*args: Any, **kwargs: Any) -> None:
        raise OSError("the disk is full")

    target = tmp_path / "sessions" / "copy"
    with monkeypatch.context() as patched:
        patched.setattr(fork_module, "write_snapshot", unwritable)
        with pytest.raises(ForkError, match="snapshot of the copy could not be written"):
            fork_record(source, target)
    assert not target.exists() or list(target.iterdir()) == []
    assert fork_record(source, target).entries > 0 and read_snapshot(target).compactions == 1
    # Nothing is left of the name it was written under first, the lock file kept next to it included.
    assert [path.name for path in target.iterdir() if ".partial" in path.name] == []

    # The other's snapshot is behind its record (summed up, and stopped before what every request
    # begins with was put together anew): the copy's is behind as well, so that it does the same on opening.
    behind = dataclasses.replace(read_snapshot(source), compactions=0)
    write_snapshot(source, behind)
    stale = tmp_path / "sessions" / "stale"
    fork_record(source, stale)
    assert read_snapshot(stale).compactions == 0
    assert sum(1 for entry in _entries(stale) if entry.type == "compaction") == 1


# ---------------------------------------------------------------------------
# What the fourth round of review found
# ---------------------------------------------------------------------------


async def test_a_record_that_was_written_is_told_of_though_whoever_waited_was_cancelled_before_hearing_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Recorder()
    runtime = _runtime({"s1": ScriptedLLM([[say("Done."), finish()]])}, recorder)
    spec = make_spec(tmp_path)
    try:
        await runtime.start(spec)
        await runtime.prompt("s1", "Do it.")
        await recorder.wait_ended("s1")
        copy = make_spec(tmp_path, "copy")
        waiting: list[asyncio.Future[Any]] = []
        in_thread = asyncio.to_thread

        async def written_and_then_cancelled(func: Any, *args: Any, **kwargs: Any) -> Any:
            made = await in_thread(func, *args, **kwargs)
            if getattr(func, "__name__", "") == "fork_record":
                # The record is written. Whoever waits for it is cancelled before getting to hear of it.
                waiting[0].cancel()
            return made

        with monkeypatch.context() as patched:
            patched.setattr(asyncio, "to_thread", written_and_then_cancelled)
            waiting.append(
                asyncio.ensure_future(
                    runtime.fork("copy", session_dir=copy.session_dir, source_session_id="s1", source_dir=spec.session_dir)
                )
            )
            await asyncio.gather(*waiting, return_exceptions=True)
        assert waiting[0].cancelled()

        # Said all the same, once: a host that no longer waits has to learn of the record, to remove it.
        await recorder.wait_for(lambda event: event.get("type") == "forked" and event.get("session_id") == "copy")
        await asyncio.sleep(0.05)
        made = [event for event in recorder.of("forked") if event["session_id"] == "copy"]
        assert len(made) == 1 and made[0]["source_session_id"] == "s1" and made[0]["entries"] > 0
        assert not [event for event in recorder.of("error") if event.get("command") == "fork"]
        assert "copy" not in runtime.core._forking and not runtime.core._copying
    finally:
        await runtime.shutdown()


async def test_shutting_down_waits_for_a_record_that_is_being_copied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from nekoclaw.runtime import fork as fork_module

    recorder = Recorder()
    runtime = _runtime({"s1": ScriptedLLM([[say("Done."), finish()]])}, recorder)
    spec = make_spec(tmp_path)
    go, entered = threading.Event(), threading.Event()
    real = fork_module.fork_record

    def slow(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert go.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(fork_module, "fork_record", slow)
    copying = None
    try:
        await runtime.start(spec)
        await runtime.prompt("s1", "Do it.")
        await recorder.wait_ended("s1")
        copy = make_spec(tmp_path, "copy")
        copying = asyncio.ensure_future(
            runtime.fork("copy", session_dir=copy.session_dir, source_session_id="s1", source_dir=spec.session_dir)
        )
        await asyncio.to_thread(entered.wait, 10)

        shutting = asyncio.ensure_future(runtime.shutdown())
        await asyncio.sleep(0.2)
        # The thread still writes: the runtime is not gone before it is said what became of the copy.
        assert not shutting.done() and not recorder.of("forked")
        go.set()
        await asyncio.wait_for(shutting, 10)
        made = [event for event in recorder.of("forked") if event["session_id"] == "copy"]
        assert len(made) == 1 and made[0]["entries"] > 0
        assert not runtime.core._copying and "copy" not in runtime.core._forking
    finally:
        go.set()
        if copying is not None:
            await asyncio.gather(copying, return_exceptions=True)
        await runtime.shutdown()


async def test_a_tool_called_in_place_of_a_summary_is_a_summary_that_failed(tmp_path: Path) -> None:
    from nekoclaw.runtime.context import SUMMARY_FALLBACK

    spec = make_spec(tmp_path, model=_small_model())
    llm = ScriptedLLM()
    session, events = await open_session(spec, llm)
    try:
        await _seed(session, events, llm)
        # The request offers the tools, as every request of the session does, and the model calls one.
        llm.add(say("Let me look at the files first."), call("glob", {"pattern": "*"}), usage(2_400, 40), finish("tool_calls"))

        # As after a request that was refused as too long: the older part goes either way.
        await session._fit(force=True)

        said = "\n".join(
            getattr(message, "text", None) or str(getattr(message, "content", "")) for message in session.harness.message_list
        )
        # What led up to the call sums nothing up: not kept as a summary that ends early.
        assert "Let me look at the files first." not in said and SUMMARY_CUT_SHORT_NOTE not in said
        assert SUMMARY_FALLBACK in said
        # What the request used is told all the same.
        told = events.of("usage")[-1]
        assert (told["summary"], told["input"], told["output"]) == (True, 2_400, 40)
        assert session._compactions == 1
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# What the fifth round of review found
# ---------------------------------------------------------------------------


async def test_nothing_is_left_of_a_copy_whose_record_could_not_be_put_in_its_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from nekoclaw.runtime.snapshot import SYSTEM_FILE

    spec = make_spec(tmp_path, model=_small_model())
    llm = ScriptedLLM()
    session, events = await open_session(spec, llm)
    try:
        await _seed(session, events, llm)
    finally:
        await session.close()
    source = Path(spec.session_dir)
    assert read_snapshot(source) is not None
    target = tmp_path / "sessions" / "copy"
    replace = os.replace

    def refused(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        # The record, written under another name first, cannot be given its own.
        if str(src).endswith(".partial"):
            raise OSError("the file is in use")
        replace(src, dst, *args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(os, "replace", refused)
        with pytest.raises(ForkError, match="could not be put in its place"):
            fork_record(source, target)
    # Neither the record, nor the snapshot that was written for it this time, nor what it was written under first.
    assert not target.exists() or [path.name for path in target.iterdir()] == []

    # Made again when the other session's snapshot can no longer be read, the copy has none, and
    # puts its own together when it is opened: it is not handed the one of the copy that failed.
    (source / SYSTEM_FILE).unlink()
    assert fork_record(source, target).entries > 0
    assert read_snapshot(target) is None and not (target / SYSTEM_FILE).exists()


# ---------------------------------------------------------------------------
# What stood in the bodies of the reviews (the sixth batch)
# ---------------------------------------------------------------------------


async def test_the_request_that_sums_a_conversation_up_does_not_carry_what_the_host_said_about_usage(tmp_path: Path) -> None:
    answers: list[Any] = []

    async def budget(session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        del session_id, payload
        return answers.pop(0) if answers else None

    llm = ScriptedLLM()
    session, events = await open_session(make_spec(tmp_path, model=_small_model()), llm, HostCallbacks(budget=budget))
    try:
        # Enough was said for a summary to be asked for.
        await _seed(session, events, llm)
        answers.append({"notice": "No more may be used. Hand over what you have."})
        # What it hands over is longer than what a summary leaves word for word: what the host
        # said in front of it is in the part that is summed up, unless it was taken out first.
        llm.add(say("Handing over: half is done. " + _words(4_000, "hw")), finish())
        await _ask(session, events, "Sort the photos.")
        assert _said(llm.requests[-1])[-1] == "user: No more may be used. Hand over what you have."

        llm.add(say("## Current Work\n- half is done"), usage(2_500, 40), finish())
        mark, before = len(events.events), len(llm.requests)
        session.compact()
        await events.wait_ended(start=mark)

        # The request was made, and its summary taken ...
        assert len(llm.requests) == before + 1 and len(events.of("compacted")) == 1
        asked = _said(llm.requests[-1])
        assert asked[-1] == f"user: {texts.COMPACTION_INSTRUCTION}" and any("Finished step 0." in line for line in asked)
        # ... and what the host said about usage in the run that is over was not in it.
        assert not any("No more may be used" in line for line in asked)
    finally:
        await session.close()


async def test_what_the_host_answers_about_usage_is_not_said_to_a_run_that_was_stopped_meanwhile(tmp_path: Path) -> None:
    holder: list[Any] = []

    async def budget(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        del session_id, payload
        # The stop and the answer come together: the run is stopped by the time the answer is looked at.
        holder[0].abort()
        return {"notice": "No more may be used. Hand over what you have."}

    llm = ScriptedLLM([[say("Never said."), finish()]])
    spec = make_spec(tmp_path)
    session, events = await open_session(spec, llm, HostCallbacks(budget=budget))
    holder.append(session)
    try:
        ended = await _ask(session, events, "Sort the photos.")
        assert ended["reason"] == "aborted" and llm.requests == []
        # Nothing more was said to the session, in its conversation or on its record.
        assert not any(getattr(message, "custom_type", None) == "notice:usage" for message in session.harness.message_list)
    finally:
        await session.close()
    assert "No more may be used" not in (Path(spec.session_dir) / TRANSCRIPT_FILE).read_text(encoding="utf-8")


async def test_a_session_with_a_background_command_still_running_is_not_summed_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = ScriptedLLM()
    session, events = await open_session(make_spec(tmp_path, model=_small_model()), llm)
    try:
        await _seed(session, events, llm)
        before = len(llm.requests)
        # What it says when it ends would come while the conversation is summed up, and reach no run.
        monkeypatch.setattr(type(session.jobs), "running", lambda self: ["a job"])
        assert session.busy and not session.running
        with pytest.raises(SessionError, match="background command"):
            session.compact()
        assert not session.running and len(llm.requests) == before
        monkeypatch.undo()
        # With none it is summed up as before.
        llm.add(say("## Current Work\n- the four steps are done"), finish())
        mark = len(events.events)
        session.compact()
        await events.wait_ended(start=mark)
        assert len(events.of("compacted")) == 1
    finally:
        await session.close()


async def test_a_copy_that_could_not_be_begun_is_told_of_and_leaves_its_id_free(tmp_path: Path) -> None:
    recorder = Recorder()
    runtime = _runtime({"s1": ScriptedLLM([[say("Done."), finish()]])}, recorder)
    spec = make_spec(tmp_path)
    try:
        await runtime.start(spec)
        await runtime.prompt("s1", "Do it.")
        await recorder.wait_ended("s1")
        copy = make_spec(tmp_path, "copy")
        source = runtime.core._sessions["s1"]
        flush = source.flush

        async def unwritable() -> None:
            raise OSError("the disk is full")

        # What the other session has said so far cannot be written out: nothing is copied.
        source.flush = unwritable
        mark = len(recorder.events)
        try:
            await runtime.fork("copy", session_dir=copy.session_dir, source_session_id="s1", source_dir=spec.session_dir)
        finally:
            source.flush = flush
        refused = await recorder.wait_for(lambda event: event.get("type") == "error", start=mark)
        assert (refused["session_id"], refused["command"]) == ("copy", "fork") and "the disk is full" in refused["message"]
        assert not [event for event in recorder.events[mark:] if event.get("type") == "forked"]
        # The id is not left taken: the same copy can be asked for again, and is made.
        assert "copy" not in runtime.core._forking and not runtime.core._copying
        mark = len(recorder.events)
        await runtime.fork("copy", session_dir=copy.session_dir, source_session_id="s1", source_dir=spec.session_dir)
        made = await recorder.wait_for(lambda event: event.get("type") == "forked", start=mark)
        assert made["session_id"] == "copy" and made["entries"] > 0
    finally:
        await runtime.shutdown()


async def test_a_summing_up_that_made_no_summary_does_not_say_that_the_conversation_was_summed_up(tmp_path: Path) -> None:
    llm = ScriptedLLM()
    session, events = await open_session(make_spec(tmp_path, model=_small_model()), llm)
    try:
        await _seed(session, events, llm)
        # A summary that was made before the session was last closed still waits for what follows
        # it (the system prompt put together anew): that is what a session opened again finds.
        session._compaction_due = True

        async def fails() -> None:
            raise RuntimeError("the provider is away")

        llm.add(fails)
        mark = len(events.events)
        session.compact()
        assert (await events.wait_ended(start=mark))["reason"] == "completed"
        # What was due is done, and nothing is said of a summing up: there was none this time, and
        # the host would take the word for this one.
        assert session._compaction_due is False and not events.of("compacted")

        # One that does make a summary is told of, as before.
        llm.add(say("## Current Work\n- the four steps are done"), finish())
        mark = len(events.events)
        session.compact()
        await events.wait_ended(start=mark)
        assert len(events.of("compacted")) == 1
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# The ninth batch of the review
# ---------------------------------------------------------------------------


async def test_a_copy_holds_what_is_on_the_record_and_not_what_the_other_session_could_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nekoclaw.runtime import session as session_module

    # (No waiting between the attempts to write an entry.)
    monkeypatch.setattr(session_module, "APPEND_RETRY_DELAYS", ())
    recorder = Recorder()
    llm = ScriptedLLM([[say("The first is done."), finish()], [say("The second is done."), finish()]])
    runtime = _runtime({"s1": llm}, recorder)
    spec = make_spec(tmp_path)
    try:
        await runtime.start(spec)
        await runtime.prompt("s1", "Do the first.", opening=True)
        await recorder.wait_ended("s1")
        writer = runtime.core._sessions["s1"]._recorder
        append = writer._append_blocking

        def full(entry: Any) -> None:
            raise OSError("the disk is full")

        # What is said from here on cannot be written, and is given up on: the session goes on.
        writer._append_blocking = full
        mark = len(recorder.events)
        await runtime.prompt("s1", "Do the second.")
        ended = await recorder.wait_for(lambda event: event.get("type") == "ended", start=mark)
        assert ended["text"] == "The second is done."
        writer._append_blocking = append

        copy = make_spec(tmp_path, "copy")
        mark = len(recorder.events)
        await runtime.fork("copy", session_dir=copy.session_dir, source_session_id="s1", source_dir=spec.session_dir)
        made = await recorder.wait_for(lambda event: event.get("type") == "forked", start=mark)

        # The copy is of the record as it is on the disk: what a session opened from it later would
        # find, and no more. Not an error of the copying.
        on_record = (Path(spec.session_dir) / TRANSCRIPT_FILE).read_text(encoding="utf-8")
        copied = (Path(copy.session_dir) / TRANSCRIPT_FILE).read_text(encoding="utf-8")
        assert "The first is done." in on_record and "The second is done." not in on_record
        assert "The first is done." in copied and "The second is done." not in copied and "Do the second." not in copied
        there = [entry for entry in _entries(Path(spec.session_dir)) if entry.type != "leaf"]
        here = [entry for entry in _entries(Path(copy.session_dir)) if entry.type != "leaf"]
        # Every entry of the record, the mark of the commission that was opened among them, and
        # then the mark of the copy; the count is of what was copied.
        assert any(entry.type == "custom" for entry in there)
        assert [entry.id for entry in here[:-1]] == [entry.id for entry in there]
        assert here[-1].type == "custom" and made["entries"] == len(there)
    finally:
        await runtime.shutdown()


async def test_a_copy_that_was_still_being_prepared_when_the_runtime_shut_down_is_not_made_after_that(tmp_path: Path) -> None:
    recorder = Recorder()
    runtime = _runtime({"s1": ScriptedLLM([[say("Done."), finish()]])}, recorder)
    spec = make_spec(tmp_path)
    go = asyncio.Event()
    try:
        await runtime.start(spec)
        await runtime.prompt("s1", "Do it.")
        await recorder.wait_ended("s1")
        source = runtime.core._sessions["s1"]
        flush = source.flush
        entered = asyncio.Event()

        async def slow() -> None:
            # What the other session has said so far takes its time to be written out.
            entered.set()
            await go.wait()
            await flush()

        source.flush = slow
        copy = make_spec(tmp_path, "copy")
        mark = len(recorder.events)
        forking = asyncio.ensure_future(
            runtime.fork("copy", session_dir=copy.session_dir, source_session_id="s1", source_dir=spec.session_dir)
        )
        await asyncio.wait_for(entered.wait(), 10)

        # The runtime is shut down meanwhile, and that is over: whoever asked for the copy may have
        # tidied up after it, and listens no longer.
        await runtime.core.shutdown(timeout=0.05)
        # Let go of then and there, while what it waited for is still not through: it says once
        # that it is not made, and its id is free.
        await asyncio.wait_for(forking, 10)
        assert not go.is_set()

        refused = await recorder.wait_for(
            lambda event: event.get("type") == "error" and event.get("command") == "fork", start=mark
        )
        assert refused["session_id"] == "copy" and "shutting down" in refused["message"]
        # Nothing was written behind its back, and the id is not left taken.
        assert not [event for event in recorder.events[mark:] if event.get("type") == "forked"]
        assert not (Path(copy.session_dir) / TRANSCRIPT_FILE).exists()
        assert "copy" not in runtime.core._forking and not runtime.core._copying and not runtime.core._preparing
        # The same copy can be asked for again: the other session is closed by now, and its record is there.
        mark = len(recorder.events)
        await runtime.fork("copy", session_dir=copy.session_dir, source_session_id="s1", source_dir=spec.session_dir)
        made = await recorder.wait_for(lambda event: event.get("type") == "forked", start=mark)
        assert made["session_id"] == "copy" and made["entries"] > 0
    finally:
        go.set()
        await runtime.shutdown()


async def test_what_can_be_removed_of_a_copy_that_failed_is_removed_though_one_file_cannot_be(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from nekoclaw.runtime.snapshot import SYSTEM_FILE

    spec = make_spec(tmp_path, model=_small_model())
    llm = ScriptedLLM()
    session, events = await open_session(spec, llm)
    try:
        await _seed(session, events, llm)
    finally:
        await session.close()
    source = Path(spec.session_dir)
    target = tmp_path / "sessions" / "copy"
    replace = os.replace
    unlink = Path.unlink

    def refused(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        if str(src).endswith(".partial"):
            raise OSError("the file is in use")
        replace(src, dst, *args, **kwargs)

    def not_the_snapshot(self: Path, *args: Any, **kwargs: Any) -> None:
        if self.name == SYSTEM_FILE and self.parent == target:
            raise OSError("the snapshot is in use")
        unlink(self, *args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(os, "replace", refused)
        patched.setattr(Path, "unlink", not_the_snapshot)
        # Told as what went wrong first: that the record could not be put in its place.
        with pytest.raises(ForkError, match="could not be put in its place"):
            fork_record(source, target)
    # The snapshot could not be removed; the record under its first name and its lock file were.
    assert [path.name for path in target.iterdir()] == [SYSTEM_FILE]


# ---------------------------------------------------------------------------
# The tenth batch of the review
# ---------------------------------------------------------------------------


async def test_a_summary_the_record_did_not_take_is_not_told_of_as_a_summing_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nekoclaw.runtime import session as session_module

    # (No waiting between the attempts to write an entry.)
    monkeypatch.setattr(session_module, "APPEND_RETRY_DELAYS", ())
    spec = make_spec(tmp_path, model=_small_model())
    llm = ScriptedLLM()
    session, events = await open_session(spec, llm)
    try:
        await _seed(session, events, llm)
        length = len(session.harness.message_list)
        writer = session._recorder
        append = writer._append_blocking
        # (What tells the capability gateway how often the conversation was summed up.)
        gateway = types.SimpleNamespace(compactions=0)
        session._gateway = gateway

        def not_the_summary(entry: Any) -> None:
            if entry.type == "compaction":
                raise OSError("the disk is full")
            append(entry)

        writer._append_blocking = not_the_summary
        llm.add(say("## Current Work\n- the four steps are done"), finish())
        mark = len(events.events)
        session.compact()
        assert (await events.wait_ended(start=mark))["reason"] == "completed"
        # The record did not take the summary: the conversation is what it was, as the record has
        # it and as a session opened from the record later finds it. It is not counted, here or
        # in the snapshot, and the host is not told that the conversation was summed up.
        assert not events.of("compacted")
        assert len(session.harness.message_list) == length and session._compactions == 0
        assert session._compaction_due is False and not read_snapshot(Path(spec.session_dir)).compactions
        # Nor is the gateway told of one: it would take the tools it handed over for forgotten.
        assert gateway.compactions == 0
        assert '"type":"compaction"' not in (Path(spec.session_dir) / TRANSCRIPT_FILE).read_text(encoding="utf-8")

        # Summed up again once the record takes it: told of, and the summary stands for what was
        # said, all of which is on the record.
        writer._append_blocking = append
        llm.add(say("## Current Work\n- said again: the four steps are done"), finish())
        mark = len(events.events)
        session.compact()
        await events.wait_ended(start=mark)
        assert len([event for event in events.events[mark:] if event.get("type") == "compacted"]) == 1
        on_record = _entries(Path(spec.session_dir))
        (summary,) = [entry for entry in on_record if entry.type == "compaction"]
        assert "said again: the four steps are done" in summary.summary
        assert set(summary.replaces_entry_ids) <= {entry.id for entry in on_record}
        assert gateway.compactions == 1
    finally:
        session._gateway = None
        await session.close()


# ---------------------------------------------------------------------------
# The eleventh batch of the review
# ---------------------------------------------------------------------------


async def test_a_copy_of_a_copy_that_ends_in_what_the_first_session_said_reads_what_that_one_kept(tmp_path: Path) -> None:
    # A long result is cut in the conversation and kept whole in a file of the first session.
    first = make_spec(tmp_path)
    session, events = await open_session(first, ScriptedLLM([[say("Read."), finish()]]))
    await _ask(session, events, "Read the long file.")
    await session.close()
    kept = Path(first.session_dir) / "outputs" / "pwsh-call-1.txt"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("the whole of a long result\n", encoding="utf-8")
    said_then = [entry.id for entry in _entries(Path(first.session_dir)) if entry.type == "message"][-1]

    # A copy of it, in which more is said; and a copy of that one which ends where the first did.
    second = make_spec(tmp_path, "second")
    fork_record(Path(first.session_dir), Path(second.session_dir))
    session, events = await open_session(second, ScriptedLLM([[say("More."), finish()]]))
    await _ask(session, events, "Say more.")
    await session.close()
    third = make_spec(tmp_path, "third")
    fork_record(Path(second.session_dir), Path(third.session_dir), upto=said_then)

    # The mark of where the first kept its output lies behind where this copy ends: carried along.
    mark = _entries(Path(third.session_dir))[-1]
    assert mark.data["outputs"] == str(Path(second.session_dir) / "outputs")
    assert mark.data["outputs_behind"] == [str(kept.parent)]
    # (A copy of all of it holds that mark itself, and names nothing behind it.)
    whole = make_spec(tmp_path, "whole")
    fork_record(Path(second.session_dir), Path(whole.session_dir))
    assert "outputs_behind" not in _entries(Path(whole.session_dir))[-1].data

    asks: list[dict[str, Any]] = []

    async def decide(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        asks.append({"session": session_id, **payload})
        return {"allow": payload["category"] == "workdir_files", "reason": "the user said no"}

    reader = ScriptedLLM([[call("read", {"file_path": str(kept)}), finish("tool_calls")], [say("Seen."), finish()]])
    copy, copy_events = await open_session(third, reader, HostCallbacks(decide_permission=decide))
    try:
        await _ask(copy, copy_events, "Look at what was kept.")
        (read,) = [event for event in copy_events.of("tool_end") if event["tool_name"] == "read"]
        # Read without anybody being asked, as the copy it was made from reads it.
        assert read["is_error"] is False and "the whole of a long result" in read["text"]
        assert not [ask for ask in asks if ask.get("category") == "outside_read"]
    finally:
        await copy.close()


async def test_a_copy_whose_preparing_ends_just_as_the_runtime_begins_to_shut_down_is_not_made(tmp_path: Path) -> None:
    recorder = Recorder()
    runtime = _runtime({"s1": ScriptedLLM([[say("Done."), finish()]])}, recorder)
    spec = make_spec(tmp_path)
    try:
        await runtime.start(spec)
        await runtime.prompt("s1", "Do it.")
        await recorder.wait_ended("s1")
        source = runtime.core._sessions["s1"]
        flush = source.flush

        async def written_as_the_shutdown_begins() -> None:
            await flush()
            # A shutdown begins in this moment: after everything the copy waited for, and before
            # the copying is handed to its thread.
            runtime.core._shutdowns_begun += 1

        source.flush = written_as_the_shutdown_begins
        copy = make_spec(tmp_path, "copy")
        mark = len(recorder.events)
        try:
            await runtime.fork("copy", session_dir=copy.session_dir, source_session_id="s1", source_dir=spec.session_dir)
        finally:
            source.flush = flush
        refused = await recorder.wait_for(
            lambda event: event.get("type") == "error" and event.get("command") == "fork", start=mark
        )
        assert refused["session_id"] == "copy" and "shutting down" in refused["message"]
        assert not (Path(copy.session_dir) / TRANSCRIPT_FILE).exists()
        assert "copy" not in runtime.core._forking and not runtime.core._copying and not runtime.core._preparing
    finally:
        await runtime.shutdown()
