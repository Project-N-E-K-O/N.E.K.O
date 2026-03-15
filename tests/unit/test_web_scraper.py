import os
import sys

import pytest
from utils.llm_client import AIMessage, HumanMessage, SystemMessage


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

import utils.config_manager as config_manager_module
import utils.web_scraper as web_scraper


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

        async def ainvoke(self, messages):
            captured["messages"] = messages
            return AIMessage(content="关键词A\n关键词B\n关键词C")

    monkeypatch.setattr(config_manager_module, "ConfigManager", FakeConfigManager)
    monkeypatch.setattr(web_scraper, "ChatOpenAI", FakeLLM)
    monkeypatch.setattr(web_scraper, "is_china_region", lambda: True)
    monkeypatch.setattr(web_scraper, "get_extra_body", lambda model: {"thinking": False})

    result = await web_scraper.generate_diverse_queries("Project N.E.K.O.")

    assert result == ["关键词A", "关键词B", "关键词C"]
    assert captured["kwargs"]["extra_body"] == {"thinking": False}
    assert len(captured["messages"]) == 2
    assert isinstance(captured["messages"][0], SystemMessage)
    assert isinstance(captured["messages"][1], HumanMessage)
    assert "Project N.E.K.O." in captured["messages"][1].content
