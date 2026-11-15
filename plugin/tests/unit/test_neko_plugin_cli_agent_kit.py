from __future__ import annotations

from pathlib import Path

import pytest

from plugin.neko_plugin_cli.public.pack_rules import PackRuleSet
from plugin.neko_plugin_cli.public.pack_rules import should_skip_path as pack_should_skip
from plugin.neko_plugin_cli.templates import agent_kit

pytestmark = pytest.mark.plugin_unit

_COMMANDS = {
    "command_context": "From the N.E.K.O repository root:",
    "check_command": "uv run neko-plugin check market_demo",
    "build_command": "uv run neko-plugin build market_demo",
    "sync_command": "uv run neko-plugin sync market_demo --clean",
}


def _write_kit(root: Path, *, with_skill: bool = True) -> None:
    (root / "references").mkdir(parents=True)
    if with_skill:
        (root / "SKILL.md").write_text("skill v1\n", encoding="utf-8")
    (root / "references" / "topic.md").write_text("reference v1\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("guide {plugin_id}: {check_command}\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("claude\n", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "junk.pyc").write_bytes(b"\x00")


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


def test_agents_template_substitutes_every_placeholder() -> None:
    content = agent_kit.render_agents_md("market_demo", **_COMMANDS)

    assert "{" not in content
    assert "uv run neko-plugin check market_demo" in content
    assert "uv run neko-plugin sync market_demo --clean" in content
    assert ".agents/skills/neko-plugin/" in content
    assert "plugin/plugins/<plugin_id>/" in content


def test_claude_template_points_at_agents_guide() -> None:
    content = agent_kit.render_claude_md()

    assert "AGENTS.md" in content
    assert ".agents/skills/neko-plugin/" in content


def test_kit_root_prefers_authored_source_over_staged_copy() -> None:
    # A stale staged copy from a previous prepare must not shadow source edits.
    cli_root = Path(agent_kit.__file__).resolve().parents[1]
    assert agent_kit._kit_root() == cli_root.parent.parent / ".agent" / "skills" / "neko-plugin"


def test_kit_excludes_root_templates_and_reads_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit = tmp_path / "agent_kit"
    _write_kit(kit)
    monkeypatch.setattr(agent_kit, "_kit_root", lambda: kit)

    assert agent_kit.skill_files() == {
        agent_kit.SKILL_TARGET_DIR / "SKILL.md": "skill v1\n",
        agent_kit.SKILL_TARGET_DIR / "references" / "topic.md": "reference v1\n",
    }
    assert agent_kit.render_agents_md("demo", **_COMMANDS) == (
        "guide demo: uv run neko-plugin check market_demo\n"
    )

    (kit / "references" / "topic.md").write_text("reference v2\n", encoding="utf-8")
    assert agent_kit.skill_files()[
        agent_kit.SKILL_TARGET_DIR / "references" / "topic.md"
    ] == "reference v2\n"


def test_kit_without_skill_entry_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit = tmp_path / "agent_kit"
    _write_kit(kit, with_skill=False)
    monkeypatch.setattr(agent_kit, "_kit_root", lambda: kit)

    with pytest.raises(agent_kit.AgentKitUnavailableError, match="SKILL.md"):
        agent_kit.skill_files()


def test_missing_kit_raises_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_kit, "_kit_root", lambda: tmp_path / "absent")

    with pytest.raises(agent_kit.AgentKitUnavailableError, match="prepare_nuitka_plugins"):
        agent_kit.skill_files()
    with pytest.raises(agent_kit.AgentKitUnavailableError, match="AGENTS.md"):
        agent_kit.render_agents_md("demo", **_COMMANDS)


def test_public_pack_rules_skip_scaffolded_agent_kit() -> None:
    rules = PackRuleSet()

    for path, is_dir in (
        (Path(".agents"), True),
        (Path(".agents/skills/neko-plugin/SKILL.md"), False),
        (Path(".claude"), True),
        (Path("AGENTS.md"), False),
        (Path("CLAUDE.md"), False),
    ):
        assert pack_should_skip(path, is_dir=is_dir, rules=rules) is True
