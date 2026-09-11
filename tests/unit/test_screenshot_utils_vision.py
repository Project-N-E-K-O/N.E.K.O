from __future__ import annotations

from types import SimpleNamespace

import pytest

from utils import config_manager
from utils import screenshot_utils


pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_custom_vision_endpoint_allows_an_empty_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ConfigManager:
        async def aget_model_api_config(self, model_type: str) -> dict[str, object]:
            assert model_type == "vision"
            return {
                "model": "local-vision",
                "api_key": "",
                "base_url": "http://127.0.0.1:11434/v1",
                "is_custom": True,
                "provider_type": "openai",
            }

    class _VisionClient:
        async def __aenter__(self) -> "_VisionClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def ainvoke(self, _messages: object) -> SimpleNamespace:
            return SimpleNamespace(content="local description")

    create_calls: list[dict[str, object]] = []

    async def _create_chat_llm_async(**kwargs: object) -> _VisionClient:
        create_calls.append(kwargs)
        return _VisionClient()

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(
        screenshot_utils,
        "create_chat_llm_async",
        _create_chat_llm_async,
    )

    result = await screenshot_utils.analyze_image_with_vision_model("encoded-image")

    assert result == "local description"
    assert create_calls[0]["api_key"] == ""


@pytest.mark.asyncio
async def test_builtin_vision_endpoint_still_requires_an_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ConfigManager:
        async def aget_model_api_config(self, model_type: str) -> dict[str, object]:
            assert model_type == "vision"
            return {
                "model": "hosted-vision",
                "api_key": "",
                "base_url": "https://example.invalid/v1",
                "is_custom": False,
                "provider_type": "openai",
            }

    async def _unexpected_create(**_kwargs: object) -> object:
        raise AssertionError("hosted vision without an API key must not create a client")

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(
        screenshot_utils,
        "create_chat_llm_async",
        _unexpected_create,
    )

    assert await screenshot_utils.analyze_image_with_vision_model("encoded-image") is None
