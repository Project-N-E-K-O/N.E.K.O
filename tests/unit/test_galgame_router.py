import asyncio
import json
import logging
import os
import sys
from types import SimpleNamespace

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from config.prompts.prompts_galgame import get_galgame_fallback_options
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


@pytest.mark.parametrize(
    "model_output, expected",
    [
        # Shape A: top-level label-keyed dict
        (
            {"A": "先确认你刚才说的重点。", "B": "我在这里陪你慢慢说。", "C": "那就把它变成月亮地图吧。"},
            ["先确认你刚才说的重点。", "我在这里陪你慢慢说。", "那就把它变成月亮地图吧。"],
        ),
        # Shape B: nested label-keyed dict under "options"
        (
            {"options": {"A": "认真听。", "B": "陪着你。", "C": "幻想一下。"}},
            ["认真听。", "陪着你。", "幻想一下。"],
        ),
    ],
    ids=["top_level_label_map", "nested_label_map"],
)
@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_accepts_dict_shaped_options(model_output, expected, monkeypatch):
    """Some models emit option maps instead of canonical lists. Don't discard them."""
    config_manager = FakeConfigManager(
        {
            "model": "local-summary",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "",
        }
    )

    class MapLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def ainvoke(self, messages):
            return SimpleNamespace(content=json.dumps(model_output, ensure_ascii=False))

    monkeypatch.setattr(galgame_router, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr(galgame_router, "create_chat_llm", lambda *a, **kw: MapLLM())

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
    assert "partial" not in data
    assert _option_texts(data) == expected


@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_partial_options_filled_from_fallback(monkeypatch):
    """Model returned only A and B — C must be filled from fallback, not the whole batch discarded."""
    config_manager = FakeConfigManager(
        {
            "model": "local-summary",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "",
        }
    )

    class PartialLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def ainvoke(self, messages):
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "options": [
                            {"label": "A", "text": "先确认你刚才说的重点。"},
                            {"label": "B", "text": "我在这里陪你慢慢说。"},
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    monkeypatch.setattr(galgame_router, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr(galgame_router, "create_chat_llm", lambda *a, **kw: PartialLLM())

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
    assert data["partial"] is True
    assert data["missing_labels"] == ["C"]
    fb = get_galgame_fallback_options("zh")
    assert _option_texts(data) == ["先确认你刚才说的重点。", "我在这里陪你慢慢说。", fb[2]]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_unparseable_output_returns_fallback(monkeypatch, caplog):
    """Garbage output → full fallback. INFO log must carry metadata only,
    never the raw model text (privacy: the raw output is generated from
    recent chat context)."""
    config_manager = FakeConfigManager(
        {
            "model": "local-summary",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "",
        }
    )

    raw_content = "抱歉，我不太理解你的问题。"

    class GarbageLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def ainvoke(self, messages):
            return SimpleNamespace(content=raw_content)

    monkeypatch.setattr(galgame_router, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr(galgame_router, "create_chat_llm", lambda *a, **kw: GarbageLLM())

    with caplog.at_level(logging.INFO, logger=galgame_router.logger.name):
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
    assert _option_texts(data) == list(get_galgame_fallback_options("zh"))

    # The INFO-level fallback log records parse_error + raw_len, but must NOT
    # leak the raw model output (it can carry conversational PII).
    info_messages = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.INFO and "unparseable" in record.getMessage()
    ]
    assert info_messages, "expected an INFO log entry on unparseable output"
    joined = " ".join(info_messages)
    assert "raw_len=" in joined
    assert "parse_error=" in joined
    assert raw_content not in joined


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
