# -*- coding: utf-8 -*-
"""Vision-slot readiness / same-id endpoint switch regressions."""
from __future__ import annotations

import pytest

from main_logic.proactive_chat.generation import ProactiveModelConfig


def test_has_vision_model_allows_empty_key_when_base_url_set() -> None:
    cfg = ProactiveModelConfig(
        conversation_model="qwen2.5:7b",
        conversation_base_url="http://127.0.0.1:11434/v1",
        conversation_api_key="ollama",
        conversation_provider_type=None,
        vision_model="llava",
        vision_base_url="http://127.0.0.1:11434/v1",
        vision_api_key="",
    )
    assert cfg.has_vision_model is True


def test_has_vision_model_false_without_model_or_endpoint() -> None:
    empty = ProactiveModelConfig(
        conversation_model="m",
        conversation_base_url=None,
        conversation_api_key="k",
        conversation_provider_type=None,
        vision_model="",
        vision_base_url="",
        vision_api_key="",
    )
    assert empty.has_vision_model is False
    model_only = ProactiveModelConfig(
        conversation_model="m",
        conversation_base_url=None,
        conversation_api_key="k",
        conversation_provider_type=None,
        vision_model="llava",
        vision_base_url="",
        vision_api_key="",
    )
    assert model_only.has_vision_model is False


@pytest.mark.asyncio
async def test_switch_model_same_id_still_applies_vision_endpoint(monkeypatch) -> None:
    """Same vision/chat model id with a different URL must still switch."""
    pytest.importorskip("langchain_core")
    from main_logic.omni_offline_client import OmniOfflineClient
    import main_logic.omni_offline_client._streaming as streaming_mod

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._model_switch_lock = None
    client.model = "shared-multimodal"
    client.base_url = "http://chat.local/v1"
    client.api_key = "chat-key"
    client.vision_model = "shared-multimodal"
    client.vision_base_url = "http://vision.local/v1"
    client.vision_api_key = "vision-key"
    client.max_response_length = 300
    client._genai_client = None
    client._use_genai_sdk = False
    client._genai_tools_unsupported = False
    client._openai_tools_unsupported = False
    client.provider_type = None
    client.vision_provider_type = None

    class _FakeLLM:
        max_completion_tokens = 100

        async def aclose(self):
            return None

    client.llm = _FakeLLM()
    created = []

    async def fake_create(model, base_url, api_key, **kwargs):
        created.append({"model": model, "base_url": base_url, "api_key": api_key})
        return _FakeLLM()

    monkeypatch.setattr(streaming_mod, "create_chat_llm_async", fake_create)

    await client.switch_model("shared-multimodal", use_vision_config=True)

    assert created == [
        {
            "model": "shared-multimodal",
            "base_url": "http://vision.local/v1",
            "api_key": "vision-key",
        }
    ]
    assert client.base_url == "http://vision.local/v1"
    assert client.api_key == "vision-key"
