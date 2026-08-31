# -*- coding: utf-8 -*-
"""Contract for pushing fresh ban-topic directives into the LIVE session.

``_build_initial_prompt`` runs once per session, so a directive recorded at
turn N would otherwise not reach the model's prompt until the next session
rebuild — which in normal chatting is up to ``SESSION_TURN_THRESHOLD`` user
turns away. In between, the user has said "stop bringing X up" and watches the
character keep bringing X up. ``_inject_pending_user_directives`` closes that
window to the next turn.

Layering note: ``memory`` (L3) cannot import ``main_logic`` (L4), so the sink
only persists and raises a flag; the injection itself lives in L4. These tests
pin both halves of that split.
"""

from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import memory.user_directives as user_directives_module
from main_logic.core.notify import NotifyMixin


class _Manager(NotifyMixin):
    """Minimal stand-in exposing just what the helper touches.

    ``text_session`` decides which session shape ``self.session`` presents.
    ``_conversation_history`` is the same discriminator ``append_context`` uses:
    only ``OmniOfflineClient`` (the text path) has one.
    """

    def __init__(self, lanlan_name="Neko", user_language="zh", *, text_session=True):
        self.lanlan_name = lanlan_name
        self.user_language = user_language
        self.session = (
            SimpleNamespace(_conversation_history=[])
            if text_session
            else SimpleNamespace(prime_context=AsyncMock())
        )
        self.append_context = AsyncMock(
            return_value=SimpleNamespace(appended=True, targets=("active_history",))
        )


def _install(
    monkeypatch, *, pending, block="\n\n[用户最近明确表示过...]\n- 加班",
    terms=("加班",),
):
    stub = MagicMock()
    stub.take_pending_injection.return_value = pending
    stub.render_prompt_block.return_value = block
    stub.get_active_terms.return_value = list(terms)
    monkeypatch.setattr(
        user_directives_module, "get_user_directives_manager", lambda: stub,
    )
    return stub


@pytest.mark.asyncio
async def test_injects_when_a_directive_was_just_recorded(monkeypatch):
    _install(monkeypatch, pending=True)
    mgr = _Manager()

    await mgr._inject_pending_user_directives()

    mgr.append_context.assert_awaited_once()
    kwargs = mgr.append_context.await_args.kwargs
    assert "加班" in kwargs["text"]
    assert kwargs["audience"] == "model"
    # session_family：既进当前会话，也写进 next-session 缓存。后者是**热切换
    # 竞态**的唯一解——预热已经跑过 _build_initial_prompt 之后才落盘的指令，
    # 会整个错过那次 swap。降成 current_session 的话这条会红。
    assert kwargs["lifetime"] == "session_family"
    # when_ready：会话还没建好时排队，不丢。
    assert kwargs["timing"] == "when_ready"
    # request_id 让 append_context 的去重生效（同一组 term 只注入一次）
    assert kwargs.get("request_id")


@pytest.mark.asyncio
async def test_voice_session_never_touches_the_live_turn(monkeypatch):
    """⚠️⚠️ Realtime sessions must NOT receive a current-session injection."""
    # ``append_context`` 对没有 _conversation_history 的会话回落到
    # ``prime_context(skipped=True)``，而那条路在 Gemini 上会
    # ``send_client_content(turn_complete=True)`` 另开一个 user turn 并置
    # _skip_until_next_response —— 本轮所有 transcript 与 audio 被整段吞掉。
    # 用户刚说完"以后别再提加班"，角色一个音都不发，恰好坏在这个功能最该起
    # 作用的那一刻（对抗审查用真 Gemini 客户端 A/B 实测复现）。
    # 语音侧只写 next-session 缓存：不比改动前差（那时也是等下次重建读盘），
    # 且仍能赶上热切换。
    _install(monkeypatch, pending=True)
    mgr = _Manager(text_session=False)

    await mgr._inject_pending_user_directives()

    kwargs = mgr.append_context.await_args.kwargs
    assert kwargs["lifetime"] == "next_session", (
        "realtime 会话拿到 current_session/session_family 会让 Gemini 吞掉整轮回复"
    )


@pytest.mark.asyncio
async def test_repeat_of_the_same_directive_reuses_one_request_id(monkeypatch):
    """Saying the same thing twice must not append two identical blocks."""
    # E 那半按 hit_count 递增 TTL，整个设计就预期用户会重复说同一条；而
    # ``record`` 的刷新分支同样会置待注入标记。不给稳定 request_id 的话，
    # 每次都原样再追加一份：文字侧堆进 history 还会被算进归档 token 预算、
    # 提前触发热切换，语音侧堆进 next-session 缓存。
    _install(monkeypatch, pending=True)
    mgr = _Manager()

    await mgr._inject_pending_user_directives()
    first = mgr.append_context.await_args.kwargs["request_id"]
    await mgr._inject_pending_user_directives()
    second = mgr.append_context.await_args.kwargs["request_id"]

    assert first == second, "同一组 term 必须得到同一个 request_id 才能被去重"


@pytest.mark.asyncio
async def test_a_new_term_gets_a_different_request_id(monkeypatch):
    """Adding a term must produce a fresh id, or the update is silently deduped."""
    # 对照组：没有这条，"request_id 写死成常量"也能让上面那条通过——而那会
    # 让用户后来说的每一条新禁令都被当成重复、永远进不了当前会话。
    stub = _install(
        monkeypatch, pending=True, block="\n\n[...]\n- 加班", terms=("加班",),
    )
    mgr = _Manager()
    await mgr._inject_pending_user_directives()
    first = mgr.append_context.await_args.kwargs["request_id"]

    stub.render_prompt_block.return_value = "\n\n[...]\n- 加班\n- 股票"
    stub.get_active_terms.return_value = ["加班", "股票"]
    await mgr._inject_pending_user_directives()
    second = mgr.append_context.await_args.kwargs["request_id"]

    assert first != second


@pytest.mark.asyncio
async def test_distinct_term_sets_never_collide_on_a_separator(monkeypatch):
    """Two different term sets must not share a request id via separator ambiguity."""
    # ⚠️ `_normalize_term` 只做 strip + 长度校验，term 里什么字符都可能有。
    # 指纹若拿分隔符 join，`["aa | bb", "cc"]` 与 `["aa", "bb | cc"]` 会得到
    # 同一个串 → 同一个 request_id → 后一组禁令被 append_context 当成重复
    # 跳过、本会话不生效（coderabbit）。JSON 序列化天然无歧义。
    stub = _install(
        monkeypatch, pending=True, block="\n\n[...]\n- aa | bb\n- cc",
        terms=("aa | bb", "cc"),
    )
    mgr = _Manager()
    await mgr._inject_pending_user_directives()
    first = mgr.append_context.await_args.kwargs["request_id"]

    stub.render_prompt_block.return_value = "\n\n[...]\n- aa\n- bb | cc"
    stub.get_active_terms.return_value = ["aa", "bb | cc"]
    await mgr._inject_pending_user_directives()
    second = mgr.append_context.await_args.kwargs["request_id"]

    assert first != second, (
        "两组不同的 term 集合不能因为分隔符歧义撞成同一个 request_id"
    )


@pytest.mark.asyncio
async def test_request_id_is_independent_of_display_order(monkeypatch):
    """⚠️ Reordering the same term set must NOT produce a new request id."""
    # 渲染块里的 term 按 ``last_seen_at`` 降序排，于是「重复说**较旧**的那一条」
    # 会把它顶到最前 —— 集合没变、字节变了。指纹若取渲染块，这里就会得到新 id、
    # 又追加一份完整拷贝；而这恰恰是延长 TTL 的常规路径（E 那半的整个设计就预期
    # 用户重复说），等于去重在最该生效的场景下被绕过（codex P2）。
    stub = _install(
        monkeypatch, pending=True,
        block="\n\n[...]\n- 加班\n- 股票", terms=("加班", "股票"),
    )
    mgr = _Manager()
    await mgr._inject_pending_user_directives()
    first = mgr.append_context.await_args.kwargs["request_id"]

    # 用户又说了一遍"别再提股票" → 股票 last_seen 更新 → 排到最前，集合没变
    stub.render_prompt_block.return_value = "\n\n[...]\n- 股票\n- 加班"
    stub.get_active_terms.return_value = ["股票", "加班"]
    await mgr._inject_pending_user_directives()
    second = mgr.append_context.await_args.kwargs["request_id"]

    assert first == second, (
        "同一组 term 换个显示顺序必须仍是同一个 request_id，否则重复说话就会"
        "把整块禁令再追加一份"
    )


@pytest.mark.asyncio
async def test_no_pending_flag_means_no_injection(monkeypatch):
    """An ordinary chat turn must not append anything to the prompt."""
    _install(monkeypatch, pending=False)
    mgr = _Manager()

    await mgr._inject_pending_user_directives()

    mgr.append_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_injects_full_active_list_not_just_the_new_term(monkeypatch):
    """Injects the FULL active list — the same block _build_initial_prompt uses."""
    # 只注入增量的话，模型在本会话里看到的禁令集合会取决于"哪几条恰好是这一
    # 轮说的"，跟会话重建后看到的集合不一致。
    stub = _install(
        monkeypatch, pending=True, block="\n\n[...]\n- 加班\n- 股票\n- 前任",
    )
    mgr = _Manager()

    await mgr._inject_pending_user_directives()

    text = mgr.append_context.await_args.kwargs["text"]
    assert "加班" in text and "股票" in text and "前任" in text
    stub.render_prompt_block.assert_called_once()


@pytest.mark.asyncio
async def test_empty_block_is_not_injected(monkeypatch):
    """Flag set but the render is empty (all expired / cleared) -> inject nothing."""
    _install(monkeypatch, pending=True, block="")
    mgr = _Manager()

    await mgr._inject_pending_user_directives()

    mgr.append_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_uses_default_bucket_when_character_unnamed(monkeypatch):
    """The read key must match the sink's bucket rule, or it never finds anything."""
    stub = _install(monkeypatch, pending=True)
    mgr = _Manager(lanlan_name="")

    await mgr._inject_pending_user_directives()

    stub.take_pending_injection.assert_called_once_with("default")


@pytest.mark.asyncio
async def test_append_failure_is_swallowed(monkeypatch):
    """An injection failure must not take the user's turn down with it."""
    _install(monkeypatch, pending=True)
    mgr = _Manager()
    mgr.append_context = AsyncMock(side_effect=RuntimeError("session gone"))

    await mgr._inject_pending_user_directives()  # 不抛


@pytest.mark.asyncio
async def test_memory_unavailable_is_swallowed(monkeypatch):
    def _boom():
        raise RuntimeError("no memory module")

    monkeypatch.setattr(
        user_directives_module, "get_user_directives_manager", _boom,
    )
    mgr = _Manager()

    await mgr._inject_pending_user_directives()  # 不抛
    mgr.append_context.assert_not_awaited()


def test_worst_case_block_fits_the_registered_token_budget():
    """⚠️ The worst renderable directive block must fit its transport budget.

    Not a constant-vs-constant check: it renders a real block at the two hard
    limits (term length, active count) and measures it, because "the numbers
    look fine" has never been evidence that the payload fits.
    """
    # 越线的后果不是"少几条"：request_id 按**完整** term 集合算，截断后的重试
    # 要么被去重、要么原样再追加同一份截断载荷，被截掉的禁令永远进不去。
    from config import USER_DIRECTIVE_MAX_ACTIVE
    from config.prompts.prompts_directives import render_directives_block
    from main_logic.core._shared import _CONTEXT_APPEND_SOURCE_MAX_TOKENS
    from utils.tokenize import count_tokens

    # 走已 import 的模块对象取常量，别再 ``from memory.user_directives import``
    # ——同一模块既 ``import`` 又 ``import from`` 会被 code-quality 扫出来。
    _TERM_MAX_LEN = user_directives_module._TERM_MAX_LEN

    budget = _CONTEXT_APPEND_SOURCE_MAX_TOKENS.get("user_directives")
    assert budget, "user_directives 必须登记自己的预算，别落到 1000 默认值"

    # 已知 token 密度最高的一类：日文假名。用满两个硬上限。
    dense_term = "ぬ" * _TERM_MAX_LEN
    worst = render_directives_block(
        [dense_term] * USER_DIRECTIVE_MAX_ACTIVE, "ja",
    )
    measured = count_tokens(worst)
    assert measured <= budget, (
        f"最坏情况渲染块 {measured} tokens 超出登记预算 {budget} —— "
        f"会被静默截断，且被截掉的禁令永远补不回去。"
        f"调大 _TERM_MAX_LEN({_TERM_MAX_LEN}) 或 "
        f"USER_DIRECTIVE_MAX_ACTIVE({USER_DIRECTIVE_MAX_ACTIVE}) 时要同步调预算"
    )


# ── 调用点守卫 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("module_path", "method_name"),
    (
        ("main_logic.core.streaming", "_process_stream_data_internal"),
        ("main_logic.core.turn", "handle_input_transcript"),
    ),
)
def test_both_utterance_entry_points_await_the_injection(module_path, method_name):
    """Both utterance entry points must be wired, text and voice alike."""
    # ⚠️ helper 单测全绿也证明不了它被调用过。这条按 AST 确认两个入口里都
    # 存在 ``await self._inject_pending_user_directives()``。对偶性守卫：
    # 只接一条的话另一条会红——语音用户否则拿不到这个修复。
    module = __import__(module_path, fromlist=["_"])
    source = inspect.getsource(module)
    tree = ast.parse(source)

    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == method_name:
            target = node
            break
    assert target is not None, f"{module_path} 里找不到 {method_name}"

    found = any(
        isinstance(inner, ast.Await)
        and isinstance(inner.value, ast.Call)
        and getattr(inner.value.func, "attr", "") == "_inject_pending_user_directives"
        for inner in ast.walk(target)
    )
    assert found, (
        f"{module_path}.{method_name} 必须 await "
        "_inject_pending_user_directives()，否则该入口的用户说完"
        "「别再提 X」后要等下一次 session 重建才生效"
    )
