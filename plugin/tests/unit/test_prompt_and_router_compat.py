from __future__ import annotations

import importlib

from config.prompts_chara import get_lanlan_prompt, is_default_prompt

agent_router_module = importlib.import_module("main_routers.agent_router")


def test_is_default_prompt_accepts_legacy_prompt_without_skills_line() -> None:
    legacy_prompt = "\n".join(
        line for line in get_lanlan_prompt("zh").splitlines()
        if not line.strip().startswith("- Skills: ")
    )
    assert is_default_prompt(legacy_prompt) is True


def test_is_default_prompt_keeps_custom_skills_line_non_default() -> None:
    base_prompt = get_lanlan_prompt("zh")
    default_skills_line = next(
        line for line in base_prompt.splitlines() if line.strip().startswith("- Skills: ")
    )
    customized_prompt = base_prompt.replace(
        default_skills_line,
        "- Skills: 可以写代码，也会主动解释自己的实现思路。",
    )
    assert is_default_prompt(customized_prompt) is False


def test_get_nekoclaw_channel_url_prefers_plugin_config(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_router_module,
        "load_plugin_config",
        lambda plugin_id, validate=False: {
            "plugin_id": plugin_id,
            "config": {"nekoclaw": {"url": "http://10.0.0.2:18089/"}},
        },
    )
    assert agent_router_module._get_nekoclaw_channel_url() == "http://10.0.0.2:18089"


def test_get_nekoclaw_channel_url_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setattr(agent_router_module, "load_plugin_config", lambda *args, **kwargs: {})
    assert agent_router_module._get_nekoclaw_channel_url() == agent_router_module.DEFAULT_NEKOCLAW_CHANNEL_URL


def test_get_nekoclaw_channel_url_handles_load_exception(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("load failed")

    monkeypatch.setattr(agent_router_module, "load_plugin_config", _boom)
    assert agent_router_module._get_nekoclaw_channel_url() == agent_router_module.DEFAULT_NEKOCLAW_CHANNEL_URL
