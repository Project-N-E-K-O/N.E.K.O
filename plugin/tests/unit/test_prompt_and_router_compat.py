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
        (line for line in base_prompt.splitlines() if line.strip().startswith("- Skills: ")),
        None,
    )
    if default_skills_line is None:
        assert "- Skills:" not in base_prompt
        return
    customized_prompt = base_prompt.replace(
        default_skills_line,
        "- Skills: 可以写代码，也会主动解释自己的实现思路。",
    )
    assert is_default_prompt(customized_prompt) is False

def test_agent_router_exports_openclaw_availability_proxy() -> None:
    paths = {
        path
        for path in (
            getattr(route, "path", None)
            for route in getattr(agent_router_module.router, "routes", [])
        )
        if isinstance(path, str)
    }
    assert "/api/agent/openclaw/availability" not in paths
    assert "/openclaw/availability" in paths
