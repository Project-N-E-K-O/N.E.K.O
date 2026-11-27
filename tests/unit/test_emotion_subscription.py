import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,key,expected",
    [
        ("chatgpt", "", True),
        ("openai_compatible", "", False),
        ("openai_compatible", "test-key", True),
    ],
)
async def test_emotion_auth_uses_subscription_without_api_key(
    monkeypatch, provider, key, expected
):
    module = importlib.import_module("main_routers.system_router.emotion")
    monkeypatch.setattr(
        module, "_validate_local_mutation_request", lambda request: None
    )
    config = SimpleNamespace(
        aget_model_api_config=AsyncMock(
            return_value={
                "provider_type": provider,
                "api_key": key,
                "model": "configured-model",
                "base_url": "https://provider.invalid",
            }
        )
    )
    monkeypatch.setattr(module, "get_config_manager", lambda: config)
    monkeypatch.setattr(module, "_resolve_emotion_prompt_language", lambda *args: "en")
    updates = []
    monkeypatch.setattr(
        module, "_push_emotion_update", lambda *args: updates.append(args)
    )
    monkeypatch.setattr(
        module,
        "_emotion_response",
        lambda emotion, confidence, *args: {
            "emotion": emotion,
            "confidence": confidence,
        },
    )
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def ainvoke(self, messages):
            return SimpleNamespace(content='{"emotion":"sad","confidence":0.9}')

    async def factory(*args, **kwargs):
        assert kwargs.get("temperature") is None
        assert kwargs["timeout"] > 0 and kwargs["max_completion_tokens"] > 0
        calls.append((args, kwargs))
        return Client()

    monkeypatch.setattr(module, "create_chat_llm_async", factory)
    request = SimpleNamespace(
        json=AsyncMock(
            return_value={"text": "I feel sad and miss you.", "lanlan_name": "test"}
        )
    )
    result = await module.emotion_analysis(request)
    assert bool(calls) == expected
    if expected:
        assert result["emotion"] == "sad"
        assert calls[0][1]["provider_type"] == provider
        assert updates[-1][1] == "sad"
    else:
        assert "error" in result
