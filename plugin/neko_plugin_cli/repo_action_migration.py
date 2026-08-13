"""Safely add standard Market GitHub Actions files to a plugin repository."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .templates.generator import (
    MARKET_ACTIONS_MANAGED_HEADER,
    PluginSpec,
    render_release_workflow,
    render_ruff_config,
    render_verify_workflow,
)

# Exact normalized fingerprints of the inline workflows emitted at PR base
# d5f4b36 and the first thin callers emitted at a250623. Dynamic values are
# normalized by _is_known_legacy_template before hashing.
_LEGACY_TEMPLATE_FINGERPRINTS = {
    Path(".github/workflows/verify.yml"): frozenset(
        {
            "fef67dc6fcc51cdab28c7a2b6ddd7534792b4b1f5cdf00918608b4476d758164",
            "d564c7e4f5a8ebbc3477c342321f5b1d6a218821f04bd57312a9b9d73a35112f",
        }
    ),
    Path(".github/workflows/release.yml"): frozenset(
        {
            "620a86589cca4bca0be0ea04cb8dc276a5debd0f1dbc1013984c8c496cf6bec7",
            "5669fe1c13e944bc4e0ba20d0a3dc47626e68e80aa310ff1fae3973596b68e0c",
        }
    ),
}


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
        Path("ruff.toml"): render_ruff_config(),
        Path(".github/workflows/verify.yml"): render_verify_workflow(spec),
        Path(".github/workflows/release.yml"): render_release_workflow(spec),
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
                elif existing == content.removeprefix(MARKET_ACTIONS_MANAGED_HEADER):
                    status = ActionFileStatus.UPGRADE
                elif _is_known_legacy_template(relative_path, existing, spec):
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


def _is_known_legacy_template(
    relative_path: Path,
    content: str,
    spec: PluginSpec,
) -> bool:
    fingerprints = _LEGACY_TEMPLATE_FINGERPRINTS.get(relative_path)
    if not fingerprints:
        return False
    normalized = content.removeprefix(MARKET_ACTIONS_MANAGED_HEADER)
    for current, placeholder in (
        (f"  PLUGIN_ID: {spec.plugin_id}", "  PLUGIN_ID: <PLUGIN_ID>"),
        (
            f"  NEKO_REPOSITORY: {spec.neko_repository}",
            "  NEKO_REPOSITORY: <NEKO_REPOSITORY>",
        ),
        (f"  NEKO_REF: {spec.neko_ref}", "  NEKO_REF: <NEKO_REF>"),
        (
            f"    uses: {spec.neko_repository}/.github/workflows/"
            f"plugin-market-verify.yml@{spec.neko_ref}",
            "    uses: <NEKO_REPOSITORY>/.github/workflows/"
            "plugin-market-verify.yml@<NEKO_REF>",
        ),
        (
            f"    uses: {spec.neko_repository}/.github/workflows/"
            f"plugin-market-release.yml@{spec.neko_ref}",
            "    uses: <NEKO_REPOSITORY>/.github/workflows/"
            "plugin-market-release.yml@<NEKO_REF>",
        ),
        (f"      plugin-id: {spec.plugin_id}", "      plugin-id: <PLUGIN_ID>"),
        (f"      neko-ref: {spec.neko_ref}", "      neko-ref: <NEKO_REF>"),
    ):
        normalized = normalized.replace(current, placeholder)
    fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return fingerprint in fingerprints


def _has_conflicting_parent(path: Path, target_dir: Path) -> bool:
    for parent in path.parents:
        if parent == target_dir:
            return False
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            return True
    return True
