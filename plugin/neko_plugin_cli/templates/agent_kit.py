"""Single source for the agent kit copied into every new plugin scaffold.

The repository keeps one authored copy of the neko-plugin skill under
``.agent/skills/neko-plugin``. Desktop builds stage that directory plus the
``AGENTS.md`` template into ``plugin/neko_plugin_cli/agent_kit`` (see
``scripts/prepare_nuitka_plugins.py prepare``), and wheel builds map the
authored directory to the same package path, so installed CLIs carry it as
package data. Source checkouts always read the authored copy directly, even
when a stale staged copy exists. Scaffolds snapshot the kit into each
generated plugin, so plugin repositories stay self-contained without a second
maintained skills directory.
"""

from __future__ import annotations

from pathlib import Path

SKILL_NAME = "neko-plugin"
SKILL_TARGET_DIR = Path(".agents") / "skills" / SKILL_NAME
SKILL_ENTRY_NAME = "SKILL.md"
AGENTS_TEMPLATE_NAME = "AGENTS.md"
CLAUDE_TEMPLATE_NAME = "CLAUDE.md"

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


def _read_kit_file(relative: Path) -> str:
    path = _kit_root() / relative
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentKitUnavailableError(
            f"agent kit file missing: {path}. {_MISSING_HINT}"
        ) from exc


def render_agents_md(
    plugin_id: str,
    *,
    command_context: str,
    check_command: str,
    build_command: str,
    sync_command: str,
) -> str:
    template = _read_kit_file(Path(AGENTS_TEMPLATE_NAME))
    replacements = {
        "{command_context}": command_context,
        "{check_command}": check_command,
        "{build_command}": build_command,
        "{sync_command}": sync_command,
        "{plugin_id}": plugin_id,
    }
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    return template


def render_claude_md() -> str:
    return _read_kit_file(Path(CLAUDE_TEMPLATE_NAME))


def skill_files() -> dict[Path, str]:
    """Snapshot of the neko-plugin skill, keyed by plugin-relative target path.

    Read fresh on every call so a long-lived workbench process picks up edits
    to the authored skill together with the re-read AGENTS.md / CLAUDE.md.
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
        if relative.as_posix() in (AGENTS_TEMPLATE_NAME, CLAUDE_TEMPLATE_NAME):
            continue
        if path.suffix in (".pyc", ".pyo") or "__pycache__" in relative.parts:
            continue
        try:
            files[SKILL_TARGET_DIR / relative] = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AgentKitUnavailableError(
                f"agent kit file unreadable: {path}. {_MISSING_HINT}"
            ) from exc
    return files
