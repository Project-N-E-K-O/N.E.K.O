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

import re
from fnmatch import fnmatchcase
from pathlib import Path

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
    build_command: str,
    sync_command: str,
) -> str:
    values = {
        "plugin_id": plugin_id,
        "command_context": command_context,
        "check_command": check_command,
        "build_command": build_command,
        "sync_command": sync_command,
    }
    template = _read_guide(AGENTS_TEMPLATE_NAME)
    # One pass over the template: inserted values (e.g. a checkout path that
    # itself contains "{plugin_id}") are never substituted again.
    return _PLACEHOLDER_RE.sub(
        lambda match: values.get(match.group(1), match.group(0)), template
    )


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
    return files
