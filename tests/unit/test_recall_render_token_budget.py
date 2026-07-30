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

    lines = rendered.split("\n")
    listed = [ln for ln in lines[1:] if ln.strip()]
    assert listed, "夹具失效：一条都没渲染出来"
    assert str(len(listed)) in lines[0], (
        f"首行总览与实际条数不符：{lines[0]!r} vs {len(listed)} 条"
    )


# ── discovery guard: no un-budgeted third renderer ───────────────────


def test_every_recall_renderer_is_token_budgeted():
    """Discovered, not listed.

    A hand-kept list of renderers only covers the ones whoever wrote it
    happened to know about, and this repo's recall block has already grown
    from one site to two. The marker is ``render_recall_entry_tag`` — the
    shared label table every recall renderer goes through — so a third
    renderer shows up here the moment it is written.
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

    assert len(renderers) >= 2, (
        f"发现式扫描只找到 {sorted(renderers)}；标记词失效了，这条护栏已经形同虚设"
    )
    unbudgeted = [
        rel for rel, source in sorted(renderers.items())
        if "RECALL_RENDER_ENTRY_MAX_TOKENS" not in source
        or "RECALL_RENDER_TOTAL_MAX_TOKENS" not in source
    ]
    assert unbudgeted == [], (
        f"这些召回渲染点没有 token 预算，会把任意长度的记忆原文塞进 "
        f"prompt：{unbudgeted}"
    )


def test_shared_budget_helper_is_what_both_renderers_use():
    """Both twins call one helper, so their budgets cannot drift apart.

    Checked on the AST rather than by grepping the constant, because a
    renderer could import the constants and then hand-roll a loop that
    quietly differs from the other side.
    """
    callers = set()
    for rel in (
        "plugin/plugins/qq_auto_reply/memory_bridge.py",
        "main_logic/core/tool_calling.py",
    ):
        tree = ast.parse((_REPO_ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "take_lines_within_token_budget"
            ):
                callers.add(rel)
    assert callers == {
        "plugin/plugins/qq_auto_reply/memory_bridge.py",
        "main_logic/core/tool_calling.py",
    }, f"只有 {sorted(callers)} 走共享预算 helper，另一侧会独自漂移"
