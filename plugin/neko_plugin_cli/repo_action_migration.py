"""Safely add standard Market GitHub Actions files to a plugin repository."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .templates.generator import (
    PluginSpec,
    _MARKET_ACTIONS_MANAGED_HEADER,
    _render_release_workflow,
    _render_ruff_config,
    _render_verify_workflow,
)


class ActionFileStatus(StrEnum):
    ADD = "ADD"
    CURRENT = "CURRENT"
    UPGRADE = "UPGRADE"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class ActionFileChange:
    relative_path: Path
    status: ActionFileStatus
    content: str


def migrate_github_actions(
    spec: PluginSpec,
    target_dir: Path,
    *,
    dry_run: bool = False,
) -> list[ActionFileChange]:
    """Plan and apply conflict-free standard GitHub Actions file changes."""
    rendered = {
        Path("ruff.toml"): _render_ruff_config(),
        Path(".github/workflows/verify.yml"): _render_verify_workflow(spec),
        Path(".github/workflows/release.yml"): _render_release_workflow(spec),
    }
    changes: list[ActionFileChange] = []
    for relative_path, content in rendered.items():
        path = target_dir / relative_path
        if path.is_symlink() or _has_conflicting_parent(path, target_dir):
            status = ActionFileStatus.CONFLICT
        elif not path.exists():
            status = ActionFileStatus.ADD
        elif path.is_file():
            try:
                existing = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                status = ActionFileStatus.CONFLICT
            else:
                if existing == content:
                    status = ActionFileStatus.CURRENT
                elif existing == content.removeprefix(_MARKET_ACTIONS_MANAGED_HEADER):
                    status = ActionFileStatus.UPGRADE
                else:
                    status = ActionFileStatus.CONFLICT
        else:
            status = ActionFileStatus.CONFLICT
        changes.append(ActionFileChange(relative_path, status, content))

    if dry_run or any(
        change.status is ActionFileStatus.CONFLICT for change in changes
    ):
        return changes

    for change in changes:
        if change.status not in {ActionFileStatus.ADD, ActionFileStatus.UPGRADE}:
            continue
        path = target_dir / change.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(change.content, encoding="utf-8", newline="\n")
    return changes


def _has_conflicting_parent(path: Path, target_dir: Path) -> bool:
    for parent in path.parents:
        if parent == target_dir:
            return False
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            return True
    return True
