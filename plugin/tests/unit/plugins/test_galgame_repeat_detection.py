from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugin.plugins.galgame_plugin.llm_gateway import LLMGateway, _response_similarity


class _Backend:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke(self, *, operation: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((operation, context))
        if self.responses:
            return self.responses.pop(0)
        if operation == "summarize_scene":
            return {"summary": "ok", "key_points": []}
        return {"reply": "fallback"}

    async def shutdown(self) -> None:
        return None


def _config(**overrides: Any) -> SimpleNamespace:
    values = {
        "llm_target_entry_ref": "",
        "llm_call_timeout_seconds": 1.0,
        "llm_max_in_flight": 2,
        "llm_request_cache_ttl_seconds": 0.0,
        "llm_scene_summary_cache_ttl_seconds": 0.0,
        "context_metrics_enabled": False,
        "llm_repeat_detection_enabled": True,
        "llm_repeat_similarity_threshold": 0.85,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_repeat_guard_retries_identical_agent_reply_once() -> None:
    backend = _Backend(
        [
            {"reply": "The scene is quiet."},
            {"reply": "The scene is quiet."},
            {"reply": "The current line adds new tension."},
        ]
    )
    gateway = LLMGateway(None, None, _config(), backend=backend)

    first = await gateway.agent_reply({"prompt": "status", "public_context": {}})
    second = await gateway.agent_reply({"prompt": "status", "public_context": {}})

    assert first["reply"] == "The scene is quiet."
    assert second["reply"] == "The current line adds new tension."
    assert len(backend.calls) == 3
    assert "_anti_repeat_instruction" in backend.calls[2][1]


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_repeat_guard_does_not_retry_different_response() -> None:
    backend = _Backend(
        [
            {"reply": "The scene is quiet."},
            {"reply": "A new choice has appeared."},
        ]
    )
    gateway = LLMGateway(None, None, _config(), backend=backend)

    await gateway.agent_reply({"prompt": "status", "public_context": {}})
    result = await gateway.agent_reply({"prompt": "status", "public_context": {}})

    assert result["reply"] == "A new choice has appeared."
    assert len(backend.calls) == 2


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_repeat_guard_retry_failure_returns_original_response() -> None:
    backend = _Backend(
        [
            {"reply": "The scene is quiet."},
            {"reply": "The scene is quiet."},
            {"reply": ""},
        ]
    )
    gateway = LLMGateway(None, None, _config(), backend=backend)

    await gateway.agent_reply({"prompt": "status", "public_context": {}})
    result = await gateway.agent_reply({"prompt": "status", "public_context": {}})

    assert result["degraded"] is False
    assert result["reply"] == "The scene is quiet."
    assert len(backend.calls) == 3


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_repeat_guard_similar_retry_does_not_loop() -> None:
    backend = _Backend(
        [
            {"reply": "The scene is quiet."},
            {"reply": "The scene is quiet."},
            {"reply": "The scene is quiet."},
        ]
    )
    gateway = LLMGateway(None, None, _config(), backend=backend)

    await gateway.agent_reply({"prompt": "status", "public_context": {}})
    result = await gateway.agent_reply({"prompt": "status", "public_context": {}})

    assert result["reply"] == "The scene is quiet."
    assert len(backend.calls) == 3


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_repeat_guard_ignores_non_agent_operations() -> None:
    backend = _Backend(
        [
            {"summary": "same", "key_points": []},
            {"summary": "same", "key_points": []},
        ]
    )
    gateway = LLMGateway(None, None, _config(), backend=backend)

    await gateway.summarize_scene({"scene_id": "scene-a"})
    await gateway.summarize_scene({"scene_id": "scene-a"})

    assert len(backend.calls) == 2
    assert all("_anti_repeat_instruction" not in context for _, context in backend.calls)


def test_response_similarity_detects_highly_similar_replies() -> None:
    assert _response_similarity({"reply": "The scene is quiet."}, {"reply": "The scene is quiet"}) > 0.85
