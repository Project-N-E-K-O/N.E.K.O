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
    budget = count_tokens(lines[0]) + count_tokens(lines[1])

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

    The per-entry cap trims ``text``; the block cap counts the rendered
    line (index, localized tier/entity tag, date suffix). Reasoning about
    that in a comment is how the arithmetic went wrong the first time, so
    it is asserted here — and ``limit`` is read off the signature rather
    than typed in, so raising it in a later PR fails here instead of
    silently shrinking the block.
    """
    import inspect

    from config import RECALL_RENDER_LINE_OVERHEAD_TOKENS
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge

    limit = inspect.signature(
        QQMemoryBridge.query_relevant_memory
    ).parameters["limit"].default
    assert isinstance(limit, int) and limit > 0

    assert RECALL_RENDER_TOTAL_MAX_TOKENS >= limit * (
        RECALL_RENDER_ENTRY_MAX_TOKENS + RECALL_RENDER_LINE_OVERHEAD_TOKENS
    ), (
        f"整段预算 {RECALL_RENDER_TOTAL_MAX_TOKENS} 装不下 {limit} 条满额条目"
        f"（每条 {RECALL_RENDER_ENTRY_MAX_TOKENS} tok 正文 + "
        f"{RECALL_RENDER_LINE_OVERHEAD_TOKENS} tok 行装饰）"
    )


def test_line_overhead_allowance_covers_what_the_renderer_actually_adds():
    """The allowance above is only honest if it matches reality. Measure
    the decoration on a real rendered line instead of trusting the number.
    """
    from config import RECALL_RENDER_LINE_OVERHEAD_TOKENS

    body = "群里聊过的一件事情，" * 400
    with patch("utils.language_utils.get_global_language", return_value="zh"):
        rendered = _bridge().render_relevant_memory([
            {
                "text": body,
                "tier": "reflection",
                "entity": "group_participant",
                "created_at": "2026-05-01T10:00:00",
            },
        ])

    overhead = count_tokens(rendered) - RECALL_RENDER_ENTRY_MAX_TOKENS
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
    it — the entry is there because it ranked highest for this query."""
    long_text = "露营的细节" * 2000
    assert count_tokens(long_text) > RECALL_RENDER_ENTRY_MAX_TOKENS

    with patch("utils.language_utils.get_global_language", return_value="zh"):
        rendered = _bridge().render_relevant_memory([_result(long_text)])

    assert rendered, "超长条目应被截断保留，而不是整条消失"
    assert "露营的细节" in rendered
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
    fix without the private-side one before."""
    long_text = "露营的细节" * 2000
    rendered = await _call_tool([_result(long_text)])

    assert "露营的细节" in rendered
    assert count_tokens(rendered) <= RECALL_RENDER_ENTRY_MAX_TOKENS + 64


@pytest.mark.asyncio
async def test_tool_recall_block_stops_at_the_total_budget():
    """hybrid_recall returns more than the plugin's five, so this side is
    where the total gate actually binds."""
    chunk = "聊过的一件事情" * 200
    results = [_result(f"{i}{chunk}") for i in range(10)]

    rendered = await _call_tool(results)

    assert count_tokens(rendered) <= RECALL_RENDER_TOTAL_MAX_TOKENS + 64, (
        "召回工具结果整体超过预算（+64 是 i18n 首行的余量）"
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


def _called_function_names(source: str) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


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
        missing = required - _called_function_names(source)
        if missing:
            unbudgeted[rel] = sorted(missing)
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
        if "take_lines_within_token_budget" not in _called_function_names(source)
    ]
    assert hand_rolled == [], (
        f"{hand_rolled} 没走共享预算 helper，会独自漂移"
    )