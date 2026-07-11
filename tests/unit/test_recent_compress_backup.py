# -*- coding: utf-8 -*-
"""best-effort 后台压缩（主路径压缩失败时兜底）回归测试。

主路径 update_history 压缩失败（如 RPM 限流连续失败）→ _on_compress_done(ok=False)
起一个受保护的一次性后台压缩；主路径某轮成功 → ok=True cancel 在跑的后台。失败退避
（复用 review 的 Gate 6 模式）防 summary 模型持续故障时每轮起注定失败的任务空烧。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.llm_client import AIMessage, HumanMessage, SystemMessage


def _history(n: int):
    out = []
    for i in range(n):
        out.append(HumanMessage(content=f"u{i}") if i % 2 == 0 else AIMessage(content=f"a{i}"))
    return out


async def _cleanup_task(task):
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            # cleanup-only：吞掉 cancel 抛出的 CancelledError 及 task 内部任何异常
            pass


@pytest.mark.unit
@pytest.mark.asyncio
async def test_on_compress_done_failure_spawns_backup():
    from app import memory_server
    name = "测试角色C"
    snapshot = _history(6)
    memory_server.gates._maint_state.pop(name, None)
    memory_server.compress_backup_tasks.pop(name, None)

    async def _slow_compress(*a, **k):
        await asyncio.sleep(30)

    fake_mgr = MagicMock()
    fake_mgr.compress_history = _slow_compress

    with patch.object(memory_server, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_asave_maint_state", AsyncMock()):
        await memory_server._on_compress_done(name, snapshot, ok=False, detailed=False)
        task = memory_server.compress_backup_tasks.get(name)
        assert task is not None and not task.done()  # 起了后台兜底
        await _cleanup_task(task)

    memory_server.compress_backup_tasks.pop(name, None)
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_on_compress_done_success_cancels_backup():
    from app import memory_server
    name = "测试角色C"
    task = MagicMock()
    task.done.return_value = False
    memory_server.compress_backup_tasks[name] = task

    with patch.object(memory_server.gates, "_asave_maint_state", AsyncMock()):
        await memory_server._on_compress_done(name, [], ok=True, detailed=False)

    task.cancel.assert_called_once()  # 主路径成功 → cancel 在跑的后台
    memory_server.compress_backup_tasks.pop(name, None)
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_on_compress_done_in_flight_guard():
    from app import memory_server
    name = "测试角色C"
    memory_server.gates._maint_state.pop(name, None)

    existing = MagicMock()
    existing.done.return_value = False
    memory_server.compress_backup_tasks[name] = existing

    fake_mgr = MagicMock()
    fake_mgr.compress_history = AsyncMock(return_value=None)
    with patch.object(memory_server, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_asave_maint_state", AsyncMock()):
        await memory_server._on_compress_done(name, _history(6), ok=False, detailed=False)

    # 同角色已有后台在跑 → 不重复起，仍是原 task
    assert memory_server.compress_backup_tasks[name] is existing
    memory_server.compress_backup_tasks.pop(name, None)
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_on_compress_done_deadletter_skips_spawn():
    from app import memory_server
    from memory.recent import build_review_fingerprint
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    name = "测试角色C"
    snapshot = _history(6)
    memory_server.compress_backup_tasks.pop(name, None)
    memory_server.gates._maint_state[name] = {
        "compress_backup_fail_attempts": MEMORY_LIVENESS_MAX_ATTEMPTS,
        "compress_backup_fail_fp": build_review_fingerprint(snapshot),
    }

    fake_mgr = MagicMock()
    fake_mgr.enforce_hard_cap = AsyncMock()
    with patch.object(memory_server, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_asave_maint_state", AsyncMock()):
        await memory_server._on_compress_done(name, snapshot, ok=False, detailed=False)

    # 连续失败 ≥ N 且输入未变 → dead-letter，不再起后台；但仍裁剪兜底
    assert name not in memory_server.compress_backup_tasks
    fake_mgr.enforce_hard_cap.assert_awaited_once()
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_on_compress_done_deadletter_resets_when_input_changed():
    from app import memory_server
    from memory.recent import build_review_fingerprint
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    name = "测试角色C"
    memory_server.compress_backup_tasks.pop(name, None)
    # 退避计数已满，但记录的是「旧输入」的 fingerprint
    memory_server.gates._maint_state[name] = {
        "compress_backup_fail_attempts": MEMORY_LIVENESS_MAX_ATTEMPTS,
        "compress_backup_fail_fp": build_review_fingerprint(_history(4)),
    }
    new_snapshot = _history(8)  # 输入变了

    async def _slow_compress(*a, **k):
        await asyncio.sleep(30)

    fake_mgr = MagicMock()
    fake_mgr.compress_history = _slow_compress
    with patch.object(memory_server, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_asave_maint_state", AsyncMock()):
        await memory_server._on_compress_done(name, new_snapshot, ok=False, detailed=False)
        # 输入变了 → 复位放行，起了后台
        task = memory_server.compress_backup_tasks.get(name)
        assert task is not None
        await _cleanup_task(task)

    memory_server.compress_backup_tasks.pop(name, None)
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_backup_compress_failure_bumps_backoff():
    from app import memory_server
    from memory.recent import build_review_fingerprint
    name = "测试角色C"
    snapshot = _history(6)
    memory_server.gates._maint_state.pop(name, None)

    fake_mgr = MagicMock()
    fake_mgr.compress_history = AsyncMock(return_value=None)
    fake_mgr.enforce_hard_cap = AsyncMock()
    with patch.object(memory_server, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_asave_maint_state", AsyncMock()):
        await memory_server._run_backup_compress(name, snapshot, False)

    state = memory_server.gates._maint_state[name]
    assert state["compress_backup_fail_attempts"] == 1
    assert state["compress_backup_fail_fp"] == build_review_fingerprint(snapshot)
    fake_mgr.enforce_hard_cap.assert_awaited_once()  # 后台也压不成 → 裁剪兜底
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_backup_compress_merges_and_clears_backoff():
    from app import memory_server
    name = "测试角色C"
    snapshot = _history(6)
    memory_server.gates._maint_state[name] = {"compress_backup_fail_attempts": 2}

    fake_mgr = MagicMock()
    fake_mgr.compress_history = AsyncMock(return_value=(SystemMessage(content="memo"), "memo"))
    fake_mgr.merge_backup_memo = AsyncMock(return_value="merged")
    with patch.object(memory_server, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_asave_maint_state", AsyncMock()):
        await memory_server._run_backup_compress(name, snapshot, False)

    fake_mgr.merge_backup_memo.assert_awaited_once()  # 成功 → 合并写回
    assert not memory_server.gates._maint_state[name].get("compress_backup_fail_attempts")  # 退避清零
    memory_server.gates._maint_state.pop(name, None)
