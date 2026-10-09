from __future__ import annotations

import asyncio
from types import SimpleNamespace

import browser_use.llm
import pytest
from anthropic.types import Message
from browser_use.llm.messages import SystemMessage, UserMessage
from pydantic import BaseModel

import brain.browser_use_anthropic as bu_anthropic
from brain.browser_use_adapter import BrowserUseAdapter
from brain.browser_use_anthropic import (
    NekoChatAnthropic,
    anthropic_rejects_forced_tool_choice,
)


class _ConfigManager:
    def __init__(self, config: dict):
        self._config = config

    def get_model_api_config(self, _tier: str) -> dict:
        return dict(self._config)


def _adapter(config: dict) -> BrowserUseAdapter:
    adapter = object.__new__(BrowserUseAdapter)
    adapter._config_manager = _ConfigManager(config)
    return adapter


def test_build_llm_selects_anthropic_for_provider_type(monkeypatch):
    captured = {}

    def fake_anthropic(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(kind="anthropic")

    def fail_openai(**_kwargs):
        raise AssertionError("Anthropic provider must not use ChatOpenAI")

    monkeypatch.setattr(bu_anthropic, "NekoChatAnthropic", fake_anthropic)
    monkeypatch.setattr(browser_use.llm, "ChatOpenAI", fail_openai)

    llm = _adapter({
        "model": "proxy-claude",
        "base_url": "https://anthropic-proxy.example/v1",
        "api_key": "sk-test",
        "provider_type": "anthropic",
    })._build_llm()

    assert llm.kind == "anthropic"
    assert captured == {
        "model": "proxy-claude",
        "api_key": "sk-test",
        "base_url": "https://anthropic-proxy.example/v1",
        "default_headers": None,
        "structured_output": "forced_tool",
    }


def test_build_llm_normalizes_claude_and_adds_kimi_user_agent(monkeypatch):
    calls = []

    def fake_anthropic(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(kind="anthropic")

    monkeypatch.setattr(bu_anthropic, "NekoChatAnthropic", fake_anthropic)

    _adapter({
        "model": "claude-sonnet-4-6",
        "base_url": "https://api.anthropic.com/v1",
        "api_key": "sk-claude",
        "provider_type": "anthropic",
    })._build_llm()
    _adapter({
        "model": "kimi-for-coding",
        "base_url": "https://api.kimi.com/coding",
        "api_key": "sk-kimi",
        "provider_type": "anthropic",
    })._build_llm()

    assert calls[0]["base_url"] == "https://api.anthropic.com"
    assert calls[0]["default_headers"] is None
    assert calls[1]["base_url"] == "https://api.kimi.com/coding"
    assert calls[1]["default_headers"] == {"User-Agent": "claude-code/0.1.0"}


def test_build_llm_keeps_openai_path_and_protocol_in_signature(monkeypatch):
    captured = {}

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(kind="openai")

    monkeypatch.setattr(browser_use.llm, "ChatOpenAI", fake_openai)
    shared_config = {
        "model": "gpt-test",
        "base_url": "https://openai-proxy.example/v1",
        "api_key": "sk-test",
    }
    adapter = _adapter({
        **shared_config,
        "provider_type": "openai_compatible",
    })
    anthropic_adapter = _adapter({
        **shared_config,
        "provider_type": "anthropic",
    })

    assert adapter._build_llm().kind == "openai"
    assert captured["dont_force_structured_output"] is False
    openai_signature = adapter._current_api_signature()
    anthropic_signature = anthropic_adapter._current_api_signature()
    assert openai_signature == (
        "openai_compatible|https://openai-proxy.example/v1|gpt-test"
    )
    assert anthropic_signature == (
        "anthropic|https://openai-proxy.example/v1|gpt-test"
    )
    assert anthropic_signature != openai_signature


def _claude_preset_agent_model() -> str:
    import json
    from pathlib import Path

    providers = json.loads(
        (Path(__file__).resolve().parents[2] / "config" / "api_providers.json")
        .read_text(encoding="utf-8")
    )
    return providers["assist_api_providers"]["claude"]["agent_model"]


def test_build_llm_claude_preset_sends_no_temperature():
    llm = _adapter({
        "model": _claude_preset_agent_model(),
        "base_url": "https://api.anthropic.com/v1",
        "api_key": "sk-claude",
        "provider_type": "anthropic",
    })._build_llm()

    assert isinstance(llm, NekoChatAnthropic)
    assert llm.base_url == "https://api.anthropic.com"
    assert llm.temperature is None
    assert llm.top_p is None
    invoke_params = llm._get_client_params_for_invoke()
    assert "temperature" not in invoke_params
    assert "top_p" not in invoke_params


@pytest.mark.parametrize(
    ("model", "mode", "expected_mode"),
    [
        ("claude-sonnet-5-5", "schema", "auto_tool"),
        ("claude-opus-5-5", "schema", "auto_tool"),
        ("claude-fable-5-1", "schema", "auto_tool"),
        ("claude-sonnet-5", "schema", "forced_tool"),
        ("claude-haiku-4-5-20251001", "schema", "forced_tool"),
        ("kimi-for-coding", "schema", "forced_tool"),
        ("kimi-for-coding", "text", "auto_tool"),
        ("claude-sonnet-5", "text", "auto_tool"),
    ],
)
def test_build_llm_anthropic_structured_output_by_model_and_mode(model, mode, expected_mode):
    llm = _adapter({
        "model": model,
        "base_url": "https://api.anthropic.com/v1",
        "api_key": "sk-claude",
        "provider_type": "anthropic",
    })._build_llm(mode=mode)

    assert isinstance(llm, NekoChatAnthropic)
    assert llm.structured_output == expected_mode
    assert llm.temperature is None


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-sonnet-5-5", True),
        ("claude-opus-5-5", True),
        ("claude-fable-5-1", True),
        ("claude-mythos-5-1", True),
        ("anthropic.claude-opus-5-5", True),
        ("anthropic/claude-sonnet-5.5", True),
        ("claude-opus-6", True),
        ("claude-sonnet-5", False),
        ("claude-opus-5", False),
        ("claude-fable-5", False),
        ("claude-opus-4-8", False),
        ("claude-sonnet-4-20250514", False),
        ("claude-3-5-sonnet-20241022", False),
        ("kimi-for-coding", False),
        ("", False),
        (None, False),
    ],
)
def test_anthropic_rejects_forced_tool_choice(model, expected):
    assert anthropic_rejects_forced_tool_choice(model) is expected


class _Answer(BaseModel):
    value: str


def _message(content: list[dict], stop_reason: str = "end_turn") -> Message:
    return Message.model_validate({
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    })


_THINKING = {"type": "thinking", "thinking": "", "signature": "sig"}


def _llm_with_fake_client(monkeypatch, response: Message, *, mode: str) -> tuple[NekoChatAnthropic, list[dict]]:
    calls: list[dict] = []

    async def create(**kwargs):
        calls.append(kwargs)
        return response

    llm = NekoChatAnthropic(model="claude-sonnet-5-5", api_key="sk-test", structured_output=mode)
    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(llm, "get_client", lambda: client)
    return llm, calls


_MESSAGES = [SystemMessage(content="You are a browser agent."), UserMessage(content="go")]


def test_auto_tool_structured_output_uses_auto_tool_choice(monkeypatch):
    response = _message(
        [_THINKING, {"type": "tool_use", "id": "toolu_1", "name": "_Answer", "input": {"value": "ok"}}],
        stop_reason="tool_use",
    )
    llm, calls = _llm_with_fake_client(monkeypatch, response, mode="auto_tool")

    result = asyncio.run(llm.ainvoke(_MESSAGES, _Answer))

    assert result.completion == _Answer(value="ok")
    (call,) = calls
    assert call["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert [t["name"] for t in call["tools"]] == ["_Answer"]
    assert "temperature" not in call
    system_text = call["system"] if isinstance(call["system"], str) else " ".join(
        block["text"] for block in call["system"]
    )
    assert "You are a browser agent." in system_text
    assert "`_Answer` is your only tool" in system_text


def test_structured_output_falls_back_to_json_text_when_no_tool_call(monkeypatch):
    response = _message([
        _THINKING,
        {"type": "text", "text": 'Here you go:\n```json\n{"value": "from-text"}\n```'},
    ])
    llm, _calls = _llm_with_fake_client(monkeypatch, response, mode="auto_tool")

    result = asyncio.run(llm.ainvoke(_MESSAGES, _Answer))

    assert result.completion == _Answer(value="from-text")


def test_structured_output_without_tool_or_json_raises_provider_error(monkeypatch):
    from browser_use.llm.exceptions import ModelProviderError

    llm, _calls = _llm_with_fake_client(
        monkeypatch, _message([{"type": "text", "text": "no structure here"}]), mode="auto_tool"
    )

    with pytest.raises(ModelProviderError):
        asyncio.run(llm.ainvoke(_MESSAGES, _Answer))


def test_text_completion_skips_leading_thinking_block(monkeypatch):
    response = _message([_THINKING, {"type": "text", "text": "hello"}, {"type": "text", "text": " world"}])
    llm, calls = _llm_with_fake_client(monkeypatch, response, mode="forced_tool")

    result = asyncio.run(llm.ainvoke(_MESSAGES))

    assert result.completion == "hello world"
    assert "tools" not in calls[0]
    assert "temperature" not in calls[0]


def test_forced_mode_keeps_browser_use_tool_choice(monkeypatch):
    response = _message(
        [{"type": "tool_use", "id": "toolu_1", "name": "_Answer", "input": {"value": "forced"}}],
        stop_reason="tool_use",
    )
    llm, calls = _llm_with_fake_client(monkeypatch, response, mode="forced_tool")

    result = asyncio.run(llm.ainvoke(_MESSAGES, _Answer))

    assert result.completion == _Answer(value="forced")
    assert calls[0]["tool_choice"] == {"type": "tool", "name": "_Answer"}
    assert "temperature" not in calls[0]
    system_text = calls[0]["system"] if isinstance(calls[0]["system"], str) else " ".join(
        block["text"] for block in calls[0]["system"]
    )
    assert "only tool" not in system_text


@pytest.mark.parametrize("mode", ["forced_tool", "auto_tool"])
def test_structured_refusal_raises_category_in_both_modes(monkeypatch, mode):
    from browser_use.llm.exceptions import ModelProviderError

    response = _message([], stop_reason="refusal")
    response.stop_details = {"type": "refusal", "category": "cyber"}
    llm, _calls = _llm_with_fake_client(monkeypatch, response, mode=mode)

    with pytest.raises(ModelProviderError, match="category=cyber"):
        asyncio.run(llm.ainvoke(_MESSAGES, _Answer))


@pytest.mark.parametrize("mode", ["forced_tool", "auto_tool"])
def test_structured_output_truncated_by_max_tokens_says_so(monkeypatch, mode):
    from browser_use.llm.exceptions import ModelProviderError

    response = _message(
        [{"type": "tool_use", "id": "toolu_1", "name": "_Answer", "input": {}}],
        stop_reason="max_tokens",
    )
    llm, _calls = _llm_with_fake_client(monkeypatch, response, mode=mode)

    with pytest.raises(ModelProviderError, match="max_tokens"):
        asyncio.run(llm.ainvoke(_MESSAGES, _Answer))


def test_auto_tool_rejects_call_to_a_different_tool(monkeypatch):
    from browser_use.llm.exceptions import ModelProviderError

    # Observed live on Sonnet 5.5: an action name called as if it were a tool.
    response = _message(
        [{"type": "tool_use", "id": "toolu_1", "name": "find_elements", "input": {"selector": "h1"}}],
        stop_reason="tool_use",
    )
    llm, _calls = _llm_with_fake_client(monkeypatch, response, mode="auto_tool")

    with pytest.raises(ModelProviderError, match="find_elements"):
        asyncio.run(llm.ainvoke(_MESSAGES, _Answer))


def test_refusal_raises_provider_error_with_category(monkeypatch):
    from browser_use.llm.exceptions import ModelProviderError

    response = _message(
        [{"type": "tool_use", "id": "toolu_1", "name": "_Answer", "input": {"value": "parti"}}],
        stop_reason="refusal",
    )
    response.stop_details = {"type": "refusal", "category": "reasoning_extraction"}
    llm, _calls = _llm_with_fake_client(monkeypatch, response, mode="auto_tool")

    with pytest.raises(ModelProviderError, match="reasoning_extraction"):
        asyncio.run(llm.ainvoke(_MESSAGES, _Answer))


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-sonnet-5-5", False),
        ("claude-opus-5-5", False),
        ("claude-sonnet-5", True),
        ("kimi-for-coding", True),
    ],
)
def test_agent_use_thinking_only_off_for_classifier_claude_models(model, expected):
    llm = NekoChatAnthropic(model=model, api_key="sk-test")

    assert BrowserUseAdapter._agent_use_thinking(llm) is expected


def test_agent_use_thinking_stays_on_for_openai_branch():
    llm = browser_use.llm.ChatOpenAI(model="claude-sonnet-5-5", api_key="sk-test")

    assert BrowserUseAdapter._agent_use_thinking(llm) is True
