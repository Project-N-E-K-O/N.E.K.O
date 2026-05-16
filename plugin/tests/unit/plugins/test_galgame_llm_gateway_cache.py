from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugin.plugins.galgame_plugin.llm_gateway import (
    LLMGateway,
    _hash_line,
    _observed_similarity,
)


class _Backend:
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, *, operation: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if operation == "explain_line":
            return {
                "explanation": f"explain-{self.calls}",
                "evidence": [
                    {
                        "type": "current_line",
                        "text": str(context.get("text") or "line"),
                        "line_id": str(context.get("line_id") or "line-1"),
                        "speaker": str(context.get("speaker") or "A"),
                        "scene_id": str(context.get("scene_id") or "scene-a"),
                        "route_id": "",
                    }
                ],
            }
        if operation == "summarize_scene":
            return {
                "summary": f"summary-{self.calls}",
                "key_points": [
                    {
                        "type": "plot",
                        "text": "plot",
                        "line_id": "line-1",
                        "speaker": "A",
                        "scene_id": "scene-a",
                        "route_id": "",
                    }
                ],
            }
        if operation == "suggest_choice":
            return {
                "choices": [
                    {
                        "choice_id": "choice-1",
                        "text": "left",
                        "rank": 1,
                        "reason": "reason",
                    }
                ]
            }
        return {"reply": f"reply-{self.calls}"}

    async def shutdown(self) -> None:
        return None


def _config(**overrides: Any) -> SimpleNamespace:
    values = {
        "llm_target_entry_ref": "",
        "llm_call_timeout_seconds": 1.0,
        "llm_max_in_flight": 2,
        "llm_request_cache_ttl_seconds": 2.0,
        "llm_explain_cache_ttl_seconds": 8.0,
        "llm_scene_summary_cache_ttl_seconds": 10.0,
        "llm_choice_cache_ttl_seconds": 4.0,
        "llm_near_match_cache_enabled": False,
        "llm_near_match_cache_ttl_seconds": 15.0,
        "context_metrics_enabled": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _explain_context(**overrides: Any) -> dict[str, Any]:
    context = {
        "game_id": "game-a",
        "session_id": "session-a",
        "scene_id": "scene-a",
        "route_id": "",
        "line_id": "line-1",
        "speaker": "A",
        "text": "same current line",
        "stable_lines": [
            {
                "line_id": "line-1",
                "speaker": "A",
                "text": "same current line",
                "scene_id": "scene-a",
                "route_id": "",
            }
        ],
        "observed_lines": [
            {
                "line_id": "ocr-1",
                "speaker": "A",
                "text": "same current line",
                "scene_id": "scene-a",
            }
        ],
        "recent_lines": [],
        "current_snapshot": {"line_id": "line-1", "text": "same current line"},
    }
    context.update(overrides)
    return context


def test_llm_gateway_ttl_for_operation_uses_phase3_fields() -> None:
    gateway = LLMGateway(
        None,
        None,
        _config(
            llm_explain_cache_ttl_seconds=7,
            llm_scene_summary_cache_ttl_seconds=11,
            llm_choice_cache_ttl_seconds=5,
            llm_request_cache_ttl_seconds=3,
        ),
        backend=_Backend(),
    )

    assert gateway._ttl_for_operation("explain_line") == 7
    assert gateway._ttl_for_operation("summarize_scene") == 11
    assert gateway._ttl_for_operation("suggest_choice") == 5
    assert gateway._ttl_for_operation("agent_reply") == 3


def test_repeat_config_fingerprint_preserves_zero_threshold() -> None:
    zero_gateway = LLMGateway(
        None,
        None,
        _config(llm_repeat_detection_enabled=True, llm_repeat_similarity_threshold=0.0),
        backend=_Backend(),
    )
    default_gateway = LLMGateway(
        None,
        None,
        _config(llm_repeat_detection_enabled=True),
        backend=_Backend(),
    )

    assert zero_gateway._repeat_config_fingerprint() != default_gateway._repeat_config_fingerprint()


@pytest.mark.asyncio
async def test_llm_gateway_near_match_reuses_safe_explain_result() -> None:
    backend = _Backend()
    gateway = LLMGateway(
        None,
        None,
        _config(llm_near_match_cache_enabled=True, llm_request_cache_ttl_seconds=0),
        backend=backend,
    )

    first = await gateway.explain_line(_explain_context())
    second = await gateway.explain_line(
        _explain_context(
            current_snapshot={"line_id": "line-1", "text": "ocr jitter"},
            observed_lines=[
                {
                    "line_id": "ocr-1",
                    "speaker": "A",
                    "text": "same current line.",
                    "scene_id": "scene-a",
                }
            ],
        )
    )

    assert first["explanation"] == "explain-1"
    assert second["explanation"] == "explain-1"
    assert backend.calls == 1


@pytest.mark.asyncio
async def test_update_config_clears_near_match_cache_on_context_budget_change() -> None:
    backend = _Backend()
    gateway = LLMGateway(
        None,
        None,
        _config(
            llm_near_match_cache_enabled=True,
            llm_request_cache_ttl_seconds=0,
            context_counting_mode="token",
            context_max_tokens=300,
        ),
        backend=backend,
    )

    try:
        await gateway.explain_line(_explain_context())
        assert len(gateway._near_match_cache) == 1

        gateway.update_config(
            _config(
                llm_near_match_cache_enabled=True,
                llm_request_cache_ttl_seconds=0,
                context_counting_mode="token",
                context_max_tokens=1000,
            )
        )

        assert len(gateway._near_match_cache) == 0
    finally:
        await gateway.shutdown()


@pytest.mark.asyncio
async def test_llm_gateway_near_match_rejects_stable_line_change() -> None:
    backend = _Backend()
    gateway = LLMGateway(
        None,
        None,
        _config(llm_near_match_cache_enabled=True, llm_request_cache_ttl_seconds=0),
        backend=backend,
    )

    await gateway.explain_line(_explain_context())
    result = await gateway.explain_line(
        _explain_context(
            stable_lines=[
                {
                    "line_id": "line-2",
                    "speaker": "A",
                    "text": "different fact",
                    "scene_id": "scene-a",
                    "route_id": "",
                }
            ]
        )
    )

    assert result["explanation"] == "explain-2"
    assert backend.calls == 2


@pytest.mark.asyncio
async def test_llm_gateway_near_match_does_not_apply_to_suggest_choice() -> None:
    backend = _Backend()
    gateway = LLMGateway(
        None,
        None,
        _config(llm_near_match_cache_enabled=True, llm_request_cache_ttl_seconds=0),
        backend=backend,
    )
    context = {
        "visible_choices": [{"choice_id": "choice-1", "text": "left"}],
    }

    await gateway.suggest_choice(context)
    await gateway.suggest_choice({**context, "current_snapshot": {"noise": True}})

    assert backend.calls == 2


def test_near_match_helpers_hash_and_observed_similarity() -> None:
    assert _hash_line({"line_id": "1", "text": "hello"}) == _hash_line(
        {"line_id": "1", "text": "hello"}
    )
    assert _observed_similarity(["same current line"], ["same current line."]) >= 0.85
    assert _observed_similarity(["same current line"], ["different plot"]) < 0.85
