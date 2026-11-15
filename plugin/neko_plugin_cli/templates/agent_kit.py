"""Single source for the agent kit copied into every new plugin scaffold.

The repository keeps one authored copy of the neko-plugin skill under
``.agent/skills/neko-plugin``. Desktop builds stage that directory plus the
``AGENTS.md`` template into ``plugin/neko_plugin_cli/agent_kit`` (see
``scripts/prepare_nuitka_plugins.py prepare``) so the frozen CLI carries it as
package data; source checkouts read the authored copy directly. Scaffolds
snapshot the kit into each generated plugin, so plugin repositories stay
self-contained without a second maintained skills directory.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

SKILL_NAME = "neko-plugin"
SKILL_TARGET_DIR = Path(".agents") / "skills" / SKILL_NAME
AGENTS_TEMPLATE_NAME = "AGENTS.md"
CLAUDE_TEMPLATE_NAME = "CLAUDE.md"


class AgentKitUnavailableError(RuntimeError):
    """The authored or staged agent kit is missing from this installation."""


def _kit_root() -> Path:
    cli_root = Path(__file__).resolve().parents[1]
    staged = cli_root / "agent_kit"
    if staged.is_dir():
        return staged
    # <repo>/plugin/neko_plugin_cli/templates/agent_kit.py -> <repo>
    return cli_root.parent.parent / ".agent" / "skills" / SKILL_NAME


def _read_kit_file(relative: Path) -> str:
    path = _kit_root() / relative
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentKitUnavailableError(
            f"agent kit file missing: {path}. Source checkouts keep it under "
            f".agent/skills/{SKILL_NAME}; desktop builds stage it via "
            "scripts/prepare_nuitka_plugins.py prepare."
        ) from exc


def render_agents_md(plugin_id: str) -> str:
    template = _read_kit_file(Path(AGENTS_TEMPLATE_NAME))
    return template.replace("{plugin_id}", plugin_id)


def render_claude_md() -> str:
    return _read_kit_file(Path(CLAUDE_TEMPLATE_NAME))


@cache
def skill_files() -> dict[Path, str]:
    """Snapshot of the neko-plugin skill, keyed by plugin-relative target path."""
    root = _kit_root()
    if not root.is_dir():
        raise AgentKitUnavailableError(
            f"agent kit directory missing: {root}. Source checkouts keep it under "
            f".agent/skills/{SKILL_NAME}; desktop builds stage it via "
            "scripts/prepare_nuitka_plugins.py prepare."
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
        files[SKILL_TARGET_DIR / relative] = path.read_text(encoding="utf-8")
    if not files:
        raise AgentKitUnavailableError(f"agent kit directory is empty: {root}")
    return files