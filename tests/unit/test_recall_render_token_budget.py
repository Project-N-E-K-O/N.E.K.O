"""Guards for the L21 recall-block render budget.

``/query_memory`` results go straight into a prompt. Before this the only
bound was "take the first 5"; a single merged reflection could be
arbitrarily long, so the block had no ceiling at all.

There is now ONE renderer — ``memory.recall_render.render_recall_block``.
The QQ plugin's ``render_relevant_memory`` and the main app's
``recall_memory`` tool handler are thin shells over it. That collapse is
what these tests rest on, and it is why they look different from the
version this file shipped with:

Until issue #2588 the two shells were hand-written twins, and the guard
against a third un-budgeted renderer was structural — parse the repo, find
functions that render recall lines, check they CALL the two budget
functions. That inference was defeated five times running (substring match,
module-wide AST walk, a dead helper nested inside the renderer, a budget
call parked in a return annotation, one parked in ``if False:``), and an
adversarial pass then measured 12 more ways through, up to 95x over budget.
The root problem is not fixable by tightening: "will this call execute, and
is its result used" is undecidable, and each tightening also produced false
positives that invite someone to weaken the guard later.

So the budget is no longer inferred from source shape. It is measured, on
the single render path, by the behavioural tests below — oversized input in,
``count_tokens`` on what comes out. Two much cruder structural checks
remain, and neither has to reason about execution: nobody but the entry
point may call the recall label table, and every module that talks to
``/query_memory`` must import the entry point.
"""

from __future__ import annotations

import ast
import re
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import RECALL_RENDER_ENTRY_MAX_TOKENS, RECALL_RENDER_TOTAL_MAX_TOKENS
from memory.recall_render import render_recall_block
from utils.tokenize import count_tokens

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _result(text: str, *, tier: str = "fact", entity: str = "group_chat") -> dict:
    return {"text": text, "tier": tier, "entity": entity}


# ── the shared helper both renderers go through ──────────────────────


def test_shared_helper_takes_a_prefix_and_reports_what_it_dropped():
    from utils.tokenize import take_lines_within_token_budget

    lines = ["一二三四五" * 20, "短一点的一条", "更短"]
    # Exactly the first two lines as the caller will emit them — joined,
    # so the separator between them is part of the price.
    budget = count_tokens("\n".join(lines[:2]))

    kept, dropped = take_lines_within_token_budget(lines, budget)

    assert kept == lines[:2]
    assert dropped == 1


def test_shared_helper_stops_rather_than_letting_a_shorter_line_jump_the_queue():
    """Prefix, not skip-and-continue.

    The fixture above cannot tell the two apart — with descending lengths
    both strategies return the same two lines. Here a short line sits
    behind a long one: skipping would smuggle it in ahead of the entry it
    was ranked below, which reorders recall results by length.
    """
    from utils.tokenize import take_lines_within_token_budget

    lines = ["甲" * 5, "乙" * 400, "丙" * 5]
    budget = count_tokens(lines[0]) + count_tokens(lines[2]) + 1
    assert count_tokens(lines[1]) > budget, "夹具失效：中间那条并没有放不下"

    kept, dropped = take_lines_within_token_budget(lines, budget)

    assert kept == [lines[0]], (
        "放不下的一条应当终止整段，而不是跳过它把后面更短的塞进来"
    )
    assert dropped == 2


def test_shared_helper_charges_the_joiner_it_was_given():
    """The budget covers ``separator.join(kept)``, not the bare lines.

    A newline usually costs one token, so counting lines alone
    undercounts by one per gap. Small, but it is an undercount — the
    unsafe direction — and it is the same mistake as capping ``text``
    while budgeting the whole rendered line.
    """
    from utils.tokenize import take_lines_within_token_budget

    lines = ["露营", "钓鱼", "爬山"]
    bare = sum(count_tokens(ln) for ln in lines)
    assert count_tokens("\n".join(lines)) > bare, (
        "夹具失效：这几行拼起来时换行被 BPE 吞了，量不出分隔符开销"
    )

    kept, dropped = take_lines_within_token_budget(lines, bare)

    assert count_tokens("\n".join(kept)) <= bare, (
        "预算没算分隔符：实际拼出来的整段超过了给定预算"
    )
    assert kept and dropped >= 1


def test_shared_helper_always_emits_the_top_ranked_line():
    """Zero lines is the wrong answer for a relevance-ranked list: the
    caller asked for the best match and would get an empty memory block
    instead. Per-entry truncation is what actually bounds this first line
    — the helper only guarantees forward progress."""
    from utils.tokenize import take_lines_within_token_budget

    only_line = "一条比整段预算还长的记忆" * 50
    assert count_tokens(only_line) > 10

    kept, dropped = take_lines_within_token_budget([only_line, "另一条"], 10)

    assert kept == [only_line]
    assert dropped == 1


# ── the constants have to add up ─────────────────────────────────────


def test_entry_cap_cannot_exceed_the_block_cap():
    """The per-entry cap is what keeps the helper's always-emit-one rule
    from blowing the block budget. Raise it above the total and the first
    entry alone can overshoot."""
    assert RECALL_RENDER_ENTRY_MAX_TOKENS <= RECALL_RENDER_TOTAL_MAX_TOKENS


def test_block_cap_funds_a_full_page_of_max_length_entries():
    """The block cap has to cover ``limit`` entries at the per-entry cap
    PLUS the line decoration, or the last relevance hit is dropped for a
    reason nobody chose.

    Asserted against the BUDGET HELPER, not against arithmetic between the
    constants. ``RECALL_RENDER_TOTAL_MAX_TOKENS >= limit * (ENTRY +
    OVERHEAD)`` is the derivation written in the constant's comment, and
    the derivation was wrong: ``take_lines_within_token_budget`` charges
    the separator it joins with, which the comment's model of the cost did
    not include. ``limit`` lines have ``limit - 1`` gaps, so the real
    requirement is 4 tokens higher than the arithmetic — and 2200 passed
    the arithmetic while the helper dropped the fifth line. Ask the thing
    that actually collects the fee.

    ``limit`` is read off the signature rather than typed in, so raising it
    in a later PR fails here instead of silently shrinking the block.
    """
    import inspect

    from config import (
        RECALL_RENDER_LINE_OVERHEAD_TOKENS,
        RECALL_RENDER_LINE_SEPARATOR_TOKENS,
    )
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge
    from utils.tokenize import take_lines_within_token_budget

    limit = inspect.signature(
        QQMemoryBridge.query_relevant_memory
    ).parameters["limit"].default
    assert isinstance(limit, int) and limit > 0

    # The renderer caps each rendered line at ENTRY + OVERHEAD, so a line
    # of exactly that size is the worst case the block has to fund.
    per_line = RECALL_RENDER_ENTRY_MAX_TOKENS + RECALL_RENDER_LINE_OVERHEAD_TOKENS
    unit = "群里聊过的一件事情，"
    line = unit * (per_line // count_tokens(unit))
    line += "阿" * (per_line - count_tokens(line))
    assert count_tokens(line) == per_line, "夹具失效：没造出恰好满额的一行"

    kept, dropped = take_lines_within_token_budget(
        [line] * limit, RECALL_RENDER_TOTAL_MAX_TOKENS,
    )
    assert dropped == 0 and len(kept) == limit, (
        f"整段预算 {RECALL_RENDER_TOTAL_MAX_TOKENS} 只装下了 {len(kept)}/{limit} 条"
        f"满额条目（每条 {RECALL_RENDER_ENTRY_MAX_TOKENS} tok 正文 + "
        f"{RECALL_RENDER_LINE_OVERHEAD_TOKENS} tok 行装饰，另加 {limit - 1} 个"
        f"拼接缝隙）——常量算术没把 separator 计费算进去"
    )
    assert RECALL_RENDER_LINE_SEPARATOR_TOKENS == count_tokens("\n"), (
        f"缝隙计费常量 {RECALL_RENDER_LINE_SEPARATOR_TOKENS} 与实测换行 "
        f"{count_tokens(chr(10))} tok 对不上，后续按 limit 重新推导会推错"
    )


def test_line_overhead_allowance_covers_what_the_renderer_actually_adds():
    """The allowance is only honest if it matches reality. Measure the
    decoration on a real rendered line instead of trusting the number.

    Measured on a SHORT entry on purpose. With a max-length one the
    per-line cap (``ENTRY + OVERHEAD``) truncates the result, so the
    measurement lands on exactly the allowance being checked and shrinking
    the constant shrinks the measurement with it — the assertion holds for
    any value, which is no assertion at all.

    The decoration is locale-dependent (the tag and the relative-time label
    are both translated), so every shipped locale has to fit the one
    allowance.
    """
    from config import RECALL_RENDER_LINE_OVERHEAD_TOKENS
    from config.prompts.prompts_memory import RECALL_ENTRY_TIER_LABEL

    body = "群里聊过的一件事情"
    for lang in sorted(RECALL_ENTRY_TIER_LABEL["reflection"]):
        rendered = render_recall_block(
            [{
                "text": body,
                "tier": "reflection",
                "entity": "group_participant",
                "created_at": "2026-05-01T10:00:00",
            }],
            lang,
        ).text

        assert body in rendered, f"[{lang}] 夹具失效：短条目被截断了，量到的就不是纯装饰"
        overhead = count_tokens(rendered) - count_tokens(body)
        assert 0 < overhead <= RECALL_RENDER_LINE_OVERHEAD_TOKENS, (
            f"[{lang}] 实测行装饰 {overhead} tok 超出预留的 "
            f"{RECALL_RENDER_LINE_OVERHEAD_TOKENS} tok：{rendered!r}"
        )


# ── the single entry point: measured, not inferred ───────────────────


def test_entry_point_truncates_an_oversized_entry_instead_of_dropping_it():
    """A merged reflection can be thousands of tokens. Cut it, don't lose
    it — the entry is there because it ranked highest for this query.

    The per-line cap would bound the total on its own, so size alone
    cannot tell the two apart. What only the per-entry cap buys is room
    for the decoration: it trims the TEXT, leaving the trailing time
    anchor intact, where a line-level cut would take the date off the end.
    """
    long_text = "露营的细节" * 2000
    assert count_tokens(long_text) > RECALL_RENDER_ENTRY_MAX_TOKENS

    rendered = render_recall_block([{
        "text": long_text,
        "tier": "fact",
        "entity": "group_chat",
        "created_at": "2026-05-01T10:00:00",
    }], "zh").text

    assert "露营的细节" in rendered, "超长条目应被截断保留，而不是整条消失"
    assert re.search(r"\(2026-05-01(, .+)?\)$", rendered.rstrip()), (
        f"正文没先按单条上限截断，时间后缀被整行截断吃掉了：{rendered[-40:]!r}"
    )
    assert count_tokens(rendered) <= RECALL_RENDER_ENTRY_MAX_TOKENS + 32, (
        f"单条召回未按 {RECALL_RENDER_ENTRY_MAX_TOKENS} tok 截断"
    )


def test_entry_point_block_stops_at_the_total_budget():
    """Ten near-max entries must not add up to a 4000-token prompt block."""
    chunk = "群里聊过的一件事情" * 200
    results = [_result(f"{i}{chunk}") for i in range(10)]

    block = render_recall_block(results, "zh")

    assert block.text
    assert count_tokens(block.text) <= RECALL_RENDER_TOTAL_MAX_TOKENS, (
        f"召回段整体 {count_tokens(block.text)} tok 超过 "
        f"{RECALL_RENDER_TOTAL_MAX_TOKENS} tok 预算"
    )
    # The block is a prefix of the relevance ranking, not a length-sorted
    # subset: the top hit is always in and the tail is what goes.
    assert block.text.startswith("1. ")
    assert "10. " not in block.text
    assert block.kept + block.dropped == 10, (
        f"kept={block.kept} + dropped={block.dropped} 对不上喂进去的 10 条，"
        f"调用方的日志会按这个数报错"
    )


def test_entry_point_keeps_short_entries_verbatim():
    """The budget must not touch the ordinary case."""
    block = render_recall_block([
        _result("群里在聊露营"),
        _result("阿离喜欢辣条", tier="reflection", entity="group_participant"),
    ], "zh")

    assert "群里在聊露营" in block.text
    assert "阿离喜欢辣条" in block.text
    assert block.text.count("\n") == 1
    assert (block.kept, block.dropped) == (2, 0)


def test_entry_point_numbers_stay_contiguous_when_an_entry_has_no_text():
    """A textless entry is skipped BEFORE numbering, not after.

    Both former twins filtered inside an ``enumerate`` over the raw
    results, so an empty hit burned its index and the model was handed
    ``1. ... / 3. ...`` — a gap it can only read as "entry 2 was withheld
    from me".
    """
    block = render_recall_block([
        _result("第一条"),
        _result("   "),
        {"text": None, "tier": "fact", "entity": "group_chat"},
        _result("最后一条"),
    ], "zh")

    numbers = [ln.split(".", 1)[0] for ln in block.text.split("\n")]
    assert numbers == ["1", "2"], f"编号出现跳号：{block.text!r}"
    assert block.kept == 2


def test_entry_point_skips_non_mapping_entries():
    """Upstream JSON is not a contract. A malformed ``results`` list with a
    string or None in it must not take the whole tool call down with an
    AttributeError."""
    block = render_recall_block(
        ["不是 dict", None, 42, _result("真正的一条")], "zh",
    )

    assert block.text.startswith("1. ")
    assert "真正的一条" in block.text
    assert block.kept == 1


_LONG_TAG_RESULT = {
    "text": "群里在聊露营",
    "tier": "fact",
    # `render_recall_entry_tag` echoes unknown enums verbatim, and a
    # hand-edited facts.json can hold anything here. The per-entry cap
    # only trims `text`, so without a line-level cap this rides straight
    # into the prompt — and the block's always-keep-one rule guarantees
    # it is never the entry that gets dropped.
    "entity": "损坏的超长 entity 值" * 500,
}


def test_entry_point_bounds_a_line_whose_tag_is_corrupt():
    rendered = render_recall_block([_LONG_TAG_RESULT], "zh").text

    assert count_tokens(rendered) <= RECALL_RENDER_TOTAL_MAX_TOKENS, (
        f"畸形 entity 让整段涨到 {count_tokens(rendered)} tok，越过了 "
        f"{RECALL_RENDER_TOTAL_MAX_TOKENS} 的安全上限"
    )


@pytest.mark.parametrize(
    "entry,expected",
    [
        (
            {
                "event_end_at": "2026-05-03T10:00:00",
                "event_start_at": "2026-05-02T10:00:00",
                "created_at": "2026-06-01T10:00:00",
            },
            "2026-05-03",
        ),
        (
            {
                "event_start_at": "2026-05-02T10:00:00",
                "created_at": "2026-06-01T10:00:00",
            },
            "2026-05-02",
        ),
        ({"created_at": "2026-06-01T10:00:00"}, "2026-06-01"),
        # Non-empty but unparseable high-priority fields must fall through
        # rather than stop the search — a manual edit or a migration leaves
        # exactly this shape, and picking by truthiness renders a garbage
        # date while a usable anchor sits one field down.
        (
            {
                "event_end_at": "去年夏天",
                "event_start_at": 1234567890,
                "created_at": "2026-06-01T10:00:00",
            },
            "2026-06-01",
        ),
    ],
    ids=["event_end", "event_start", "created", "unparseable-falls-through"],
)
def test_entry_point_anchors_on_when_it_happened_not_when_it_was_written(
    entry, expected,
):
    """``event_end_at`` → ``event_start_at`` → ``created_at``, same order as
    the persona stale block and temporal ``_past_anchor``.

    These fields had ZERO fixtures in this file before issue #2588, which
    is how B7 ("append un-budgeted text keyed on a field nobody sets") got
    past all 24 tests here.
    """
    rendered = render_recall_block(
        [{"text": "露营", "tier": "fact", "entity": "user", **entry}], "zh",
    ).text

    assert expected in rendered, f"时间锚点选错了：{rendered!r}"


def test_entry_point_stays_in_budget_with_every_time_field_populated():
    """Budget measured on entries where EVERY renderable field is set.

    The sparse fixtures elsewhere in this file leave the event-time fields
    empty, so text appended after the budget on their account was free —
    measured at 1.01x and climbing with the data (issue #2588, B7). Fill
    them in and the same append is a red test.
    """
    chunk = "露营的细节" * 200
    results = [
        {
            "text": f"{i}{chunk}",
            "tier": "reflection",
            "entity": "group_participant",
            "event_start_at": "2026-05-01T09:00:00",
            "event_end_at": "2026-05-03T18:30:00",
            "created_at": "2026-06-01T10:00:00",
        }
        for i in range(10)
    ]

    block = render_recall_block(results, "zh")

    assert count_tokens(block.text) <= RECALL_RENDER_TOTAL_MAX_TOKENS, (
        f"条目字段填满后整段 {count_tokens(block.text)} tok 超过预算 "
        f"{RECALL_RENDER_TOTAL_MAX_TOKENS}——有内容是在预算之后追加的"
    )
    assert block.dropped, "夹具失效：没触发丢弃，这条用例什么都没测到"


@pytest.mark.parametrize("lang", ["zh", "en", "ja"])
def test_entry_point_charges_the_header_to_the_same_budget(lang):
    """The overview line lands in the same string, so it comes out of the
    same allowance.

    The gate is set right at "two entries plus the header", where leaving
    the header unpaid buys exactly one entry too many. A roomier fixture
    cannot show this: the greedy stop usually leaves more slack than a
    header costs, so the block stays under budget either way and the
    assertion passes for the wrong reason. Header width is locale-
    dependent (``ja`` is over twice ``en``), hence the parametrize —
    a reservation tuned against Chinese would pass zh and fail ja.
    """
    from config.prompts.prompts_memory import RECALL_MEMORY_TOOL_FOUND_HEADER

    header_template = RECALL_MEMORY_TOOL_FOUND_HEADER[lang]
    results = [_result(f"第{i}条召回到的记忆内容") for i in range(4)]
    probe = render_recall_block(results[:1], lang, header_template=header_template)
    line_cost = count_tokens(probe.text.split("\n")[1])
    gate = 2 * line_cost + count_tokens(header_template.format(n=2))

    with patch("config.RECALL_RENDER_TOTAL_MAX_TOKENS", gate):
        block = render_recall_block(results, lang, header_template=header_template)

    assert block.kept, "夹具失效：一条都没渲染出来"
    assert count_tokens(block.text) <= gate, (
        f"locale={lang}：整段 {count_tokens(block.text)} tok 超过闸门 {gate}"
        f"——首行总览没有从预算里扣掉"
    )


def test_entry_point_header_counts_what_survived():
    """Announcing "found 10" and then listing 3 makes the model believe it
    lost seven results and call the tool again."""
    from config.prompts.prompts_memory import RECALL_MEMORY_TOOL_FOUND_HEADER

    chunk = "聊过的一件事情" * 200
    template = RECALL_MEMORY_TOOL_FOUND_HEADER["zh"]
    block = render_recall_block(
        [_result(f"{i}{chunk}") for i in range(10)], "zh",
        header_template=template,
    )

    lines = block.text.split("\n")
    listed = [ln for ln in lines[1:] if ln.strip()]
    assert listed, "夹具失效：一条都没渲染出来"
    assert len(listed) < 10, "夹具失效：没触发丢弃，这条用例什么都没测到"
    # 整行相等，不是子串包含：`str(4) in "找到 41 条相关记忆"` 会放过任何
    # 以正确数字开头的错误计数。
    assert lines[0] == template.format(n=len(listed)), (
        f"首行总览与实际条数不符：{lines[0]!r} vs {len(listed)} 条"
    )
    assert block.kept == len(listed)


def test_a_full_page_of_max_length_entries_all_survive():
    """Behavioural mirror of the arithmetic above: five hits, each longer
    than the per-entry cap, all five reach the prompt."""
    body = "群里聊过的一件事情，" * 400
    results = [
        {
            "text": f"{i}{body}",
            "tier": "fact",
            "entity": "group_chat",
            "created_at": "2026-05-01T10:00:00",
        }
        for i in range(5)
    ]

    rendered = render_recall_block(results, "zh").text

    assert len(rendered.split("\n")) == 5, (
        f"5 条满额召回没能全部进 prompt：\n{rendered[:200]}"
    )
    assert rendered.startswith("1. ") and "\n5. " in rendered


# ── the two shells that call it ──────────────────────────────────────


def _bridge():
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge

    return QQMemoryBridge(SimpleNamespace(logger=MagicMock()))


def test_plugin_shell_renders_the_budgeted_block():
    """The QQ side is a shell: same input, same string as the entry point.

    Compared against the entry point's own output rather than re-asserting
    the budget, so this stays true if the format changes — what it pins is
    that the shell adds no line building of its own. (It has: this side
    used to cut its own date suffix with ``anchor[:10]``, losing the
    relative-time label the main app showed.)
    """
    results = [
        _result("群里在聊露营"),
        {
            "text": "阿离喜欢辣条",
            "tier": "reflection",
            "entity": "group_participant",
            "created_at": "2026-05-01T10:00:00",
        },
    ]
    kept_out: list[int] = []

    with patch(
        "utils.language_utils.get_global_language_full", return_value="zh",
    ):
        rendered = _bridge().render_relevant_memory(results, kept_count_out=kept_out)

    assert rendered == render_recall_block(results, "zh").text
    assert kept_out == [2]


def test_plugin_shell_reports_the_drop_without_needing_a_logger():
    """A missing logger must never cost the user their memory block.

    ``render_relevant_memory`` had no plugin dependency at all until a
    diagnostic line was added; an AttributeError there is swallowed by the
    caller's ``except`` and the whole recall block silently disappears.
    """
    chunk = "群里聊过的一件事情" * 200
    bridge = _bridge()
    bridge.plugin = SimpleNamespace()  # no .logger at all

    with patch(
        "utils.language_utils.get_global_language_full", return_value="zh",
    ):
        rendered = bridge.render_relevant_memory(
            [_result(f"{i}{chunk}") for i in range(10)],
        )

    assert rendered.startswith("1. ")
    assert count_tokens(rendered) <= RECALL_RENDER_TOTAL_MAX_TOKENS


class _ToolHarness:
    def __init__(self):
        from main_logic.core.tool_calling import ToolCallingMixin

        self.__class__ = type("_H", (_ToolHarness, ToolCallingMixin), {})
        self.user_language = "zh"
        self.lanlan_name = "小天"
        self.input_mode = "text"
        self.session = None
        self.memory_server_port = 12345


async def _call_tool_in(lang: str, results: list[dict]) -> str:
    harness = _ToolHarness()
    harness.user_language = lang
    payload = {"results": results, "elapsed_ms": 3.0}
    response = SimpleNamespace(
        is_success=True, status_code=200, text="", json=lambda: payload,
    )
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    with patch(
        "utils.internal_http_client.get_internal_http_client",
        return_value=client,
    ):
        return await harness._handle_recall_memory_call({"query": "露营"})


async def _call_tool(results: list[dict]) -> str:
    return await _call_tool_in("zh", results)


@pytest.mark.asyncio
async def test_tool_shell_block_stops_at_the_total_budget():
    """hybrid_recall returns more than the plugin's five, so this side is
    where the total gate actually binds.

    Budgeted against the WHOLE returned string, with no slack: the i18n
    overview line and the newlines both go into the model's context, so
    both have to be paid for out of the same allowance. An earlier version
    of this assertion carried a ``+64`` fudge, which is exactly how the
    header got to stay outside the budget unnoticed.
    """
    chunk = "聊过的一件事情" * 200
    results = [_result(f"{i}{chunk}") for i in range(10)]

    rendered = await _call_tool(results)

    assert count_tokens(rendered) <= RECALL_RENDER_TOTAL_MAX_TOKENS, (
        f"召回工具结果整体 {count_tokens(rendered)} tok 超过预算 "
        f"{RECALL_RENDER_TOTAL_MAX_TOKENS}（首行总览与换行也要算进去）"
    )


@pytest.mark.asyncio
async def test_tool_shell_passes_its_localized_header_to_the_entry_point():
    """The overview line is the tool side's own contribution, and it is
    charged to the block budget only because the shell hands it over as
    ``header_template`` — a shell that formatted it itself and prepended
    the result would put it outside the budget again."""
    from config.prompts.prompts_memory import RECALL_MEMORY_TOOL_FOUND_HEADER

    results = [_result("露营那次带了帐篷"), _result("阿离没去")]

    rendered = await _call_tool(results)

    assert rendered == render_recall_block(
        results, "zh", header_template=RECALL_MEMORY_TOOL_FOUND_HEADER["zh"],
    ).text


@pytest.mark.asyncio
async def test_tool_shell_header_count_matches_what_was_actually_rendered():
    chunk = "聊过的一件事情" * 200
    rendered = await _call_tool([_result(f"{i}{chunk}") for i in range(10)])

    from config.prompts.prompts_memory import RECALL_MEMORY_TOOL_FOUND_HEADER

    lines = rendered.split("\n")
    listed = [ln for ln in lines[1:] if ln.strip()]
    assert listed, "夹具失效：一条都没渲染出来"
    assert len(listed) < 10, "夹具失效：没触发丢弃，这条用例什么都没测到"
    assert lines[0] == RECALL_MEMORY_TOOL_FOUND_HEADER["zh"].format(
        n=len(listed)
    ), f"首行总览与实际条数不符：{lines[0]!r} vs {len(listed)} 条"


# ── recall tokenization stays off the event loop ─────────────────────


def _thread_recording_truncate():
    """A ``truncate_to_tokens`` stand-in that records where it ran.

    Binds the real function before the caller installs the patch, so the
    stand-in delegates to the genuine tokenizer rather than to itself.
    """
    from utils.tokenize import truncate_to_tokens as real

    threads: list[int] = []

    def _recording(*args, **kwargs):
        threads.append(threading.get_ident())
        return real(*args, **kwargs)

    return _recording, threads


@pytest.mark.asyncio
async def test_plugin_recall_render_runs_off_the_event_loop():
    """``truncate_to_tokens`` encodes the text BEFORE truncation, and the
    whole reason this budget exists is that upstream can return an
    enormous merged reflection. tiktoken degrades quadratically on a chunk
    the pretokenizer cannot split, so running it inline would stall every
    other session in the process."""
    recording, threads = _thread_recording_truncate()
    payload = {"results": [_result("露营的细节" * 200)], "elapsed_ms": 1.0}
    response = SimpleNamespace(
        status_code=200, text="", json=lambda: payload,
        raise_for_status=lambda: None,
    )
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    bridge = _bridge()

    with patch.object(bridge, "_client", return_value=client), \
            patch("utils.tokenize.truncate_to_tokens", recording), \
            patch("utils.language_utils.get_global_language_full", return_value="zh"):
        await bridge.query_relevant_memory("Neko", "露营")

    assert threads, "夹具失效：渲染根本没调用 truncate_to_tokens"
    assert all(t != threading.get_ident() for t in threads), (
        "召回渲染在事件循环线程上跑 tiktoken，超长条目会卡住整个进程"
    )


@pytest.mark.asyncio
async def test_tool_recall_render_runs_off_the_event_loop():
    """Main-app twin — this one is on the voice path, where a stall is
    immediately audible."""
    recording, threads = _thread_recording_truncate()
    with patch("utils.tokenize.truncate_to_tokens", recording):
        await _call_tool([_result("露营的细节" * 200)])

    assert threads, "夹具失效：渲染根本没调用 truncate_to_tokens"
    assert all(t != threading.get_ident() for t in threads), (
        "recall_memory 工具在事件循环线程上跑 tiktoken"
    )


def test_qq_section_wrapper_stays_fixed_size():
    """The wrapper around the QQ recall block is prompt boilerplate, not
    recalled content, so it is NOT charged to
    ``RECALL_RENDER_TOTAL_MAX_TOKENS`` — that budget bounds the memories.
    Charging fixed template text to it would shrink the memory allowance
    to pay for a heading that is present regardless.

    That reasoning only holds while the wrapper is genuinely fixed. Pin
    it: the day someone interpolates variable content into it, it stops
    being boilerplate and the budget question has to be reopened.
    """
    from plugin.plugins.qq_auto_reply.prompt_fragment_templates import (
        LONG_TERM_MEMORY_SECTION,
    )

    empty = LONG_TERM_MEMORY_SECTION.format(memory_context="")
    assert "{" not in empty and "}" not in empty, (
        "包裹模板里出现了 memory_context 之外的占位符——它不再是定长样板，"
        "得重新考虑要不要计进召回预算"
    )
    assert count_tokens(empty) <= 120, (
        f"包裹模板涨到 {count_tokens(empty)} tok；不计进召回预算的前提是它小且定长"
    )


# ── structural guards: the entry point is the only way in ────────────
#
# Deliberately crude. Neither of these asks whether a budget call executes
# or whether its result is used — that question is undecidable, five
# rounds of trying is what issue #2588 documents, and the answer here is
# that there is only one render path and it is measured above. What is
# left is to keep it the ONLY one.

_ENTRY_POINT = "memory/recall_render.py"

_SKIP_DIR_PARTS = {
    ".venv", "venv", "node_modules", "__pycache__", ".git", "build",
    "dist", ".claude", "tests", "docs",
}


def _repo_python_sources() -> dict[str, str]:
    """``{relative posix path: source}`` for every non-test module.

    An unreadable file FAILS rather than being skipped. The previous scan
    swallowed ``OSError`` / ``UnicodeDecodeError`` with a ``continue``, so
    a module in any other encoding was silently exempt from every guard
    below — measured at 10x over budget (issue #2588, A3).
    """
    sources: dict[str, str] = {}
    unreadable: list[str] = []
    for path in _REPO_ROOT.rglob("*.py"):
        # Relative parts, not absolute: this checkout lives under a
        # `.claude/worktrees/...` path, so filtering on absolute parts
        # skips the entire repository and the scan silently finds nothing.
        rel = path.relative_to(_REPO_ROOT)
        if _SKIP_DIR_PARTS & set(rel.parts):
            continue
        try:
            sources[rel.as_posix()] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            unreadable.append(f"{rel.as_posix()} ({type(exc).__name__})")
    assert not unreadable, (
        f"这些 .py 读不出来，下面的护栏对它们是空转的：{unreadable}。"
        f"非 UTF-8 模块请转成 UTF-8，别让扫描静默跳过"
    )
    return sources


def _files_calling(sources: dict[str, str], func_name: str) -> set[str]:
    """Files with a CALL to ``func_name`` — ``def`` and ``import`` don't count.

    Call sites, not text: mentioning the name in a comment, importing it,
    or defining it is not calling it. Attribute calls (``mod.f(...)``)
    count on the attribute name, so re-exporting the table under another
    module does not launder it.
    """
    out: set[str] = set()
    for rel, source in sources.items():
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover — a broken file fails elsewhere
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute)
                else None
            )
            if name == func_name:
                out.add(rel)
                break
    return out


def _files_with_url_literal(sources: dict[str, str], needle: str) -> set[str]:
    """Files with ``needle`` inside a real string literal.

    Not a substring scan over the source: the endpoint is named in
    comments and docstrings all over the memory subsystem (the budget
    constants explain what they bound, ``hybrid_recall`` says which route
    calls it), and counting those would make the client set unpinnable.
    Docstrings are excluded for the same reason; an f-string's literal
    parts are included, since that is how both real call sites build the
    URL.
    """
    out: set[str] = set()
    for rel, source in sources.items():
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover — a broken file fails elsewhere
            continue
        docstrings = set()
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            body = getattr(node, "body", None) or []
            if (
                body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and needle in node.value
                and id(node) not in docstrings
            ):
                out.add(rel)
                break
    return out


def test_repo_scan_reaches_the_files_it_claims_to_cover():
    """A scan that finds nothing passes every guard below while proving
    nothing. Pin that it actually reads this repo's modules."""
    sources = _repo_python_sources()

    assert len(sources) > 200, f"仓库扫描只找到 {len(sources)} 个 .py，路径过滤把仓库滤没了"
    for rel in (_ENTRY_POINT, "main_logic/core/tool_calling.py",
                "plugin/plugins/qq_auto_reply/memory_bridge.py"):
        assert rel in sources, f"扫描没覆盖到 {rel}，下面的护栏是空转的"


def test_only_the_entry_point_labels_recall_entries():
    """``render_recall_entry_tag`` is called from exactly one module.

    This is the check that keeps the entry point single. A second renderer
    that wants localized ``[tier/entity]`` prefixes has to call the table,
    and calling it from anywhere else fails here.

    Note what is NOT exempted: ``config/prompts/prompts_memory.py`` defines
    the table, and the old scan skipped that file BY NAME — which made it
    the one place a shared line-rendering helper could be parked to launder
    a second renderer past every guard (issue #2588, A2, measured at 59x
    over budget). Defining a function is not calling it, so no exemption is
    needed and none is granted.
    """
    callers = _files_calling(_repo_python_sources(), "render_recall_entry_tag")

    assert callers == {_ENTRY_POINT}, (
        f"召回条目标签表的调用点应当只有 {_ENTRY_POINT}，实测 {sorted(callers)}。\n"
        f"召回渲染在 #2588 之后收口到单一入口：新的渲染面请调用 "
        f"memory.recall_render.render_recall_block，别再手搓一份带预算的行渲染"
    )


def test_the_entry_point_has_no_runtime_switch():
    """No env lookup inside the renderer.

    This is the one shape the behavioural tests structurally cannot catch:
    a budget that only runs when a flag says so passes every measurement
    here (the tests take the default path) and is off in production —
    measured at 6.4x over budget (issue #2588, B3). Tests cannot enumerate
    the environments they were not run in, so the rule is that the render
    path has no environments: same input, same output, budget always on.

    Narrow on purpose, and NOT a general reachability check — that is the
    inference this whole file stopped making. It pins one property of one
    module: nothing here reads the environment. A flag threaded in from a
    caller would still get through; what this stops is the cheap version.
    """
    tree = ast.parse((_REPO_ROOT / _ENTRY_POINT).read_text(encoding="utf-8"))
    env_reads = sorted({
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(tree)
        if (isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"})
        or (isinstance(node, ast.Name) and node.id in {"environ", "getenv"})
    })

    assert env_reads == [], (
        f"{_ENTRY_POINT} 读了环境变量 {env_reads}：召回渲染必须是同样输入同样"
        f"输出、预算恒定生效的纯函数。挂在开关后面的预算，测试跑的是默认路径，"
        f"量不出生产环境关掉之后的结果"
    )


# Every module that talks to the structured recall endpoint. The memory
# server's own route file defines it rather than calling it.
_QUERY_MEMORY_ROUTE_DEFINITION = "app/memory_server/routes.py"
_KNOWN_RECALL_CLIENTS = {
    "main_logic/core/tool_calling.py",
    "plugin/plugins/qq_auto_reply/memory_bridge.py",
}


def test_every_query_memory_client_renders_through_the_entry_point():
    """Whoever fetches recall results has to render them through the entry point.

    The complement of the label check: that one catches a renderer that
    wants tags, this one catches a renderer that does NOT. Dropping the
    ``[fact/user]`` prefix is a reasonable product choice in a 1:1 chat
    where every entity is the same — and it was the biggest hole in the
    old marker-based scan, since such a file contained no marker, was never
    parsed, and every guard silently skipped it (issue #2588, A1, measured
    at 95x over budget). It cannot skip this one: it still has to ask the
    memory server for the results.

    Membership is asserted by equality, so a new client is a loud failure
    that has to come here and say what it renders through.
    """
    sources = _repo_python_sources()
    clients = _files_with_url_literal(
        sources, "/query_memory",
    ) - {_QUERY_MEMORY_ROUTE_DEFINITION}

    assert clients == _KNOWN_RECALL_CLIENTS, (
        f"/query_memory 的调用方集合变了：新增 {sorted(clients - _KNOWN_RECALL_CLIENTS)}，"
        f"消失 {sorted(_KNOWN_RECALL_CLIENTS - clients)}。\n"
        f"新调用方请把结果交给 memory.recall_render.render_recall_block 渲染，"
        f"再加进 _KNOWN_RECALL_CLIENTS"
    )
    entry_module = _ENTRY_POINT[:-3].replace("/", ".")
    not_rendering = sorted(
        rel for rel in clients if entry_module not in sources[rel]
    )
    assert not_rendering == [], (
        f"{not_rendering} 拿了召回结果却没走 {entry_module}——召回段就没有 token 上限了"
    )
