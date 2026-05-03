import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from config.prompts_galgame import get_galgame_fallback_options
from main_routers import galgame_router


class FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class FakeConfigManager:
    def __init__(self, summary_config):
        self._summary_config = summary_config
        self.calls = []

    async def aget_character_data(self):
        return "主人", "猫娘", None, None

    def get_model_api_config(self, model_type):
        self.calls.append(model_type)
        if model_type == "summary":
            return self._summary_config
        raise AssertionError(f"Unexpected model type: {model_type}")


def _decode_response(response):
    return json.loads(response.body.decode("utf-8"))


def _option_texts(data):
    return [item["text"] for item in data["options"]]


def _expected_llm_kwargs():
    return {
        "max_completion_tokens": galgame_router.GALGAME_OPTION_MAX_TOKENS,
        "timeout": galgame_router.GALGAME_OPTION_TIMEOUT_SECONDS,
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_uses_summary_model_without_temperature(monkeypatch):
    captured = {}
    config_manager = FakeConfigManager(
        {
            "model": "local-summary",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "",
        }
    )

    class FakeLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def ainvoke(self, messages):
            captured["messages"] = messages
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "options": [
                            {"label": "A", "text": "先确认你刚才说的重点。"},
                            {"label": "B", "text": "我在这里陪你慢慢说。"},
                            {"label": "C", "text": "那就把它变成月亮地图吧。"},
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    def fake_create_chat_llm(model, base_url, api_key, **kwargs):
        captured["model"] = model
        captured["base_url"] = base_url
        captured["api_key"] = api_key
        captured["kwargs"] = kwargs
        return FakeLLM()

    monkeypatch.setattr(
        galgame_router,
        "get_config_manager",
        lambda: config_manager,
    )
    monkeypatch.setattr(galgame_router, "create_chat_llm", fake_create_chat_llm)

    response = await galgame_router.generate_galgame_options(
        FakeRequest(
            {
                "messages": [{"role": "assistant", "text": "刚才那件事你怎么看？"}],
                "language": "zh-CN",
            }
        )
    )

    data = _decode_response(response)
    assert data["success"] is True
    assert "fallback" not in data
    assert data["options"][0]["text"] == "先确认你刚才说的重点。"
    assert captured["model"] == "local-summary"
    assert captured["base_url"] == "http://127.0.0.1:11434/v1"
    assert captured["api_key"] == ""
    assert captured["kwargs"] == _expected_llm_kwargs()
    assert config_manager.calls == ["summary"]
    assert "刚才那件事你怎么看？" in captured["messages"][1].content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_option_generation_timeout_returns_fallback(monkeypatch):
    config_manager = FakeConfigManager(
        {
            "model": "local-summary",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "",
        }
    )
    captured = {}

    class SlowLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            captured["exit_exc_type"] = exc_type
            return None

        async def ainvoke(self, messages):
            await asyncio.sleep(1)
            return SimpleNamespace(content="[]")

    def fake_create_chat_llm(model, base_url, api_key, **kwargs):
        captured["kwargs"] = kwargs
        return SlowLLM()

    monkeypatch.setattr(galgame_router, "GALGAME_OPTION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(galgame_router, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr(galgame_router, "create_chat_llm", fake_create_chat_llm)

    response = await galgame_router.generate_galgame_options(
        FakeRequest(
            {
                "messages": [{"role": "assistant", "text": "What do you think?"}],
                "language": "en",
            }
        )
    )

    data = _decode_response(response)
    assert data["success"] is True
    assert data["fallback"] is True
    assert data["error"] == "timeout"
    assert _option_texts(data) == list(get_galgame_fallback_options("en"))
    assert captured["kwargs"] == {
        "max_completion_tokens": galgame_router.GALGAME_OPTION_MAX_TOKENS,
        "timeout": 0.01,
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_missing_model_base_url_returns_fallback(monkeypatch):
    monkeypatch.setattr(
        galgame_router,
        "get_config_manager",
        lambda: FakeConfigManager({"model": "local-summary", "base_url": "", "api_key": ""}),
    )
    monkeypatch.setattr(
        galgame_router,
        "create_chat_llm",
        lambda *args, **kwargs: pytest.fail("LLM should not be created without a base_url"),
    )

    response = await galgame_router.generate_galgame_options(
        FakeRequest(
            {
                "messages": [{"role": "assistant", "text": "刚才那件事你怎么看？"}],
                "language": "zh-CN",
            }
        )
    )

    data = _decode_response(response)
    assert data["success"] is True
    assert data["fallback"] is True
    assert "error" not in data
    assert [item["text"] for item in data["options"]] == list(get_galgame_fallback_options("zh"))
