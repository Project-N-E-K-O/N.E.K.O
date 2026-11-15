from __future__ import annotations

from pathlib import Path

import pytest

from plugin.neko_plugin_cli.templates import agent_kit

pytestmark = pytest.mark.plugin_unit


def test_authored_kit_loads_from_source_checkout() -> None:
    files = agent_kit.skill_files()

    assert agent_kit.SKILL_TARGET_DIR / "SKILL.md" in files
    assert agent_kit.SKILL_TARGET_DIR / "references" / "core-plugin-contract.md" in files
    assert all(
        path.parts[:2] == (".agents", "skills") for path in files
    )
    assert not any(
        path.name in ("AGENTS.md", "CLAUDE.md") and path.parent == agent_kit.SKILL_TARGET_DIR
        for path in files
    )


def test_agents_template_substitutes_plugin_id() -> None:
    content = agent_kit.render_agents_md("market_demo")

    assert "{plugin_id}" not in content
    assert "neko-plugin check market_demo" in content
    assert ".agents/skills/neko-plugin/" in content


def test_claude_template_points_at_agents_guide() -> None:
    content = agent_kit.render_claude_md()

    assert "AGENTS.md" in content
    assert ".agents/skills/neko-plugin/" in content


def test_staged_kit_takes_precedence_and_excludes_root_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = tmp_path / "agent_kit"
    (staged / "references").mkdir(parents=True)
    (staged / "SKILL.md").write_text("staged skill\n", encoding="utf-8")
    (staged / "references" / "topic.md").write_text("staged reference\n", encoding="utf-8")
    (staged / "AGENTS.md").write_text("staged {plugin_id}\n", encoding="utf-8")
    (staged / "CLAUDE.md").write_text("staged claude\n", encoding="utf-8")
    (staged / "__pycache__").mkdir()
    (staged / "__pycache__" / "junk.pyc").write_bytes(b"\x00")

    monkeypatch.setattr(agent_kit, "_kit_root", lambda: staged)
    agent_kit.skill_files.cache_clear()
    try:
        files = agent_kit.skill_files()
        assert files == {
            agent_kit.SKILL_TARGET_DIR / "SKILL.md": "staged skill\n",
            agent_kit.SKILL_TARGET_DIR / "references" / "topic.md": "staged reference\n",
        }
        assert agent_kit.render_agents_md("demo") == "staged demo\n"
    finally:
        agent_kit.skill_files.cache_clear()


def test_missing_kit_raises_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_kit, "_kit_root", lambda: tmp_path / "absent")
    agent_kit.skill_files.cache_clear()
    try:
        with pytest.raises(agent_kit.AgentKitUnavailableError, match="prepare_nuitka_plugins"):
            agent_kit.skill_files()
        with pytest.raises(agent_kit.AgentKitUnavailableError, match="AGENTS.md"):
            agent_kit.render_agents_md("demo")
    finally:
        agent_kit.skill_files.cache_clear()