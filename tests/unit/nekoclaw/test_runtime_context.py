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
    compact_ratio,
    kind_of,
    output_allowance,
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


@pytest.mark.parametrize(
    ("window", "configured", "allowed"),
    [
        # What the model is configured for, where the window holds it four times over.
        (200_000, 8_192, 8_192),
        (32_768, 8_192, 8_192),
        # A quarter of a smaller window: the rest is for what the request sends.
        (32_767, 8_192, 8_191),
        (16_384, 8_192, 4_096),
        (4_000, 8_192, 1_000),
        # Never more than configured, and never next to nothing.
        (16_384, 512, 512),
        (800, 8_192, 256),
    ],
)
def test_the_output_a_request_asks_for_is_a_quarter_of_the_window_at_the_most(
    window: int, configured: int, allowed: int
) -> None:
    assert output_allowance(window, configured) == allowed


def test_thresholds_scale_with_the_window() -> None:
    # The owner's numbers (2026-11-18): a window of 200,000 is summed up from 170,000 on.
    preset = ContextPolicy(window=200_000, max_output=8_192)
    assert preset.threshold == 170_000
    assert preset.threshold + preset.max_output + 20_000 <= 200_000
    small = ContextPolicy(window=32_000, max_output=4_000)
    assert small.headroom == 3_200
    assert small.threshold == 24_800  # window - output - headroom is the tighter bound
    assert small.retain_tokens == int(28_000 * 0.16)
    large = ContextPolicy(window=1_000_000, max_output=64_000)
    assert large.headroom == 65_536
    assert large.threshold == 850_000  # 17 in 20 of the window is the tighter bound
    # The free tier's model: later than the 102,400 of a window managed by four fifths, and
    # well inside the 131,000 it takes.
    assert ContextPolicy(window=128_000, max_output=8_192).threshold == 107_008
    assert large.result_cap == 12_500
    assert ContextPolicy(window=8_000, max_output=1_000).result_cap == 2_000


@pytest.mark.parametrize(
    ("window", "compact_at", "threshold"),
    [
        # Told where to sum up from: that point, as long as the output and the margin have room.
        (200_000, 170_000, 170_000),
        (200_000, 150_000, 150_000),
        (200_000, 100_000, 100_000),
        # As early as it was set (the host takes a point from 1,000 tokens on).
        (200_000, 15_000, 15_000),
        (200_000, 1_000, 1_000),
        (200_000, 199_000, 171_808),
        (200_000, 400_000, 171_808),
        # Not told: 17 in 20 of the window.
        (200_000, None, 170_000),
        (100_000, None, 81_808),
        (64_000, None, 49_408),
    ],
)
def test_a_conversation_is_summed_up_from_where_the_session_was_told(
    window: int, compact_at: int | None, threshold: int
) -> None:
    policy = ContextPolicy(window=window, max_output=8_192, threshold_ratio=compact_ratio(window, compact_at))

    assert policy.threshold == threshold


def test_the_part_of_the_window_follows_a_window_that_turns_out_smaller() -> None:
    import dataclasses

    told = ContextPolicy(window=200_000, max_output=8_192, threshold_ratio=compact_ratio(200_000, 150_000))
    # As a session does that learns its model takes less: the same part of the smaller window.
    smaller = dataclasses.replace(told, window=100_000)

    assert (told.threshold, smaller.threshold) == (150_000, 75_000)


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


def _blocks_manager(tmp_path: Path, window: int) -> ContextManager:
    return _manager(tmp_path, ContextPolicy(window=window, max_output=1_000), [])


async def test_a_result_of_several_blocks_is_capped_block_by_block_and_a_short_block_in_front_stays_whole(
    tmp_path: Path,
) -> None:
    # A window so small that the cap on one result is three thousand tokens.
    manager = _blocks_manager(tmp_path, 12_000)
    cap = manager.policy.result_cap
    assert cap == 3_000
    # What the host's gateway sends with the call that starts a group of
    # tools: the group, then the page the call opened, then a picture.
    group = "GROUP-START " + _words(800, "tool") + " GROUP-END\n"
    page = "PAGE-START " + _words(6_000) + " PAGE-END"
    shot = ImageContent(data="QUJD", mime_type="image/jpeg")
    # The group is more than the half of the cap a result cut as one text
    # keeps of its beginning, and less than the cap.
    assert cap // 2 < count_tokens(group) < cap < count_tokens(group + page)
    result = AgentToolResult(content=[TextContent(text=group), TextContent(text=page), shot])

    capped = await manager.cap_tool_result("capability_call", "c1", result)

    first, second, third = capped.content
    # The group to the letter; the page cut down to what the group left of the cap.
    assert first == TextContent(text=group)
    assert second.text.startswith("PAGE-START") and texts.SPILL_GAP in second.text
    assert "PAGE-END" in second.text and "Full formatted result stored at" in second.text
    assert third == shot
    # Together within the cap, give or take the path the notice names.
    assert count_tokens(first.text) + count_tokens(second.text) <= cap + 64
    # The page is saved whole, the group is not saved at all.
    (saved,) = (tmp_path / "outputs").iterdir()
    assert saved.read_text(encoding="utf-8") == page


async def test_blocks_that_fit_together_are_left_as_they_are_and_a_short_one_behind_a_long_one_is_not_cut(
    tmp_path: Path,
) -> None:
    manager = _blocks_manager(tmp_path, 12_000)
    cap = manager.policy.result_cap
    small = AgentToolResult(content=[TextContent(text="clicked\n"), TextContent(text="the window moved")])
    note = "[1 picture(s) of this result could not be passed on.]"
    long = "START " + _words(6_000) + " END\n"
    behind = AgentToolResult(content=[TextContent(text=long), TextContent(text=note)])

    assert await manager.cap_tool_result("capability_call", "c1", small) is small
    capped = await manager.cap_tool_result("capability_call", "c2", behind)

    first, second = capped.content
    # The long block alone is cut to the cap, and still ends where the next begins.
    assert first.text.startswith("START") and texts.SPILL_GAP in first.text and first.text.endswith("\n")
    assert count_tokens(first.text) <= cap + 64
    # The line after it had nothing left of the cap, and is whole all the same.
    assert second == TextContent(text=note)
    assert [path.read_text(encoding="utf-8") for path in (tmp_path / "outputs").iterdir()] == [long]


async def test_every_block_too_long_for_what_is_left_is_cut_and_saved_under_a_name_of_its_own(
    tmp_path: Path,
) -> None:
    manager = _blocks_manager(tmp_path, 12_000)
    cap = manager.policy.result_cap
    one = "ONE-START " + _words(3_000, "a") + " ONE-END\n"
    two = "TWO-START " + _words(3_000, "b") + " TWO-END"

    capped = await manager.cap_tool_result(
        "capability_call", "c1", AgentToolResult(content=[TextContent(text=one), TextContent(text=two)])
    )

    first, second = capped.content
    assert first.text.startswith("ONE-START") and second.text.startswith("TWO-START")
    assert texts.SPILL_GAP in first.text and texts.SPILL_GAP in second.text
    # The first took the cap but what was kept back for the second, and the two together stay within it.
    assert count_tokens(second.text) <= 256 + 64
    assert count_tokens(first.text) + count_tokens(second.text) <= cap + 64
    saved = sorted(path.name for path in (tmp_path / "outputs").iterdir())
    assert len(saved) == 2 and saved[0] != saved[1]
    assert {path.read_text(encoding="utf-8") for path in (tmp_path / "outputs").iterdir()} == {one, two}


async def test_a_result_of_many_short_blocks_is_two_texts_within_the_cap(tmp_path: Path) -> None:
    manager = _blocks_manager(tmp_path, 12_000)
    cap = manager.policy.result_cap
    # Forty blocks, none of them long, four times the cap together.
    blocks = [TextContent(text=f"BLOCK{n} " + _words(100, f"w{n}x") + "\n") for n in range(40)]
    result = AgentToolResult(content=blocks)
    assert max(count_tokens(block.text) for block in blocks) < 512 and count_tokens(result.text) > 4 * cap

    capped = await manager.cap_tool_result("capability_call", "c1", result)

    # However many blocks there were: the first, and all the rest as one
    # text, cut to what the first left of the cap.
    first, rest = capped.content
    assert first == blocks[0]
    assert rest.text.startswith("BLOCK1 ") and texts.SPILL_GAP in rest.text and "BLOCK39 " in rest.text
    assert count_tokens(capped.text) <= cap + 64
    (saved,) = (tmp_path / "outputs").iterdir()
    assert saved.read_text(encoding="utf-8") == "".join(block.text for block in blocks[1:])


async def test_a_first_block_small_enough_never_to_be_pruned_arrives_whole_whatever_the_cap(tmp_path: Path) -> None:
    # The smallest window there can be: the cap on one result is a quarter of it.
    manager = _blocks_manager(tmp_path, 4_096)
    cap, never_pruned = manager.policy.result_cap, manager.policy.prune_threshold_tokens
    assert (cap, never_pruned) == (1_024, 2_048)
    group = "GROUP-START " + _words(800, "tool") + " GROUP-END\n"
    page = "PAGE-START " + _words(3_000) + " PAGE-END"
    assert cap < count_tokens(group) < never_pruned

    capped = await manager.cap_tool_result(
        "capability_call", "c1", AgentToolResult(content=[TextContent(text=group), TextContent(text=page)])
    )

    # Over the cap, and whole: what was handed over in front must not arrive cut.
    first, rest = capped.content
    assert first == TextContent(text=group)
    assert rest.text.startswith("PAGE-START") and texts.SPILL_GAP in rest.text
    # The page got what the group left of the length one text may have.
    left = never_pruned - count_tokens(group)
    assert left > 256 and count_tokens(rest.text) <= left + 64

    # A first block over that size is cut down to it, like any text.
    huge = "HUGE-START " + _words(3_000, "h") + " HUGE-END\n"
    capped = await manager.cap_tool_result(
        "capability_call", "c2", AgentToolResult(content=[TextContent(text=huge), TextContent(text="a note")])
    )

    first, rest = capped.content
    assert first.text.startswith("HUGE-START") and texts.SPILL_GAP in first.text
    assert count_tokens(first.text) <= never_pruned + 64
    assert rest == TextContent(text="a note")


async def test_what_follows_a_first_block_gets_what_it_left_of_the_length_one_text_may_have(tmp_path: Path) -> None:
    # The smallest window there can be. One text may be as long as an old
    # result is left whole at, which is twice a quarter of this window.
    manager = _blocks_manager(tmp_path, 4_096)
    cap, never_pruned = manager.policy.result_cap, manager.policy.prune_threshold_tokens
    assert (cap, never_pruned) == (1_024, 2_048)
    # A group handed over with a first call that is alone over a quarter of
    # the window, and a page behind it longer than what is kept back for
    # the text after a first block whatever that block took.
    group = "GROUP-START " + _words(750, "tool") + " GROUP-END\n"
    page = "PAGE-START " + _words(150) + " PAGE-END"
    assert cap < count_tokens(group) and 256 < count_tokens(page)
    assert count_tokens(group + page) <= never_pruned
    whole = AgentToolResult(content=[TextContent(text=group), TextContent(text=page)])

    # Together no longer than one text may be: whole, as that text would be.
    single = AgentToolResult(content=group + page)
    assert await manager.cap_tool_result("capability_call", "c1", whole) is whole
    assert await manager.cap_tool_result("bash", "c2", single) is single
    assert not (tmp_path / "outputs").exists() or not list((tmp_path / "outputs").iterdir())

    # A longer page is cut to what the group left of that length, not to
    # what is kept back for it.
    long = "PAGE-START " + _words(3_000) + " PAGE-END"
    capped = await manager.cap_tool_result(
        "capability_call", "c3", AgentToolResult(content=[TextContent(text=group), TextContent(text=long)])
    )

    first, rest = capped.content
    left = never_pruned - count_tokens(group)
    assert first == TextContent(text=group)
    assert rest.text.startswith("PAGE-START") and texts.SPILL_GAP in rest.text and "PAGE-END" in rest.text
    assert 256 + 64 < count_tokens(rest.text) <= left + 64
    assert count_tokens(first.text) + count_tokens(rest.text) <= never_pruned + 64

    # With a first block that leaves next to nothing, the text after it
    # still has what is kept back for it, and that is all a result is ever
    # over the length one text may have.
    full = "GROUP-START " + _words(1_000, "tool") + " GROUP-END\n"
    assert never_pruned - 256 < count_tokens(full) <= never_pruned
    capped = await manager.cap_tool_result(
        "capability_call", "c4", AgentToolResult(content=[TextContent(text=full), TextContent(text=long)])
    )

    first, rest = capped.content
    assert first == TextContent(text=full)
    assert rest.text.startswith("PAGE-START") and texts.SPILL_GAP in rest.text
    assert count_tokens(rest.text) <= 256 + 64
    assert count_tokens(first.text) + count_tokens(rest.text) <= never_pruned + 256 + 64


async def test_a_text_small_enough_never_to_be_pruned_is_not_capped_either_whatever_the_window(
    tmp_path: Path,
) -> None:
    # The smallest window there can be: a quarter of it is half of what an old result is left whole at.
    manager = _blocks_manager(tmp_path, 4_096)
    cap, never_pruned = manager.policy.result_cap, manager.policy.prune_threshold_tokens
    assert (cap, never_pruned) == (1_024, 2_048)
    kept = AgentToolResult(content="GROUP-START " + _words(900, "tool") + " GROUP-END")
    cut = AgentToolResult(content="LONG-START " + _words(3_000) + " LONG-END")
    assert cap < count_tokens(kept.text) < never_pruned < count_tokens(cut.text)

    # Over the cap, and whole: a group of tools loaded by its name is such a text.
    assert await manager.cap_tool_result("capability_search", "c1", kept) is kept
    # A longer one is cut down to that size, and saved.
    capped = await manager.cap_tool_result("bash", "c2", cut)

    assert capped.text.startswith("LONG-START") and texts.SPILL_GAP in capped.text
    assert count_tokens(capped.text) <= never_pruned + 64
    (saved,) = (tmp_path / "outputs").iterdir()
    assert saved.read_text(encoding="utf-8") == cut.text
    # In a window whose quarter is more than that, the quarter is the cap as before.
    wide = _blocks_manager(tmp_path / "wide", 64_000)
    assert wide.policy.result_cap == 12_500 > count_tokens(cut.text)
    assert await wide.cap_tool_result("bash", "c3", cut) is cut


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


def _old_result(*content: TextContent | ImageContent) -> list[Row]:
    """A conversation in which a tool result of these blocks is no longer the newest."""
    result = ToolResultMessage(tool_call_id="call-1", tool_name="capability_call", content=list(content))
    opening = AssistantMessage(content=[ToolCall(id="call-1", name="capability_call", arguments={})])
    return _rows([UserMessage(content="open it"), opening, result, *_call_turn(2, "small")])


def test_pruning_cuts_a_result_block_by_block_and_a_small_block_stays_whole() -> None:
    policy = ContextPolicy(window=32_000, max_output=4_000)
    # What the host's gateway sends with the call that starts a group of
    # tools: the group, then what the call returned, then its picture.
    group = "GROUP-START " + _words(400, "tool") + " GROUP-END\n"
    page = "PAGE-START " + _words(6_000) + " PAGE-END"
    shot = ImageContent(data="QUJD", mime_type="image/jpeg")
    assert count_tokens(group) < policy.prune_threshold_tokens < count_tokens(page)
    rows = _old_result(TextContent(text=group), TextContent(text=page), shot)

    pruned = prune_rows(rows, policy, include_latest=False)

    assert pruned is not None
    first, second, third = pruned[2].message.content
    # The small block to the letter, although the result as one text is far
    # over the size above which old results are cut.
    assert first == TextContent(text=group)
    assert second.text.startswith("PAGE-START") and second.text.endswith("PAGE-END")
    assert texts.PRUNED_MIDDLE_MARKER in second.text
    assert count_tokens(second.text) <= policy.prune_threshold_tokens
    # The picture where it was.
    assert third == shot
    assert pruned[2].entry_id == "e2" and pruned[2].message.tool_call_id == "call-1"
    assert prune_rows(pruned, policy, include_latest=False) is None


def test_pruning_cuts_every_large_block_and_leaves_a_result_of_small_ones_alone() -> None:
    policy = ContextPolicy(window=32_000, max_output=4_000)
    small = _words(400, "tool")
    large = "START " + _words(6_000) + " END"
    assert count_tokens(small) < policy.prune_threshold_tokens < count_tokens(small * 3)

    both = prune_rows(
        _old_result(TextContent(text=large), TextContent(text=large)), policy, include_latest=False
    )
    untouched = prune_rows(
        _old_result(TextContent(text=small), TextContent(text=small), TextContent(text=small)),
        policy,
        include_latest=False,
    )

    assert both is not None
    for block in both[2].message.content:
        assert block.text.startswith("START") and block.text.endswith("END")
        assert texts.PRUNED_MIDDLE_MARKER in block.text
    # The first block is small, and so is what follows it taken together: nothing to cut.
    assert untouched is None


def test_pruning_takes_all_that_follows_the_first_block_as_one_text() -> None:
    policy = ContextPolicy(window=32_000, max_output=4_000)
    # Twelve blocks, none of them large by itself.
    blocks = [TextContent(text=f"BLOCK{n} " + _words(400, f"w{n}x") + "\n") for n in range(12)]
    assert max(count_tokens(block.text) for block in blocks) < policy.prune_threshold_tokens

    pruned = prune_rows(_old_result(*blocks), policy, include_latest=False)

    # Left block by block, such a result would never be cut at all. It is
    # two texts: the first block whole, the rest cut as one.
    assert pruned is not None
    first, rest = pruned[2].message.content
    assert first == blocks[0]
    assert rest.text.startswith("BLOCK1 ") and texts.PRUNED_MIDDLE_MARKER in rest.text
    assert count_tokens(rest.text) <= policy.prune_threshold_tokens
    assert prune_rows(pruned, policy, include_latest=False) is None


def test_what_the_summarizer_is_handed_is_cut_block_by_block_too() -> None:
    from nekoclaw.runtime.context import _shrink_for_summary

    policy = ContextPolicy(window=32_000, max_output=4_000)
    group = "GROUP-START " + _words(400, "tool") + " GROUP-END\n"
    page = "PAGE-START " + _words(6_000) + " PAGE-END"
    region = _old_result(TextContent(text=group), TextContent(text=page))[1:3]

    (_call, handed), size = _shrink_for_summary(region, policy, 100_000)
    assert 0 < size <= 100_000

    # The summary is written from the group as it was handed over; only the
    # long block next to it is cut for the summarizer.
    assert handed.content[0] == TextContent(text=group)
    assert handed.content[1].text.startswith("PAGE-START") and texts.PRUNED_MIDDLE_MARKER in handed.content[1].text
    # The conversation itself is left as it is.
    assert region[1].message.content[1] == TextContent(text=page)


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


def _shot(tool: str, index: int, *, image: bool = True) -> ToolResultMessage:
    """One result of ``tool``; with a screenshot unless ``image`` is False."""
    content: list = [TextContent(text=f"{tool} {index}")]
    if image:
        content.append(ImageContent(data="AAAA", mime_type="image/jpeg"))
    return ToolResultMessage(tool_call_id=f"{tool}-{index}", tool_name=tool, content=content)


def _has_image(row: Row) -> bool:
    return any(isinstance(block, ImageContent) for block in row.message.content)


def test_tools_of_one_pool_are_counted_together() -> None:
    rows = _rows(
        [
            _shot("browser_take_screenshot", 1),
            _shot("browser_click", 2),
            _shot("computer", 3),
            _shot("browser_take_screenshot", 4),
            # A result without a picture uses up nothing.
            _shot("browser_click", 5, image=False),
            _shot("browser_click", 6),
        ]
    )
    retention = {"browser_take_screenshot": 2, "browser_click": 2, "computer": 2}
    pools = {"browser_take_screenshot": "browser", "browser_click": "browser", "computer": "keyboard_mouse"}

    kept = retain_recent_images(rows, retention, pools)

    # The two newest of the browser as a whole, whichever of its tools took
    # them; the desktop is a pool of its own.
    assert kept is not None
    assert [_has_image(row) for row in kept] == [False, False, True, True, False, True]
    assert [block.text for block in kept[0].message.content] == [
        "browser_take_screenshot 1",
        texts.IMAGE_OMITTED_PLACEHOLDER,
    ]
    # Rows that keep their pictures, and rows that never had one, are the same objects.
    assert [kept[index] is rows[index] for index in range(6)] == [False, False, True, True, True, True]
    assert retain_recent_images(kept, retention, pools) is None
    # Counted one by one, every tool is within its own two.
    assert retain_recent_images(rows, retention) is None
    assert retain_recent_images(rows, retention, {}) is None


def test_a_tool_outside_every_pool_keeps_its_own_count() -> None:
    rows = _rows(
        [
            _shot("browser_click", 1),
            _shot("read_image", 2),
            _shot("browser_take_screenshot", 3),
            _shot("read_image", 4),
            _shot("browser_click", 5),
        ]
    )
    retention = {"browser_click": 1, "browser_take_screenshot": 1, "read_image": 1}
    pools = {"browser_click": "browser", "browser_take_screenshot": "browser"}

    kept = retain_recent_images(rows, retention, pools)

    # One for the browser pool, and one for the tool that is in no pool.
    assert kept is not None
    assert [_has_image(row) for row in kept] == [False, False, False, True, True]


@pytest.mark.parametrize(
    "retention",
    [{"browser_click": 1, "browser_take_screenshot": 3}, {"browser_take_screenshot": 3, "browser_click": 1}],
)
def test_a_pool_keeps_the_largest_number_given_for_any_of_its_tools(retention: dict) -> None:
    rows = _rows([_shot("browser_click", index) for index in range(1, 6)])
    pools = {"browser_click": "browser", "browser_take_screenshot": "browser"}

    kept = retain_recent_images(rows, retention, pools)

    # Three, though the tool that took them all was given one.
    assert kept is not None
    assert [_has_image(row) for row in kept] == [False, False, True, True, True]


def test_results_of_tools_that_are_not_listed_are_never_touched() -> None:
    rows = _rows(
        [
            _shot("browser_hover", 1),
            _shot("read_image", 2),
            _shot("browser_click", 3),
            _shot("browser_hover", 4),
            _shot("browser_click", 5),
        ]
    )
    # ``browser_hover`` is put in the pool but given no number; ``read_image`` is not mentioned at all.
    pools = {"browser_click": "browser", "browser_hover": "browser"}

    kept = retain_recent_images(rows, {"browser_click": 1}, pools)

    assert kept is not None
    assert [_has_image(row) for row in kept] == [True, True, False, True, True]
    # Their pictures do not count against the pool either: the newest of the listed tool stays.
    assert [kept[index] is rows[index] for index in range(5)] == [True, True, False, True, True]
    assert retain_recent_images(rows, {}, pools) is None


async def test_before_a_request_old_screenshots_are_dropped_by_pool(tmp_path: Path) -> None:
    manager = _manager(tmp_path, ContextPolicy(window=128_000, max_output=8_000), [])
    manager.image_retention = {"browser_click": 1, "browser_take_screenshot": 1}
    manager.image_pools = {"browser_click": "browser", "browser_take_screenshot": "browser"}
    messages = [
        UserMessage(content="click, then look"),
        _shot("browser_click", 1),
        _shot("browser_take_screenshot", 2),
    ]
    transcript = Transcript(messages, _counter())
    for index, message in enumerate(messages):
        transcript.register(message, f"e{index}", kind_of(message))

    assert await manager.fit(transcript) == []

    assert [_has_image(row) for row in transcript.rows()[1:]] == [False, True]
    # Nothing but the picture changed, and the row is still the entry it was.
    assert [row.entry_id for row in transcript.rows()] == ["e0", "e1", "e2"]


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
    # A pinned row is kept whatever its size, and takes nothing from the room of the others.
    assert select_user_rows(region, sizes, 250, pinned={"e0"}) == {"e0", "e4", "e2"}
    assert select_user_rows(region, sizes, 50, pinned={"e0"}) == {"e0"}
    assert select_user_rows(region, sizes, 150, pinned={"e4"}) == {"e4", "e2"}
    # One that is not in the part being summed up changes nothing.
    assert select_user_rows(region, sizes, 250, pinned={"elsewhere"}) == {"e4", "e2"}


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


async def test_a_conversation_that_could_stay_is_left_whole_when_no_summary_is_to_be_had(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Summed up with nothing wrong with its length (a session nothing is at work in): nothing has to go.
    policy = ContextPolicy(window=32_000, max_output=4_000)
    messages = [UserMessage(content="go")]
    for index in range(1, 9):
        # About three thousand tokens each: what is cut before a summary is asked for.
        messages.extend(_call_turn(index, _words(625, f"big{index}x")))
    whole = [getattr(message, "text", None) or str(message.content) for message in messages]

    # The request fails: as it was, the tool results that were cut for it included.
    asked: list = []
    cut: list[bool] = []

    async def failing(prepared: list, max_tokens: int):  # noqa: ANN202
        asked.append((list(prepared), max_tokens))
        # (In the conversation itself, when the summary is asked for: cut, and then put back.)
        cut.append(
            any(texts.PRUNED_MIDDLE_MARKER in (getattr(message, "text", None) or str(message.content)) for message in messages)
        )
        raise RuntimeError("model unavailable")

    manager = ContextManager(
        policy=policy, spill=SpillStore(tmp_path / "outputs"), summarize=failing, new_entry_id=_counter()
    )
    transcript = Transcript(messages, _counter())
    assert await manager.fit(transcript, force=True, tight=False, or_drop=False) == []
    assert len(asked) == 1 and cut == [True]
    assert [getattr(message, "text", None) or str(message.content) for message in messages] == whole
    assert not any(texts.PRUNED_MIDDLE_MARKER in text for text in whole)

    # Where the conversation has to get shorter the same request has the older part dropped, as before.
    dropped = list(messages)
    assert await _manager(tmp_path, policy, [], fail=True).fit(Transcript(dropped, _counter()), force=True)
    assert any(isinstance(m, UserMessage) and SUMMARY_FALLBACK in str(m.content) for m in dropped)

    # What is to be summed up does not fit into one request: a summary would stand for a part it was
    # never shown, so none is asked for, and the conversation stays.
    narrow = ContextPolicy(window=8_000, max_output=1_000)
    asked = []
    manager = _manager(tmp_path, narrow, asked)
    long = [UserMessage(content="go")]
    for index in range(1, 9):
        long.extend(_call_turn(index, _words(900, f"wide{index}x")))
    before = [getattr(message, "text", None) or str(message.content) for message in long]
    assert await manager.fit(Transcript(long, _counter()), force=True, tight=False, or_drop=False) == []
    assert asked == []
    assert [getattr(message, "text", None) or str(message.content) for message in long] == before
    # (The same conversation is summed up where it has to get shorter, from as much of it as fits.)
    asked = []
    assert await _manager(tmp_path, narrow, asked).fit(Transcript(list(long), _counter()), force=True)
    assert len(asked) >= 1

    # Summed up because it was told to, a conversation is summed up once: whoever told it to counted
    # on one request. Where it has to get shorter it is looked at a second time while it is too long.
    seen: list = []
    inner = ContextManager._compact

    async def counted(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        seen.append(kwargs.get("tight"))
        return await inner(self, *args, **kwargs)

    async def too_long_whatever_is_done(self, _messages) -> int:  # noqa: ANN001
        return policy.threshold + 1

    monkeypatch.setattr(ContextManager, "_compact", counted)
    monkeypatch.setattr(ContextManager, "measure", too_long_whatever_is_done)
    await _manager(tmp_path, policy, []).fit(Transcript(list(messages), _counter()), force=True, tight=False, or_drop=False)
    once = list(seen)
    seen.clear()
    await _manager(tmp_path, policy, []).fit(Transcript(list(messages), _counter()), force=True, tight=False)
    assert once == [False] and seen == [False, True]


async def test_a_conversation_that_could_stay_is_whole_again_when_its_summing_up_is_cancelled(tmp_path: Path) -> None:
    policy = ContextPolicy(window=32_000, max_output=4_000)

    def conversation() -> list:
        made: list = [UserMessage(content="go")]
        for index in range(1, 9):
            # About three thousand tokens each: what is cut before a summary is asked for.
            made.extend(_call_turn(index, _words(625, f"big{index}x")))
        return made

    def said(messages: list) -> list[str]:
        return [getattr(message, "text", None) or str(message.content) for message in messages]

    # Cancelled while the summary is asked for: none was made, so the tool results that were cut
    # for the request are whole again, as when the request fails.
    messages = conversation()
    whole = said(messages)
    asked = asyncio.Event()
    cut: list[bool] = []

    async def never(prepared: list, max_tokens: int):  # noqa: ANN202
        cut.append(any(texts.PRUNED_MIDDLE_MARKER in text for text in said(messages)))
        asked.set()
        await asyncio.Event().wait()

    manager = ContextManager(
        policy=policy, spill=SpillStore(tmp_path / "outputs"), summarize=never, new_entry_id=_counter()
    )
    fitting = asyncio.ensure_future(manager.fit(Transcript(messages, _counter()), force=True, tight=False, or_drop=False))
    await asyncio.wait_for(asked.wait(), 10)
    fitting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(fitting, 10)
    # (They were cut when the summary was asked for.)
    assert cut == [True]
    assert said(messages) == whole

    # Cancelled after the summary replaced the older part: the summary stays.
    messages = conversation()
    later: list[asyncio.Future] = []
    manager = _manager(tmp_path, policy, [])
    later.append(
        asyncio.ensure_future(
            manager.fit(
                Transcript(messages, _counter()),
                force=True,
                tight=False,
                or_drop=False,
                on_compaction=lambda _compaction: later[0].cancel(),
            )
        )
    )
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(later[0], 10)
    assert any(texts.SUMMARY_OPEN_TAG in text for text in said(messages))
    assert len(messages) < len(whole)


async def test_a_conversation_that_could_stay_is_not_summed_up_from_a_part_too_long_for_one_request(tmp_path: Path) -> None:
    # The older part is one message, which is never left out of what the summary is asked for from,
    # and it is longer than the request has room for: the request would be refused. Where the
    # conversation could stay as it is, none is made.
    narrow = ContextPolicy(window=8_000, max_output=1_000)
    long_reply = AssistantMessage(content=[TextContent(text=_words(4_000, "long"))])

    def conversation() -> list:
        return [
            UserMessage(content="go"),
            long_reply,
            UserMessage(content="and now?"),
            AssistantMessage(content=[TextContent(text="done")]),
        ]

    room = narrow.window - narrow.summary_cap - 2_000
    assert count_tokens(long_reply.content[0].text) > room

    asked: list = []
    messages = conversation()
    before = [getattr(message, "text", None) or str(message.content) for message in messages]
    assert await _manager(tmp_path, narrow, asked).fit(Transcript(messages, _counter()), force=True, tight=False, or_drop=False) == []
    assert asked == []
    assert [getattr(message, "text", None) or str(message.content) for message in messages] == before

    # (Where it has to get shorter the request is made all the same, as before: this is the last resort.)
    asked = []
    assert await _manager(tmp_path, narrow, asked).fit(Transcript(conversation(), _counter()), force=True)
    assert len(asked) >= 1 and long_reply in asked[0][0]


async def test_a_conversation_that_could_stay_is_not_summed_up_from_a_part_in_which_a_call_has_two_results(
    tmp_path: Path,
) -> None:
    policy = ContextPolicy(window=32_000, max_output=4_000)

    def conversation() -> list:
        made: list = [UserMessage(content="go")]
        for index in range(1, 9):
            made.extend(_call_turn(index, _words(625, f"big{index}x")))
        # One call is answered twice (its result was written to the record twice, say): a request
        # is sent one of the two, and a summary would stand for both.
        made.insert(3, ToolResultMessage(tool_call_id="call-1", tool_name="grep", content="answered a second time"))
        return made

    messages = conversation()
    before = [getattr(message, "text", None) or str(message.content) for message in messages]
    asked: list = []
    assert await _manager(tmp_path, policy, asked).fit(Transcript(messages, _counter()), force=True, tight=False, or_drop=False) == []
    assert asked == []
    assert [getattr(message, "text", None) or str(message.content) for message in messages] == before

    # (Where it has to get shorter the request is made all the same, as before.)
    asked = []
    assert await _manager(tmp_path, policy, asked).fit(Transcript(conversation(), _counter()), force=True)
    assert len(asked) >= 1


async def test_a_conversation_that_could_stay_is_not_summed_up_from_a_part_in_which_a_result_has_no_call(
    tmp_path: Path,
) -> None:
    policy = ContextPolicy(window=32_000, max_output=4_000)

    def conversation() -> list:
        made: list = [UserMessage(content="go")]
        for index in range(1, 9):
            made.extend(_call_turn(index, _words(625, f"big{index}x")))
        # The record took a result and not the reply that made its call (the disk refused that one):
        # a request is not sent a result without its call, and a summary would stand for it.
        made.insert(3, AssistantMessage(content=[TextContent(text="looking further")]))
        made.insert(4, ToolResultMessage(tool_call_id="call-lost", tool_name="grep", content="found by a call that is on no record"))
        return made

    messages = conversation()
    before = [getattr(message, "text", None) or str(message.content) for message in messages]
    asked: list = []
    assert await _manager(tmp_path, policy, asked).fit(Transcript(messages, _counter()), force=True, tight=False, or_drop=False) == []
    assert asked == []
    assert [getattr(message, "text", None) or str(message.content) for message in messages] == before

    # (Where it has to get shorter the request is made all the same, as before.)
    asked = []
    assert await _manager(tmp_path, policy, asked).fit(Transcript(conversation(), _counter()), force=True)
    assert len(asked) >= 1


async def test_calls_of_one_id_that_each_have_their_result_are_summed_up_where_the_conversation_could_stay(
    tmp_path: Path,
) -> None:
    policy = ContextPolicy(window=32_000, max_output=4_000)
    messages: list = [UserMessage(content="go")]
    for index in range(1, 9):
        # Every call has the same id (a provider that numbers them anew in each reply), and its own
        # result right after it: a request is sent all of them, and a summary stands for what it was shown.
        messages.extend(_call_turn(1, _words(625, f"big{index}x")))
    asked: list = []
    assert await _manager(tmp_path, policy, asked).fit(Transcript(messages, _counter()), force=True, tight=False, or_drop=False)
    assert len(asked) == 1
    shown = [message for message in asked[0][0] if isinstance(message, ToolResultMessage)]
    assert len(shown) >= 2 and {message.tool_call_id for message in shown} == {"call-1"}
