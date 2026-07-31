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
async def test_release_character_drains_old_identity_review_and_backup_tasks():
    from app import memory_server

    name = "即将改名角色"
    cancel_event = asyncio.Event()
    review_task = asyncio.create_task(asyncio.sleep(30))
    backup_task = asyncio.create_task(asyncio.sleep(30))
    memory_server.review.correction_cancel_flags[name] = cancel_event
    memory_server.review.correction_tasks[name] = review_task
    memory_server.review.compress_backup_tasks[name] = backup_task
    memory_server.review.compress_backup_task_generations[name] = ("old", 0)
    fake_time_manager = MagicMock()

    with patch.object(memory_server.runtime, "time_manager", fake_time_manager):
        result = await memory_server.runtime.release_character_resources(name)

    assert result["status"] == "success"
    assert result["cancelled_derived_tasks"] == 2
    assert cancel_event.is_set()
    assert review_task.cancelled()
    assert backup_task.cancelled()
    assert name not in memory_server.review.correction_tasks
    assert name not in memory_server.review.compress_backup_tasks
    assert name not in memory_server.review.compress_backup_task_generations
    fake_time_manager.dispose_engine.assert_called_once_with(name)
    memory_server.review._retired_derived_task_names.discard(name)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_blocks_review_respawn_until_published_identity_reload():
    from app import memory_server

    name = "改名发布窗口角色"
    fake_mgr = MagicMock()
    fake_mgr.aget_recent_history = AsyncMock(return_value=([], ("path", 0)))

    await memory_server.review.cancel_character_derived_tasks(name)
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), patch.object(
        memory_server.gates, "_ais_review_enabled", AsyncMock(return_value=True),
    ):
        await memory_server.review.maybe_spawn_review(name)
        fake_mgr.aget_recent_history.assert_not_awaited()

        await memory_server.review.reconcile_character_derived_task_admission({name})
        await memory_server.review.maybe_spawn_review(name)
        fake_mgr.aget_recent_history.assert_awaited_once_with(
            name, include_admission=True,
        )

    memory_server.review._retired_derived_task_names.discard(name)


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

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
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

    with patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
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
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
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
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
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
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
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
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._run_backup_compress(name, snapshot, False)

    state = memory_server.gates._maint_state[name]
    assert state["compress_backup_fail_attempts"] == 1
    assert state["compress_backup_fail_fp"] == build_review_fingerprint(snapshot)
    fake_mgr.enforce_hard_cap.assert_awaited_once()  # 后台也压不成 → 裁剪兜底
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_backup_compress_merges_and_clears_backoff(tmp_path):
    from app import memory_server
    from utils import recent_file
    name = "测试角色C"
    snapshot = _history(6)
    recent_path = tmp_path / "recent.json"
    recent_path.write_text("[]", encoding="utf-8")
    admission_generation = recent_file.capture_recent_generation(recent_path)
    memory_server.gates._maint_state[name] = {"compress_backup_fail_attempts": 2}

    fake_mgr = MagicMock()
    fake_mgr.compress_history = AsyncMock(return_value=(SystemMessage(content="memo"), "memo"))
    fake_mgr.merge_backup_memo = AsyncMock(return_value="merged")
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._run_backup_compress(
            name, snapshot, False, admission_generation,
        )

    fake_mgr.merge_backup_memo.assert_awaited_once_with(
        name,
        snapshot,
        SystemMessage(content="memo"),
        expected_generation=admission_generation,
    )
    assert not memory_server.gates._maint_state[name].get("compress_backup_fail_attempts")  # 退避清零
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_backup_failure_does_not_record_or_trim_new_identity(tmp_path):
    from app import memory_server
    from utils import recent_file

    name = "测试角色C"
    recent_path = tmp_path / "recent.json"
    recent_path.write_text("[]", encoding="utf-8")
    admission_generation = recent_file.capture_recent_generation(recent_path)
    recent_file.activate_recent_paths([recent_path])
    memory_server.gates._maint_state.pop(name, None)

    fake_mgr = MagicMock()
    fake_mgr.compress_history = AsyncMock(return_value=None)
    fake_mgr.enforce_hard_cap = AsyncMock()
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._run_backup_compress(
            name, _history(6), False, admission_generation,
        )

    assert name not in memory_server.gates._maint_state
    fake_mgr.enforce_hard_cap.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_backup_success_does_not_clear_new_identity_backoff(tmp_path):
    from app import memory_server
    from utils import recent_file

    name = "测试角色C"
    recent_path = tmp_path / "recent.json"
    recent_path.write_text("[]", encoding="utf-8")
    old_generation = recent_file.capture_recent_generation(recent_path)
    recent_file.activate_recent_paths([recent_path])
    new_generation = recent_file.capture_recent_generation(recent_path)
    memory_server.gates._maint_state[name] = {
        "compress_backup_fail_attempts": 2,
        "compress_backup_fail_fp": "new-identity-fingerprint",
        "compress_backup_generation": list(new_generation),
    }

    fake_mgr = MagicMock()
    fake_mgr.compress_history = AsyncMock(
        return_value=(SystemMessage(content="stale-memo"), "stale-memo"),
    )
    fake_mgr.merge_backup_memo = AsyncMock(return_value="moot")
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._run_backup_compress(
            name, _history(6), False, old_generation,
        )

    fake_mgr.merge_backup_memo.assert_not_awaited()
    assert memory_server.gates._maint_state[name]["compress_backup_fail_attempts"] == 2
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_deadletter_callback_does_not_trim_new_identity(tmp_path):
    from app import memory_server
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    from memory.recent import build_review_fingerprint
    from utils import recent_file

    name = "测试角色C"
    snapshot = _history(6)
    recent_path = tmp_path / "recent.json"
    recent_path.write_text("[]", encoding="utf-8")
    admission_generation = recent_file.capture_recent_generation(recent_path)
    memory_server.gates._maint_state[name] = {
        "compress_backup_fail_attempts": MEMORY_LIVENESS_MAX_ATTEMPTS,
        "compress_backup_fail_fp": build_review_fingerprint(snapshot),
        "compress_backup_generation": list(admission_generation),
    }
    real_amutate = memory_server.gates._amutate_maint_state

    async def _switch_identity_before_locked_mutation(lanlan_name, mutator):
        recent_file.activate_recent_paths([recent_path])
        return await real_amutate(lanlan_name, mutator)

    fake_mgr = MagicMock()
    fake_mgr.enforce_hard_cap = AsyncMock()
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(
             memory_server.gates,
             "_amutate_maint_state",
             side_effect=_switch_identity_before_locked_mutation,
         ), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._on_compress_done(
            name,
            snapshot,
            ok=False,
            detailed=False,
            admission_generation=admission_generation,
        )

    assert memory_server.gates._maint_state[name]["compress_backup_fail_attempts"] == (
        MEMORY_LIVENESS_MAX_ATTEMPTS
    )
    fake_mgr.enforce_hard_cap.assert_not_awaited()
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_failure_cannot_overwrite_new_generation_backoff(tmp_path):
    from app import memory_server
    from utils import recent_file

    name = "测试角色C"
    recent_path = tmp_path / "recent.json"
    recent_path.write_text("[]", encoding="utf-8")
    old_generation = recent_file.capture_recent_generation(recent_path)
    memory_server.gates._maint_state.pop(name, None)
    old_reached_write = asyncio.Event()
    release_old_write = asyncio.Event()
    real_amutate = memory_server.gates._amutate_maint_state
    calls = 0

    async def _delay_first_write(lanlan_name, mutator):
        nonlocal calls
        calls += 1
        if calls == 1:
            old_reached_write.set()
            await release_old_write.wait()
        return await real_amutate(lanlan_name, mutator)

    with patch.object(
        memory_server.gates, "_amutate_maint_state", side_effect=_delay_first_write,
    ), patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        old_record = asyncio.create_task(memory_server._record_compress_backup_failure(
            name, _history(4), old_generation,
        ))
        await asyncio.wait_for(old_reached_write.wait(), timeout=3)
        recent_file.activate_recent_paths([recent_path])
        new_generation = recent_file.capture_recent_generation(recent_path)
        assert await memory_server._record_compress_backup_failure(
            name, _history(6), new_generation,
        ) == 1
        release_old_write.set()
        assert await old_record is None

    state = memory_server.gates._maint_state[name]
    assert state["compress_backup_fail_attempts"] == 1
    assert state["compress_backup_generation"] == list(new_generation)
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reused_identity_replaces_stale_in_flight_backup(tmp_path):
    """A stale name-keyed task must not block the reused identity's fallback."""
    from app import memory_server
    from utils import recent_file

    name = "测试角色C-reused"
    path = tmp_path / "recent.json"
    path.write_text("[]", encoding="utf-8")
    old_generation = recent_file.capture_recent_generation(path)
    recent_file.activate_recent_paths([path])
    new_generation = recent_file.capture_recent_generation(path)

    old_task = asyncio.create_task(asyncio.sleep(30))
    memory_server.compress_backup_tasks[name] = old_task
    memory_server.review.compress_backup_task_generations[name] = old_generation

    async def _slow_compress(*args, **kwargs):
        await asyncio.sleep(30)

    fake_mgr = MagicMock()
    fake_mgr.compress_history = _slow_compress
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr):
        await memory_server._on_compress_done(
            name,
            _history(6),
            ok=False,
            detailed=False,
            admission_generation=new_generation,
        )
        new_task = memory_server.compress_backup_tasks[name]
        await _cleanup_task(old_task)

    assert new_task is not old_task
    assert old_task.cancelled()
    assert memory_server.review.compress_backup_task_generations[name] == new_generation
    await _cleanup_task(new_task)
    memory_server.compress_backup_tasks.pop(name, None)
    memory_server.review.compress_backup_task_generations.pop(name, None)
