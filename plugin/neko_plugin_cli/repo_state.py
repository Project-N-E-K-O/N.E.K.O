"""Local git facts of a plugin source checkout, read without network access.

The workbench publish panel shows these. ``publish`` keeps its own stricter
checks and remains the only code that pushes anything.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .commands._resolve import parse_github_repository_remote

_GIT_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class RepoState:
    git_available: bool
    is_repository: bool
    tag: str
    origin_url: str | None = None
    github_repository: str | None = None
    branch: str | None = None
    upstream: str | None = None
    head: str | None = None
    # Compared with the remote-tracking branch as of the last fetch.
    head_pushed: bool | None = None
    # Tracked changes only: untracked sync work dirs are not plugin source.
    worktree_clean: bool | None = None
    tag_exists: bool = False
    tag_at_head: bool | None = None


def _git(plugin_dir: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=plugin_dir,
            text=True,
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def read_repo_state(plugin_dir: Path, *, version: str) -> RepoState:
    """Describe the plugin directory's own git repository for release ``v<version>``."""
    tag = f"v{version}"
    if shutil.which("git") is None:
        return RepoState(git_available=False, is_repository=False, tag=tag)
    # Like ``publish``, the plugin directory must own its repository.
    if not (plugin_dir / ".git").exists():
        return RepoState(git_available=True, is_repository=False, tag=tag)

    origin_url = _git(plugin_dir, "remote", "get-url", "origin") or None
    head = _git(plugin_dir, "rev-parse", "HEAD")
    upstream = _git(plugin_dir, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    upstream_head = _git(plugin_dir, "rev-parse", "@{upstream}") if upstream else None
    status = _git(plugin_dir, "status", "--porcelain", "--untracked-files=no")
    tag_commit = _git(plugin_dir, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
    return RepoState(
        git_available=True,
        is_repository=True,
        tag=tag,
        origin_url=origin_url,
        github_repository=parse_github_repository_remote(origin_url) if origin_url else None,
        branch=_git(plugin_dir, "symbolic-ref", "--short", "-q", "HEAD") or None,
        upstream=upstream or None,
        head=head or None,
        head_pushed=None if not head else upstream_head == head,
        worktree_clean=None if status is None else not status,
        tag_exists=bool(tag_commit),
        tag_at_head=(tag_commit == head) if tag_commit and head else None,
    )
