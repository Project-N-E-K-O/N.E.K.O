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

def test_is_default_prompt_accepts_legacy_prompt_without_memory_integrity() -> None:
    """Old stored default (before Memory Integrity was added) should still be recognised."""
    legacy_prompt = "\n".join(
        line for line in get_lanlan_prompt("zh").splitlines()
        if not line.strip().startswith("- Memory Integrity:")
    )
    assert is_default_prompt(legacy_prompt) is True


def test_is_default_prompt_rejects_custom_memory_integrity_line() -> None:
    """User who edited the Memory Integrity line should NOT be treated as default."""
    base_prompt = get_lanlan_prompt("zh")
    memory_line = next(
        line for line in base_prompt.splitlines()
        if line.strip().startswith("- Memory Integrity:")
    )
    customized_prompt = base_prompt.replace(
        memory_line,
        "- Memory Integrity: 我改过这行，加了自己的规则。",
    )
    assert is_default_prompt(customized_prompt) is False


def test_is_default_prompt_accepts_legacy_in_place_wording() -> None:
    """Old shipped defaults that differ only by in-place wording edits must still be
    classified as default — otherwise existing users lose auto-localization on
    every cosmetic prompt tweak."""
    cases = {
        "zh": ("无需客套", "无需客气"),
        "zh-TW": ("無需客套", "無需客氣"),
        "ja": ("他人行儀は不要", "遠慮は不要"),
        "en": ('"settings/character setting"', '"character setting"'),
    }
    for lang, (current, legacy) in cases.items():
        current_prompt = get_lanlan_prompt(lang)
        assert current in current_prompt, f"{lang}: current wording absent from template"
        legacy_prompt = current_prompt.replace(current, legacy)
        assert is_default_prompt(legacy_prompt) is True, f"{lang}: legacy wording not normalized"


def test_is_default_prompt_accepts_legacy_screen_capture_typo() -> None:
    """Old shipped defaults had ``an screen capture`` typo — fixed to ``a screen capture``.
    Users who stored the typo'd version should still be classified as default."""
    current_prompt = get_lanlan_prompt("zh")
    legacy_prompt = current_prompt.replace("a screen capture", "an screen capture")
    assert legacy_prompt != current_prompt
    assert is_default_prompt(legacy_prompt) is True


def test_agent_router_exports_openclaw_availability_proxy() -> None:
    paths = {
        path
        for path in (
            getattr(route, "path", None)
            for route in getattr(agent_router_module.router, "routes", [])
        )
        if isinstance(path, str)
    }
    assert "/api/agent/openclaw/availability" in paths
