import os
import sys

import pytest
from utils.llm_client import AIMessage, HumanMessage, SystemMessage


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

import utils.config_manager as config_manager_module
import utils.web_scraper as web_scraper
import utils.web_scraper.window_context as window_context


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_diverse_queries_sends_user_message(monkeypatch):
    captured = {}

    class FakeConfigManager:
        def get_model_api_config(self, model_type):
            assert model_type == "summary"
            return {
                "model": "gemini-3-flash-preview",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                "api_key": "test-key",
            }

    class FakeLLM:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def ainvoke(self, messages):
            captured["messages"] = messages
            return AIMessage(content="关键词A\n关键词B\n关键词C")

    def fake_create_chat_llm(*args, **kwargs):
        return FakeLLM(**kwargs)

    monkeypatch.setattr(config_manager_module, "ConfigManager", FakeConfigManager)
    monkeypatch.setattr("utils.llm_client.create_chat_llm", fake_create_chat_llm)
    monkeypatch.setattr(window_context, "is_china_region", lambda: True)

    result = await web_scraper.generate_diverse_queries("Project N.E.K.O.")

    assert result == ["关键词A", "关键词B", "关键词C"]
    assert len(captured["messages"]) == 2
    assert isinstance(captured["messages"][0], SystemMessage)
    assert isinstance(captured["messages"][1], HumanMessage)
    assert "Project N.E.K.O." in captured["messages"][1].content
