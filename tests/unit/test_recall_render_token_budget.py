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


def _discovered_recall_renderers() -> dict[str, str]:
    """Every module that renders a recall block, found by scanning.

    A hand-kept list only covers the sites whoever wrote it knew about,
    and this repo's recall block has already grown from one to two. The
    marker is ``render_recall_entry_tag`` — the shared label table every
    recall renderer goes through — so a third one shows up here the moment
    it is written.
    """
    marker = "render_recall_entry_tag"
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


_RECALL_RENDER_MARKER = "render_recall_entry_tag"


def _called_function_names(node: ast.AST) -> set[str]:
    """Every function name called anywhere under `node`."""
    names = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _recall_render_functions(source: str) -> list[ast.AST]:
    """The function(s) in `source` that actually render a recall block.

    Scoping is the whole point. Collecting call names from the MODULE root
    sweeps up every call in the file, so a function nobody ever calls can
    satisfy the guards below on the renderer's behalf: rewrite the real
    ``take_lines_within_token_budget(...)`` as a hand-rolled loop, park a
    dead helper mentioning the old names at the bottom of the module, and
    both guards stay green while unbounded memory text reaches the prompt.
    Substring matching was the first hole in this wall (a comment could
    satisfy it) and AST-over-the-module was the second.

    A renderer is identified by the same marker the module discovery uses —
    the shared label-table call every recall renderer goes through — so
    "is this a recall renderer" has one definition here, not two that drift.
    Innermost wins: if a nested function is the one calling the marker, the
    enclosing function is not also counted, or a dead sibling nested
    alongside the real renderer would be back in scope.
    """
    tree = ast.parse(source)
    candidates = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _RECALL_RENDER_MARKER in _called_function_names(node)
    ]
    inner = {
        id(nested)
        for node in candidates
        for nested in ast.walk(node)
        if nested is not node and id(nested) in {id(c) for c in candidates}
    }
    return [node for node in candidates if id(node) not in inner]


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