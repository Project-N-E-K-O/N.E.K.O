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
    """Minimal stand-in exposing just what the helper touches."""

    def __init__(self, lanlan_name="Neko", user_language="zh"):
        self.lanlan_name = lanlan_name
        self.user_language = user_language
        self.append_context = AsyncMock(
            return_value=SimpleNamespace(appended=True, targets=("active_history",))
        )


def _install(monkeypatch, *, pending, block="\n\n[用户最近明确表示过...]\n- 加班"):
    stub = MagicMock()
    stub.take_pending_injection.return_value = pending
    stub.render_prompt_block.return_value = block
    monkeypatch.setattr(
        user_directives_module, "get_user_directives_manager", lambda: stub,
    )
    return stub


@pytest.mark.asyncio
async def test_injects_when_a_directive_was_just_recorded(monkeypatch):
    stub = _install(monkeypatch, pending=True)
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
