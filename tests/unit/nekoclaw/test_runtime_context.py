"""The four layers of context management, measured in tokens."""

from __future__ import annotations

import asyncio
import itertools
import json
from pathlib import Path

import pytest

from config.prompts import prompts_nekoclaw as texts
from nekoclaw.runtime.context import (
    SUMMARY_FALLBACK,
    ContextManager,
    ContextPolicy,
    Row,
    Transcript,
    cap_result_text,
    choose_cut,
    kind_of,
    prune_rows,
    retain_recent_images,
    select_user_rows,
)
from nekoclaw.runtime.loop.messages import (
    AssistantMessage,
    CustomMessage,
    ImageContent,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from nekoclaw.runtime.loop.session import SessionState
from nekoclaw.runtime.loop.tools import AgentToolResult
from nekoclaw.runtime.protocol import ModelConfig
from nekoclaw.runtime.session import TRANSCRIPT_FILE, SessionRecorder
from nekoclaw.runtime.tools.base import SpillStore
from tests.unit.nekoclaw.fakes import ScriptedLLM, call, finish, make_spec, open_session, say, usage
from utils.tokenize import count_tokens


def _words(count: int, word: str = "token") -> str:
    return " ".join(f"{word}{index}" for index in range(count))


def _call_turn(index: int, result_text: str) -> list:
    call_id = f"call-{index}"
    return [
        AssistantMessage(content=[TextContent(text=f"step {index}"), ToolCall(id=call_id, name="grep", arguments={"pattern": str(index)})]),
        ToolResultMessage(tool_call_id=call_id, tool_name="grep", content=result_text),
    ]


def _rows(messages: list) -> list[Row]:
    return [Row(f"e{index}", kind_of(message), message) for index, message in enumerate(messages)]


def _counter():  # noqa: ANN202
    state = {"n": 0}

    def new_id() -> str:
        state["n"] += 1
        return f"new-{state['n']}"

    return new_id


def _manager(tmp_path: Path, policy: ContextPolicy, summaries: list, *, fail: bool = False) -> ContextManager:
    async def summarize(messages: list, max_tokens: int):  # noqa: ANN202
        summaries.append((list(messages), max_tokens))
        if fail:
            raise RuntimeError("model unavailable")
        return f"## Primary Request and Intent\n- summary number {len(summaries)}", Usage(input=5, output=5)

    return ContextManager(
        policy=policy,
        spill=SpillStore(tmp_path / "outputs"),
        summarize=summarize,
        new_entry_id=_counter(),
    )


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def test_thresholds_scale_with_the_window() -> None:
    small = ContextPolicy(window=32_000, max_output=4_000)
    assert small.headroom == 4_000
    assert small.threshold == 24_000  # window - output - headroom is the tighter bound
    assert small.retain_tokens == int(28_000 * 0.16)
    large = ContextPolicy(window=1_000_000, max_output=64_000)
    assert large.headroom == 65_536
    assert large.threshold == 800_000  # 80% of the window is the tighter bound
    assert large.result_cap == 12_500
    assert ContextPolicy(window=8_000, max_output=1_000).result_cap == 2_000


# ---------------------------------------------------------------------------
# Layer 1: one result
# ---------------------------------------------------------------------------


def test_an_oversized_result_keeps_head_and_tail_and_is_saved_whole(tmp_path: Path) -> None:
    text = "HEAD-MARKER " + _words(30_000) + " TAIL-MARKER"
    spill = SpillStore(tmp_path / "outputs")
    capped = cap_result_text(text, 12_500, spill, "bash-call-1")
    assert capped is not None
    assert count_tokens(capped) <= 12_500
    assert capped.startswith("HEAD-MARKER")
    assert "TAIL-MARKER" in capped
    assert texts.SPILL_GAP in capped
    notice = capped.rsplit("\n\n", 1)[1]
    saved = next((tmp_path / "outputs").iterdir())
    assert saved.read_text(encoding="utf-8") == text
    omitted = int(notice.split()[1])
    assert notice == (
        f"(Omitted {omitted} bytes. Full formatted result stored at: {saved}. "
        "Use read with offset/limit, or grep this path to search within it.)"
    )
    assert 0 < omitted < len(text)
    assert cap_result_text("short result", 12_500, spill, "x") is None


async def test_the_session_caps_tool_results_but_leaves_read_alone(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    session, _events = await open_session(spec, ScriptedLLM())
    try:
        big = AgentToolResult(content=_words(30_000))
        capped, is_error = await session._after_tool_call(
            ToolCall(id="c1", name="grep", arguments={"pattern": "x"}), big, False
        )
        assert is_error is False
        assert count_tokens(capped.text) <= 12_500
        assert "Full formatted result stored at: " in capped.text
        assert list((Path(spec.session_dir) / "outputs").iterdir())
        untouched, _ = await session._after_tool_call(
            ToolCall(id="c2", name="read", arguments={"file_path": "x"}), big, False
        )
        assert untouched is big
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# Layer 2: pruning
# ---------------------------------------------------------------------------


def test_pruning_cuts_old_results_and_spares_the_newest_batch() -> None:
    policy = ContextPolicy(window=32_000, max_output=4_000)
    messages = [
        UserMessage(content="find it"),
        *_call_turn(1, "OLD-START " + _words(6_000) + " OLD-END"),
        *_call_turn(2, "small"),
        *_call_turn(3, "NEW-START " + _words(6_000) + " NEW-END"),
    ]
    rows = _rows(messages)
    pruned = prune_rows(rows, policy, include_latest=False)
    assert pruned is not None
    old = pruned[2].message
    assert isinstance(old, ToolResultMessage)
    assert texts.PRUNED_MIDDLE_MARKER in old.text
    assert old.text.startswith("OLD-START") and old.text.endswith("OLD-END")
    assert count_tokens(old.text) <= policy.prune_threshold_tokens
    assert old.tool_call_id == "call-1" and pruned[2].entry_id == "e2"
    # Unchanged rows are the same objects; the newest batch is whole.
    assert pruned[4] is rows[4]
    assert pruned[6] is rows[6]
    # Pruning again finds nothing left to do.
    assert prune_rows(pruned, policy, include_latest=False) is None
    forced = prune_rows(rows, policy, include_latest=True)
    assert forced is not None and texts.PRUNED_MIDDLE_MARKER in forced[6].message.text


def test_only_the_newest_images_of_a_tool_stay_in_context() -> None:
    def shot(index: int) -> ToolResultMessage:
        return ToolResultMessage(
            tool_call_id=f"s{index}",
            tool_name="screenshot",
            content=[TextContent(text=f"shot {index}"), ImageContent(data="AAAA", mime_type="image/png")],
        )

    other = ToolResultMessage(
        tool_call_id="r", tool_name="read_image", content=[ImageContent(data="BBBB", mime_type="image/png")]
    )
    rows = _rows([shot(1), other, shot(2), shot(3)])
    kept = retain_recent_images(rows, {"screenshot": 2})
    assert kept is not None
    first = kept[0].message
    assert [type(block).__name__ for block in first.content] == ["TextContent", "TextContent"]
    assert first.content[1].text == texts.IMAGE_OMITTED_PLACEHOLDER
    assert kept[1] is rows[1] and kept[2] is rows[2] and kept[3] is rows[3]
    assert retain_recent_images(kept, {"screenshot": 2}) is None
    assert retain_recent_images(rows, {}) is None


# ---------------------------------------------------------------------------
# Layer 3: summary
# ---------------------------------------------------------------------------


def test_cut_never_splits_a_call_from_its_result_and_keeps_the_last_turn() -> None:
    messages = [
        UserMessage(content="start"),
        *_call_turn(1, "r1"),
        *_call_turn(2, "r2"),
        *_call_turn(3, "r3"),
    ]
    rows = _rows(messages)
    sizes = [100] * len(rows)
    # Budget for two and a half messages: the cut must not land on a tool result.
    cut = choose_cut(rows, sizes, 250)
    assert rows[cut].kind == "assistant" and cut == 5
    # A tiny budget still keeps the whole last turn.
    assert choose_cut(rows, sizes, 1) == 5
    # A generous budget keeps everything.
    assert choose_cut(rows, sizes, 10_000) == 0


def test_recent_user_messages_are_selected_newest_first_within_budget() -> None:
    region = _rows(
        [
            UserMessage(content="oldest"),
            AssistantMessage(content="a"),
            UserMessage(content="middle"),
            CustomMessage(custom_type="notice:job", content="notice"),
            UserMessage(content="newest"),
        ]
    )
    sizes = [100, 10, 100, 10, 100]
    assert select_user_rows(region, sizes, 250) == {"e4", "e2"}
    assert select_user_rows(region, sizes, 1_000) == {"e0", "e2", "e4"}
    assert select_user_rows(region, sizes, 50) == set()


async def test_summary_replaces_old_work_and_keeps_user_text_verbatim(tmp_path: Path) -> None:
    policy = ContextPolicy(window=8_000, max_output=1_000)
    summaries: list = []
    manager = _manager(tmp_path, policy, summaries)
    messages = [UserMessage(content="Build the importer; never touch config.json.")]
    for index in range(1, 9):
        messages.extend(_call_turn(index, f"result {index} " + _words(900, f"r{index}x")))
        if index == 4:
            messages.append(UserMessage(content="Use the streaming parser, please."))
    transcript = Transcript(messages, _counter())
    before = len(messages)
    assert await manager.measure(messages) > policy.threshold

    compactions = await manager.fit(transcript)

    assert len(compactions) >= 1
    assert len(messages) < before
    assert await manager.measure(messages) <= policy.threshold
    contents = [m.content for m in messages if isinstance(m, UserMessage)]
    # Both things the user said survive word for word, outside the summary.
    assert "Build the importer; never touch config.json." in contents
    assert "Use the streaming parser, please." in contents
    summary = next(c for c in contents if isinstance(c, str) and texts.SUMMARY_OPEN_TAG in c)
    assert summary.startswith("Previous conversation summary:\n" + texts.CHECKPOINT_PREAMBLE)
    assert summary.rstrip().endswith(texts.SUMMARY_CLOSE_TAG)
    # The last turn (its result may be unseen) is intact, call next to result.
    assert isinstance(messages[-2], AssistantMessage) and messages[-2].tool_calls[0].id == "call-8"
    assert isinstance(messages[-1], ToolResultMessage) and messages[-1].tool_call_id == "call-8"
    # The summarizer was not handed the user's text to condense.
    condensed, cap = summaries[0]
    assert not any(isinstance(m, UserMessage) for m in condensed)
    assert cap == policy.summary_cap
    first = compactions[0]
    assert "new-" in first.summary_row.entry_id
    assert set(first.replaced_entry_ids).isdisjoint({row.entry_id for row in transcript.rows()})


async def test_pruning_alone_is_enough_when_it_brings_the_size_down(tmp_path: Path) -> None:
    policy = ContextPolicy(window=32_000, max_output=4_000)
    summaries: list = []
    manager = _manager(tmp_path, policy, summaries)
    messages = [UserMessage(content="go")]
    for index in range(1, 13):
        # About three thousand tokens each: above the pruning threshold.
        messages.extend(_call_turn(index, _words(625, f"big{index}x")))
    transcript = Transcript(messages, _counter())
    assert await manager.measure(messages) > policy.threshold

    assert await manager.fit(transcript) == []

    assert summaries == []  # no model call was needed
    assert await manager.measure(messages) <= policy.threshold
    assert texts.PRUNED_MIDDLE_MARKER in messages[2].text
    assert texts.PRUNED_MIDDLE_MARKER not in messages[-1].text


async def test_a_failed_summary_drops_the_span_instead_of_failing_the_run(tmp_path: Path) -> None:
    policy = ContextPolicy(window=8_000, max_output=1_000)
    manager = _manager(tmp_path, policy, [], fail=True)
    messages = [UserMessage(content="task")]
    for index in range(1, 9):
        messages.extend(_call_turn(index, _words(900, f"f{index}x")))
    transcript = Transcript(messages, _counter())
    compactions = await manager.fit(transcript)
    assert compactions
    assert any(isinstance(m, UserMessage) and SUMMARY_FALLBACK in str(m.content) for m in messages)
    assert messages[0].content == "task"


async def test_a_summary_is_reported_before_the_next_one_can_be_aborted(tmp_path: Path) -> None:
    policy = ContextPolicy(window=8_000, max_output=1_000)
    requests: list[int] = []

    async def summarize(messages: list, max_tokens: int):  # noqa: ANN202
        del max_tokens
        requests.append(len(messages))
        if len(requests) == 2:
            raise asyncio.CancelledError  # the user aborts during the second summary
        return "## Current Work\n- the first summary", None

    summary_ids = itertools.count(1)
    manager = ContextManager(
        policy=policy,
        spill=SpillStore(tmp_path / "outputs"),
        summarize=summarize,
        new_entry_id=lambda: f"summary-{next(summary_ids)}",
    )
    # The system prompt and tools take most of the window here, so one summary
    # is not enough and a second, tighter one is asked for.
    manager.fixed_tokens = 5_600
    messages = [UserMessage(content="task")]
    for index in range(1, 13):
        messages.extend(_call_turn(index, _words(40, f"r{index}x")))
    transcript = Transcript(messages, _counter())
    reported: list = []

    with pytest.raises(asyncio.CancelledError):
        await manager.fit(transcript, on_compaction=reported.append)

    assert len(requests) == 2
    # The first summary had already replaced the older messages when the
    # second one was aborted. It was handed over at that moment, so the caller
    # could save it; fit() itself never got to return anything.
    assert [compaction.summary_row.entry_id for compaction in reported] == ["summary-1"]
    live = [row.entry_id for row in transcript.rows()]
    assert "summary-1" in live
    assert reported[0].replaced_entry_ids
    assert set(reported[0].replaced_entry_ids).isdisjoint(live)


async def test_size_is_anchored_on_reported_usage_plus_an_estimate(tmp_path: Path) -> None:
    manager = _manager(tmp_path, ContextPolicy(window=128_000, max_output=8_000), [])
    manager.fixed_tokens = 1_000
    history = [UserMessage(content="hello there"), AssistantMessage(content="hi")]
    estimated = await manager.measure(history)
    assert estimated > 1_000
    manager.note_usage(Usage(input=4_000, cache_read=1_000, output=200), len(history))
    assert await manager.measure(history) == 5_200
    history.append(UserMessage(content=_words(50)))
    grown = await manager.measure(history)
    assert 5_200 < grown < 5_200 + 400
    manager.invalidate()
    assert await manager.measure(history) < 5_200  # back to a pure estimate


# ---------------------------------------------------------------------------
# Layer 4 and persistence, through a session
# ---------------------------------------------------------------------------


def _small_model() -> ModelConfig:
    # Small enough to cross with little text, large enough that the system
    # prompt and tool definitions (about six thousand tokens) leave room.
    return ModelConfig(model="fake-model", api_key="k", context_window=32_000, max_output_tokens=2_000)


async def _seed_history(session, events, llm: ScriptedLLM, turns: int) -> None:  # noqa: ANN001
    for index in range(turns):
        llm.add(call("todo_write", {"todos": [{"content": f"step {index}", "status": "in_progress"}]}, f"t{index}"), finish("tool_calls"))
        llm.add(say(f"Finished step {index}. " + _words(300, f"s{index}w")), finish())
        mark = len(events.events)
        session.prompt(f"Do step {index} and keep the word marker{index}.")
        await events.wait_ended(start=mark)


async def test_overflow_is_recovered_by_compacting_and_retrying_once(tmp_path: Path) -> None:
    spec = make_spec(tmp_path, model=_small_model())
    llm = ScriptedLLM()
    session, events = await open_session(spec, llm)
    try:
        await _seed_history(session, events, llm, 4)
        seeded_requests = len(llm.requests)
        overflow = RuntimeError("Error code: 400 - This model's maximum context length is 32000 tokens.")
        llm.add(overflow)
        llm.add(say("## Primary Request and Intent\n- do the steps\n## Current Work\n- step 4"), finish())
        llm.add(say("Recovered and done."), finish())

        mark = len(events.events)
        session.prompt("Now do the final step.")
        ended = await events.wait_ended(start=mark)

        assert ended["reason"] == "completed"
        assert ended["text"] == "Recovered and done."
        # Exactly: the failed request, the summary request, one retry.
        assert len(llm.requests) == seeded_requests + 3
        summary_request = llm.requests[seeded_requests + 1]
        assert summary_request[-1] == {"role": "user", "content": texts.COMPACTION_INSTRUCTION}
        retry = llm.requests[seeded_requests + 2]
        retry_text = json.dumps(retry, ensure_ascii=False)
        assert texts.SUMMARY_OPEN_TAG in retry_text
        assert "s0w1 " not in retry_text  # the old answers are gone ...
        # ... while what the user said is still there, word for word.
        for index in range(4):
            assert {"role": "user", "content": f"Do step {index} and keep the word marker{index}."} in retry
        assert retry[-1] == {"role": "user", "content": "Now do the final step."}
        assert len(retry) < len(llm.requests[seeded_requests])
        # The failed attempt announced no response of its own.
        assert len([e for e in events.events[mark:] if e["type"] == "ended"]) == 1
        memory_view = [m.model_dump(exclude={"timestamp"}) for m in session.harness.messages]
    finally:
        await session.close()

    # The transcript replays to the same conversation.
    lines = (Path(spec.session_dir) / TRANSCRIPT_FILE).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["type"] for line in lines].count("compaction") == 1
    entries = await SessionRecorder(Path(spec.session_dir) / TRANSCRIPT_FILE).read_all()
    replayed = [m.model_dump(exclude={"timestamp"}) for m in SessionState.from_entries(entries).messages]
    assert replayed == memory_view


# An aborted request whose stream is left for the garbage collector shows up
# as an unraisable "async generator ignored GeneratorExit"; make that a failure.
@pytest.mark.filterwarnings("error::pytest.PytestUnraisableExceptionWarning")
async def test_an_applied_summary_is_saved_when_the_next_one_is_aborted(tmp_path: Path) -> None:
    spec = make_spec(tmp_path, model=_small_model())
    llm = ScriptedLLM()
    session, events = await open_session(spec, llm)
    second_summary_asked = asyncio.Event()
    release = asyncio.Event()

    async def never_answers() -> None:
        second_summary_asked.set()
        await release.wait()

    try:
        await _seed_history(session, events, llm, 8)
        # As if the system prompt and tools took most of the window: one
        # summary does not bring the conversation under the threshold.
        session.context.fixed_tokens = 23_000
        seeded_requests = len(llm.requests)
        llm.add(say("## Current Work\n- the first summary"), finish())
        llm.add(never_answers)

        mark = len(events.events)
        session.prompt("Carry on.")
        await asyncio.wait_for(second_summary_asked.wait(), 20)
        session.abort()
        ended = await events.wait_ended(start=mark)

        assert ended["reason"] == "aborted"
        # Two summary requests and nothing else: the reply itself was never asked for.
        assert len(llm.requests) == seeded_requests + 2
        for request in llm.requests[seeded_requests:]:
            assert request[-1] == {"role": "user", "content": texts.COMPACTION_INSTRUCTION}
        # The aborted request's stream was closed on the way out, not left open
        # until the garbage collector gets to it.
        assert llm.closed_streams == len(llm.requests)
        memory_view = [m.model_dump(exclude={"timestamp"}) for m in session.harness.messages]
        assert texts.SUMMARY_OPEN_TAG in json.dumps(memory_view, ensure_ascii=False)
    finally:
        release.set()
        await session.close()

    # The first summary is in the conversation the session goes on with, so it
    # is in the saved transcript too, and loading that gives the same conversation.
    lines = (Path(spec.session_dir) / TRANSCRIPT_FILE).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["type"] for line in lines].count("compaction") == 1
    entries = await SessionRecorder(Path(spec.session_dir) / TRANSCRIPT_FILE).read_all()
    replayed = [m.model_dump(exclude={"timestamp"}) for m in SessionState.from_entries(entries).messages]
    assert replayed == memory_view


async def test_cut_results_are_saved_whole_and_cut_again_after_a_reopen(tmp_path: Path) -> None:
    spec = make_spec(tmp_path, model=_small_model())
    workdir = Path(spec.workdir)
    # Looked for in JSON text, where the blank lines around it are escaped.
    marker = texts.PRUNED_MIDDLE_MARKER.strip()

    def conversation(session) -> str:  # noqa: ANN001
        return json.dumps(
            [m.model_dump(exclude={"timestamp"}) for m in session.harness.messages], ensure_ascii=False
        )

    def results_of(messages) -> dict[str, str]:  # noqa: ANN001
        return {m.tool_call_id: m.text for m in messages if isinstance(m, ToolResultMessage)}

    llm = ScriptedLLM()
    session, events = await open_session(spec, llm)
    try:
        threshold = session.context.policy.threshold
        for index in range(8):
            target = workdir / f"notes{index}.txt"
            target.write_text(
                "\n".join(_words(15, f"n{index}l{line}x") for line in range(60)), encoding="utf-8"
            )
            llm.add(call("read", {"file_path": str(target)}, f"r{index}"), finish("tool_calls"))
            llm.add(say(f"Read file {index}."), finish())
            mark = len(events.events)
            session.prompt(f"Read notes{index}.txt.")
            await events.wait_ended(start=mark)
        ends = events.of("tool_end")
        assert [event["is_error"] for event in ends] == [False] * 8
        # What the model was given for each call, before anything was cut.
        given = {event["tool_call_id"]: event["text"] for event in ends}
        assert sorted(given) == [f"r{index}" for index in range(8)]
        # Older results were cut to make room; that was enough, no summary.
        live = conversation(session)
        assert marker in live
        assert texts.SUMMARY_OPEN_TAG not in live
        assert results_of(session.harness.messages) != given
    finally:
        await session.close()

    # Cutting is a view of the conversation, not a change to the record: the
    # transcript keeps every result whole, word for word.
    saved = (Path(spec.session_dir) / TRANSCRIPT_FILE).read_text(encoding="utf-8")
    assert marker not in saved
    assert "compaction" not in [json.loads(line)["type"] for line in saved.splitlines()]
    entries = await SessionRecorder(Path(spec.session_dir) / TRANSCRIPT_FILE).read_all()
    assert results_of(SessionState.from_entries(entries).messages) == given

    # So a reopened session starts from the whole results, above the threshold,
    # and cuts them again before its first request goes out.
    reopened_llm = ScriptedLLM([[say("Back."), finish()]])
    session, events = await open_session(spec, reopened_llm)
    try:
        assert results_of(session.harness.messages) == given
        assert await session.context.measure(session.harness.messages) > threshold
        session.prompt("Go on.")
        await events.wait_ended()
        assert len(reopened_llm.requests) == 1  # no summary request was needed here either
        assert marker in json.dumps(reopened_llm.requests[0], ensure_ascii=False)
        assert await session.context.measure(session.harness.messages) <= threshold
    finally:
        await session.close()


async def test_a_second_overflow_ends_the_run_with_an_error(tmp_path: Path) -> None:
    spec = make_spec(tmp_path, model=_small_model())
    llm = ScriptedLLM()
    session, events = await open_session(spec, llm)
    try:
        await _seed_history(session, events, llm, 3)
        overflow = RuntimeError("prompt is too long: 33001 tokens > 32000 maximum")
        llm.add(overflow)
        llm.add(say("## Current Work\n- still going"), finish())
        llm.add(RuntimeError("prompt is too long: 32500 tokens > 32000 maximum"))
        mark = len(events.events)
        session.prompt("Go on.")
        ended = await events.wait_ended(start=mark)
        assert ended["reason"] == "error"
        assert "does not fit the model's context window even after compaction" in ended["error"]
    finally:
        await session.close()


async def test_growing_context_is_compacted_before_the_request_is_sent(tmp_path: Path) -> None:
    spec = make_spec(tmp_path, model=_small_model())
    llm = ScriptedLLM()
    session, events = await open_session(spec, llm)
    try:
        # The provider reports a growing prompt; the last report is above the
        # threshold (25,600 tokens for this window), whatever a local estimate says.
        for index in range(3):
            llm.add(say(f"Answer {index}. " + _words(300, f"a{index}w")), finish(), usage(9_000 * (index + 1), 1_300))
            mark = len(events.events)
            session.prompt(f"Question {index}?")
            await events.wait_ended(start=mark)
        requests_before = len(llm.requests)
        llm.add(say("## Primary Request and Intent\n- answer questions"), finish())
        llm.add(say("Final answer."), finish(), usage(900, 5))
        mark = len(events.events)
        session.prompt("Last question?")
        ended = await events.wait_ended(start=mark)
        assert ended["text"] == "Final answer."
        # One summary request was made on the way, without any provider error.
        assert len(llm.requests) == requests_before + 2
        assert llm.requests[requests_before][-1]["content"] == texts.COMPACTION_INSTRUCTION
        final_request = json.dumps(llm.requests[-1], ensure_ascii=False)
        assert texts.SUMMARY_OPEN_TAG in final_request
        assert "Question 0?" in final_request and "Last question?" in final_request
        assert events.of("usage")[-1]["context_tokens"] == 905
    finally:
        await session.close()
