from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugin.plugins.galgame_plugin.llm_backend import (
    _JSON_CORRECTION_BAD_OUTPUT_MAX_CHARS,
    GalgameLLMBackend,
)
from plugin.plugins.galgame_plugin.llm_gateway import LLMGateway


class RecordingBackend(GalgameLLMBackend):
    def __init__(self, responses: list[str]) -> None:
        super().__init__(logger=None)
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def _call_model(
        self,
        *,
        operation: str,
        messages: list[dict[str, str]],
    ) -> str:
        self.calls.append({"operation": operation, "messages": list(messages)})
        if not self._responses:
            raise AssertionError("unexpected extra llm call")
        return self._responses.pop(0)


def _gateway(backend: RecordingBackend) -> LLMGateway:
    config = SimpleNamespace(
        llm_target_entry_ref="",
        llm_call_timeout_seconds=1.0,
        llm_max_in_flight=2,
        llm_request_cache_ttl_seconds=0.0,
    )
    return LLMGateway(plugin=None, logger=None, config=config, backend=backend)


def _summarize_context() -> dict[str, Any]:
    return {
        "scene_id": "scene-a",
        "route_id": "route-1",
        "recent_lines": [
            {
                "speaker": "雪乃",
                "text": "今天一起回家吗？",
                "line_id": "line-1",
                "scene_id": "scene-a",
                "route_id": "route-1",
            }
        ],
        "recent_choices": [],
        "current_snapshot": {},
        "scene_summary_seed": "seed summary",
    }


def _suggest_context() -> dict[str, Any]:
    return {
        "scene_id": "scene-a",
        "route_id": "route-1",
        "visible_choices": [
            {"choice_id": "choice-1", "text": "好啊"},
            {"choice_id": "choice-2", "text": "下次吧"},
        ],
        "recent_lines": [],
        "recent_choices": [],
        "current_snapshot": {},
    }


def _agent_reply_context() -> dict[str, Any]:
    line = {
        "speaker": "雪乃",
        "text": "今天一起回家吗？",
        "line_id": "line-1",
        "scene_id": "scene-a",
        "route_id": "route-1",
    }
    return {
        "scene_id": "scene-a",
        "route_id": "route-1",
        "prompt": "现在发生了什么？",
        "public_context": {
            "latest_line": "今天一起回家吗？",
            "recent_lines": [line],
            "recent_choices": [],
            "current_line": line,
        },
    }


@pytest.mark.asyncio
async def test_json_correction_prompt_bounds_bad_output_and_succeeds() -> None:
    bad_output = (
        "not json "
        + ("x" * (_JSON_CORRECTION_BAD_OUTPUT_MAX_CHARS + 32))
        + "TAIL_SHOULD_NOT_BE_INCLUDED"
    )
    backend = RecordingBackend([bad_output, '{"reply":"ok"}'])

    result = await backend.invoke(
        operation="agent_reply",
        context=_agent_reply_context(),
    )

    assert result == {"reply": "ok"}
    assert len(backend.calls) == 2
    correction_messages = backend.calls[1]["messages"]
    assistant_bad_output = correction_messages[-2]["content"]
    correction_prompt = correction_messages[-1]["content"]
    assert len(assistant_bad_output) < len(bad_output)
    assert "TAIL_SHOULD_NOT_BE_INCLUDED" not in assistant_bad_output
    assert "...[truncated " in assistant_bad_output
    assert "JSON 修正请求 1/1" in correction_prompt
    assert "operation=agent_reply" in correction_prompt


@pytest.mark.parametrize(
    ("operation", "context_factory", "gateway_method", "expected_field"),
    [
        ("summarize_scene", _summarize_context, "summarize_scene", "summary"),
        ("suggest_choice", _suggest_context, "suggest_choice", "choices"),
        ("agent_reply", _agent_reply_context, "agent_reply", "reply"),
    ],
)
@pytest.mark.asyncio
async def test_json_correction_failure_falls_back_after_one_attempt(
    operation: str,
    context_factory,
    gateway_method: str,
    expected_field: str,
) -> None:
    backend = RecordingBackend(["not json", "still not json"])
    gateway = _gateway(backend)

    result = await getattr(gateway, gateway_method)(context_factory())

    assert result["degraded"] is True
    assert expected_field in result
    assert "after 1 correction attempt(s)" in result["diagnostic"]
    assert len(backend.calls) == 2
    assert [call["operation"] for call in backend.calls] == [operation, operation]
