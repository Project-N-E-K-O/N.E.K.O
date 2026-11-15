# -*- coding: utf-8 -*-
"""Regression for user audit #1: memory_review failure backoff (dead letter).

Logs confirmed: the correction model repeatedly timed out → review timed out 3×110s each round and then returned 'failed' →
neither refreshed ts nor backed off; combined with long-idle bypass proactively keeping it alive → it kept burning resources all night about every 6 minutes.

Fix: on 'failed', bump review_fail_attempts and record the tail fingerprint of the failed input;
maybe_spawn_review Gate 6 skips spawn when attempts ≥ MEMORY_LIVENESS_MAX_ATTEMPTS and the input
has not changed. When input changes (master sends a new message, fingerprint changes) → reset and retry.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from utils import recent_file
from utils.llm_client import AIMessage, HumanMessage


def _history(n: int):
    """Create n alternating user/AI messages (length ≥ REVIEW_SKIP_HISTORY_LEN=8)."""
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
    """review_history returns ('failed', None) → bump review_fail_attempts and record the fingerprint."""
    from app import memory_server
    from memory.recent import build_review_fingerprint

    name = "测试角色"
    snapshot = _history(10)
    memory_server.gates._maint_state.pop(name, None)

    fake_mgr = MagicMock()
    fake_mgr.review_history = AsyncMock(return_value=("failed", None))
    cancel_event = asyncio.Event()  # Not set → genuine failure.

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._run_review_in_background(name, snapshot, cancel_event)

    state = memory_server.gates._maint_state[name]
    assert state["review_fail_attempts"] == 1
    assert state["review_fail_fp"] == build_review_fingerprint(snapshot)
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_review_cancelled_does_not_bump():
    """When cancel_event is set, ('failed', None) means an intentional cancellation and must not count toward failure backoff."""
    from app import memory_server

    name = "测试角色"
    snapshot = _history(10)
    memory_server.gates._maint_state.pop(name, None)

    fake_mgr = MagicMock()
    fake_mgr.review_history = AsyncMock(return_value=("failed", None))
    cancel_event = asyncio.Event()
    cancel_event.set()  # Simulate cancel_correction being set.

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._run_review_in_background(name, snapshot, cancel_event)

    state = memory_server.gates._maint_state.get(name, {})
    assert state.get("review_fail_attempts", 0) == 0
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_review_patched_clears_backoff():
    """Successful patch → clear the failure backoff count and fingerprint."""
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
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._run_review_in_background(name, snapshot, cancel_event)

    state = memory_server.gates._maint_state[name]
    assert state["review_fail_attempts"] == 0
    assert state["review_fail_fp"] is None
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_review_patched_save_failure_does_not_bump():
    """A state persistence hiccup after a successful (patched) review must not be mistaken for a failed bump (Codex P2)."""
    from app import memory_server

    name = "测试角色保存抖动"
    snapshot = _history(10)
    memory_server.gates._maint_state.pop(name, None)
    fake_mgr = MagicMock()
    fake_mgr.review_history = AsyncMock(return_value=("patched", [{"type": "ai", "content": "x"}]))
    cancel_event = asyncio.Event()

    persist = MagicMock(side_effect=RuntimeError("disk full"))
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", persist):
        await memory_server._run_review_in_background(name, snapshot, cancel_event)

    # First confirm that the injected failure was actually triggered; otherwise this test would
    # pass vacuously when the patched branch does not persist anything (a weaker assertion than the claim).
    assert persist.call_count >= 1, "patched 分支没有落盘，注入的写盘失败被绕过了"
    state = memory_server.gates._maint_state.get(name, {})
    assert state.get("review_fail_attempts", 0) == 0, (
        "成功 review 的 save 失败被外层 except 当成 review 失败 bump 了"
    )
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_review_failed_save_failure_counts_once():
    """If save raises in the 'failed' branch, record it only once; do not bump again in the outer except (Codex P2)."""
    from app import memory_server

    name = "测试角色失败保存"
    snapshot = _history(10)
    memory_server.gates._maint_state.pop(name, None)
    fake_mgr = MagicMock()
    fake_mgr.review_history = AsyncMock(return_value=("failed", None))
    cancel_event = asyncio.Event()

    persist = MagicMock(side_effect=RuntimeError("disk full"))
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", persist):
        await memory_server._run_review_in_background(name, snapshot, cancel_event)

    assert persist.call_count >= 1, "'failed' 分支没有落盘，注入的写盘失败被绕过了"
    # _record_review_failure's mutator has already incremented the in-memory count before persistence; the write failure is caught by
    # _mutate_maint_state_locked inside the critical section (warning only, not re-raised), so the outer except will not bump it again.
    assert memory_server.gates._maint_state.get(name, {}).get("review_fail_attempts", 0) == 1, (
        "save 失败导致 _record_review_failure 抛出后被外层 except 重复计数了"
    )
    memory_server.gates._maint_state.pop(name, None)


async def _drive_spawn(memory_server, name, history):
    """Run maybe_spawn_review with gates 1–5 all open (patched out); test only Gate 6."""
    fake_mgr = MagicMock()
    fake_mgr.aget_recent_history = AsyncMock(
        return_value=(
            history,
            recent_file.capture_recent_generation("review-test-recent.json"),
        ),
    )
    # When the spawned background task actually runs, it calls review_history — provide a safe return value.
    fake_mgr.review_history = AsyncMock(return_value=("white", None))

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_ais_review_enabled", AsyncMock(return_value=True)), \
         patch.object(memory_server.review, "_count_new_user_msgs_since_last_review", return_value=999), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server.maybe_spawn_review(name)
        task = memory_server.correction_tasks.get(name)
        if task is not None:
            await task
    return fake_mgr


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generic_review_does_not_rewrite_theater_messages():
    """Generic review skips theater capsules but keeps ordinary chat before and after them."""  # noqa: DOCSTRING_CJK
    from app import memory_server

    name = "测试角色-theater-review"
    history = _history(16)
    history[2] = HumanMessage(
        content="把虚构合同递过去。",
        metadata={"source": "theater_numeric_v2", "session_id": "theater_session"},
    )
    memory_server.correction_tasks.pop(name, None)
    memory_server.gates._maint_state.pop(name, None)

    fake_mgr = await _drive_spawn(memory_server, name, history)

    fake_mgr.review_history.assert_awaited_once()
    reviewed_snapshot = fake_mgr.review_history.await_args.args[1]
    assert reviewed_snapshot == history[:2] + history[3:]
    assert all(
        message.metadata.get("source") != "theater_numeric_v2"
        for message in reviewed_snapshot
    )
    assert name not in memory_server.correction_tasks


@pytest.mark.unit
@pytest.mark.asyncio
async def test_spawn_passes_snapshot_admission_generation():
    """The background review must retain the identity token from its snapshot read."""
    from app import memory_server
    from utils import recent_file

    name = "测试角色review-generation"
    history = _history(12)
    memory_server.correction_tasks.pop(name, None)
    memory_server.gates._maint_state.pop(name, None)

    admission_generation = recent_file.capture_recent_generation(
        "review-test-recent.json",
    )
    fake_mgr = MagicMock()
    fake_mgr.aget_recent_history = AsyncMock(
        return_value=(history, admission_generation),
    )
    fake_mgr.review_history = AsyncMock(return_value=("white", None))

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_ais_review_enabled", AsyncMock(return_value=True)), \
         patch.object(memory_server.review, "_count_new_user_msgs_since_last_review", return_value=999), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server.maybe_spawn_review(name)
        await memory_server.correction_tasks[name]

    assert fake_mgr.review_history.await_args.kwargs["expected_generation"] == admission_generation
    memory_server.correction_tasks.pop(name, None)
    memory_server.correction_cancel_flags.pop(name, None)
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_review_failure_cannot_mutate_reused_identity(tmp_path):
    """An old failed review must not consume the new identity's retry budget."""
    from app import memory_server
    from utils import recent_file

    name = "测试角色-stale-review-failure"
    path = tmp_path / "recent.json"
    path.write_text("[]", encoding="utf-8")
    old_generation = recent_file.capture_recent_generation(path)
    recent_file.activate_recent_paths([path])
    memory_server.gates._maint_state.pop(name, None)

    attempts = await memory_server.review._record_review_failure(
        name, _history(10), old_generation,
    )

    assert attempts is None
    assert memory_server.gates._maint_state.get(name) == {}
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_background_review_skips_manager_call(tmp_path):
    """A review invalidated before task start must not enter the LLM manager."""
    from app import memory_server
    from utils import recent_file

    name = "测试角色-stale-before-review-call"
    path = tmp_path / "recent.json"
    path.write_text("[]", encoding="utf-8")
    old_generation = recent_file.capture_recent_generation(path)
    recent_file.activate_recent_paths([path])
    fake_mgr = MagicMock()
    fake_mgr.review_history = AsyncMock(return_value=("failed", None))

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr):
        await memory_server._run_review_in_background(
            name, _history(10), asyncio.Event(), old_generation,
        )

    fake_mgr.review_history.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gate6_skips_when_dead_lettered_and_input_unchanged():
    """attempts ≥ MAX and the current history tail fingerprint matches the recorded failure → do not spawn."""
    from app import memory_server
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    from memory.recent import build_review_fingerprint

    name = "测试角色G6"
    history = _history(12)
    memory_server.correction_tasks.pop(name, None)
    generation = recent_file.capture_recent_generation("review-test-recent.json")
    memory_server.gates._maint_state[name] = {
        "review_fail_attempts": MEMORY_LIVENESS_MAX_ATTEMPTS,
        "review_fail_fp": build_review_fingerprint(history),
        "review_fail_generation": list(generation),
    }

    await _drive_spawn(memory_server, name, history)

    assert name not in memory_server.correction_tasks or memory_server.correction_tasks[name] is None, \
        "dead-letter + 输入未变时不应 spawn 新 review"
    memory_server.gates._maint_state.pop(name, None)
    memory_server.correction_tasks.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reused_identity_does_not_inherit_review_dead_letter():
    """A same-fingerprint new generation must clear the old identity's backoff."""
    from app import memory_server
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    from memory.recent import build_review_fingerprint

    name = "测试角色G6-generation-reuse"
    history = _history(12)
    path = "review-test-recent.json"
    old_generation = recent_file.capture_recent_generation(path)
    memory_server.gates._maint_state[name] = {
        "review_fail_attempts": MEMORY_LIVENESS_MAX_ATTEMPTS,
        "review_fail_fp": build_review_fingerprint(history),
        "review_fail_generation": list(old_generation),
    }
    recent_file.activate_recent_paths([path])

    fake_mgr = await _drive_spawn(memory_server, name, history)

    fake_mgr.review_history.assert_awaited_once()
    memory_server.gates._maint_state.pop(name, None)
    memory_server.correction_tasks.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reused_identity_does_not_inherit_output_exhaustion_breaker():
    """A new generation must not inherit the previous identity's token breaker."""
    from app import memory_server
    from config import MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS

    name = "测试角色G6a-generation-reuse"
    history = _history(12)
    path = "review-test-recent.json"
    old_generation = recent_file.capture_recent_generation(path)
    memory_server.gates._maint_state[name] = {
        "review_output_exhaustion_attempts": (
            MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS
        ),
        "review_output_exhaustion_min_context_tokens": 1,
        "review_output_exhaustion_blocked": True,
        "review_output_exhaustion_generation": list(old_generation),
    }
    recent_file.activate_recent_paths([path])

    fake_mgr = await _drive_spawn(memory_server, name, history)

    fake_mgr.review_history.assert_awaited_once()
    memory_server.gates._maint_state.pop(name, None)
    memory_server.correction_tasks.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gate6_resets_and_spawns_when_input_changed():
    """attempts ≥ MAX but the current fingerprint differs from the failure record (new message) → reset and spawn."""
    from app import memory_server
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS

    name = "测试角色G6b"
    history = _history(12)
    memory_server.correction_tasks.pop(name, None)
    # The failed fp is from a different input (not the current history tail).
    memory_server.gates._maint_state[name] = {
        "review_fail_attempts": MEMORY_LIVENESS_MAX_ATTEMPTS,
        "review_fail_fp": [{"type": "human", "content": "完全不同的旧输入"}],
    }

    fake_mgr = await _drive_spawn(memory_server, name, history)

    # Input changed → reset the count.
    assert memory_server.gates._maint_state[name]["review_fail_attempts"] == 0
    # Also ensure the background path actually reached review_history, rather than merely creating and immediately canceling the task.
    fake_mgr.review_history.assert_awaited_once()
    assert name not in memory_server.correction_tasks
    memory_server.gates._maint_state.pop(name, None)
    memory_server.correction_tasks.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_resets_budget_on_input_change():
    """Failures on different history tails must not accumulate across inputs (Codex P2): each input change starts again at 1."""
    from app import memory_server
    from memory.recent import build_review_fingerprint

    name = "测试角色累积"
    memory_server.gates._maint_state.pop(name, None)
    fake_mgr = MagicMock()
    fake_mgr.review_history = AsyncMock(return_value=("failed", None))
    cancel_event = asyncio.Event()

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        for n in (8, 10, 12):  # Three inputs with distinct tail fingerprints.
            await memory_server._run_review_in_background(name, _history(n), cancel_event)

    state = memory_server.gates._maint_state[name]
    assert state["review_fail_attempts"] == 1, "输入每次都变应复位，不该累积到 3"
    assert state["review_fail_fp"] == build_review_fingerprint(_history(12))
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_review_exception_bumps_backoff():
    """An exception raised directly by review_history (instead of returning ('failed', None)) must also count toward failure backoff,
    otherwise Gate 6 never gets a budget and repeated resource burn will continue (CodeRabbit Major)."""
    from app import memory_server
    from memory.recent import build_review_fingerprint

    name = "测试角色异常"
    snapshot = _history(10)
    memory_server.gates._maint_state.pop(name, None)
    fake_mgr = MagicMock()
    fake_mgr.review_history = AsyncMock(side_effect=RuntimeError("boom"))
    cancel_event = asyncio.Event()

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._run_review_in_background(name, snapshot, cancel_event)

    state = memory_server.gates._maint_state[name]
    assert state["review_fail_attempts"] == 1
    assert state["review_fail_fp"] == build_review_fingerprint(snapshot)
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_same_input_accumulates_budget():
    """Only repeated failures on the same input accumulate budget (paired with reset on input change)."""
    from app import memory_server

    name = "测试角色累积2"
    memory_server.gates._maint_state.pop(name, None)
    same = _history(10)
    fake_mgr = MagicMock()
    fake_mgr.review_history = AsyncMock(return_value=("failed", None))
    cancel_event = asyncio.Event()

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        for _ in range(3):
            await memory_server._run_review_in_background(name, same, cancel_event)

    assert memory_server.gates._maint_state[name]["review_fail_attempts"] == 3
    memory_server.gates._maint_state.pop(name, None)
