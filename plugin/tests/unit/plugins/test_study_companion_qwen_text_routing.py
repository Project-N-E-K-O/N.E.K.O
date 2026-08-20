from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from plugin.plugins.study_companion.qwen_compatible_transport import (
    QwenCompatibleResult,
)
from plugin.plugins.study_companion.qwen_native_client import QwenNativeClient


pytestmark = pytest.mark.unit


class _Logger:
    def warning(self, *_args: Any, **_kwargs: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_compatible_agent_config_never_calls_native_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import qwen_native_client
    from utils import config_manager

    class _ConfigManager:
        def get_model_api_config(self, group: str) -> dict[str, str]:
            assert group == "agent"
            return {
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": "qwen3.7-plus-2026-05-26",
                "api_key": "secret",
            }

    class _ForbiddenGeneration:
        @staticmethod
        async def call(**_kwargs: Any) -> SimpleNamespace:
            raise AssertionError("compatible configuration must not call AioGeneration")

    seen: dict[str, Any] = {}

    class _CompatibleTransport:
        async def chat_completions(self, **kwargs: Any) -> QwenCompatibleResult:
            seen.update(kwargs)
            return QwenCompatibleResult(
                text="OK",
                model="qwen3.7-plus-2026-05-26",
                request_id="req-compatible",
                input_tokens=7,
                output_tokens=1,
            )

    async def _discard_usage(_client: object, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(qwen_native_client, "AioGeneration", _ForbiddenGeneration)
    monkeypatch.setattr(QwenNativeClient, "_record_usage", _discard_usage)
    client = QwenNativeClient(logger=_Logger())
    client._compatible_transport = _CompatibleTransport()  # type: ignore[assignment]

    result = await client.call(
        [{"role": "user", "content": "hello"}],
        operation="document_chunk_analyze",
        deadline=time.monotonic() + 10,
    )

    assert result.text == "OK"
    assert result.request_id == "req-compatible"
    assert (result.input_tokens, result.output_tokens) == (7, 1)
    assert seen["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert seen["max_tokens"] == 1200
    assert seen["messages"] == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_native_agent_config_never_calls_compatible_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import qwen_native_client
    from utils import config_manager

    class _ConfigManager:
        def get_model_api_config(self, group: str) -> dict[str, str]:
            assert group == "agent"
            return {
                "base_url": "https://dashscope.aliyuncs.com/api/v1",
                "model": "qwen-plus",
                "api_key": "secret",
            }

    native_calls: list[dict[str, Any]] = []

    class _Generation:
        @staticmethod
        async def call(**kwargs: Any) -> SimpleNamespace:
            native_calls.append(dict(kwargs))
            return SimpleNamespace(
                status_code=200,
                request_id="req-native",
                usage=SimpleNamespace(input_tokens=2, output_tokens=1),
                output=SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
                ),
            )

    class _ForbiddenCompatibleTransport:
        async def chat_completions(self, **_kwargs: Any) -> QwenCompatibleResult:
            raise AssertionError("native configuration must not call compatible transport")

    async def _discard_usage(_client: object, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(qwen_native_client, "AioGeneration", _Generation)
    monkeypatch.setattr(QwenNativeClient, "_record_usage", _discard_usage)
    client = QwenNativeClient(logger=_Logger())
    client._compatible_transport = _ForbiddenCompatibleTransport()  # type: ignore[assignment]

    result = await client.call(
        [{"role": "user", "content": "hello"}],
        operation="concept_explain",
        deadline=time.monotonic() + 10,
    )

    assert result.text == "OK"
    assert native_calls[0]["base_address"] == "https://dashscope.aliyuncs.com/api/v1"
    assert native_calls[0]["max_tokens"] == 3072


@pytest.mark.asyncio
async def test_non_dashscope_text_endpoint_is_rejected_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion.qwen_native_client import QwenNativeError
    from utils import config_manager

    class _ConfigManager:
        def get_model_api_config(self, group: str) -> dict[str, str]:
            assert group == "agent"
            return {
                "base_url": "https://example.test/compatible-mode/v1",
                "model": "qwen-plus",
                "api_key": "secret",
            }

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())

    with pytest.raises(QwenNativeError) as raised:
        await QwenNativeClient(logger=_Logger()).call(
            [{"role": "user", "content": "hello"}],
            operation="concept_explain",
            deadline=time.monotonic() + 10,
        )

    assert raised.value.diagnostic == "invalid_endpoint"
