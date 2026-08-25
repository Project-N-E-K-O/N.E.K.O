from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from plugin.plugins.study_companion.constants import LLM_OPERATION_CONCEPT_EXPLAIN
from plugin.plugins.study_companion.qwen_compatible_transport import (
    QwenCompatibleResult,
    QwenCompatibleTransport,
)
from plugin.plugins.study_companion.qwen_native_client import QwenNativeClient


pytestmark = pytest.mark.unit


class _Logger:
    def __init__(self) -> None:
        self.infos: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.warnings: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def info(self, *args: object, **kwargs: object) -> None:
        self.infos.append((args, kwargs))

    def warning(self, *args: object, **kwargs: object) -> None:
        self.warnings.append((args, kwargs))


@pytest.mark.asyncio
async def test_compatible_request_disables_thinking_and_parses_token_details() -> None:
    seen_body: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_body.update(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"x-request-id": "req-token-details"},
            json={
                "model": "qwen3.7-plus",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": "safe answer",
                            "reasoning_content": "must not be retained",
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 21,
                    "completion_tokens": 1800,
                    "completion_tokens_details": {
                        "reasoning_tokens": 1500,
                        "text_tokens": 300,
                    },
                },
            },
        )

    result = await QwenCompatibleTransport(
        transport=httpx.MockTransport(handler)
    ).chat_completions(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="secret",
        model="qwen3.7-plus",
        messages=[{"role": "user", "content": "private prompt"}],
        max_tokens=1200,
        timeout_seconds=10,
    )

    assert seen_body["enable_thinking"] is False
    assert result.output_tokens == 1800
    assert result.reasoning_tokens == 1500
    assert result.text_tokens == 300
    assert result.finish_reason == "stop"
    assert result.termination_unknown is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("finish_reason", "output_limit_reached", "termination_unknown"),
    [
        ("length", True, False),
        ("stop", False, False),
        ("content_filter", False, True),
        ("", False, True),
    ],
)
async def test_completion_termination_uses_finish_reason_only(
    monkeypatch: pytest.MonkeyPatch,
    finish_reason: str,
    output_limit_reached: bool,
    termination_unknown: bool,
) -> None:
    from plugin.plugins.study_companion import qwen_native_client

    class _ConfigManager:
        def get_model_api_config(self, group: str) -> dict[str, str]:
            assert group == "agent"
            return {
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": "qwen3.7-plus",
                "api_key": "agent-key",
            }

    class _ConfigModule:
        @staticmethod
        def get_config_manager() -> _ConfigManager:
            return _ConfigManager()

    class _CompatibleTransport:
        async def chat_completions(self, **_kwargs: Any) -> QwenCompatibleResult:
            return QwenCompatibleResult(
                text="private answer",
                model="qwen3.7-plus",
                request_id="req-termination",
                input_tokens=100,
                output_tokens=3072,
                finish_reason=finish_reason,
                reasoning_tokens=2500,
                text_tokens=572,
                termination_unknown=termination_unknown,
            )

    async def _discard_usage(_client: object, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(qwen_native_client, "_config_manager_module", _ConfigModule())
    monkeypatch.setattr(
        qwen_native_client,
        "dashscope_http_url_from_base",
        lambda _base, _path: "https://dashscope.aliyuncs.com",
    )
    monkeypatch.setattr(QwenNativeClient, "_record_usage", _discard_usage)
    logger = _Logger()
    client = QwenNativeClient(logger=logger)
    client._compatible_transport = _CompatibleTransport()  # type: ignore[assignment]

    result = await client.call(
        [{"role": "user", "content": "private prompt"}],
        operation=LLM_OPERATION_CONCEPT_EXPLAIN,
        deadline=time.monotonic() + 10,
    )

    assert result.output_limit_reached is output_limit_reached
    assert result.termination_unknown is termination_unknown
    assert result.reasoning_tokens == 2500
    assert result.text_tokens == 572

    diagnostics = repr(logger.infos)
    assert "operation" in diagnostics
    assert "transport" in diagnostics
    assert "thinking" in diagnostics
    assert "elapsed_ms" in diagnostics
    assert "timeout_seconds" in diagnostics
    assert "req-termination" in diagnostics
    assert "prompt_tokens" in diagnostics
    assert "completion_tokens" in diagnostics
    assert "reasoning_tokens" in diagnostics
    assert "text_tokens" in diagnostics
    assert "private prompt" not in diagnostics
    assert "private answer" not in diagnostics


@pytest.mark.asyncio
async def test_native_token_details_are_preserved_without_retaining_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import qwen_native_client

    class _ConfigManager:
        def get_model_api_config(self, group: str) -> dict[str, str]:
            assert group == "agent"
            return {
                "base_url": "https://dashscope.aliyuncs.com/api/v1",
                "model": "qwen-plus",
                "api_key": "agent-key",
            }

    class _ConfigModule:
        @staticmethod
        def get_config_manager() -> _ConfigManager:
            return _ConfigManager()

    class _Generation:
        @staticmethod
        async def call(**kwargs: Any) -> SimpleNamespace:
            assert kwargs["enable_thinking"] is False
            return SimpleNamespace(
                status_code=200,
                request_id="req-native-details",
                output=SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            finish_reason="stop",
                            message=SimpleNamespace(content="native answer"),
                        )
                    ]
                ),
                usage=SimpleNamespace(
                    input_tokens=12,
                    output_tokens=27,
                    output_tokens_details=SimpleNamespace(
                        reasoning_tokens=20,
                        text_tokens=7,
                    ),
                ),
            )

    async def _discard_usage(_client: object, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(qwen_native_client, "_config_manager_module", _ConfigModule())
    monkeypatch.setattr(
        qwen_native_client,
        "dashscope_http_url_from_base",
        lambda _base, _path: "https://dashscope.aliyuncs.com",
    )
    monkeypatch.setattr(qwen_native_client, "AioGeneration", _Generation)
    monkeypatch.setattr(QwenNativeClient, "_record_usage", _discard_usage)
    logger = _Logger()

    result = await QwenNativeClient(logger=logger).call(
        [{"role": "user", "content": "native private prompt"}],
        operation=LLM_OPERATION_CONCEPT_EXPLAIN,
        deadline=time.monotonic() + 10,
    )

    assert result.reasoning_tokens == 20
    assert result.text_tokens == 7
    assert result.termination_unknown is False
    assert "native private prompt" not in repr(logger.infos)
    assert "native answer" not in repr(logger.infos)
