"""Guards for the L21 recall-block render budget.

``/query_memory`` results go straight into a prompt. Before this the only
bound was "take the first 5"; a single merged reflection could be
arbitrarily long, so the block had no ceiling at all.

Two twins render that block — the QQ plugin's ``render_relevant_memory``
and the main program's ``recall_memory`` tool handler. Both cap each entry
by tokens (truncate, never drop — recall is relevance-ranked, so half of
the hit still beats none of it) and both stop the block at the total
budget. The last test in this file discovers the renderers instead of
listing them, so a third one cannot be added un-budgeted.
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import RECALL_RENDER_ENTRY_MAX_TOKENS, RECALL_RENDER_TOTAL_MAX_TOKENS
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

    # Both renderers cap each rendered line at ENTRY + OVERHEAD, so a line
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
    """
    from config import RECALL_RENDER_LINE_OVERHEAD_TOKENS

    body = "群里聊过的一件事情"
    with patch("utils.language_utils.get_global_language", return_value="zh"):
        rendered = _bridge().render_relevant_memory([
            {
                "text": body,
                "tier": "reflection",
                "entity": "group_participant",
                "created_at": "2026-05-01T10:00:00",
            },
        ])

    assert body in rendered, "夹具失效：短条目被截断了，量到的就不是纯装饰"
    overhead = count_tokens(rendered) - count_tokens(body)
    assert 0 < overhead <= RECALL_RENDER_LINE_OVERHEAD_TOKENS, (
        f"实测行装饰 {overhead} tok 超出预留的 "
        f"{RECALL_RENDER_LINE_OVERHEAD_TOKENS} tok"
    )


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

    with patch("utils.language_utils.get_global_language", return_value="zh"):
        rendered = _bridge().render_relevant_memory(results)

    assert len(rendered.split("\n")) == 5, (
        f"5 条满额召回没能全部进 prompt：\n{rendered[:200]}"
    )
    assert rendered.startswith("1. ") and "\n5. " in rendered


# ── plugin side: memory_bridge.render_relevant_memory ────────────────


def _bridge():
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge

    return QQMemoryBridge(SimpleNamespace(logger=MagicMock()))


def test_plugin_recall_truncates_an_oversized_entry_instead_of_dropping_it():
    """A merged reflection can be thousands of tokens. Cut it, don't lose
    it — the entry is there because it ranked highest for this query.

    The per-line cap would bound the total on its own, so size alone
    cannot tell the two apart. What only the per-entry cap buys is room
    for the decoration: it trims the TEXT, leaving the trailing date
    intact, where a line-level cut would take the date off the end.
    """
    long_text = "露营的细节" * 2000
    assert count_tokens(long_text) > RECALL_RENDER_ENTRY_MAX_TOKENS

    with patch("utils.language_utils.get_global_language", return_value="zh"):
        rendered = _bridge().render_relevant_memory([{
            "text": long_text,
            "tier": "fact",
            "entity": "group_chat",
            "created_at": "2026-05-01T10:00:00",
        }])

    assert rendered, "超长条目应被截断保留，而不是整条消失"
    assert "露营的细节" in rendered
    assert rendered.rstrip().endswith("(2026-05-01)"), (
        "正文没先按单条上限截断，日期后缀被整行截断吃掉了"
    )
    assert count_tokens(rendered) <= RECALL_RENDER_ENTRY_MAX_TOKENS + 32, (
        f"单条召回未按 {RECALL_RENDER_ENTRY_MAX_TOKENS} tok 截断"
    )


def test_plugin_recall_block_stops_at_the_total_budget():
    """Ten near-max entries must not add up to a 4000-token prompt block."""
    chunk = "群里聊过的一件事情" * 200
    results = [_result(f"{i}{chunk}") for i in range(10)]

    with patch("utils.language_utils.get_global_language", return_value="zh"):
        rendered = _bridge().render_relevant_memory(results)

    assert rendered
    assert count_tokens(rendered) <= RECALL_RENDER_TOTAL_MAX_TOKENS, (
        f"召回段整体超过 {RECALL_RENDER_TOTAL_MAX_TOKENS} tok 预算"
    )
    # The block is a prefix of the relevance ranking, not a length-sorted
    # subset: the top hit is always in and the tail is what goes.
    assert rendered.startswith("1. ")
    assert "10. " not in rendered


def test_plugin_recall_keeps_short_entries_verbatim():
    """The budget must not touch the ordinary case."""
    with patch("utils.language_utils.get_global_language", return_value="zh"):
        rendered = _bridge().render_relevant_memory([
            _result("群里在聊露营"),
            _result("阿离喜欢辣条", tier="reflection", entity="group_participant"),
        ])

    assert "群里在聊露营" in rendered
    assert "阿离喜欢辣条" in rendered
    assert rendered.count("\n") == 1


# ── main-app twin: the recall_memory tool handler ────────────────────


class _ToolHarness:
    def __init__(self):
        from main_logic.core.tool_calling import ToolCallingMixin

        self.__class__ = type("_H", (_ToolHarness, ToolCallingMixin), {})
        self.user_language = "zh"
        self.lanlan_name = "小天"
        self.input_mode = "text"
        self.session = None
        self.memory_server_port = 12345


async def _call_tool(results: list[dict]) -> str:
    payload = {"results": results, "elapsed_ms": 3.0}
    response = SimpleNamespace(
        is_success=True, status_code=200, text="", json=lambda: payload,
    )
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    with patch(
        "utils.internal_http_client.get_internal_http_client",
        return_value=client,
    ):
        return await _ToolHarness()._handle_recall_memory_call({"query": "露营"})


@pytest.mark.asyncio
async def test_tool_recall_truncates_an_oversized_entry_instead_of_dropping_it():
    """Main-app twin of the plugin cap. This repo has shipped a group-side
    fix without the private-side one before.

    Same reasoning as the plugin twin: the time suffix surviving is what
    distinguishes trimming the text from cutting the whole line.
    """
    long_text = "露营的细节" * 2000
    rendered = await _call_tool([{
        "text": long_text,
        "tier": "fact",
        "entity": "group_chat",
        "created_at": "2026-05-01T10:00:00",
    }])

    assert "露营的细节" in rendered
    assert rendered.rstrip().endswith(")"), (
        "正文没先按单条上限截断，时间后缀被整行截断吃掉了"
    )
    assert "2026-05-01" in rendered
    assert count_tokens(rendered) <= RECALL_RENDER_ENTRY_MAX_TOKENS + 64


@pytest.mark.asyncio
async def test_tool_recall_block_stops_at_the_total_budget():
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


@pytest.mark.parametrize("lang", ["zh", "en", "ja"])
@pytest.mark.asyncio
async def test_tool_recall_reserves_room_for_its_localized_header(lang):
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

    results = [_result(f"第{i}条召回到的记忆内容") for i in range(4)]
    rendered_probe = await _call_tool_in(lang, results[:1])
    header_probe = RECALL_MEMORY_TOOL_FOUND_HEADER[lang].format(n=2)
    line_cost = count_tokens(rendered_probe.split("\n")[1])
    header_cost = count_tokens(header_probe)
    gate = 2 * line_cost + header_cost

    with patch(
        # tool_calling 在函数体里 `from config import ...`，每次调用都重新
        # 绑定，所以必须打在 config 上而不是模块属性上。
        "config.RECALL_RENDER_TOTAL_MAX_TOKENS", gate,
    ):
        rendered = await _call_tool_in(lang, results)

    listed = [ln for ln in rendered.split("\n")[1:] if ln.strip()]
    assert listed, "夹具失效：一条都没渲染出来"
    assert count_tokens(rendered) <= gate, (
        f"locale={lang}：整段 {count_tokens(rendered)} tok 超过闸门 {gate}"
        f"——首行总览没有从预算里扣掉"
    )


@pytest.mark.asyncio
async def test_tool_recall_header_count_matches_what_was_actually_rendered():
    """Announcing "found 10" and then listing 3 makes the model believe it
    lost seven results and call the tool again."""
    chunk = "聊过的一件事情" * 200
    rendered = await _call_tool([_result(f"{i}{chunk}") for i in range(10)])

    from config.prompts.prompts_memory import RECALL_MEMORY_TOOL_FOUND_HEADER

    lines = rendered.split("\n")
    listed = [ln for ln in lines[1:] if ln.strip()]
    assert listed, "夹具失效：一条都没渲染出来"
    assert len(listed) < 10, "夹具失效：没触发丢弃，这条用例什么都没测到"
    # 整行相等，不是子串包含：`str(4) in "找到 41 条相关记忆"` 会放过任何
    # 以正确数字开头的错误计数。
    assert lines[0] == RECALL_MEMORY_TOOL_FOUND_HEADER["zh"].format(
        n=len(listed)
    ), f"首行总览与实际条数不符：{lines[0]!r} vs {len(listed)} 条"


# ── nothing on a rendered line is unbounded ──────────────────────────


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


def test_plugin_recall_bounds_a_line_whose_tag_is_corrupt():
    with patch("utils.language_utils.get_global_language", return_value="zh"):
        rendered = _bridge().render_relevant_memory([_LONG_TAG_RESULT])

    assert count_tokens(rendered) <= RECALL_RENDER_TOTAL_MAX_TOKENS, (
        f"畸形 entity 让整段涨到 {count_tokens(rendered)} tok，越过了 "
        f"{RECALL_RENDER_TOTAL_MAX_TOKENS} 的安全上限"
    )


@pytest.mark.asyncio
async def test_tool_recall_bounds_a_line_whose_tag_is_corrupt():
    """Main-app twin of the same hole."""
    rendered = await _call_tool([_LONG_TAG_RESULT])

    assert count_tokens(rendered) <= RECALL_RENDER_TOTAL_MAX_TOKENS


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
            patch("utils.language_utils.get_global_language", return_value="zh"):
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


# ── discovery guard: no un-budgeted third renderer ───────────────────

# ONE literal, used by both the module scan and the function scan. They
# each held their own copy until the two were noticed drifting apart in
# review — and a docstring below claimed they were already unified.
_RECALL_RENDER_MARKER = "render_recall_entry_tag"


def _discovered_recall_renderers() -> dict[str, str]:
    """Every module that renders a recall block, found by scanning.

    A hand-kept list only covers the sites whoever wrote it knew about,
    and this repo's recall block has already grown from one to two. The
    marker is ``render_recall_entry_tag`` — the shared label table both
    shipped recall renderers go through — so a third one written the same
    way shows up here the moment it exists.

    ⚠️ KNOWN BLIND SPOT — see issue #2588. The marker is a proxy for "renders
    the localized tier/entity prefix", not for "renders recall results into
    a prompt", and the two come apart for any surface that reasonably
    chooses NOT to label its entries — a 1:1 chat, where every entity is
    the same, is the obvious case. Such a renderer contains no marker, is
    never parsed, and every guard below silently skips it. Nothing here
    detects that; do not read a green run as "no un-budgeted renderer
    exists".
    """
    marker = _RECALL_RENDER_MARKER
    skip_parts = {
        ".venv", "venv", "node_modules", "__pycache__", ".git", "build",
        "dist", ".claude", "tests",
    }
    renderers: dict[str, str] = {}
    for path in _REPO_ROOT.rglob("*.py"):
        # Relative parts, not absolute: this checkout lives under a
        # `.claude/worktrees/...` path, so filtering on absolute parts
        # skips the entire repository and the scan silently finds nothing.
        rel = path.relative_to(_REPO_ROOT)
        if skip_parts & set(rel.parts):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if marker not in source:
            continue
        # The label table's own module defines the marker; it renders no block.
        if rel.as_posix() == "config/prompts/prompts_memory.py":
            continue
        renderers[rel.as_posix()] = source
    return renderers


_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _called_function_names(node: ast.AST) -> set[str]:
    """Function names called on `node`'s OWN execution path.

    Two exclusions, both of them holes this guard has already been through:

    Nested scopes. ``ast.walk`` descends into nested ``def`` / ``class`` /
    ``lambda`` bodies, which attributes a nested helper's calls to the
    function enclosing it: park a never-called ``def`` inside the real
    renderer, move both budget calls into it, and the renderer looks
    budgeted while its actual render path is not. A nested function that
    renders a recall block of its own is picked up on its own by
    ``_recall_render_functions`` — being nested does not exempt it, and
    being an enclosing scope does not earn credit for it.

    Signature and decorator fields. Seeding from ``iter_child_nodes(node)``
    also sweeps ``decorator_list``, ``args`` (parameter defaults and
    annotations) and ``returns``. Those run at def/import time, not on the
    render path — and under ``from __future__ import annotations`` an
    annotation is never evaluated at all — so a budget call parked in a
    return annotation satisfies the guard while the body stays unbounded.
    Seed from ``node.body``: the statements that actually execute when the
    renderer is called.

    Statically dead statements. ``if False:`` / ``if 0:`` /
    ``if TYPE_CHECKING:`` bodies, ``while False:`` bodies, and anything
    after an unconditional ``return`` / ``raise`` / ``break`` / ``continue``
    never run, so a budget call parked in one of them is the nested-helper
    trick again without the helper.

    Comprehensions are NOT skipped: they carry their own scope at runtime
    but a call inside one is genuinely on this function's execution path.

    ⚠️ WHAT THIS DOES NOT DO, stated because this docstring has already
    been wrong twice. It is constant folding, not reachability analysis:
    a branch on a module-level or env flag, a call after ``sys.exit()``,
    an ``except`` clause that cannot fire, a loop that never iterates —
    all still count as "called". And even perfect reachability would not
    settle the real question, because the guard checks that the budget
    functions are CALLED, never that their results are USED: discard the
    return value of ``take_lines_within_token_budget`` and join the
    original list, or append un-budgeted text after the budget runs, and
    every check here stays green while the prompt is unbounded. Both were
    measured (45x and 1.01x-and-climbing over budget); issue #2588 has the
    full list.

    So: a green run here means "no recall renderer has an obviously
    decorative budget call". It does not mean the budget runs, and it does
    not mean the block is bounded. What actually bounds the two shipped
    renderers is the behavioural tests in this file, which render
    oversized input and measure the result. Adversarial fixtures for every
    shape this function DOES catch live in
    ``test_call_scan_ignores_names_that_never_run``, with the strict dual
    in ``test_call_scan_still_sees_budget_calls_that_do_run``.
    """
    names: set[str] = set()
    _collect_calls_in_block(getattr(node, 'body', []) or [], names)
    return names


_TERMINATORS = (ast.Return, ast.Raise, ast.Break, ast.Continue)

# `match_case` only exists on 3.10+; the tuple stays valid either way.
_MATCH_CASE = getattr(ast, "match_case", ())


def _constant_truth(test: ast.AST) -> bool | None:
    """`True`/`False` when `test` is decidable at parse time, else `None`.

    ``TYPE_CHECKING`` counts as False: it is True only for type checkers,
    and never when the renderer actually runs.
    """
    if isinstance(test, ast.Constant):
        try:
            return bool(test.value)
        except Exception:  # pragma: no cover — exotic __bool__ in a literal
            return None
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _constant_truth(test.operand)
        return None if inner is None else (not inner)
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return False
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return False
    return None


def _collect_calls_in_block(body: list, names: set[str]) -> None:
    """Walk a statement list, skipping branches that cannot execute."""
    for stmt in body:
        if isinstance(stmt, _NESTED_SCOPES):
            continue
        if isinstance(stmt, ast.If):
            truth = _constant_truth(stmt.test)
            if truth is True:
                _collect_calls_in_block(stmt.body, names)
                continue
            if truth is False:
                _collect_calls_in_block(stmt.orelse, names)
                continue
        if isinstance(stmt, ast.While) and _constant_truth(stmt.test) is False:
            _collect_calls_in_block(stmt.orelse, names)
            continue
        # Hand nested statement LISTS over whole, not one statement at a
        # time. `iter_child_nodes` flattens them, and a one-element list
        # makes the terminator rule below a no-op inside every compound
        # statement — `for ...: return; truncate_to_tokens(...)` would
        # still count. That made the docstring's "anything after an
        # unconditional return" true only of the outermost block.
        for field, value in ast.iter_fields(stmt):
            if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
                _collect_calls_in_block(value, names)
            elif isinstance(value, ast.stmt):
                _collect_calls_in_block([value], names)
            elif isinstance(value, ast.AST):
                _collect_calls_in_expr(value, names)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.AST):
                        _collect_calls_in_expr(item, names)
        if isinstance(stmt, _TERMINATORS):
            return


def _collect_calls_in_expr(node: ast.AST, names: set[str]) -> None:
    """Collect call names from an expression subtree.

    Recurses back into `_collect_calls_in_block` for any statement it meets
    so the two halves cannot drift. ``except`` clauses and ``match`` cases
    are neither statements nor expressions in the grammar (`excepthandler`
    / `match_case`), so they get an explicit hand-off — otherwise their
    bodies arrive here one statement at a time and the terminator rule
    stops applying inside them.
    """
    stack = [node]
    while stack:
        child = stack.pop()
        if isinstance(child, _NESTED_SCOPES):
            continue
        if isinstance(child, ast.stmt):
            _collect_calls_in_block([child], names)
            continue
        if isinstance(child, (ast.ExceptHandler, _MATCH_CASE)):
            for _field, value in ast.iter_fields(child):
                if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
                    _collect_calls_in_block(value, names)
                elif isinstance(value, ast.AST):
                    _collect_calls_in_expr(value, names)
            continue
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
        stack.extend(ast.iter_child_nodes(child))


def _recall_render_functions(source: str) -> list[ast.AST]:
    """The function(s) in `source` that actually render a recall block.

    Scoping is the whole point. Collecting call names from the MODULE root
    sweeps up every call in the file, so a function nobody ever calls can
    satisfy the guards below on the renderer's behalf: rewrite the real
    ``take_lines_within_token_budget(...)`` as a hand-rolled loop, park a
    dead helper mentioning the old names at the bottom of the module, and
    both guards stay green while unbounded memory text reaches the prompt.
    Substring matching was the first hole in this wall (a comment could
    satisfy it), AST-over-the-module was the second, and AST-over-the-
    function-including-its-nested-defs was the third — same trick, one
    indent level in.

    A renderer is identified by `_RECALL_RENDER_MARKER`, the same literal
    the module scan uses — one constant, so the two steps cannot come to
    disagree about what a recall renderer is. (They held separate copies
    of the string until review noticed, while this paragraph already
    claimed they were unified.) Matching is on the function's own body, so
    an enclosing scope is not credited with a nested renderer's marker
    call either: whichever function directly renders the block is the one
    that has to carry the budget.

    Inherits the module scan's blind spot — see
    `_discovered_recall_renderers`: a renderer that does not label its
    entries never reaches this function at all.
    """
    tree = ast.parse(source)
    return [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _RECALL_RENDER_MARKER in _called_function_names(node)
    ]


def _unbudgeted_recall_functions(source: str, required: set[str]) -> dict[str, list[str]]:
    """`{function name: sorted missing calls}` for this module's renderers.

    Checked per function, not against the union of all of them. The union
    reopens the hole one level up: plant the marker in the dead helper too
    and it is counted as a renderer, its budget calls join the pooled set,
    and the real renderer is unbudgeted again with every guard green. Each
    function that renders a recall block has to carry its own budget.
    """
    out: dict[str, list[str]] = {}
    for node in _recall_render_functions(source):
        missing = required - _called_function_names(node)
        if missing:
            out[node.name] = sorted(missing)
    return out


def test_every_recall_renderer_is_token_budgeted():
    """Discovered, not listed — and checked on calls, not on text.

    Substring-matching the constant names would pass on an ``import`` that
    is never used, or on a ``# TODO: hook up RECALL_RENDER_*`` comment. The
    claim is that the budget RUNS, so both budget calls have to be present
    as actual calls.
    """
    renderers = _discovered_recall_renderers()
    assert len(renderers) >= 2, (
        f"发现式扫描只找到 {sorted(renderers)}；标记词失效了，这条护栏已经形同虚设"
    )
    required = {"truncate_to_tokens", "take_lines_within_token_budget"}
    unbudgeted = {}
    for rel, source in sorted(renderers.items()):
        assert _recall_render_functions(source), (
            f"{rel} 因为出现标记词而被认成召回渲染点，却找不到任何真正调用它的"
            f"函数——护栏在这个文件上是空转的（标记词只出现在注释/字符串里？）"
        )
        missing = _unbudgeted_recall_functions(source, required)
        if missing:
            unbudgeted[rel] = missing
    assert unbudgeted == {}, (
        f"这些召回渲染点没有真正调用 token 预算（只是提到了常量名不算），"
        f"会把任意长度的记忆原文塞进 prompt：{unbudgeted}"
    )


def test_every_recall_renderer_goes_through_the_shared_budget_helper():
    """One helper for both, so the two budgets cannot drift apart.

    The renderer set comes from the same discovery as above rather than a
    second hardcoded list — otherwise a newly discovered third renderer
    would be budget-checked but never helper-checked.
    """
    renderers = _discovered_recall_renderers()
    expected = {
        "plugin/plugins/qq_auto_reply/memory_bridge.py",
        "main_logic/core/tool_calling.py",
    }
    assert expected <= set(renderers), (
        f"已知的两处渲染点没被发现式扫描找到：{expected - set(renderers)}"
    )
    hand_rolled = [
        rel for rel, source in sorted(renderers.items())
        if _unbudgeted_recall_functions(source, {"take_lines_within_token_budget"})
    ]
    assert hand_rolled == [], (
        f"{hand_rolled} 没走共享预算 helper，会独自漂移"
    )


_KNOWN_RECALL_RENDERERS = {
    "plugin/plugins/qq_auto_reply/memory_bridge.py",
    "main_logic/core/tool_calling.py",
}


def test_the_known_recall_renderers_are_exactly_these_two():
    """Pin the renderer set, so a new one is not waved through on an AST guess.

    The two guards above infer "this is budgeted" from the SHAPE of the
    source, and that inference has been defeated five times running —
    substring match, module-wide AST walk, a dead helper nested in the
    renderer, a budget call in a return annotation, a budget call in an
    ``if False:`` branch. Each fix was correct and each was followed by a
    new way in, because "does this call run, and is its result used"
    is undecidable in general.

    The two renderers this repo ships do not rest on that inference —
    they have behavioural budget tests here that render oversized input
    and measure the result. A renderer nobody has written yet has none, so
    this makes its arrival a loud failure rather than a silent AST pass.

    ⚠️ IT IS NOT A BACKSTOP FOR EVERYTHING (an earlier version of this
    docstring said it was). It reuses `_discovered_recall_renderers`, so
    it inherits that scan's blind spot exactly: a new renderer that never
    calls the shared tag helper is not in `found`, `found` therefore still
    equals the known set, and this passes. It catches a new renderer
    written like the existing two — not one written differently. Issue
    #2588 tracks that, with a measured case at 95x over budget.
    """
    found = set(_discovered_recall_renderers())
    assert found == _KNOWN_RECALL_RENDERERS, (
        f"召回渲染点集合变了：新增 {sorted(found - _KNOWN_RECALL_RENDERERS)}，"
        f"消失 {sorted(_KNOWN_RECALL_RENDERERS - found)}。\n"
        f"新增渲染点请补**行为**预算测试（喂超长输入、量渲染结果的 token 数），"
        f"再把它加进 _KNOWN_RECALL_RENDERERS——上面两条护栏只能从源码形状推断"
        f"「调了预算」，那个推断已经被绕过五次，不足以单独担保一个新渲染点。"
    )


# ── the call scan's own adversarial fixtures ─────────────────────────
#
# Every shape below defeated this guard at some point. They are checked
# against the helper directly, so the next round of hardening cannot
# silently give one of them back.

_BUDGET_CALLS = {"truncate_to_tokens", "take_lines_within_token_budget"}

_DEAD_SHAPES = [
    ("nested-def", '''
def render(results):
    def _unused():
        truncate_to_tokens("", 1)
        take_lines_within_token_budget([], 1)
    return "".join(results)
'''),
    ("return-annotation", '''
def render(results) -> truncate_to_tokens(take_lines_within_token_budget([], 1), 1):
    return "".join(results)
'''),
    ("param-default", '''
def render(results, _x=truncate_to_tokens("", 1), *, _y=take_lines_within_token_budget([], 1)):
    return "".join(results)
'''),
    ("decorator", '''
@register(truncate_to_tokens("", 1), take_lines_within_token_budget([], 1))
def render(results):
    return "".join(results)
'''),
    ("if-False", '''
def render(results):
    if False:
        truncate_to_tokens("", 1)
        take_lines_within_token_budget([], 1)
    return "".join(results)
'''),
    ("if-zero", '''
def render(results):
    if 0:
        truncate_to_tokens("", 1)
        take_lines_within_token_budget([], 1)
    return "".join(results)
'''),
    ("if-TYPE_CHECKING", '''
def render(results):
    if TYPE_CHECKING:
        truncate_to_tokens("", 1)
        take_lines_within_token_budget([], 1)
    return "".join(results)
'''),
    ("else-of-if-True", '''
def render(results):
    if True:
        pass
    else:
        truncate_to_tokens("", 1)
        take_lines_within_token_budget([], 1)
    return "".join(results)
'''),
    ("while-False", '''
def render(results):
    while False:
        truncate_to_tokens("", 1)
        take_lines_within_token_budget([], 1)
    return "".join(results)
'''),
    ("after-return", '''
def render(results):
    return "".join(results)
    truncate_to_tokens("", 1)
    take_lines_within_token_budget([], 1)
'''),
    ("nested-if-False-inside-live-branch", '''
def render(results, flag):
    if flag:
        if False:
            truncate_to_tokens("", 1)
            take_lines_within_token_budget([], 1)
    return "".join(results)
'''),
    ("class-body", '''
def render(results):
    class _Unused:
        x = truncate_to_tokens("", 1)
        y = take_lines_within_token_budget([], 1)
    return "".join(results)
'''),
    # The terminator rule used to apply only to the function's own
    # statement list: every compound statement handed its children over one
    # at a time, so a one-element list made the rule a no-op inside them.
    ("after-return-inside-for", '''
def render(results):
    for r in results:
        return r
        truncate_to_tokens("", 1)
        take_lines_within_token_budget([], 1)
    return "".join(results)
'''),
    ("after-return-inside-live-if", '''
def render(results, flag):
    if flag:
        return "".join(results)
        truncate_to_tokens("", 1)
        take_lines_within_token_budget([], 1)
    return ""
'''),
    ("after-raise-inside-try", '''
def render(results):
    try:
        raise ValueError("x")
        truncate_to_tokens("", 1)
        take_lines_within_token_budget([], 1)
    except ValueError:
        pass
    return "".join(results)
'''),
    ("after-continue-inside-except", '''
def render(results):
    out = []
    for r in results:
        try:
            out.append(r)
        except ValueError:
            continue
            truncate_to_tokens("", 1)
            take_lines_within_token_budget([], 1)
    return "".join(out)
'''),
    ("if-False-inside-with", '''
def render(results, lock):
    with lock:
        if False:
            truncate_to_tokens("", 1)
            take_lines_within_token_budget([], 1)
    return "".join(results)
'''),
]

_LIVE_SHAPES = [
    ("straight-line", '''
def render(results):
    lines = [truncate_to_tokens(r, 400) for r in results]
    kept, _ = take_lines_within_token_budget(lines, 2204)
    return "\\n".join(kept)
'''),
    ("inside-a-real-branch", '''
def render(results, flag):
    if flag:
        lines = [truncate_to_tokens(r, 400) for r in results]
        kept, _ = take_lines_within_token_budget(lines, 2204)
        return "\\n".join(kept)
    return ""
'''),
    ("inside-try-except", '''
def render(results):
    try:
        lines = [truncate_to_tokens(r, 400) for r in results]
        kept, _ = take_lines_within_token_budget(lines, 2204)
    except ValueError:
        return ""
    return "\\n".join(kept)
'''),
    ("else-of-if-False", '''
def render(results):
    if False:
        pass
    else:
        lines = [truncate_to_tokens(r, 400) for r in results]
        kept, _ = take_lines_within_token_budget(lines, 2204)
    return "\\n".join(kept)
'''),
    ("inside-a-real-loop", '''
def render(results):
    out = []
    for r in results:
        out.append(truncate_to_tokens(r, 400))
    kept, _ = take_lines_within_token_budget(out, 2204)
    return "\\n".join(kept)
'''),
    ("inside-an-except-handler", '''
def render(results):
    try:
        raise ValueError("x")
    except ValueError:
        lines = [truncate_to_tokens(r, 400) for r in results]
        kept, _ = take_lines_within_token_budget(lines, 2204)
        return "\\n".join(kept)
    return ""
'''),
    ("inside-a-with-block", '''
def render(results, lock):
    with lock:
        lines = [truncate_to_tokens(r, 400) for r in results]
        kept, _ = take_lines_within_token_budget(lines, 2204)
    return "\\n".join(kept)
'''),
    ("after-an-early-return-in-a-branch", '''
def render(results, flag):
    if not flag:
        return ""
    lines = [truncate_to_tokens(r, 400) for r in results]
    kept, _ = take_lines_within_token_budget(lines, 2204)
    return "\\n".join(kept)
'''),
]


@pytest.mark.parametrize("name,source", _DEAD_SHAPES, ids=[s[0] for s in _DEAD_SHAPES])
def test_call_scan_ignores_names_that_never_run(name, source):
    """A budget call that cannot execute must not count as budgeting.

    Each of these is a way the guard was defeated (or could be): the render
    path is a bare join with no ceiling, while both budget names appear
    somewhere the interpreter never reaches.
    """
    fn = ast.parse(source).body[0]
    assert isinstance(fn, ast.FunctionDef), f"夹具失效：{name} 没解析出函数"
    seen = _called_function_names(fn)
    assert not (_BUDGET_CALLS & seen), (
        f"[{name}] 永远不会执行的预算调用被算成了「调过预算」：{sorted(_BUDGET_CALLS & seen)}"
    )


@pytest.mark.parametrize("name,source", _LIVE_SHAPES, ids=[s[0] for s in _LIVE_SHAPES])
def test_call_scan_still_sees_budget_calls_that_do_run(name, source):
    """The strict dual — and the half that keeps the tightening honest.

    A scan that returned nothing would pass every test above while being
    useless; each round of hardening has to keep real, reachable budget
    calls visible or the guard degrades into a permanent red that someone
    eventually deletes.
    """
    fn = ast.parse(source).body[0]
    seen = _called_function_names(fn)
    assert _BUDGET_CALLS <= seen, (
        f"[{name}] 真实执行路径上的预算调用没被看见：缺 {sorted(_BUDGET_CALLS - seen)}"
    )