"""Standard plugin project creation shared by the CLI and local UI."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plugin.neko_plugin_cli.templates.generator import PluginSpec


class ProjectCreationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def prepare_project(
    spec: PluginSpec, target_dir: Path, *, repo_root: Path | None
) -> dict[Path, str]:
    from plugin.neko_plugin_cli.templates.generator import render_plugin_files

    if not re.fullmatch(r"[a-z][a-z0-9_]*", spec.plugin_id):
        raise ProjectCreationError(
            "INVALID_PLUGIN_ID", "Use a lowercase plugin ID starting with a letter"
        )
    if target_dir.exists() or target_dir.is_symlink():
        raise ProjectCreationError(
            "PROJECT_EXISTS", f"Directory already exists: {target_dir}"
        )
    return render_plugin_files(spec, target_dir, repo_root=repo_root)


def _run_git(command: list[str], *, cwd: Path) -> None:
    try:
        subprocess.run(
            ["git", *command],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise ProjectCreationError("GIT_INIT_FAILED", str(detail).strip()) from exc


def create_project(
    spec: PluginSpec,
    target_dir: Path,
    *,
    repo_root: Path | None,
    remote: str | None = None,
    git_runner: Callable[..., None] = _run_git,
) -> list[Path]:
    files = prepare_project(spec, target_dir, repo_root=repo_root)
    if shutil.which("git") is None:
        raise ProjectCreationError(
            "GIT_NOT_FOUND", "Git is required to initialize the plugin repository"
        )
    # Only a directory exclusively created by this operation may be rolled back.
    target_dir = target_dir.absolute()
    parent = target_dir.parent.resolve()
    created = False
    try:
        target_dir.mkdir(parents=True, exist_ok=False)
        created = True
        for relative, content in files.items():
            path = target_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        git_runner(["init"], cwd=target_dir)
        git_runner(["symbolic-ref", "HEAD", "refs/heads/main"], cwd=target_dir)
        if remote:
            git_runner(["remote", "add", "origin", remote], cwd=target_dir)
    except Exception:
        if (
            created
            and target_dir.is_dir()
            and not target_dir.is_symlink()
            and target_dir.resolve().parent == parent
        ):
            shutil.rmtree(target_dir)
        raise
    return [target_dir / relative for relative in files]
