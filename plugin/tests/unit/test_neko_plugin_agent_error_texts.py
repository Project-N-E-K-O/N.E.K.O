"""Guard the error texts the agent guides quote for troubleshooting.

Agents match these strings against plugin logs and CLI output. If the code
stops emitting one, the guide sends them looking for a message that never
appears, so every quoted text must still exist in the plugin source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.plugin_unit

REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_DIR = REPO_ROOT / "plugin"
SKILL_DIR = REPO_ROOT / ".agent" / "skills" / "neko-plugin"
GUIDES_DIR = PLUGIN_DIR / "neko_plugin_cli" / "templates" / "agent_guides"
# Code that emits the texts; tests and built-in plugins only mention them, and
# third-party trees (vendor/, node_modules/, virtualenvs) are not ours.
_SKIPPED_TOP_DIRS = ("plugins",)
_SKIPPED_DIRS = ("tests", "vendor", "node_modules", "site-packages")

# (guide file, quoted error text or code). Add an entry when a guide quotes a
# new error text for troubleshooting.
CITED_ERROR_TEXTS = (
    (GUIDES_DIR / "AGENTS.md.tmpl", "MissingPythonDependencies"),
    (GUIDES_DIR / "AGENTS.md.tmpl", "Entry must be 'async def'"),
    (GUIDES_DIR / "AGENTS.md.tmpl", "Plugin event-loop stall"),
    (SKILL_DIR / "references" / "runtime-model.md", "Entry must be 'async def'"),
    (SKILL_DIR / "references" / "runtime-model.md", "Plugin event-loop stall"),
    (SKILL_DIR / "references" / "core-plugin-contract.md", "PLUGIN_OUTPUT_SCHEMA_VIOLATION"),
    (SKILL_DIR / "references" / "core-plugin-contract.md", "EntryConflictError"),
    (SKILL_DIR / "references" / "dependencies-and-market.md", "MissingPythonDependencies"),
)


def _file_id(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _is_plugin_source(path: Path) -> bool:
    parts = path.relative_to(PLUGIN_DIR).parts
    directories = parts[:-1]
    return (
        parts[0] not in _SKIPPED_TOP_DIRS
        and not any(part in _SKIPPED_DIRS or part.startswith(".") for part in directories)
    )


def _plugin_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(PLUGIN_DIR.rglob("*.py"))
        if _is_plugin_source(path)
    )


@pytest.fixture(scope="module")
def plugin_source() -> str:
    return _plugin_source()


@pytest.mark.parametrize(
    ("guide", "text"),
    CITED_ERROR_TEXTS,
    ids=[f"{_file_id(guide)}:{text}" for guide, text in CITED_ERROR_TEXTS],
)
def test_cited_error_text_is_quoted_by_the_guide_and_emitted_by_the_code(
    guide: Path, text: str, plugin_source: str
) -> None:
    # The first check keeps this list in step with the guides.
    assert text in guide.read_text(encoding="utf-8"), f"{_file_id(guide)} no longer quotes {text!r}"
    assert text in plugin_source, f"{text!r} quoted by {_file_id(guide)} is not in the plugin source"
