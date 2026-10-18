# -*- coding: utf-8 -*-
"""Contract for carrying fresh ban-topic directives into the NEXT session.

``_build_initial_prompt`` reads the directive store once per session, and for a
hot swap that read happens during background warm-up — a full user turn before
the swap lands. A directive recorded inside that gap misses the very session it
was about to configure: the new session starts with a fresh
``_conversation_history`` and a system prompt that predates it, so nothing
carries it for up to another archive cycle.
``_inject_pending_user_directives`` closes exactly that gap by writing the
next-session cache.

⚠️ It deliberately does NOT inject into the live session, for either session
shape. Inside a session the user's original words stay in
``_conversation_history`` (append-only), and the session-opening prompt already
carried the full active list — so a live injection is redundant there, while
still costing the awkward ``HumanMessage("system: ...")`` shape. See
``test_directive_block_only_targets_the_next_session``.

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
    # Queue when the session is not ready yet; do not lose the request.
    assert kwargs["timing"] == "when_ready"
    # request_id enables append_context deduplication (inject each set of terms only once)
    assert kwargs.get("request_id")


@pytest.mark.parametrize("text_session", [True, False], ids=["text", "voice"])
@pytest.mark.asyncio
async def test_directive_block_only_targets_the_next_session(
    monkeypatch, text_session,
):
    """⚠️⚠️ Neither session shape may receive a current-session injection.

    Two independent reasons land on the same value, which is why this is one
    parametrized invariant rather than a per-shape branch:

    - **text**: redundant. ``_conversation_history`` is append-only within a
      session, so the user's own "stop bringing X up" stays in context for
      every later turn, and the session-opening ``_build_initial_prompt``
      already injected the full active list — the only delta this turn is the
      term the model just read in the raw utterance. What a live injection
      *does* add is shape risk: ``append_context`` renders ``role='system'``
      as ``HumanMessage("system: ...")``, i.e. it arrives looking like another
      user message that merely restates what the user just said.
    - **voice**: harmful. ``append_context`` falls back to
      ``prime_context(skipped=True)`` for sessions with no
      ``_conversation_history``, and on Gemini that path issues
      ``send_client_content(turn_complete=True)``, opening a second user turn
      and setting ``_skip_until_next_response`` — the whole turn's transcript
      and audio get swallowed. The user finishes saying "stop mentioning
      overtime" and the character makes no sound at all (reproduced A/B
      against a real Gemini client during adversarial review).

    Raising this to ``current_session`` / ``session_family`` re-opens the
    voice failure outright, so the guard covers both ids.
    """
    _install(monkeypatch, pending=True)
    mgr = _Manager(text_session=text_session)

    await mgr._inject_pending_user_directives()

    kwargs = mgr.append_context.await_args.kwargs
    assert kwargs["lifetime"] == "next_session", (
        "current_session/session_family 会让 Gemini 吞掉整轮回复，"
        "文字侧则是重复模型刚读过的原话"
    )


def test_directive_source_is_not_registered_for_bare_prime():
    """``user_directives`` must stay out of the bare-prime table (dead config).

    That table only affects the ``prime_context`` fallback, which is reachable
    only for ``lifetime in {"current_session", "session_family"}``. With the
    source pinned to ``next_session`` above, an entry here would never be read
    — and would suggest to the next reader that the block still goes out via
    realtime instructions.
    """
    from main_logic.core._shared import _CONTEXT_APPEND_BARE_PRIME_SOURCES

    assert "user_directives" not in _CONTEXT_APPEND_BARE_PRIME_SOURCES


@pytest.mark.asyncio
async def test_repeat_of_the_same_directive_reuses_one_request_id(monkeypatch):
    """Saying the same thing twice must not append two identical blocks."""
    # The E side extends TTL by hit_count, and the whole design expects users to repeat the same item; the
    # ``record`` refresh branch also marks it for injection. Without a stable request_id,
    # each repeat would append another identical copy to the next-session cache, so the next session would see the same
    # directive block several times in a row.
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
    # Control case: without this, "request_id hard-coded as a constant" would also pass the test above — but then
    # every new directive the user adds later would be treated as a duplicate and never reach the current session.
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
    # ⚠️ `_normalize_term` only strips whitespace and checks length; a term can contain any characters.
    # If the fingerprint joins with a separator, `["aa | bb", "cc"]` and `["aa", "bb | cc"]` produce
    # the same string → same request_id → append_context treats the latter set of directives as a duplicate
    # and skips it, so it has no effect in this session (coderabbit). JSON serialization is unambiguous by construction.
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
    # Terms in the rendered block are sorted by ``last_seen_at`` descending, so "repeat the **older** item"
    # moves it to the front — the set is unchanged, but the bytes differ. If the fingerprint uses the rendered block, this creates a new ID
    # and appends another full copy; this is exactly the normal TTL extension path (the E side is designed for
    # users to repeat items), so deduplication would be bypassed precisely where it matters most (codex P2).
    stub = _install(
        monkeypatch, pending=True,
        block="\n\n[...]\n- 加班\n- 股票", terms=("加班", "股票"),
    )
    mgr = _Manager()
    await mgr._inject_pending_user_directives()
    first = mgr.append_context.await_args.kwargs["request_id"]

    # User repeats "别再提股票" → the stock term's last_seen is updated → it moves to the front; the set is unchanged
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
    # If only the delta is injected, the directives the model sees in this session depend on "which ones happened to be said this
    # turn," making them inconsistent with the set seen after a session rebuild.
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

    await mgr._inject_pending_user_directives()  # Do not raise


@pytest.mark.asyncio
async def test_memory_unavailable_is_swallowed(monkeypatch):
    def _boom():
        raise RuntimeError("no memory module")

    monkeypatch.setattr(
        user_directives_module, "get_user_directives_manager", _boom,
    )
    mgr = _Manager()

    await mgr._inject_pending_user_directives()  # Do not raise
    mgr.append_context.assert_not_awaited()


def test_worst_case_block_fits_the_registered_token_budget():
    """⚠️ The worst renderable directive block must fit its transport budget.

    Not a constant-vs-constant check: it renders a real block at the two hard
    limits (term length, active count) and measures it, because "the numbers
    look fine" has never been evidence that the payload fits.
    """
    # The consequence of exceeding the limit is not "a few missing items": request_id is based on the **complete** term set; a retry after truncation
    # would either be deduplicated or append the same truncated payload again, so the omitted directives could never be added.
    from config import USER_DIRECTIVE_MAX_ACTIVE
    from config.prompts.prompts_directives import render_directives_block
    from main_logic.core._shared import _CONTEXT_APPEND_SOURCE_MAX_TOKENS
    from utils.tokenize import count_tokens

    # Read constants from the already imported module object; do not use ``from memory.user_directives import``
    # — importing the same module both with ``import`` and ``import from`` is flagged by code-quality checks.
    _TERM_MAX_LEN = user_directives_module._TERM_MAX_LEN

    budget = _CONTEXT_APPEND_SOURCE_MAX_TOKENS.get("user_directives")
    assert budget, "user_directives 必须登记自己的预算，别落到 1000 默认值"

    # Japanese kana are known to have the highest token density; use both hard limits fully.
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


# ── Call-site guard ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("module_path", "method_name"),
    (
        ("main_logic.core.streaming", "_process_stream_data_internal"),
        ("main_logic.core.turn", "handle_input_transcript"),
    ),
)
def test_both_utterance_entry_points_await_the_injection(module_path, method_name):
    """Both utterance entry points must be wired, text and voice alike."""
    # ⚠️ Passing the helper unit tests does not prove it was called. This checks the AST to confirm that both entry points
    # contain ``await self._inject_pending_user_directives()``. This symmetry guard
    # fails if only one entry point is wired, which would leave voice users without the fix.
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
