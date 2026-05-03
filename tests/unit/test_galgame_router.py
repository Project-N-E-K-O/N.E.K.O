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
    def __init__(self, summary_config, agent_config=None, quota_ok=True):
        self._summary_config = summary_config
        self._agent_config = agent_config
        self._quota_ok = quota_ok
        self.calls = []
        self.quota_calls = []

    async def aget_character_data(self):
        return "主人", "猫娘", None, None

    def get_model_api_config(self, model_type):
        self.calls.append(model_type)
        if model_type == "summary":
            return self._summary_config
        if model_type == "agent" and self._agent_config is not None:
            return self._agent_config
        raise AssertionError(f"Unexpected model type: {model_type}")

    async def aconsume_agent_daily_quota(self, source="", units=1):
        self.quota_calls.append((source, units))
        if self._quota_ok:
            return True, {"limited": True, "used": 1, "limit": 300, "remaining": 299}
        return False, {"limited": True, "used": 300, "limit": 300, "remaining": 0}


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
    assert captured["api_key"] == ""
    assert captured["kwargs"] == _expected_llm_kwargs()
    assert config_manager.calls == ["summary"]
    assert "刚才那件事你怎么看？" in captured["messages"][1].content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_free_summary_uses_agent_model_without_temperature(monkeypatch):
    captured = {}
    config_manager = FakeConfigManager(
        {
            "model": "free-model",
            "base_url": "https://www.lanlan.tech/text/v1",
            "api_key": "free-access",
        },
        {
            "model": "free-agent-model",
            "base_url": "https://www.lanlan.app/text/v1",
            "api_key": "free-access",
        },
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
                            {"label": "A", "text": "Tell me the key point first."},
                            {"label": "B", "text": "I am here with you."},
                            {"label": "C", "text": "Let's turn it into a moon map."},
                        ]
                    }
                )
            )

    def fake_create_chat_llm(model, base_url, api_key, **kwargs):
        captured["model"] = model
        captured["base_url"] = base_url
        captured["api_key"] = api_key
        captured["kwargs"] = kwargs
        return FakeLLM()

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
    assert "fallback" not in data
    assert data["options"][0]["text"] == "Tell me the key point first."
    assert captured["model"] == "free-agent-model"
    assert captured["base_url"] == "https://www.lanlan.app/text/v1"
    assert captured["api_key"] == "free-access"
    assert captured["kwargs"] == _expected_llm_kwargs()
    assert config_manager.calls == ["summary", "agent"]
    assert config_manager.quota_calls == [("galgame.options", 1)]


@pytest.mark.unit
def test_paid_lanlan_summary_config_is_not_rewritten_or_rerouted():
    config_manager = FakeConfigManager(
        {
            "model": "paid-summary-model",
            "base_url": "https://www.lanlan.tech/text/v1",
            "api_key": "sk-paid",
        },
        {
            "model": "free-agent-model",
            "base_url": "https://www.lanlan.app/text/v1",
            "api_key": "free-access",
        },
    )

    resolution = galgame_router._resolve_galgame_llm_config(config_manager)

    assert resolution.source == "summary"
    assert resolution.requires_agent_config is False
    assert resolution.uses_free_agent_quota is False
    assert resolution.config["model"] == "paid-summary-model"
    assert resolution.config["base_url"] == "https://www.lanlan.tech/text/v1"
    assert config_manager.calls == ["summary"]


@pytest.mark.unit
def test_custom_lanlan_free_access_summary_is_not_rerouted():
    config_manager = FakeConfigManager(
        {
            "model": "custom-summary",
            "base_url": "https://www.lanlan.tech/text/v1",
            "api_key": "free-access",
            "is_custom": True,
        },
        {
            "model": "free-agent-model",
            "base_url": "https://www.lanlan.app/text/v1",
            "api_key": "free-access",
        },
    )

    resolution = galgame_router._resolve_galgame_llm_config(config_manager)

    assert resolution.source == "summary"
    assert resolution.requires_agent_config is False
    assert resolution.uses_free_agent_quota is False
    assert resolution.config["model"] == "custom-summary"
    assert config_manager.calls == ["summary"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_free_summary_with_paid_agent_skips_free_quota(monkeypatch):
    captured = {}
    config_manager = FakeConfigManager(
        {
            "model": "free-model",
            "base_url": "https://www.lanlan.tech/text/v1",
            "api_key": "free-access",
        },
        {
            "model": "paid-agent-model",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-paid-agent",
            "is_custom": True,
        },
        quota_ok=False,
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
                            {"label": "A", "text": "Let's be practical."},
                            {"label": "B", "text": "I will stay with you."},
                            {"label": "C", "text": "Let's chase the comet."},
                        ]
                    }
                )
            )

    def fake_create_chat_llm(model, base_url, api_key, **kwargs):
        captured["model"] = model
        captured["base_url"] = base_url
        captured["api_key"] = api_key
        captured["kwargs"] = kwargs
        return FakeLLM()

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
    assert "fallback" not in data
    assert captured["model"] == "paid-agent-model"
    assert captured["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert captured["api_key"] == "sk-paid-agent"
    assert captured["kwargs"] == _expected_llm_kwargs()
    assert config_manager.calls == ["summary", "agent"]
    assert config_manager.quota_calls == []


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
async def test_galgame_free_summary_with_invalid_agent_returns_fallback(monkeypatch):
    config_manager = FakeConfigManager(
        {
            "model": "free-model",
            "base_url": "https://www.lanlan.tech/text/v1",
            "api_key": "free-access",
        },
        {
            "model": "free-agent-model",
            "base_url": "",
            "api_key": "free-access",
        },
    )
    monkeypatch.setattr(galgame_router, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr(
        galgame_router,
        "create_chat_llm",
        lambda *args, **kwargs: pytest.fail("LLM should not be created when free agent config is invalid"),
    )

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
    assert data["error"] == "AGENT_MODEL_NOT_CONFIGURED"
    assert "options" in data
    assert _option_texts(data) == list(get_galgame_fallback_options("en"))
    assert config_manager.calls == ["summary", "agent"]
    assert config_manager.quota_calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_free_agent_quota_exceeded_returns_fallback(monkeypatch):
    config_manager = FakeConfigManager(
        {
            "model": "free-model",
            "base_url": "https://www.lanlan.tech/text/v1",
            "api_key": "free-access",
        },
        {
            "model": "free-agent-model",
            "base_url": "https://www.lanlan.app/text/v1",
            "api_key": "free-access",
        },
        quota_ok=False,
    )
    monkeypatch.setattr(galgame_router, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr(
        galgame_router,
        "create_chat_llm",
        lambda *args, **kwargs: pytest.fail("LLM should not be created after quota exhaustion"),
    )

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
    assert data["error"] == "AGENT_QUOTA_EXCEEDED"
    assert "options" in data
    assert _option_texts(data) == list(get_galgame_fallback_options("en"))
    assert data["quota"]["limited"] is True
    assert data["quota"]["remaining"] == 0
    assert config_manager.quota_calls == [("galgame.options", 1)]


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
    assert [item["text"] for item in data["options"]] == [
        "我有点没听清，可以再说一次吗？",
        "嗯嗯，我都在听，慢慢说就好。",
        "如果我们现在掉进童话书里会怎样？",
    ]
