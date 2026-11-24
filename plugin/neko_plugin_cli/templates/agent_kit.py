"""Single source for the agent kit copied into every new plugin scaffold.

The kit has two parts:

- The neko-plugin skill. The repository keeps one authored copy under
  ``.agent/skills/neko-plugin``. Desktop builds stage it into
  ``plugin/neko_plugin_cli/agent_kit`` (see ``scripts/prepare_nuitka_plugins.py
  prepare``), and wheel builds map the authored directory to the same package
  path, so installed CLIs carry it as package data. Source checkouts always
  read the authored copy directly, even when a stale staged copy exists.
- The plugin-repository guides (``AGENTS.md`` / ``CLAUDE.md``). They are CLI
  templates under ``templates/agent_guides`` with a ``.tmpl`` suffix, so the
  main repository never contains a live guide that tells agents maintaining the
  skill they are inside a single plugin.

Scaffolds snapshot the kit into each generated plugin, so plugin repositories
stay self-contained without a second maintained skills directory.
"""

from __future__ import annotations

import os
import re
from fnmatch import fnmatchcase
from pathlib import Path

from .. import __version__ as CLI_VERSION

SKILL_NAME = "neko-plugin"
SKILL_TARGET_DIR = Path(".agents") / "skills" / SKILL_NAME
SKILL_ENTRY_NAME = "SKILL.md"
GUIDES_DIR = Path(__file__).resolve().parent / "agent_guides"
AGENTS_TEMPLATE_NAME = "AGENTS.md.tmpl"
CLAUDE_TEMPLATE_NAME = "CLAUDE.md.tmpl"
# Shared with scripts/prepare_nuitka_plugins.py so the staged desktop copy and
# the scaffold snapshot select the same files. Dot files cover .DS_Store and
# editor swap files.
SKILL_IGNORE_PATTERNS = (".*", "__pycache__", "*.pyc", "*.pyo")

# Official sources of the kit files, shown when setup-repo keeps a user's
# existing copy. main, not a tag: the CLI version maps to no repository tag.
SOURCE_URL_PREFIX = "https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/"
_GUIDES_SOURCE = "plugin/neko_plugin_cli/templates/agent_guides"

# Written into the snapshot's SKILL.md when it is rendered, never into the
# authored file, so check can flag stale snapshots. Preferred form: an Agent
# Skills ``metadata`` entry; if the frontmatter already has a ``metadata`` key
# (or there is no frontmatter), a comment line right after it instead, so the
# YAML is never edited in place.
SNAPSHOT_VERSION_KEY = "neko-plugin-cli-version"
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)^---[ \t]*\r?$", re.DOTALL | re.MULTILINE)
_METADATA_KEY_RE = re.compile(r"^metadata[ \t]*:", re.MULTILINE)
_METADATA_STAMP_RE = re.compile(
    rf'^metadata:\r?\n  {SNAPSHOT_VERSION_KEY}: "([^"\r\n]*)"\r?$', re.MULTILINE
)
_COMMENT_STAMP_RE = re.compile(rf'<!-- {SNAPSHOT_VERSION_KEY}: "([^"\r\n]*)" -->[ \t]*\r?$', re.MULTILINE)
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
_MISSING_HINT = (
    f"Source checkouts keep it under .agent/skills/{SKILL_NAME}; desktop builds "
    "stage it via scripts/prepare_nuitka_plugins.py prepare."
)


class AgentKitUnavailableError(RuntimeError):
    """The authored or staged agent kit is missing from this installation."""


def _kit_root() -> Path:
    cli_root = Path(__file__).resolve().parents[1]
    # <repo>/plugin/neko_plugin_cli/templates/agent_kit.py -> <repo>
    authored = cli_root.parent.parent / ".agent" / "skills" / SKILL_NAME
    if (authored / SKILL_ENTRY_NAME).is_file():
        return authored
    return cli_root / "agent_kit"


def is_ignored_skill_part(name: str) -> bool:
    return any(fnmatchcase(name, pattern) for pattern in SKILL_IGNORE_PATTERNS)


def _read_text(path: Path, hint: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AgentKitUnavailableError(
            f"agent kit file missing or not UTF-8 text: {path}. {hint}"
        ) from exc


def _read_guide(name: str) -> str:
    return _read_text(
        GUIDES_DIR / name,
        "It ships with plugin/neko_plugin_cli/templates/agent_guides.",
    )


def render_agents_md(
    plugin_id: str,
    *,
    command_context: str,
    check_command: str,
    lint_command: str,
    sync_command: str,
    precheck_command: str,
    build_command: str,
) -> str:
    values = {
        "plugin_id": plugin_id,
        "command_context": command_context,
        "check_command": check_command,
        "lint_command": lint_command,
        "sync_command": sync_command,
        "precheck_command": precheck_command,
        "build_command": build_command,
    }
    template = _read_guide(AGENTS_TEMPLATE_NAME)
    # One pass over the template: inserted values (e.g. a checkout path that
    # itself contains "{plugin_id}") are never substituted again.
    return _PLACEHOLDER_RE.sub(
        lambda match: values.get(match.group(1), match.group(0)), template
    )


def _stamp_skill_entry(text: str) -> str:
    stamp = f'{SNAPSHOT_VERSION_KEY}: "{CLI_VERSION}"'
    frontmatter = _FRONTMATTER_RE.match(text)
    if frontmatter is None:
        return f"<!-- {stamp} -->\n{text}"
    if _METADATA_KEY_RE.search(frontmatter.group(1)) is None:
        insert_at = frontmatter.end(1)
        return f"{text[:insert_at]}metadata:\n  {stamp}\n{text[insert_at:]}"
    insert_at = frontmatter.end()
    return f"{text[:insert_at]}\n<!-- {stamp} -->{text[insert_at:]}"


def snapshot_cli_version(skill_entry: str) -> str | None:
    """CLI version stamped into a snapshot's SKILL.md, or None if unstamped.

    Only the two places _stamp_skill_entry writes are read, so the same text
    elsewhere (a description, the body) is never mistaken for the stamp.
    """
    # A BOM (e.g. from a Windows editor) would hide the frontmatter.
    skill_entry = skill_entry.removeprefix("\ufeff")
    frontmatter = _FRONTMATTER_RE.match(skill_entry)
    position = 0
    if frontmatter is not None:
        stamp = _METADATA_STAMP_RE.search(frontmatter.group(1))
        if stamp is not None:
            return stamp.group(1)
        position = frontmatter.end()
        for newline in ("\r\n", "\n"):
            if skill_entry.startswith(newline, position):
                position += len(newline)
                break
    comment = _COMMENT_STAMP_RE.match(skill_entry, position)
    return comment.group(1) if comment else None


def _is_link(path: Path) -> bool:
    """Symlink, or a Windows junction (which is_symlink() misses on 3.11).

    Same check as commands/deps_cmd._is_link; templates do not import commands.
    """
    if path.is_symlink():
        return True
    try:
        os.readlink(path)
    except (OSError, ValueError):
        return False
    return True


def passes_through_symlink(root: Path, relative: Path) -> bool:
    """Whether root/relative, or any directory between root and it, is a link
    (symlink or Windows junction)."""
    current = root
    for part in relative.parts:
        current = current / part
        if _is_link(current):
            return True
    return False


def source_url(relative: Path) -> str | None:
    """GitHub URL of the authored file behind a kit target path, or None."""
    guides = {Path("AGENTS.md"): AGENTS_TEMPLATE_NAME, Path("CLAUDE.md"): CLAUDE_TEMPLATE_NAME}
    if relative in guides:
        return f"{SOURCE_URL_PREFIX}{_GUIDES_SOURCE}/{guides[relative]}"
    if relative.parts[: len(SKILL_TARGET_DIR.parts)] == SKILL_TARGET_DIR.parts:
        authored = Path(".agent") / "skills" / SKILL_NAME / relative.relative_to(SKILL_TARGET_DIR)
        return f"{SOURCE_URL_PREFIX}{authored.as_posix()}"
    return None


def render_claude_md() -> str:
    return _read_guide(CLAUDE_TEMPLATE_NAME)


def skill_files() -> dict[Path, str]:
    """Snapshot of the neko-plugin skill, keyed by plugin-relative target path.

    Read fresh on every call so a long-lived workbench process picks up edits
    to the authored skill together with the re-read guide templates.
    """
    root = _kit_root()
    if not root.is_dir():
        raise AgentKitUnavailableError(
            f"agent kit directory missing: {root}. {_MISSING_HINT}"
        )
    if not (root / SKILL_ENTRY_NAME).is_file():
        raise AgentKitUnavailableError(
            f"agent kit {SKILL_ENTRY_NAME} missing under {root}. {_MISSING_HINT}"
        )
    files: dict[Path, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(is_ignored_skill_part(part) for part in relative.parts):
            continue
        files[SKILL_TARGET_DIR / relative] = _read_text(path, _MISSING_HINT)
    entry = SKILL_TARGET_DIR / SKILL_ENTRY_NAME
    files[entry] = _stamp_skill_entry(files[entry])
    return files
