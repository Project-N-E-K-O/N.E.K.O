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
        patch.object(memory_server.gates, "_asave_maint_state", AsyncMock()),
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


async def _drive_review_gate(memory_server, name: str, history: list) -> None:
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
        patch.object(memory_server.gates, "_asave_maint_state", AsyncMock()),
        patch(
            "memory.recent.review_context_token_count",
            side_effect=lambda rows: len(rows) * 100,
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
        patch.object(memory_server.gates, "_asave_maint_state", AsyncMock()),
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
        patch.object(memory_server.gates, "_asave_maint_state", AsyncMock()),
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
