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

import json
from pathlib import Path
from typing import Any

import pytest

from config.prompts import prompts_nekoclaw as texts
from nekoclaw.runtime.model import is_context_overflow, window_from_overflow
from nekoclaw.runtime.protocol import ModelConfig
from nekoclaw.runtime.snapshot import (
    REASON_COMPACTED,
    REASON_OPENED,
    SYSTEM_FILE,
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
        json.dumps({"version": 2, "system": "s", "tools": []}),
        json.dumps({"version": 1, "system": "", "tools": []}),
        json.dumps({"version": 1, "system": "s", "tools": "read"}),
        json.dumps({"version": 1, "system": "s", "tools": ["read"]}),
        json.dumps({"version": 1, "system": "s", "tools": [{"name": "read", "description": "d"}]}),
        json.dumps({"version": 1, "system": "s", "tools": [{"name": "", "description": "d", "parameters": {}}]}),
        json.dumps(
            {
                "version": 1,
                "system": "s",
                "tools": [{"name": "a", "description": "d", "parameters": {}}] * 2,
            }
        ),
    ],
)
def test_a_file_that_is_not_a_snapshot_counts_as_none(tmp_path: Path, content: str) -> None:
    (tmp_path / SYSTEM_FILE).write_text(content, encoding="utf-8")
    assert read_snapshot(tmp_path) is None


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

    spec = make_spec(tmp_path, instructions="Space index: three entries by now.", reply_language="Japanese")
    saved = _saved(spec)
    assert saved["version"] == 1 and saved["reason"] == REASON_OPENED
    assert saved["system"] == _system_of(first_llm.requests[0])
    assert "Space index: nothing yet." in saved["system"]
    assert [tool["name"] for tool in saved["tools"]] == _tool_names(first_llm.overrides[0])

    # Opened for the next commission, with other instructions and another
    # language by now: the system prompt and the tools are those of before.
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
    spec = make_spec(tmp_path, model=_small_model(256_000))
    llm = ScriptedLLM([[say("ok"), finish()]])
    session, events = await open_session(spec, llm)
    try:
        await _ask(session, events, "Start.")
        await _seed(session, events, llm)
        assert session.context.policy.window == 256_000

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
    model = ModelConfig(model="fake-model", api_key="k", context_window=256_000, max_output_tokens=8_192)
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
