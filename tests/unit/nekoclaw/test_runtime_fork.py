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
import json
from pathlib import Path
from typing import Any

import pytest

from config.prompts import prompts_nekoclaw as texts
from nekoclaw.runtime import protocol
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
from nekoclaw.runtime.session import TRANSCRIPT_FILE, TaskAgentSession
from nekoclaw.runtime.snapshot import SYSTEM_FILE, read_snapshot
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
        for _attempt in range(200):
            if llm.requests and llm.requests[-1][-1].get("content") == texts.COMPACTION_INSTRUCTION:
                break
            await asyncio.sleep(0.01)
        await runtime.abort("s1")
        stopped = await asyncio.wait_for(recorder.wait_ended("s1", start=mark), 5)
        assert stopped["reason"] == "aborted"
        assert not recorder.of("compacted")
        assert len(runtime.core._sessions["s1"].harness.message_list) == length
        assert not any(entry.type == "compaction" for entry in _entries(Path(spec.session_dir)))

        # A session nobody knows.
        await runtime.compact("nobody")
        assert recorder.of("error")[-1]["command"] == "compact"
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

        # A run that is stopped while it waits ends as stopped, and sends nothing.
        answer = asyncio.get_running_loop().create_future()
        waiting.clear()
        mark = len(events.events)
        session.prompt("More work.")
        await asyncio.wait_for(waiting.wait(), 5)
        session.abort()
        assert (await asyncio.wait_for(events.wait_ended(start=mark), 5))["reason"] == "aborted"
        assert len(llm.requests) == 1
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
