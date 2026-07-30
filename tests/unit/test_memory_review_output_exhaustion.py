from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.llm_client import AIMessage, HumanMessage


def _history(length: int) -> list:
    return [
        HumanMessage(content=f"u{index}")
        if index % 2 == 0
        else AIMessage(content=f"a{index}")
        for index in range(length)
    ]


def test_review_response_detects_explicit_output_limit():
    from memory.recent import _review_response_hit_output_limit

    response = SimpleNamespace(
        content="partial json",
        response_metadata={"finish_reason": "length", "token_usage": {}},
    )

    assert _review_response_hit_output_limit(response) is True


def test_review_response_does_not_treat_short_empty_response_as_output_limit():
    from memory.recent import _review_response_hit_output_limit

    response = SimpleNamespace(
        content="",
        response_metadata={
            "finish_reason": "stop",
            "token_usage": {"completion_tokens": 12},
        },
    )

    assert _review_response_hit_output_limit(response) is False


def test_review_response_detects_empty_response_at_shared_output_guard():
    from config import LLM_OUTPUT_GUARD_MAX_TOKENS
    from memory.recent import _review_response_hit_output_limit

    response = SimpleNamespace(
        content="",
        response_metadata={
            "token_usage": {"output_tokens": LLM_OUTPUT_GUARD_MAX_TOKENS},
        },
    )

    assert _review_response_hit_output_limit(response) is True


def test_review_llm_leaves_thinking_on_model_default():
    from memory.recent import CompressedRecentHistoryManager

    model = "qwen3.7-plus-2026-05-26"
    manager = object.__new__(CompressedRecentHistoryManager)
    manager._config_manager = MagicMock()
    manager._config_manager.get_model_api_config.return_value = {
        "model": model,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "test",
        "provider_type": "openai",
    }
    sentinel = object()

    with patch("memory.recent.create_chat_llm", return_value=sentinel) as factory:
        assert manager._get_review_llm() is sentinel

    assert factory.call_args.kwargs["extra_body"] is None


@pytest.mark.asyncio
async def test_review_context_token_count_uses_async_counter():
    from memory.recent import review_context_token_count

    with patch("memory.recent.acount_tokens", AsyncMock(return_value=123)) as counter:
        result = await review_context_token_count(_history(8))

    assert result == 123
    counter.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_three_output_exhaustions_block_across_growing_contexts():
    from app import memory_server
    from config import MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS

    name = "output-limit-growing-context"
    memory_server.gates._maint_state.pop(name, None)
    fake_manager = MagicMock()
    fake_manager.review_history = AsyncMock(return_value=("output_exhausted", None))

    with (
        patch.object(memory_server.runtime, "recent_history_manager", fake_manager),
        patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()),
        patch(
            "memory.recent.review_context_token_count",
            side_effect=lambda rows: len(rows) * 100,
        ),
    ):
        for length in (10, 12, 14):
            await memory_server._run_review_in_background(
                name,
                _history(length),
                asyncio.Event(),
            )

    state = memory_server.gates._maint_state[name]
    assert state["review_output_exhaustion_attempts"] == (
        MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS
    )
    assert state["review_output_exhaustion_min_context_tokens"] == 1000
    assert state["review_output_exhaustion_blocked"] is True
    assert state.get("review_fail_attempts", 0) == 0
    memory_server.gates._maint_state.pop(name, None)


async def _drive_review_gate(
    memory_server, name: str, history: list, token_count=None
) -> None:
    fake_manager = MagicMock()
    fake_manager.aget_recent_history = AsyncMock(return_value=history)
    fake_manager.review_history = AsyncMock(return_value=("white", None))

    with (
        patch.object(memory_server.runtime, "recent_history_manager", fake_manager),
        patch.object(
            memory_server.gates,
            "_ais_review_enabled",
            AsyncMock(return_value=True),
        ),
        patch.object(
            memory_server.review,
            "_count_new_user_msgs_since_last_review",
            return_value=999,
        ),
        patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()),
        patch(
            "memory.recent.review_context_token_count",
            side_effect=token_count or (lambda rows: len(rows) * 100),
        ),
    ):
        await memory_server.maybe_spawn_review(name)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_output_exhaustion_gate_waits_for_context_to_shrink():
    from app import memory_server
    from config import MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS

    name = "output-limit-gate"
    memory_server.correction_tasks.pop(name, None)
    memory_server.gates._maint_state[name] = {
        "review_output_exhaustion_attempts": (
            MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS
        ),
        "review_output_exhaustion_min_context_tokens": 1000,
        "review_output_exhaustion_blocked": True,
    }

    await _drive_review_gate(memory_server, name, _history(14))
    assert name not in memory_server.correction_tasks

    await _drive_review_gate(memory_server, name, _history(8))
    state = memory_server.gates._maint_state[name]
    assert state["review_output_exhaustion_attempts"] == 0
    assert state["review_output_exhaustion_min_context_tokens"] is None
    assert state["review_output_exhaustion_blocked"] is False

    task = memory_server.correction_tasks.get(name)
    assert task is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    memory_server.gates._maint_state.pop(name, None)
    memory_server.correction_tasks.pop(name, None)
    memory_server.correction_cancel_flags.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_success_clears_output_exhaustion_state():
    from app import memory_server

    name = "output-limit-success"
    memory_server.gates._maint_state[name] = {
        "review_output_exhaustion_attempts": 2,
        "review_output_exhaustion_min_context_tokens": 1000,
        "review_output_exhaustion_blocked": False,
    }
    fake_manager = MagicMock()
    fake_manager.review_history = AsyncMock(return_value=("patched", []))

    with (
        patch.object(memory_server.runtime, "recent_history_manager", fake_manager),
        patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()),
    ):
        await memory_server._run_review_in_background(
            name,
            _history(10),
            asyncio.Event(),
        )

    state = memory_server.gates._maint_state[name]
    assert state["review_output_exhaustion_attempts"] == 0
    assert state["review_output_exhaustion_min_context_tokens"] is None
    assert state["review_output_exhaustion_blocked"] is False
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_white_review_clears_output_exhaustion_state():
    """Mirror of the patched-branch case: a white review must reset the breaker too.

    A white review means the cutoff anchor no longer matches, i.e. the input has
    actually changed, so the previous "context is too long to summarise" verdict
    no longer describes the current input. Leaving the breaker armed would keep
    blocking reviews on an input that was never the one that exhausted the
    output budget.
    """
    from app import memory_server

    name = "output-limit-white"
    memory_server.gates._maint_state[name] = {
        "review_output_exhaustion_attempts": 2,
        "review_output_exhaustion_min_context_tokens": 1000,
        "review_output_exhaustion_blocked": True,
        "review_fail_attempts": 3,
        "review_fail_fp": "old-fp",
        "last_reviewed_cutoff_tail": "old-anchor",
    }
    fake_manager = MagicMock()
    fake_manager.review_history = AsyncMock(return_value=("white", None))

    with (
        patch.object(memory_server.runtime, "recent_history_manager", fake_manager),
        patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()),
    ):
        await memory_server._run_review_in_background(
            name,
            _history(10),
            asyncio.Event(),
        )

    state = memory_server.gates._maint_state[name]
    assert state["review_output_exhaustion_attempts"] == 0
    assert state["review_output_exhaustion_min_context_tokens"] is None
    assert state["review_output_exhaustion_blocked"] is False
    # 顺带钉住白 review 的既有语义：清锚点、清失败退避、故意不刷 last_review_ts
    # （下轮 gate 4 用旧 ts 直接放行 + fingerprint=None 触发 gate 5 的 ∞ 通行）。
    assert state["last_reviewed_cutoff_tail"] is None
    assert state["review_fail_attempts"] == 0
    assert state["review_fail_fp"] is None
    assert "last_review_ts" not in state
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generic_failure_breaks_output_exhaustion_streak():
    from app import memory_server

    name = "output-limit-then-generic-failure"
    memory_server.gates._maint_state[name] = {
        "review_output_exhaustion_attempts": 2,
        "review_output_exhaustion_min_context_tokens": 1000,
        "review_output_exhaustion_blocked": False,
    }
    fake_manager = MagicMock()
    fake_manager.review_history = AsyncMock(return_value=("failed", None))

    with (
        patch.object(memory_server.runtime, "recent_history_manager", fake_manager),
        patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()),
    ):
        await memory_server._run_review_in_background(
            name,
            _history(10),
            asyncio.Event(),
        )

    state = memory_server.gates._maint_state[name]
    assert state["review_output_exhaustion_attempts"] == 0
    assert state["review_output_exhaustion_min_context_tokens"] is None
    assert state["review_output_exhaustion_blocked"] is False
    assert state["review_fail_attempts"] == 1
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recovery_that_loses_the_race_does_not_spawn_a_review():
    """A breaker armed while the async token count ran must still block the spawn."""
    # Gate 6a 的恢复判定只能在锁外做（review_context_token_count 是 async，进不了同步
    # mutator），锁内复查会拒绝清零。但**拒绝清零还不够** —— 调用方必须跟着放弃本轮，
    # 否则等于拿一个已经过期的判定绕过了刚 armed 的断路器，那条已经证明压不动的
    # context 又被放行重烧一轮。这条钉的是调用点，不是 mutator 的返回值。
    from app import memory_server
    from config import MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS

    name = "output-limit-lost-race"
    memory_server.correction_tasks.pop(name, None)
    memory_server.gates._maint_state[name] = {
        "review_output_exhaustion_attempts": (
            MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS
        ),
        "review_output_exhaustion_min_context_tokens": 1000,
        "review_output_exhaustion_blocked": True,
    }

    def count_while_a_concurrent_writer_arms_a_newer_breaker(rows):
        # 模拟另一个后台 review 在这次 async 计数期间又失败了一次，写下更新的断路器。
        memory_server.gates._maint_state[name][
            "review_output_exhaustion_min_context_tokens"
        ] = 400
        return len(rows) * 100

    # 8 行 → 800 tokens，与**锁外观测到的** 1000 相比像是「context 已缩短、可以恢复」。
    await _drive_review_gate(
        memory_server,
        name,
        _history(8),
        token_count=count_while_a_concurrent_writer_arms_a_newer_breaker,
    )

    assert name not in memory_server.correction_tasks, "恢复判定过期时不许 spawn"
    state = memory_server.gates._maint_state[name]
    assert state["review_output_exhaustion_min_context_tokens"] == 400, (
        "并发写者刚 armed 的断路器必须留着"
    )
    assert state["review_output_exhaustion_attempts"] == (
        MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS
    )
    assert state["review_output_exhaustion_blocked"] is True
    memory_server.gates._maint_state.pop(name, None)
