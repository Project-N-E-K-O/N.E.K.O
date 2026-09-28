# -*- coding: utf-8 -*-
"""Vision-slot readiness and same-id endpoint switch regressions."""
from __future__ import annotations

import pytest

from main_logic.proactive_chat.generation import ProactiveModelConfig


def _proactive_config(**vision) -> ProactiveModelConfig:
    return ProactiveModelConfig(
        conversation_model="qwen2.5:7b",
        conversation_base_url="http://127.0.0.1:11434/v1",
        conversation_api_key="ollama",
        conversation_provider_type=None,
        **vision,
    )


def test_custom_vision_endpoint_without_key_counts_as_configured() -> None:
    cfg = _proactive_config(
        vision_model="llava",
        vision_base_url="http://127.0.0.1:11434/v1",
        vision_api_key="",
        vision_is_custom=True,
    )
    assert cfg.has_vision_model is True


def test_non_custom_vision_without_key_is_still_unconfigured() -> None:
    cfg = _proactive_config(
        vision_model="qwen-vl-max",
        vision_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        vision_api_key="",
        vision_is_custom=False,
    )
    assert cfg.has_vision_model is False


def test_vision_without_model_is_unconfigured_even_if_custom() -> None:
    cfg = _proactive_config(
        vision_model="",
        vision_base_url="http://127.0.0.1:11434/v1",
        vision_api_key="",
        vision_is_custom=True,
    )
    assert cfg.has_vision_model is False


class _FakeLLM:
    max_completion_tokens = 100

    async def aclose(self):
        return None


def _bare_client(**attrs):
    from main_logic.omni_offline_client import OmniOfflineClient

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._model_switch_lock = None
    client.max_response_length = 300
    client._genai_client = None
    client._use_genai_sdk = False
    client._genai_tools_unsupported = False
    client._openai_tools_unsupported = False
    client.provider_type = None
    client.vision_provider_type = None
    client.llm = _FakeLLM()
    for key, value in attrs.items():
        setattr(client, key, value)
    return client


@pytest.fixture
def recorded_creates(monkeypatch):
    import main_logic.omni_offline_client._streaming as streaming_mod

    created = []

    async def fake_create(model, base_url, api_key, **kwargs):
        created.append({"model": model, "base_url": base_url, "api_key": api_key})
        return _FakeLLM()

    monkeypatch.setattr(streaming_mod, "create_chat_llm_async", fake_create)
    return created


@pytest.mark.asyncio
async def test_same_model_id_on_another_vision_endpoint_still_switches(recorded_creates) -> None:
    client = _bare_client(
        model="shared-multimodal",
        base_url="http://chat.local/v1",
        api_key="chat-key",
        vision_model="shared-multimodal",
        vision_base_url="http://vision.local/v1",
        vision_api_key="vision-key",
    )

    await client.switch_model("shared-multimodal", use_vision_config=True)

    assert recorded_creates == [
        {"model": "shared-multimodal", "base_url": "http://vision.local/v1", "api_key": "vision-key"}
    ]
    assert client.base_url == "http://vision.local/v1"
    assert client.api_key == "vision-key"


@pytest.mark.asyncio
async def test_keyless_vision_endpoint_does_not_borrow_the_conversation_key(recorded_creates) -> None:
    client = _bare_client(
        model="shared-multimodal",
        base_url="https://paid.example/v1",
        api_key="paid-conversation-key",
        vision_model="shared-multimodal",
        vision_base_url="http://192.168.1.20:11434/v1",
        vision_api_key=None,
    )

    await client.switch_model("shared-multimodal", use_vision_config=True)

    assert recorded_creates[0]["base_url"] == "http://192.168.1.20:11434/v1"
    assert recorded_creates[0]["api_key"] is None


@pytest.mark.asyncio
async def test_same_model_id_and_same_endpoint_is_a_noop(recorded_creates) -> None:
    client = _bare_client(
        model="gpt-4o",
        base_url="https://api.example/v1",
        api_key="key",
        vision_model="gpt-4o",
        vision_base_url="https://api.example/v1",
        vision_api_key="key",
    )
    original_llm = client.llm

    await client.switch_model("gpt-4o", use_vision_config=True)

    assert recorded_creates == []
    assert client.llm is original_llm


@pytest.mark.asyncio
async def test_tool_image_route_switches_same_id_on_another_endpoint(recorded_creates) -> None:
    client = _bare_client(
        model="shared-multimodal",
        base_url="http://chat.local/v1",
        api_key="chat-key",
        vision_model="shared-multimodal",
        vision_base_url="http://vision.local/v1",
        vision_api_key="vision-key",
    )

    assert await client.prepare_for_tool_images() is True
    assert [c["base_url"] for c in recorded_creates] == ["http://vision.local/v1"]


@pytest.mark.asyncio
async def test_tool_image_route_same_id_same_endpoint_needs_no_switch(recorded_creates) -> None:
    client = _bare_client(
        model="gpt-4o",
        base_url="https://api.example/v1",
        api_key="key",
        vision_model="gpt-4o",
        vision_base_url="https://api.example/v1",
        vision_api_key="key",
    )

    assert await client.prepare_for_tool_images() is True
    assert recorded_creates == []
