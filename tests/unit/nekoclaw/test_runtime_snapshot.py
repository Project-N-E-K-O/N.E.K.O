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

"""A session that is opened again begins its requests as it did before.

The system prompt and the tool definitions a session was opened with are
kept in its directory; what follows a summary of the conversation (a new
snapshot) and what a summary leaves alone (the text that opened the
commission under way); the window a provider states.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from config.prompts import prompts_nekoclaw as texts
from nekoclaw.runtime.model import is_context_overflow, window_from_overflow
from nekoclaw.runtime.protocol import ModelConfig
from nekoclaw.runtime import snapshot as snapshots
from nekoclaw.runtime.snapshot import (
    REASON_COMPACTED,
    REASON_MOVED,
    REASON_OPENED,
    SYSTEM_FILE,
    _VERSION,
    SystemSnapshot,
    ToolDefinition,
    read_snapshot,
    write_snapshot,
)
from tests.unit.nekoclaw.fakes import ScriptedLLM, call, finish, make_spec, open_session, say, usage
from utils.tokenize import count_tokens


def _words(count: int, prefix: str) -> str:
    return " ".join(f"{prefix}{index}" for index in range(count))


def _small_model(window: int = 32_000) -> ModelConfig:
    return ModelConfig(model="fake-model", api_key="k", context_window=window, max_output_tokens=2_000)


def _system_of(request: list[dict[str, Any]]) -> str:
    assert request[0]["role"] == "system"
    return str(request[0]["content"])


def _tool_names(overrides: dict[str, Any]) -> list[str]:
    return [tool["function"]["name"] for tool in overrides["tools"]]


def _saved(spec: Any) -> dict[str, Any]:
    return json.loads((Path(spec.session_dir) / SYSTEM_FILE).read_text(encoding="utf-8"))


async def _ask(session: Any, events: Any, text: str, **options: Any) -> dict[str, Any]:
    mark = len(events.events)
    session.prompt(text, **options)
    return await events.wait_ended(start=mark)


_OVERFLOW = "Error code: 400 - This model's maximum context length is 32000 tokens."


async def _seed(session: Any, events: Any, llm: ScriptedLLM, turns: int = 4) -> None:
    """Give the session enough of a conversation that there is something to sum up."""
    for index in range(turns):
        llm.add(say(f"Finished step {index}. " + _words(300, f"s{index}w")), finish())
        await _ask(session, events, f"Do step {index}.")


async def _sum_up(session: Any, events: Any, llm: ScriptedLLM, text: str = "Go on.", **options: Any) -> dict[str, Any]:
    """Have the next request refused as too long: the conversation is summed up and the request sent again."""
    llm.add(RuntimeError(_OVERFLOW))
    llm.add(say("## Current Work\n- summed up"), finish())
    llm.add(say("Went on."), finish())
    return await _ask(session, events, text, **options)


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------


def test_a_snapshot_is_read_back_as_it_was_written(tmp_path: Path) -> None:
    snapshot = SystemSnapshot(
        system="You are a task agent.",
        tools=(
            ToolDefinition("read", "Read a file.", {"type": "object", "properties": {"file_path": {"type": "string"}}}),
            ToolDefinition("report", "Report.", {"type": "object", "properties": {}}),
        ),
        reason=REASON_COMPACTED,
        made_at=1_800_000_000.0,
    )
    assert read_snapshot(tmp_path) is None

    write_snapshot(tmp_path, snapshot)

    assert read_snapshot(tmp_path) == snapshot
    assert read_snapshot(tmp_path).tool_names == {"read", "report"}


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        # Of another version. Version 1 named the language of the commission a
        # session was opened for in its system prompt, and its report tool told
        # the model to use the language of the user's request; version 2 had no
        # tools that reach other sessions. A session takes up what its version
        # brought when it is next opened.
        json.dumps({"version": snapshots._VERSION + 1, "system": "s", "tools": []}),
        json.dumps({"version": 1, "system": "s", "tools": []}),
        json.dumps({"version": 2, "system": "s", "tools": []}),
        json.dumps({"version": snapshots._VERSION, "system": "", "tools": []}),
        json.dumps({"version": snapshots._VERSION, "system": "s", "tools": "read"}),
        json.dumps({"version": snapshots._VERSION, "system": "s", "tools": ["read"]}),
        json.dumps({"version": snapshots._VERSION, "system": "s", "tools": [{"name": "read", "description": "d"}]}),
        json.dumps({"version": snapshots._VERSION, "system": "s", "tools": [{"name": "", "description": "d", "parameters": {}}]}),
        json.dumps(
            {
                "version": snapshots._VERSION,
                "system": "s",
                "tools": [{"name": "a", "description": "d", "parameters": {}}] * 2,
            }
        ),
    ],
)
def test_a_file_that_is_not_a_snapshot_counts_as_none(tmp_path: Path, content: str) -> None:
    (tmp_path / SYSTEM_FILE).write_text(content, encoding="utf-8")
    assert read_snapshot(tmp_path) is None


def test_a_file_of_this_version_is_a_snapshot(tmp_path: Path) -> None:
    # What the cases above leave out, one thing each, is all here: it is
    # for what they leave out that they count as none.
    (tmp_path / SYSTEM_FILE).write_text(json.dumps({"version": _VERSION, "system": "s", "tools": []}), encoding="utf-8")
    assert read_snapshot(tmp_path) is not None


# ---------------------------------------------------------------------------
# Opening a session again
# ---------------------------------------------------------------------------


async def test_a_session_keeps_what_it_was_opened_with_and_begins_its_requests_the_same_when_opened_again(
    tmp_path: Path,
) -> None:
    first_llm = ScriptedLLM([[say("Done."), finish()]])
    session, events = await open_session(make_spec(tmp_path, instructions="Space index: nothing yet."), first_llm)
    await _ask(session, events, "First commission.")
    await session.close()

    spec = make_spec(tmp_path, instructions="Space index: three entries by now.")
    saved = _saved(spec)
    assert saved["version"] == snapshots._VERSION and saved["reason"] == REASON_OPENED
    assert saved["system"] == _system_of(first_llm.requests[0])
    assert "Space index: nothing yet." in saved["system"]
    assert [tool["name"] for tool in saved["tools"]] == _tool_names(first_llm.overrides[0])

    # Opened for the next commission, with other instructions by now: the
    # system prompt and the tools are those of before.
    second_llm = ScriptedLLM([[say("Done again."), finish()]])
    session, events = await open_session(spec, second_llm)
    try:
        assert events.of("started")[0]["resumed"] is True
        assert events.of("started")[0]["tools"] == [tool["name"] for tool in saved["tools"]]
        await _ask(session, events, "Second commission.")
        assert _system_of(second_llm.requests[0]) == _system_of(first_llm.requests[0])
        assert second_llm.overrides[0]["tools"] == first_llm.overrides[0]["tools"]
        assert "three entries by now" not in _system_of(second_llm.requests[0])
    finally:
        await session.close()
    # Nothing was written anew.
    assert _saved(spec) == saved


async def test_a_snapshot_written_before_the_session_tools_is_put_together_anew_when_its_session_is_opened(
    tmp_path: Path,
) -> None:
    from nekoclaw.runtime.host import HostCallbacks

    async def answer(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"text": "", "message": ""}

    host = HostCallbacks(session_list=answer, session_read=answer, session_send=answer, wake_schedule=answer)
    first_llm = ScriptedLLM([[say("Done."), finish()]])
    session, events = await open_session(make_spec(tmp_path), first_llm, host)
    try:
        await _ask(session, events, "First commission.")
    finally:
        await session.close()
    spec = make_spec(tmp_path)
    # As a host of the version before left it: version 1, by its number and not by one counted
    # from this version, and none of the tools that came with the next.
    new = {"list_sessions", "read_session", "message_session", "schedule_wake"}
    saved = _saved(spec)
    assert new <= {tool["name"] for tool in saved["tools"]}
    left = {**saved, "version": 1, "tools": [tool for tool in saved["tools"] if tool["name"] not in new]}
    (Path(spec.session_dir) / SYSTEM_FILE).write_text(json.dumps(left), encoding="utf-8")
    assert read_snapshot(Path(spec.session_dir)) is None

    second_llm = ScriptedLLM([[say("Done again."), finish()]])
    session, events = await open_session(spec, second_llm, host)
    try:
        await _ask(session, events, "Second commission.")
        assert new <= set(_tool_names(second_llm.overrides[0]))
    finally:
        await session.close()
    assert _saved(spec)["version"] > 1 and new <= {tool["name"] for tool in _saved(spec)["tools"]}


async def test_the_goal_a_session_is_started_with_is_the_goal_of_the_commission_that_is_opened(tmp_path: Path) -> None:
    from nekoclaw.runtime.protocol import GoalSpec

    stopped = asyncio.Event()

    async def until_stopped() -> None:
        await stopped.wait()

    first_llm = ScriptedLLM(
        [
            [call("create_goal", {"objective": "finish the export"}, "c1"), finish("tool_calls")],
            [until_stopped, say("never said"), finish()],
        ]
    )
    session, events = await open_session(make_spec(tmp_path), first_llm)
    session.prompt("First commission.", opening=True)

    async def made() -> None:
        while not events.of("tool_end"):
            await asyncio.sleep(0.01)

    await asyncio.wait_for(made(), 10)
    await session.close()
    stopped.set()

    # Started for the next commission with a goal of its own, while the one before left one.
    spec = make_spec(tmp_path, goal=GoalSpec(objective="draw the chart", max_rounds=1))
    session, events = await open_session(spec, ScriptedLLM([[say("Done."), finish()]]))
    try:
        assert session.goals.current.objective == "finish the export"
        await _ask(session, events, "Second commission.", opening=True)
        # Not lost with the other one's: it is the goal of the commission that was opened.
        assert session.goals.current is not None and session.goals.current.objective == "draw the chart"
    finally:
        await session.close()


async def test_an_opening_message_too_long_for_the_window_is_summed_up_like_the_rest(tmp_path: Path) -> None:
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(make_spec(tmp_path, model=_small_model()), llm)
    try:
        # As a task agent hands another its part: as long as it likes.
        handed = "Your part: count the files\n" + _words(6_000, "p")
        room = (session.context.policy.threshold - session.context.fixed_tokens) // 2
        assert count_tokens(handed) > room
        await _ask(session, events, handed, opening=True)
        assert session._opening_entry_id is not None
        # Kept whole next to what every request begins with, it would leave a summary nothing
        # to shorten, and the request would be refused however often it was summed up.
        assert session._pinned() == ()
        await _seed(session, events, llm)
        ended = await _sum_up(session, events, llm)
        assert ended["reason"] == "completed" and ended["text"] == "Went on."
        assert await session._size() <= session.context.policy.threshold

        # One that has room stays word for word.
        await _ask(session, events, "Commission B\nadd a chart", opening=True)
        assert session._pinned() == (session._opening_entry_id,)
    finally:
        await session.close()


@pytest.mark.parametrize("opening", [True, False])
async def test_what_a_commission_left_unfinished_is_not_handed_to_the_next_one_of_its_session(
    tmp_path: Path, opening: bool
) -> None:
    todos = [{"content": "export the rows", "status": "in_progress"}, {"content": "write the header", "status": "pending"}]
    stopped = asyncio.Event()

    async def until_stopped() -> None:
        await stopped.wait()

    first_llm = ScriptedLLM(
        [
            [call("todo_write", {"todos": todos}, "c1"), finish("tool_calls")],
            [call("create_goal", {"objective": "finish the export"}, "c2"), finish("tool_calls")],
            [until_stopped, say("never said"), finish()],
        ]
    )
    session, events = await open_session(make_spec(tmp_path), first_llm)
    session.prompt("First commission.", opening=True)

    async def both_done() -> None:
        while len(events.of("tool_end")) < 2:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(both_done(), 10)
    assert [end["is_error"] for end in events.of("tool_end")] == [False, False]
    # Stopped half way: the list has open items and the goal is still the goal.
    await session.close()
    stopped.set()

    llm = ScriptedLLM(
        [
            [call("create_goal", {"objective": "draw the chart"}, "c3"), finish("tool_calls")],
            [say("Done."), finish()],
        ]
    )
    session, events = await open_session(make_spec(tmp_path), llm)
    try:
        # Opened again with both, as a commission that goes on needs them.
        assert [item["content"] for item in session.todos.open_items()] == ["export the rows", "write the header"]
        assert session.goals.current is not None and session.goals.current.objective == "finish the export"
        assert session.goals.current.phase == "active"

        await _ask(session, events, "Second commission." if opening else "Go on with it.", opening=opening)

        # What the runtime tells of the time and of what is open, with the first request of this start.
        notices = [
            str(message["content"])
            for message in llm.requests[0]
            if message["role"] == "user" and str(message["content"]).startswith("<runtime_context>")
        ]
        made = events.of("tool_end")[0]
        if opening:
            # Another piece of work: it is not told of the other's open items, and makes a goal of its own.
            assert "export the rows" not in notices[-1]
            assert session.todos.open_items() == []
            assert made["is_error"] is False and session.goals.current.objective == "draw the chart"
        else:
            # The same commission going on: its list is told again, and its goal is still its goal.
            assert "export the rows" in notices[-1] and "write the header" in notices[-1]
            assert made["is_error"] is True and session.goals.current.objective == "finish the export"
    finally:
        await session.close()

    # What was dropped stays dropped when the session is opened once more.
    session, events = await open_session(make_spec(tmp_path), ScriptedLLM([[say("ok"), finish()]]))
    try:
        assert bool(session.todos.open_items()) is (not opening)
        assert session.goals.current.objective == ("draw the chart" if opening else "finish the export")
    finally:
        await session.close()


async def test_a_tool_is_described_to_the_model_as_the_snapshot_has_it_and_carried_out_by_what_there_is_now(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "work"
    session, events = await open_session(make_spec(tmp_path), ScriptedLLM([[say("ok"), finish()]]))
    await _ask(session, events, "First commission.")
    await session.close()
    spec = make_spec(tmp_path)
    # As a build that worded its tools otherwise left it.
    saved = _saved(spec)
    for tool in saved["tools"]:
        tool["description"] = f"As it was: {tool['name']}."
    saved["system"] = "The system prompt of then."
    (Path(spec.session_dir) / SYSTEM_FILE).write_text(json.dumps(saved), encoding="utf-8")
    (workdir / "note.txt").write_text("the note", encoding="utf-8")

    llm = ScriptedLLM(
        [[call("read", {"file_path": str(workdir / "note.txt")}, "c1"), finish("tool_calls")], [say("Read it."), finish()]]
    )
    session, events = await open_session(spec, llm)
    try:
        await _ask(session, events, "Second commission.")
        assert _system_of(llm.requests[0]) == "The system prompt of then."
        described = {tool["function"]["name"]: tool["function"]["description"] for tool in llm.overrides[0]["tools"]}
        assert described["read"] == "As it was: read." and set(described.values()) == {
            f"As it was: {name}." for name in described
        }
        end = events.of("tool_end")[0]
        assert end["is_error"] is False and "the note" in end["text"]
        # What the conversation is measured against is what is sent.
        assert session.context.fixed_tokens == await session._fixed_tokens()
    finally:
        await session.close()


async def test_a_session_with_nothing_said_yet_is_put_together_from_what_there_is_now(tmp_path: Path) -> None:
    spec = make_spec(tmp_path, instructions="Fresh instructions.")
    directory = Path(spec.session_dir)
    directory.mkdir(parents=True)
    # Left behind by a session whose transcript was removed since.
    write_snapshot(directory, SystemSnapshot("An old prompt.", (ToolDefinition("read", "old", {}),)))

    llm = ScriptedLLM([[say("Done."), finish()]])
    session, events = await open_session(spec, llm)
    try:
        assert events.of("started")[0]["resumed"] is False
        await _ask(session, events, "Start.")
        assert "Fresh instructions." in _system_of(llm.requests[0])
        assert "An old prompt." not in _system_of(llm.requests[0])
        described = {tool["function"]["name"]: tool["function"]["description"] for tool in llm.overrides[0]["tools"]}
        assert len(described) > 5 and described["read"] != "old"
    finally:
        await session.close()
    assert _saved(spec)["reason"] == REASON_OPENED and "Fresh instructions." in _saved(spec)["system"]


async def test_a_tool_that_came_since_the_snapshot_is_told_of_only_after_the_conversation_was_summed_up(
    tmp_path: Path,
) -> None:
    session, events = await open_session(
        make_spec(tmp_path, model=_small_model(), instructions="First.", disabled_tools=["todo_write"]),
        ScriptedLLM([[say("ok"), finish()]]),
    )
    await _ask(session, events, "First commission.")
    await session.close()
    before = _saved(make_spec(tmp_path))
    assert "todo_write" not in [tool["name"] for tool in before["tools"]]

    spec = make_spec(tmp_path, model=_small_model(), instructions="Second.")
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(spec, llm)
    try:
        # The head of the requests is what it was: the session goes on without the tool.
        assert events.of("started")[0]["tools"] == [tool["name"] for tool in before["tools"]]
        await _ask(session, events, "Second commission.")
        assert "todo_write" not in _tool_names(llm.overrides[0])
        assert "First." in _system_of(llm.requests[0]) and "Second." not in _system_of(llm.requests[0])
        assert _saved(spec) == before

        # Summed up, it is told of what there is now, and the tool is carried out when called.
        await _seed(session, events, llm)
        await _sum_up(session, events, llm)
        assert "todo_write" in _tool_names(llm.overrides[-1])
        assert "Second." in _system_of(llm.requests[-1])
        saved = _saved(spec)
        assert saved["reason"] == REASON_COMPACTED and "todo_write" in [tool["name"] for tool in saved["tools"]]
        llm.add(call("todo_write", {"todos": [{"content": "x", "status": "pending"}]}, "c1"), finish("tool_calls"))
        llm.add(say("Noted it."), finish())
        ended = await _ask(session, events, "Note it down.")
        end = events.of("tool_end")[-1]
        assert end["tool_name"] == "todo_write" and end["is_error"] is False
        assert ended["text"] == "Noted it."
    finally:
        await session.close()


@pytest.mark.parametrize("on_record", [True, False])
async def test_a_session_whose_working_directory_is_another_by_now_has_its_system_prompt_put_together_anew(
    tmp_path: Path, on_record: bool
) -> None:
    first_llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(make_spec(tmp_path, instructions="First."), first_llm)
    await _ask(session, events, "First commission.")
    await session.close()
    spec = make_spec(tmp_path, instructions="Second.")
    before = _saved(spec)
    assert before["workdir"] == str(tmp_path / "work") and before["workdir"] in before["system"]
    if not on_record:
        # As a snapshot of a build that did not write it down.
        del before["workdir"]
        (Path(spec.session_dir) / SYSTEM_FILE).write_text(json.dumps(before), encoding="utf-8")

    # The data was moved: the session is opened with its working directory where that is now.
    moved = tmp_path / "moved" / "work"
    moved.mkdir(parents=True)
    spec = spec.model_copy(update={"workdir": str(moved)})
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(spec, llm)
    try:
        await _ask(session, events, "Second commission.")
        system = _system_of(llm.requests[0])
        if on_record:
            # The system prompt names the directory the tools work in: left as it was, every
            # path the task agent writes would be one that is no longer there.
            assert str(moved) in system and str(tmp_path / "work") not in system
            assert "Second." in system
            saved = _saved(spec)
            assert (saved["reason"], saved["workdir"]) == (REASON_MOVED, str(moved))
        else:
            # Nothing says which directory it was made for: kept, as every snapshot is.
            assert system == _system_of(first_llm.requests[0])
            assert _saved(spec) == before
    finally:
        await session.close()


def test_the_numbers_that_are_written_down_in_two_places_are_the_same() -> None:
    import config.nekoclaw_settings as settings
    from nekoclaw.dsh import driver, forward
    from nekoclaw.runtime import model, protocol, session

    assert protocol.MIN_CONTEXT_WINDOW == settings.TASK_AGENT_CONTEXT_WINDOW_MIN
    assert session.MIN_CONTEXT_WINDOW == driver._MIN_CONTEXT_WINDOW == protocol.MIN_CONTEXT_WINDOW
    # The forwarder does not load the model client to forward a request, so it has its own.
    assert forward._OVERFLOW_STATUSES == model.OVERFLOW_STATUSES


async def test_a_tool_of_the_snapshot_that_is_not_there_now_stays_listed_and_says_so_when_called(
    tmp_path: Path,
) -> None:
    first_llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(make_spec(tmp_path, instructions="First."), first_llm)
    await _ask(session, events, "First commission.")
    await session.close()
    before = _saved(make_spec(tmp_path))

    llm = ScriptedLLM(
        [
            [call("todo_write", {"todos": [{"content": "x", "status": "pending"}]}, "c1"), finish("tool_calls")],
            [say("Went on without it."), finish()],
        ]
    )
    spec = make_spec(tmp_path, instructions="Second.", disabled_tools=["todo_write"])
    session, events = await open_session(spec, llm)
    try:
        # The head of the requests is what it was: the tool is still described, word for word.
        assert events.of("started")[0]["tools"] == [tool["name"] for tool in before["tools"]]
        ended = await _ask(session, events, "Second commission.")
        assert llm.overrides[0]["tools"] == first_llm.overrides[0]["tools"]
        assert _system_of(llm.requests[0]) == _system_of(first_llm.requests[0])
        end = events.of("tool_end")[0]
        assert end["is_error"] is True
        assert texts.TOOL_UNAVAILABLE.replace("<<NAME>>", "todo_write") in end["text"]
        assert ended["text"] == "Went on without it."
    finally:
        await session.close()
    assert _saved(spec) == before


# ---------------------------------------------------------------------------
# After a summary
# ---------------------------------------------------------------------------


async def test_after_a_summary_the_system_prompt_and_the_tools_are_put_together_anew(tmp_path: Path) -> None:
    session, events = await open_session(
        make_spec(tmp_path, model=_small_model(), instructions="Index of then.", disabled_tools=["todo_write"]),
        ScriptedLLM([[say("ok"), finish()]]),
    )
    await _ask(session, events, "First commission.")
    await session.close()

    # Opened again with more to tell and one more tool; a tool of then is gone. More to tell
    # by now, so what every request begins with is longer than it was.
    later = "Index of later. " + "Something more was learned since. " * 12
    spec = make_spec(tmp_path, model=_small_model(), instructions=later, disabled_tools=["web_fetch"])
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(spec, llm)
    try:
        await _ask(session, events, "Second commission.")
        assert "Index of then." in _system_of(llm.requests[0])
        assert "todo_write" not in _tool_names(llm.overrides[0]) and "web_fetch" in _tool_names(llm.overrides[0])
        await _seed(session, events, llm)
        requests_before = len(llm.requests)
        fixed_before = session.context.fixed_tokens

        ended = await _sum_up(session, events, llm)

        assert ended["reason"] == "completed" and ended["text"] == "Went on."
        refused, summary, retry = llm.requests[requests_before:]
        # The request that is summed up, and the summary itself, begin as the requests before them did.
        assert _system_of(refused) == _system_of(summary) == _system_of(llm.requests[0])
        assert llm.overrides[requests_before + 1]["tools"] == llm.overrides[0]["tools"]
        assert summary[-1] == {"role": "user", "content": texts.COMPACTION_INSTRUCTION}
        # What follows the summary begins with what there is to tell by now.
        assert "Index of later." in _system_of(retry) and "Index of then." not in _system_of(retry)
        told = _tool_names(llm.overrides[requests_before + 2])
        assert "todo_write" in told and "web_fetch" not in told
        compacted = events.of("compacted")
        assert [(event["count"], event["tokens_before"] > 0) for event in compacted] == [(1, True)]
        # Told before the request that follows it is answered.
        assert events.events.index(compacted[0]) < events.events.index(ended)
        saved = _saved(spec)
        assert saved["reason"] == REASON_COMPACTED and saved["system"] == _system_of(retry)
        # What every request begins with was measured anew with it.
        assert session.context.fixed_tokens == count_tokens(
            saved["system"]
            + json.dumps(
                [[tool["name"], tool["description"], tool["parameters"]] for tool in saved["tools"]], ensure_ascii=False
            )
        )
        assert session.context.fixed_tokens != fixed_before

        # The requests after that keep beginning that way.
        llm.add(say("More."), finish())
        await _ask(session, events, "And more.")
        assert _system_of(llm.requests[-1]) == _system_of(retry)
        assert len(events.of("compacted")) == 1
    finally:
        await session.close()


async def test_the_conversation_is_fitted_again_next_to_what_the_requests_begin_with_after_a_summary(
    tmp_path: Path,
) -> None:
    session, events = await open_session(
        make_spec(tmp_path, model=_small_model(), instructions="Index of then."), ScriptedLLM([[say("ok"), finish()]])
    )
    await _ask(session, events, "First commission.")
    await session.close()

    # Much more to tell by now: what every request begins with grows when it is put together anew.
    later = "Index of later. " + "Something more was learned since. " * 400
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(make_spec(tmp_path, model=_small_model(), instructions=later), llm)
    try:
        await _ask(session, events, "Second commission.")
        await _seed(session, events, llm)
        fitted: list[tuple[bool, int]] = []
        fit = session._fit

        async def recording(*, force: bool) -> None:
            fitted.append((force, session.context.fixed_tokens))
            await fit(force=force)

        session._fit = recording
        before = session.context.fixed_tokens
        ended = await _sum_up(session, events, llm)

        assert ended["reason"] == "completed" and ended["text"] == "Went on."
        after = session.context.fixed_tokens
        assert after > before + 2_000
        # Before the request, when it was refused, and once more next to the longer beginning:
        # summed up next to the shorter one, the conversation may no longer fit.
        assert fitted == [(False, before), (True, before), (False, after)]
    finally:
        await session.close()


async def test_what_follows_a_summary_is_done_again_by_the_next_run_when_it_was_stopped_half_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(make_spec(tmp_path, model=_small_model(), instructions="Index."), llm)
    try:
        await _ask(session, events, "First commission.")
        await _seed(session, events, llm)
        await _sum_up(session, events, llm)
        assert len(events.of("compacted")) == 1 and session._compaction_due is False

        # Another summary, and the run is stopped while the snapshot is written.
        renew = session._renew_snapshot

        async def stopped(reason: str) -> None:
            raise asyncio.CancelledError

        session._compaction_due = True
        session._renew_snapshot = stopped
        with pytest.raises(asyncio.CancelledError):
            await session._after_compaction()
        # Still to be done: the next run does it, and measures against what it sends.
        assert session._compaction_due is True and len(events.of("compacted")) == 1

        session._renew_snapshot = renew
        await session._after_compaction()
        assert session._compaction_due is False and len(events.of("compacted")) == 2
    finally:
        await session.close()


async def test_a_tool_gone_since_the_snapshot_is_no_longer_told_of_after_a_summary(tmp_path: Path) -> None:
    session, events = await open_session(make_spec(tmp_path, model=_small_model()), ScriptedLLM([[say("ok"), finish()]]))
    await _ask(session, events, "First commission.")
    await session.close()

    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(make_spec(tmp_path, model=_small_model(), disabled_tools=["todo_write"]), llm)
    try:
        await _ask(session, events, "Second commission.")
        assert "todo_write" in _tool_names(llm.overrides[0])
        await _seed(session, events, llm)
        await _sum_up(session, events, llm)
        assert len(events.of("compacted")) == 1
        assert "todo_write" not in _tool_names(llm.overrides[-1])
        assert "read" in _tool_names(llm.overrides[-1])
    finally:
        await session.close()


async def test_a_summary_leaves_the_text_that_opened_the_commission_under_way_as_it_is(tmp_path: Path) -> None:
    spec = make_spec(tmp_path, model=_small_model())
    llm = ScriptedLLM()
    session, events = await open_session(spec, llm)
    # Each longer than what a summary keeps of the user's words when it has to make room.
    earlier = "Commission 6: sort the photos. " + _words(1_400, "old")
    opening = "Commission 7: tidy the notes. " + _words(1_400, "detail")
    try:
        assert count_tokens(opening) > session.context.policy.user_keep // 2
        for text in (earlier, opening):
            llm.add(say("Working on it."), finish())
            await _ask(session, events, text, opening=True)
        await _seed(session, events, llm)
        llm.add(say("Noted."), finish())
        await _ask(session, events, "Use two spaces.")

        await _sum_up(session, events, llm, "Carry on.")

        assert len(events.of("compacted")) == 1
        contents = [message["content"] for message in llm.requests[-1]]
        # The commission under way is there word for word, once, where it was; the one before it was summed up.
        assert contents.count(opening) == 1
        assert not any("old1399" in str(content) for content in contents)
        # Short things the user said were kept as well, and the request ends with what was just said.
        assert "Use two spaces." in contents and contents[-1] == "Carry on."
        assert contents.index(opening) < contents.index("Use two spaces.")

        # Summed up again, it is still there, once.
        await _seed(session, events, llm)
        await _sum_up(session, events, llm, "And on.")
        assert len(events.of("compacted")) == 2
        assert [message["content"] for message in llm.requests[-1]].count(opening) == 1
    finally:
        await session.close()

    # A session opened later knows which text it is.
    llm = ScriptedLLM()
    session, events = await open_session(spec, llm)
    try:
        await _seed(session, events, llm)
        await _sum_up(session, events, llm, "Still on it?")
        assert len(events.of("compacted")) == 1
        assert [message["content"] for message in llm.requests[-1]].count(opening) == 1
    finally:
        await session.close()


async def test_text_that_opens_no_commission_is_summed_up_like_the_rest(tmp_path: Path) -> None:
    spec = make_spec(tmp_path, model=_small_model())
    llm = ScriptedLLM([[say("Working on it."), finish()]])
    session, events = await open_session(spec, llm)
    long_text = "Do this. " + _words(1_400, "detail")
    try:
        await _ask(session, events, long_text)
        await _seed(session, events, llm)
        await _sum_up(session, events, llm, "Carry on.")
        assert len(events.of("compacted")) == 1
        assert not any("detail1399" in str(message["content"]) for message in llm.requests[-1])
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# The window a provider states
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "stated"),
    [
        ("Error code: 400 - This model's maximum context length is 32768 tokens. However, you requested 40000.", 32_768),
        ("This endpoint's maximum context length is 131,072 tokens.", 131_072),
        (
            "Error code: 400 - {'error': {'code': 400, 'message': 'the request exceeds the available context size, "
            "try increasing it', 'type': 'exceed_context_size_error', 'n_prompt_tokens': 10000, 'n_ctx': 8192}}",
            8_192,
        ),
        ('{"error":{"type":"exceed_context_size_error","n_prompt_tokens":10000,"n_ctx":4096}}', 4_096),
        ("the model is loaded with context length of only 4096 tokens, which is not enough", 4_096),
        ("prompt is too long: 210000 tokens > 200000 maximum", 200_000),
        ("InvalidParameter: Range of input length should be [1, 30720]", 30_720),
        ("The input token count (1200000) exceeds the maximum number of tokens allowed (1048576).", 1_048_576),
        # Nothing stated, or nothing that can be a window.
        ("context_length_exceeded", None),
        ("Request too large for the model.", None),
        ("maximum context length is 12 tokens", None),
        ("maximum context length is 99999999999 tokens", None),
        ("", None),
    ],
)
def test_the_window_is_read_from_what_a_provider_says_when_a_request_is_too_long(said: str, stated: Any) -> None:
    assert window_from_overflow(said) == stated


@pytest.mark.parametrize(
    "said",
    [
        "the request exceeds the available context size, try increasing it",
        "{'type': 'exceed_context_size_error', 'n_ctx': 8192}",
        "the model is loaded with context length of only 4096 tokens",
    ],
)
def test_what_a_local_server_says_of_a_request_that_is_too_long_is_recognized(said: str) -> None:
    assert is_context_overflow(RuntimeError(said))


async def test_a_session_is_managed_for_the_smaller_window_its_provider_states(tmp_path: Path) -> None:
    spec = make_spec(tmp_path, model=_small_model(200_000))
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(spec, llm)
    try:
        await _ask(session, events, "Start.")
        await _seed(session, events, llm)
        assert session.context.policy.window == 200_000

        llm.add(RuntimeError("Error code: 400 - This model's maximum context length is 32768 tokens."))
        llm.add(say("## Current Work\n- summed up"), finish())
        llm.add(say("Went on."), finish())
        ended = await _ask(session, events, "Go on.")

        assert ended["reason"] == "completed" and ended["text"] == "Went on."
        learned = events.of("model_capability")
        assert [(event["context_window"], event["supports_images"]) for event in learned] == [(32_768, None)]
        assert "maximum context length is 32768" in learned[0]["reason"]
        assert session.context.policy.window == 32_768
        assert session.context.policy.threshold < 32_768

        # What the session tells of its window from then on is the one it is managed for.
        llm.add(say("Noted."), finish(), usage(900, 5))
        await _ask(session, events, "And this.")
        assert events.of("usage")[-1]["context_window"] == 32_768

        # Stated once more, the same number teaches nothing new.
        await _seed(session, events, llm)
        llm.add(RuntimeError("Error code: 400 - This model's maximum context length is 32768 tokens."))
        llm.add(say("## Current Work\n- summed up again"), finish())
        llm.add(say("And on."), finish())
        await _ask(session, events, "Further.")
        assert len(events.of("model_capability")) == 1
    finally:
        await session.close()


async def test_the_output_asked_for_shrinks_with_the_window_the_provider_states(tmp_path: Path) -> None:
    # Configured for 8192 tokens of output, in a window that holds that many times over.
    model = ModelConfig(model="fake-model", api_key="k", context_window=200_000, max_output_tokens=8_192)
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(make_spec(tmp_path, model=model), llm)
    try:
        await _ask(session, events, "Start.")
        await _seed(session, events, llm)
        # Nothing is said of the output while it is what the model is configured for.
        assert all("max_completion_tokens" not in overrides for overrides in llm.overrides)
        assert session.context.policy.max_output == 8_192

        llm.add(RuntimeError("Error code: 400 - This model's maximum context length is 16384 tokens."))
        llm.add(say("## Current Work\n- summed up"), finish())
        llm.add(say("Went on."), finish())
        mark = len(llm.overrides)
        ended = await _ask(session, events, "Go on.")

        assert ended["reason"] == "completed" and ended["text"] == "Went on."
        # A provider counts the output a request asks for against the window too: asked for
        # 8192 in a window of 16384, half the window would be gone before anything is sent.
        assert session.context.policy.window == 16_384 and session.context.policy.max_output == 4_096
        refused, summary, retry = llm.overrides[mark:]
        assert "max_completion_tokens" not in refused
        assert summary["max_completion_tokens"] == session.context.policy.summary_cap == 4_096
        assert retry["max_completion_tokens"] == 4_096

        # And so for every request after it.
        llm.add(say("More."), finish())
        await _ask(session, events, "And more.")
        assert llm.overrides[-1]["max_completion_tokens"] == 4_096
    finally:
        await session.close()


async def test_a_session_opened_in_a_small_window_asks_for_less_output_from_the_start(tmp_path: Path) -> None:
    model = ModelConfig(model="fake-model", api_key="k", context_window=20_000, max_output_tokens=8_192)
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(make_spec(tmp_path, model=model), llm)
    try:
        await _ask(session, events, "Start.")
        assert session.context.policy.max_output == 5_000
        assert llm.overrides[0]["max_completion_tokens"] == 5_000
    finally:
        await session.close()


async def test_a_session_sums_its_conversation_up_from_where_it_was_told(tmp_path: Path) -> None:
    # The owner's numbers (2026-11-18): managed for 200,000 tokens, summed up from 170,000 on.
    model = ModelConfig(model="fake-model", api_key="k", context_window=200_000, max_output_tokens=8_192)
    (tmp_path / "preset").mkdir()
    session, _events = await open_session(make_spec(tmp_path / "preset", model=model), ScriptedLLM([]))
    try:
        assert session.context.policy.threshold == 170_000
    finally:
        await session.close()

    # Another point was set for the model.
    (tmp_path / "told").mkdir()
    told = model.model_copy(update={"compact_at": 150_000})
    session, _events = await open_session(make_spec(tmp_path / "told", model=told), ScriptedLLM([]))
    try:
        assert session.context.policy.threshold == 150_000
        # The model turns out to take less: summed up from the same part of the smaller window.
        assert session._assume_window(100_000) is True
        assert session.context.policy.threshold == 75_000
    finally:
        await session.close()


class _Refused(Exception):
    """What a model client raises for a request its provider answered with an error status."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


async def test_a_refusal_for_length_that_states_no_window_still_tells_that_it_is_smaller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config.nekoclaw_settings as settings

    monkeypatch.setattr(settings, "TASK_AGENT_INFERRED_WINDOW_MIN_TOKENS", 5_000)
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(make_spec(tmp_path, model=_small_model(200_000)), llm)
    try:
        await _ask(session, events, "Start.")
        await _seed(session, events, llm)
        before = await session._size()
        assert before > 5_000

        llm.add(RuntimeError("Error code: 400 - {'code': 'context_length_exceeded'}"))
        llm.add(say("## Current Work\n- summed up"), finish())
        llm.add(say("Went on."), finish())
        mark = len(llm.requests)
        ended = await _ask(session, events, "Go on.")

        assert ended["reason"] == "completed" and ended["text"] == "Went on."
        # The window is not known, but it is smaller than what was refused: the conversation
        # is managed for that from here on, not for the 200,000 the session was started with.
        (learned,) = events.of("model_capability")
        window = learned["context_window"]
        assert before < window < before + 300
        assert "context_length_exceeded" in learned["reason"]
        assert session.context.policy.window == window
        # So the request that sums it up is fitted to it as well.
        refused, summary, retry = llm.requests[mark:]
        assert summary[-1] == {"role": "user", "content": texts.COMPACTION_INSTRUCTION}
        assert count_tokens(json.dumps(summary, ensure_ascii=False)) < window
    finally:
        await session.close()


async def test_a_long_request_refused_in_words_that_are_not_recognized_is_summed_up_and_sent_once_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config.nekoclaw_settings as settings

    monkeypatch.setattr(settings, "TASK_AGENT_UNSURE_REFUSAL_TOKENS", 5_000)
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(make_spec(tmp_path, model=_small_model(200_000)), llm)
    try:
        await _ask(session, events, "Start.")
        await _seed(session, events, llm)
        before = await session._size()

        # Nothing in it says "too long", and the status is one such a request is refused with.
        llm.add(_Refused(400, "Bad Request: the request could not be processed"))
        llm.add(say("## Current Work\n- summed up"), finish())
        llm.add(say("Went on."), finish())
        ended = await _ask(session, events, "Go on.")

        assert ended["reason"] == "completed" and ended["text"] == "Went on."
        assert len(events.of("compacted")) == 1
        # Shorter, it went through. That does not show it was too long: summing up rewrites
        # what was said, and a refusal for something in it goes away with that as well. The
        # host is told nothing, which it would believe for a month, for every session with
        # the model, and tell the user of.
        assert events.of("model_capability") == []
        # The session itself goes on with the smaller window, which is all it needs.
        assert before < session.context.policy.window < before + 300
        assert session._window == session.context.policy.window
    finally:
        await session.close()


async def test_a_window_assumed_for_a_retry_is_given_up_when_the_run_is_stopped_before_it_is_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config.nekoclaw_settings as settings

    monkeypatch.setattr(settings, "TASK_AGENT_UNSURE_REFUSAL_TOKENS", 5_000)
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(make_spec(tmp_path, model=_small_model(200_000)), llm)
    try:
        await _ask(session, events, "Start.")
        await _seed(session, events, llm)
        reached, never = asyncio.Event(), asyncio.Event()

        async def hang() -> None:
            reached.set()
            await never.wait()

        llm.add(_Refused(400, "Bad Request: the request could not be processed"))
        # The request that sums the conversation up does not come back.
        llm.add(hang)
        mark = len(events.events)
        session.prompt("Go on.")
        await asyncio.wait_for(reached.wait(), 10)
        # A window was assumed for the request that is to be sent once more.
        assert session._window < 200_000
        assert session.abort() is True
        ended = await events.wait_ended(start=mark)

        assert ended["reason"] == "aborted"
        # It was never sent: nothing was shown, and the session is managed for the window it had.
        assert session.context.policy.window == 200_000 and session._window == 200_000
        assert events.of("model_capability") == []
    finally:
        await session.close()


async def test_what_the_provider_states_of_its_window_stands_in_the_place_of_one_that_was_assumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config.nekoclaw_settings as settings

    monkeypatch.setattr(settings, "TASK_AGENT_UNSURE_REFUSAL_TOKENS", 5_000)
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(make_spec(tmp_path, model=_small_model(200_000)), llm)
    try:
        await _ask(session, events, "Start.")
        await _seed(session, events, llm)
        assert await session._size() < 150_000

        llm.add(_Refused(400, "Bad Request: the request could not be processed"))
        llm.add(say("## Current Work\n- summed up"), finish())
        # Sent once more, the provider says what its window is: more than what was assumed.
        llm.add(RuntimeError("Error code: 400 - This model's maximum context length is 150000 tokens."))
        ended = await _ask(session, events, "Go on.")

        assert ended["reason"] == "error" and "even after compaction" in ended["error"]
        # That is the model's window, and what the host is told: not the length of a request
        # that was refused for a reason nobody knows.
        (learned,) = events.of("model_capability")
        assert learned["context_window"] == 150_000
        assert session.context.policy.window == 150_000 and session._window == 150_000
    finally:
        await session.close()


async def test_nothing_is_told_of_a_window_when_the_request_that_was_sent_once_more_did_not_go_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config.nekoclaw_settings as settings

    monkeypatch.setattr(settings, "TASK_AGENT_UNSURE_REFUSAL_TOKENS", 5_000)
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(make_spec(tmp_path, model=_small_model(200_000)), llm)
    try:
        await _ask(session, events, "Start.")
        await _seed(session, events, llm)
        llm.add(_Refused(400, "Bad Request: the request could not be processed"))
        llm.add(say("## Current Work\n- summed up"), finish())
        # Sent once more, it fails for another reason: that ends the stream without raising.
        llm.add(RuntimeError("the connection was reset"))
        ended = await _ask(session, events, "Go on.")

        assert ended["reason"] == "error" and "the connection was reset" in ended["error"]
        # Whether the first refusal was about length was never shown: the host is told nothing,
        # which it would believe for a month, and the session is managed for the window it had.
        assert events.of("model_capability") == []
        assert session.context.policy.window == 200_000 and session._window == 200_000
    finally:
        await session.close()


async def test_a_summary_that_is_refused_while_the_run_is_being_stopped_is_a_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nekoclaw.runtime.model import ContextOverflowError, ModelRequestError, RequestRefusedError

    session, _events = await open_session(make_spec(tmp_path), ScriptedLLM([]))
    try:
        stopping = {"yes": True}

        class Signal:
            def is_cancelled(self) -> bool:
                return stopping["yes"]

        monkeypatch.setattr(type(session.harness), "current_signal", property(lambda harness: Signal()))
        for error in (RequestRefusedError("400"), ContextOverflowError("too long"), ModelRequestError("no response")):

            async def refused(error: Exception = error, **kwargs: Any) -> Any:
                raise error

            monkeypatch.setattr(session._model, "complete", refused)
            # Taken for a summary that failed, the older part of the conversation would be dropped.
            stopping["yes"] = True
            with pytest.raises(asyncio.CancelledError):
                await session._summarize([], 100)
            # Not being stopped, it is the failure it is.
            stopping["yes"] = False
            with pytest.raises(type(error)):
                await session._summarize([], 100)
    finally:
        await session.close()


@pytest.mark.parametrize("behind", [True, False])
async def test_what_follows_a_summary_is_done_by_a_session_opened_again_when_it_was_left_undone(
    tmp_path: Path, behind: bool
) -> None:
    spec = make_spec(tmp_path, model=_small_model(), instructions="Index of then.")
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(spec, llm)
    await _ask(session, events, "First commission.")
    await _seed(session, events, llm)
    await _sum_up(session, events, llm)
    await session.close()
    saved = _saved(spec)
    assert (saved["reason"], saved["compactions"]) == (REASON_COMPACTED, 1)
    if behind:
        # As a run leaves it that was stopped after the summary was written down and before
        # the system prompt was put together anew: the snapshot is the one from before it.
        saved.update(reason=REASON_OPENED, compactions=0, system="The system prompt of before the summary.")
        (Path(spec.session_dir) / SYSTEM_FILE).write_text(json.dumps(saved), encoding="utf-8")

    later = make_spec(tmp_path, model=_small_model(), instructions="Index of now.")
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(later, llm)
    try:
        await _ask(session, events, "Second commission.")
        system = _system_of(llm.requests[0])
        if behind:
            # Done before the first request, as it would have been before the next one then.
            assert "Index of now." in system and "of before the summary" not in system
            assert [event["count"] for event in events.of("compacted")] == [1]
            assert (_saved(later)["reason"], _saved(later)["compactions"]) == (REASON_COMPACTED, 1)
        else:
            # Made after the summary: kept as it is, as every snapshot.
            assert "Index of then." in system and events.of("compacted") == []
            assert _saved(later) == saved
    finally:
        await session.close()


async def test_a_request_refused_again_after_it_was_summed_up_is_reported_as_it_came(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config.nekoclaw_settings as settings

    # Low enough that the request is still "long" after it was summed up: what every request
    # begins with is more than this by itself.
    monkeypatch.setattr(settings, "TASK_AGENT_UNSURE_REFUSAL_TOKENS", 1_000)
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(make_spec(tmp_path, model=_small_model(200_000)), llm)
    try:
        await _ask(session, events, "Start.")
        await _seed(session, events, llm)
        llm.add(_Refused(400, "Bad Request: tool schema is not valid"))
        llm.add(say("## Current Work\n- summed up"), finish())
        llm.add(_Refused(400, "Bad Request: tool schema is not valid"))
        mark = len(llm.requests)
        ended = await _ask(session, events, "Go on.")

        # It was not about length. Tried once, not over and over; and nothing is told of a window.
        assert ended["reason"] == "error" and "tool schema is not valid" in ended["error"]
        assert len(llm.requests) - mark == 3
        assert events.of("model_capability") == []
        # Nothing was shown of the model's window: the session is managed for the one it had.
        assert session.context.policy.window == 200_000 and session._window == 200_000
    finally:
        await session.close()


@pytest.mark.parametrize(
    ("threshold", "refusal"),
    [
        # Too short for its length to be what was refused.
        (10_000_000, _Refused(400, "Bad Request: the request could not be processed")),
        # Long, and not a status a request that is too long is refused with.
        (5_000, _Refused(401, "Unauthorized")),
        (5_000, _Refused(429, "Too Many Requests")),
        (5_000, _Refused(500, "Internal Server Error")),
        # Long, and no status at all.
        (5_000, RuntimeError("the connection was reset")),
    ],
)
async def test_a_refusal_that_is_not_taken_for_one_of_length_fails_the_run_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, threshold: int, refusal: Exception
) -> None:
    import config.nekoclaw_settings as settings

    monkeypatch.setattr(settings, "TASK_AGENT_UNSURE_REFUSAL_TOKENS", threshold)
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(make_spec(tmp_path, model=_small_model(200_000)), llm)
    try:
        await _ask(session, events, "Start.")
        await _seed(session, events, llm)
        llm.add(refusal)
        mark = len(llm.requests)
        ended = await _ask(session, events, "Go on.")

        assert ended["reason"] == "error" and str(refusal) in ended["error"]
        assert len(llm.requests) - mark == 1
        assert events.of("compacted") == [] and events.of("model_capability") == []
        assert session.context.policy.window == 200_000
    finally:
        await session.close()


@pytest.mark.parametrize("long", [True, False])
async def test_a_session_opened_again_after_its_last_request_failed_is_summed_up_first_when_it_is_long(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, long: bool
) -> None:
    import config.nekoclaw_settings as settings

    monkeypatch.setattr(settings, "TASK_AGENT_UNSURE_REFUSAL_TOKENS", 5_000 if long else 10_000_000)
    first_llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(make_spec(tmp_path, model=_small_model(200_000)), first_llm)
    await _ask(session, events, "First commission.", opening=True)
    await _seed(session, events, first_llm)
    # Not recognized as anything: the run fails, and the commission with it.
    first_llm.add(RuntimeError("upstream error"))
    assert (await _ask(session, events, "Go on."))["reason"] == "error"
    await session.close()

    # The next commission of the session finds the conversation as it was left.
    llm = ScriptedLLM([[say("## Current Work\n- summed up"), finish()], [say("Fine now."), finish()]])
    session, events = await open_session(make_spec(tmp_path, model=_small_model(200_000)), llm)
    try:
        ended = await _ask(session, events, "Second commission.", opening=True)
        if long:
            # Sent as it was, it would fail as before if that was about its length, and so
            # would every commission after it: summed up before the first request.
            assert ended["text"] == "Fine now."
            assert llm.requests[0][-1] == {"role": "user", "content": texts.COMPACTION_INSTRUCTION}
            assert len(events.of("compacted")) == 1
            # The commission that is opened stays whole.
            assert any(message.get("content") == "Second commission." for message in llm.requests[1])
            # Fitted to its own length for the summing up only: why the last request failed is
            # not known, and the session goes on being managed for the window it has.
            assert session.context.policy.window == 200_000 and session._window == 200_000
        else:
            assert ended["text"] == "## Current Work\n- summed up"
            assert events.of("compacted") == []
        # Nothing was learned of the model: the host is told of no window.
        assert events.of("model_capability") == []

        # Only once: the requests after it are fitted as every request is.
        llm.add(say("And on."), finish())
        await _ask(session, events, "More.")
        assert len(events.of("compacted")) == (1 if long else 0)
    finally:
        await session.close()


@pytest.mark.parametrize(
    ("said", "window"),
    [
        # As large as the session's own, or larger: nothing to learn.
        ("This model's maximum context length is 32000 tokens.", 32_000),
        ("This model's maximum context length is 64000 tokens.", 32_000),
        # No number.
        ("context_length_exceeded", 32_000),
        # Smaller than a session can be managed for: the smallest that can.
        ("This model's maximum context length is 2048 tokens.", 4_000),
    ],
)
async def test_what_a_refusal_teaches_about_the_window(tmp_path: Path, said: str, window: int) -> None:
    spec = make_spec(tmp_path, model=_small_model())
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(spec, llm)
    try:
        await _ask(session, events, "Start.")
        await _seed(session, events, llm)
        llm.add(RuntimeError(f"Error code: 400 - {said}"))
        llm.add(say("## Current Work\n- summed up"), finish())
        llm.add(say("Went on."), finish())
        await _ask(session, events, "Go on.")

        assert session.context.policy.window == window
        told = [event["context_window"] for event in events.of("model_capability")]
        assert told == ([] if window == 32_000 else [window])
    finally:
        await session.close()
