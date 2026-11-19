"""Text guards for the neko-plugin skill and the generated agent guides.

The skill is copied into third-party plugin projects that have no N.E.K.O
checkout, so it must not point at repository paths, must not teach the
removed extension package type, and may only name CLI subcommands that exist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from plugin.neko_plugin_cli.templates.agent_kit import is_ignored_skill_part

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_DIR = REPO_ROOT / ".agent" / "skills" / "neko-plugin"
GUIDES_DIR = REPO_ROOT / "plugin" / "neko_plugin_cli" / "templates" / "agent_guides"
CLI_DIR = REPO_ROOT / "plugin" / "neko_plugin_cli"

# Main-repository content is linked on GitHub; the link text may show the path.
_GITHUB_LINK = re.compile(r"\[[^\]]*\]\(https?://[^)]*\)")
# A repository path (optionally ./ or ../ relative) not preceded by a path or
# URL character. Only folders a plugin project never has: tests/, docs/, and
# .github/ exist there too.
_REPO_PATH = re.compile(
    r"(?<![\w./-])(?:\.\.?/)*(?:plugin|frontend|scripts|utils|main_logic|main_routers|docs/plugins|tests/unit)/"
)
_CHECKOUT_ONLY = "[checkout only]"
_HEADING = re.compile(r"^(#+)\s")
_FENCE = re.compile(r"^\s*(```|~~~)")
# A command, not prose such as "the neko-plugin skill": inline code or "uv run".
_SUBCOMMAND = re.compile(r"(?:`|uv run )neko-plugin[ \t]+([a-z][a-z0-9-]*)")
# Inside a code fence a command may also start the line.
_FENCED_SUBCOMMAND = re.compile(r"^[ \t]*neko-plugin[ \t]+([a-z][a-z0-9-]*)")


def _skill_files() -> list[Path]:
    # Same ignore rules as the snapshot (dotfiles, caches), so stray local
    # files such as .DS_Store are neither scanned nor decoded.
    return sorted(
        path
        for path in SKILL_DIR.rglob("*")
        if path.is_file()
        and not any(is_ignored_skill_part(part) for part in path.relative_to(SKILL_DIR).parts)
    )


def _guide_files() -> list[Path]:
    return _skill_files() + sorted(GUIDES_DIR.glob("*.tmpl"))


def _file_id(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _lines_outside_checkout_only(text: str) -> list[tuple[int, str]]:
    """Lines not under a heading marked [checkout only] (until a same-or-higher heading)."""
    kept: list[tuple[int, str]] = []
    in_fence = False
    skip_level: int | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            if skip_level is not None and level <= skip_level:
                skip_level = None
            if skip_level is None and _CHECKOUT_ONLY in line:
                skip_level = level
        if skip_level is None:
            kept.append((number, line))
    return kept


def _registered_subcommands() -> set[str]:
    cli_source = (CLI_DIR / "cli.py").read_text(encoding="utf-8")
    modules = re.findall(r"^\s*(\w+_cmd)\.register\(subparsers", cli_source, re.MULTILINE)
    assert modules, "no command registration found in cli.py"
    names: set[str] = set()
    for module in modules:
        source = (CLI_DIR / "commands" / f"{module}.py").read_text(encoding="utf-8")
        found = re.findall(r"add_parser\(\s*[\"']([\w-]+)[\"']", source)
        assert found, f"{module}.register adds no subcommand the guard can see"
        names.update(found)
    return names


@pytest.mark.parametrize("path", _skill_files(), ids=_file_id)
def test_skill_has_no_bare_repository_paths(path: Path) -> None:
    offenders = [
        f"{number}: {line.strip()}"
        for number, line in _lines_outside_checkout_only(path.read_text(encoding="utf-8"))
        if _REPO_PATH.search(_GITHUB_LINK.sub("", line))
    ]
    assert not offenders, (
        "repository paths do not exist in a plugin project; use a GitHub link, "
        f"<plugin_dir>, or a [checkout only] section: {offenders}"
    )


@pytest.mark.parametrize("path", _guide_files(), ids=_file_id)
def test_extension_is_only_mentioned_as_removed(path: Path) -> None:
    offenders = [
        f"{number}: {line.strip()}"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if "extension" in line.lower() and "removed" not in line.lower()
    ]
    assert not offenders, f"the extension package type was removed: {offenders}"


def _command_table_names(text: str) -> set[str]:
    """First word of each backticked item in the first cell of a "| Command |" table."""
    names: set[str] = set()
    in_table = False
    for line in text.splitlines():
        if not line.startswith("|"):
            in_table = False
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells[0] == "Command":
            in_table = True
        elif in_table and not set(cells[0]) <= set("-: "):
            names.update(re.findall(r"`([a-z][a-z0-9-]*)", cells[0]))
    return names


def _fenced_subcommands(text: str) -> set[str]:
    names: set[str] = set()
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        elif in_fence:
            names.update(_FENCED_SUBCOMMAND.findall(line))
    return names


@pytest.mark.parametrize("path", _guide_files(), ids=_file_id)
def test_named_subcommands_are_registered(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    mentioned = set(_SUBCOMMAND.findall(text)) | _command_table_names(text) | _fenced_subcommands(text)
    unknown = sorted(mentioned - _registered_subcommands())
    assert not unknown, f"neko-plugin subcommands not registered in cli.py: {unknown}"


def test_subcommand_scan_skips_template_placeholders() -> None:
    # Rendered commands such as {lint_command} are filled in by the generator,
    # so the scan must not read a placeholder as a subcommand name.
    text = "`{lint_command}`\n{precheck_command}\n`uv run neko-plugin {subcommand} .`"
    assert _SUBCOMMAND.findall(text) == []
    assert _command_table_names("| Command | Use |\n|---|---|\n| `{lint_command}` | lint |") == set()


def _numbered(text: str, pattern: str) -> list[str]:
    return [match.strip() for match in re.findall(pattern, text, re.MULTILINE)]


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start : end if end != -1 else len(text)]


def test_hard_constraint_titles_match() -> None:
    agents = _section((GUIDES_DIR / "AGENTS.md.tmpl").read_text(encoding="utf-8"), "## Hard Constraints")
    skill = _section((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8"), "## Hard Constraints")
    boundary = (SKILL_DIR / "references" / "execution-boundary.md").read_text(encoding="utf-8")

    agents_titles = _numbered(agents, r"^\d+\. \*\*(.+?)\*\*")
    skill_titles = _numbered(skill, r"^\d+\. (.+)$")
    boundary_titles = _numbered(boundary, r"^### \d+\. (.+)$")

    assert agents_titles, "no numbered hard constraints found in AGENTS.md.tmpl"
    assert skill_titles == agents_titles
    assert boundary_titles == agents_titles
