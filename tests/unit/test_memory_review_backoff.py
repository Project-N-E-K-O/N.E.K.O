# -*- coding: utf-8 -*-
"""用户审计 #1 回归：memory_review 失败退避（dead-letter）。

日志实锤：correction 模型持续超时 → review 每轮 3×110s 超时后 'failed' →
既不刷 ts 也不退避，配合长挂机 bypass 主动续命 → 整夜每 ~6min 无限重烧。

修复：'failed' 时 bump review_fail_attempts + 记下失败输入的 tail fingerprint；
maybe_spawn_review 的 Gate 6 在 attempts ≥ MEMORY_LIVENESS_MAX_ATTEMPTS 且输入
未变时跳过 spawn。输入一变（master 发新消息，fingerprint 变）→ 复位重试。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from utils.llm_client import AIMessage, HumanMessage


def _history(n: int):
    """造 n 条交替 user/ai 消息（长度 ≥ REVIEW_SKIP_HISTORY_LEN=8）。"""
    out = []
    for i in range(n):
        if i % 2 == 0:
            out.append(HumanMessage(content=f"u{i}"))
        else:
            out.append(AIMessage(content=f"a{i}"))
    return out


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_review_failed_bumps_backoff():
    """review_history 返回 ('failed', None) → bump review_fail_attempts + 记 fp。"""
    from app import memory_server
    from memory.recent import build_review_fingerprint

    name = "测试角色"
    snapshot = _history(10)
    memory_server.gates._maint_state.pop(name, None)

    fake_mgr = MagicMock()
    fake_mgr.review_history = AsyncMock(return_value=("failed", None))
    cancel_event = asyncio.Event()  # 未置位 → 真失败

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_asave_maint_state", AsyncMock()):
        await memory_server._run_review_in_background(name, snapshot, cancel_event)

    state = memory_server.gates._maint_state[name]
    assert state["review_fail_attempts"] == 1
    assert state["review_fail_fp"] == build_review_fingerprint(snapshot)
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_review_cancelled_does_not_bump():
    """cancel_event 置位时返回 ('failed', None) 是主动取消，不计入失败退避。"""
    from app import memory_server

    name = "测试角色"
    snapshot = _history(10)
    memory_server.gates._maint_state.pop(name, None)

    fake_mgr = MagicMock()
    fake_mgr.review_history = AsyncMock(return_value=("failed", None))
    cancel_event = asyncio.Event()
    cancel_event.set()  # 模拟 cancel_correction 已置位

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_asave_maint_state", AsyncMock()):
        await memory_server._run_review_in_background(name, snapshot, cancel_event)

    state = memory_server.gates._maint_state.get(name, {})
    assert state.get("review_fail_attempts", 0) == 0
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_review_patched_clears_backoff():
    """成功 patch → 清掉失败退避计数 + fp。"""
    from app import memory_server

    name = "测试角色"
    snapshot = _history(10)
    memory_server.gates._maint_state[name] = {
        "review_fail_attempts": 3,
        "review_fail_fp": [{"type": "human", "content": "stale"}],
    }

    fake_mgr = MagicMock()
    fake_mgr.review_history = AsyncMock(return_value=("patched", [{"type": "ai", "content": "x"}]))
    cancel_event = asyncio.Event()

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_asave_maint_state", AsyncMock()):
        await memory_server._run_review_in_background(name, snapshot, cancel_event)

    state = memory_server.gates._maint_state[name]
    assert state["review_fail_attempts"] == 0
    assert state["review_fail_fp"] is None
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_review_patched_save_failure_does_not_bump():
    """成功 review（patched）的 state 落盘抖动不得被误判成失败 bump（Codex P2）。"""
    from app import memory_server

    name = "测试角色保存抖动"
    snapshot = _history(10)
    memory_server.gates._maint_state.pop(name, None)
    fake_mgr = MagicMock()
    fake_mgr.review_history = AsyncMock(return_value=("patched", [{"type": "ai", "content": "x"}]))
    cancel_event = asyncio.Event()

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_asave_maint_state",
                      AsyncMock(side_effect=RuntimeError("disk full"))):
        await memory_server._run_review_in_background(name, snapshot, cancel_event)

    state = memory_server.gates._maint_state.get(name, {})
    assert state.get("review_fail_attempts", 0) == 0, (
        "成功 review 的 save 失败被外层 except 当成 review 失败 bump 了"
    )
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_review_failed_save_failure_counts_once():
    """'failed' 分支 save 抛异常时只记一次，不被外层 except 重复 bump（Codex P2）。"""
    from app import memory_server

    name = "测试角色失败保存"
    snapshot = _history(10)
    memory_server.gates._maint_state.pop(name, None)
    fake_mgr = MagicMock()
    fake_mgr.review_history = AsyncMock(return_value=("failed", None))
    cancel_event = asyncio.Event()

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_asave_maint_state",
                      AsyncMock(side_effect=RuntimeError("disk full"))):
        await memory_server._run_review_in_background(name, snapshot, cancel_event)

    # _record_review_failure 在落盘前已把内存计数 +1；外层 except 不再重复 bump
    assert memory_server.gates._maint_state.get(name, {}).get("review_fail_attempts", 0) == 1, (
        "save 失败导致 _record_review_failure 抛出后被外层 except 重复计数了"
    )
    memory_server.gates._maint_state.pop(name, None)


async def _drive_spawn(memory_server, name, history):
    """跑 maybe_spawn_review，gate 1-5 全开（patch 掉），只测 Gate 6。"""
    fake_mgr = MagicMock()
    fake_mgr.aget_recent_history = AsyncMock(return_value=history)
    # 被 spawn 的后台 task 真跑起来时会调 review_history——给个安全返回
    fake_mgr.review_history = AsyncMock(return_value=("white", None))

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_ais_review_enabled", AsyncMock(return_value=True)), \
         patch.object(memory_server.review, "_count_new_user_msgs_since_last_review", return_value=999), \
         patch.object(memory_server.gates, "_asave_maint_state", AsyncMock()):
        await memory_server.maybe_spawn_review(name)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gate6_skips_when_dead_lettered_and_input_unchanged():
    """attempts ≥ MAX 且当前 history tail fingerprint == 失败时记下的 → 不 spawn。"""
    from app import memory_server
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    from memory.recent import build_review_fingerprint

    name = "测试角色G6"
    history = _history(12)
    memory_server.correction_tasks.pop(name, None)
    memory_server.gates._maint_state[name] = {
        "review_fail_attempts": MEMORY_LIVENESS_MAX_ATTEMPTS,
        "review_fail_fp": build_review_fingerprint(history),
    }

    await _drive_spawn(memory_server, name, history)

    assert name not in memory_server.correction_tasks or memory_server.correction_tasks[name] is None, \
        "dead-letter + 输入未变时不应 spawn 新 review"
    memory_server.gates._maint_state.pop(name, None)
    memory_server.correction_tasks.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gate6_resets_and_spawns_when_input_changed():
    """attempts ≥ MAX 但当前 fingerprint 与失败记录不同（新消息）→ 复位并 spawn。"""
    from app import memory_server
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS

    name = "测试角色G6b"
    history = _history(12)
    memory_server.correction_tasks.pop(name, None)
    # 失败 fp 是另一段输入（与当前 history tail 不同）
    memory_server.gates._maint_state[name] = {
        "review_fail_attempts": MEMORY_LIVENESS_MAX_ATTEMPTS,
        "review_fail_fp": [{"type": "human", "content": "完全不同的旧输入"}],
    }

    await _drive_spawn(memory_server, name, history)

    # 输入已变 → 复位计数
    assert memory_server.gates._maint_state[name]["review_fail_attempts"] == 0
    # 并且确实 spawn 了（correction_tasks 落了一个 task）
    task = memory_server.correction_tasks.get(name)
    assert task is not None
    # 清理后台 task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        # 预期内：上面刚 task.cancel()，await 必然抛 CancelledError，吞掉即可
        pass
    memory_server.gates._maint_state.pop(name, None)
    memory_server.correction_tasks.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_resets_budget_on_input_change():
    """不同 history tail 各失败一次不应跨输入累积（Codex P2）：每次输入变都从 1 计起。"""
    from app import memory_server
    from memory.recent import build_review_fingerprint

    name = "测试角色累积"
    memory_server.gates._maint_state.pop(name, None)
    fake_mgr = MagicMock()
    fake_mgr.review_history = AsyncMock(return_value=("failed", None))
    cancel_event = asyncio.Event()

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_asave_maint_state", AsyncMock()):
        for n in (8, 10, 12):  # 三段 tail fingerprint 互不相同的输入
            await memory_server._run_review_in_background(name, _history(n), cancel_event)

    state = memory_server.gates._maint_state[name]
    assert state["review_fail_attempts"] == 1, "输入每次都变应复位，不该累积到 3"
    assert state["review_fail_fp"] == build_review_fingerprint(_history(12))
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_review_exception_bumps_backoff():
    """review_history 直接抛异常（而非返回 ('failed', None)）也要计入失败退避，
    否则 Gate 6 拿不到预算、持续重烧仍会发生（CodeRabbit Major）。"""
    from app import memory_server
    from memory.recent import build_review_fingerprint

    name = "测试角色异常"
    snapshot = _history(10)
    memory_server.gates._maint_state.pop(name, None)
    fake_mgr = MagicMock()
    fake_mgr.review_history = AsyncMock(side_effect=RuntimeError("boom"))
    cancel_event = asyncio.Event()

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_asave_maint_state", AsyncMock()):
        await memory_server._run_review_in_background(name, snapshot, cancel_event)

    state = memory_server.gates._maint_state[name]
    assert state["review_fail_attempts"] == 1
    assert state["review_fail_fp"] == build_review_fingerprint(snapshot)
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_same_input_accumulates_budget():
    """同一输入连续失败才累积预算（与跨输入复位对偶）。"""
    from app import memory_server

    name = "测试角色累积2"
    memory_server.gates._maint_state.pop(name, None)
    same = _history(10)
    fake_mgr = MagicMock()
    fake_mgr.review_history = AsyncMock(return_value=("failed", None))
    cancel_event = asyncio.Event()

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_asave_maint_state", AsyncMock()):
        for _ in range(3):
            await memory_server._run_review_in_background(name, same, cancel_event)

    assert memory_server.gates._maint_state[name]["review_fail_attempts"] == 3
    memory_server.gates._maint_state.pop(name, None)
