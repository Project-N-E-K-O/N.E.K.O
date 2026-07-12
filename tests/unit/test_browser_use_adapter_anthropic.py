from __future__ import annotations

from types import SimpleNamespace

import browser_use.llm

from brain.browser_use_adapter import BrowserUseAdapter


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

    monkeypatch.setattr(browser_use.llm, "ChatAnthropic", fake_anthropic)
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
        "temperature": 0.0,
        "default_headers": None,
    }


def test_build_llm_normalizes_claude_and_adds_kimi_user_agent(monkeypatch):
    calls = []

    def fake_anthropic(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(kind="anthropic")

    monkeypatch.setattr(browser_use.llm, "ChatAnthropic", fake_anthropic)

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
    adapter = _adapter({
        "model": "gpt-test",
        "base_url": "https://openai-proxy.example/v1",
        "api_key": "sk-test",
        "provider_type": "openai_compatible",
    })

    assert adapter._build_llm().kind == "openai"
    assert captured["dont_force_structured_output"] is False
    assert adapter._current_api_signature() == (
        "openai_compatible|https://openai-proxy.example/v1|gpt-test"
    )
