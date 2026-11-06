"""Local git facts of a plugin source checkout, read without network access.

The workbench publish panel, the server publish gate and ``publish`` all
judge "clean" and "pushed" through this module, so the three cannot disagree.
``publish`` stays the only code that pushes anything; it additionally checks
the live remote with ``ls-remote``.
"""

from __future__ import annotations

import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from plugin.utils.source_paths import VENDOR_SYNC_GLOBS

from .commands._resolve import parse_github_repository_remote

_GIT_TIMEOUT_SECONDS = 15
# Enough paths to tell the user what to commit without flooding the panel.
_DIRTY_PATHS_SHOWN = 10


@dataclass(frozen=True)
class RepoState:
    git_available: bool
    is_repository: bool
    tag: str
    # Credentials embedded in the remote URL are removed.
    origin_url: str | None = None
    github_repository: str | None = None
    branch: str | None = None
    upstream: str | None = None
    head: str | None = None
    detached: bool = False
    # True when HEAD equals its ``origin/<branch>`` upstream as of the last
    # fetch; False when it differs or the upstream is on another remote;
    # None when there is no HEAD, no upstream, or HEAD is detached.
    head_pushed: bool | None = None
    # The same definition ``publish`` enforces: tracked changes plus untracked,
    # not-ignored files, except dependency-sync work dirs.
    worktree_clean: bool | None = None
    dirty_paths: tuple[str, ...] = ()
    tag_exists: bool = False
    tag_at_head: bool | None = None


def git_output(plugin_dir: Path, *args: str, strip: bool = True) -> str | None:
    """Run ``git`` in ``plugin_dir``; UTF-8 stdout, or None on any failure.

    Pass ``strip=False`` for column-sensitive output such as ``status --porcelain``.
    """
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=plugin_dir,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() if strip else completed.stdout


@dataclass(frozen=True)
class _BranchStatus:
    """``git status --porcelain=v2 --branch`` for tracked files."""

    head: str | None
    branch: str | None
    upstream: str | None
    # True when HEAD is neither ahead of nor behind its upstream.
    level_with_upstream: bool
    tracked_paths: list[str]


def _branch_status(plugin_dir: Path) -> _BranchStatus | None:
    output = git_output(
        plugin_dir, "status", "--porcelain=v2", "--branch", "-z", "--untracked-files=no", strip=False
    )
    if output is None:
        return None
    head = branch = upstream = None
    level = False
    paths: list[str] = []
    records = iter(output.split("\0"))
    for record in records:
        if record.startswith("# branch.oid "):
            oid = record.removeprefix("# branch.oid ")
            head = None if oid == "(initial)" else oid
        elif record.startswith("# branch.head "):
            name = record.removeprefix("# branch.head ")
            branch = None if name == "(detached)" else name
        elif record.startswith("# branch.upstream "):
            upstream = record.removeprefix("# branch.upstream ")
        elif record.startswith("# branch.ab "):
            level = record.removeprefix("# branch.ab ") == "+0 -0"
        elif record[:2] in {"1 ", "u "}:
            # The path is the last field and may itself contain spaces.
            fields = 9 if record.startswith("1 ") else 11
            paths.append(record.split(" ", fields - 1)[-1])
        elif record.startswith("2 "):
            paths.append(record.split(" ", 9)[-1])
            # A rename record is followed by the original path.
            next(records, None)
    return _BranchStatus(head, branch, upstream, level, paths)


def _untracked_paths(plugin_dir: Path) -> list[str] | None:
    """Untracked, not-ignored files, except dependency-sync work dirs."""
    output = git_output(
        plugin_dir,
        "status",
        "--porcelain",
        "-z",
        "--untracked-files=all",
        "--",
        ".",
        # A wildcard pathspec matches whole paths: exclude each work dir
        # itself and everything under it.
        *(
            f":(top,exclude){pattern}{suffix}"
            for pattern in VENDOR_SYNC_GLOBS
            for suffix in ("", "/*")
        ),
        strip=False,
    )
    if output is None:
        return None
    return [record[3:] for record in output.split("\0") if record.startswith("?? ")]


def _dirty_paths(status: _BranchStatus | None, untracked: list[str] | None) -> list[str] | None:
    # Tracked changes always count, including sync work dirs committed by accident.
    if status is None or untracked is None:
        return None
    paths = list(status.tracked_paths)
    paths.extend(path for path in untracked if path not in paths)
    return paths


def worktree_dirty_paths(plugin_dir: Path) -> list[str] | None:
    """Paths that keep the worktree from being clean, or None when git fails.

    Tracked changes always count, including sync work dirs that were committed
    by accident. Untracked files count unless git ignores them or they are a
    dependency-sync work dir.
    """
    return _dirty_paths(_branch_status(plugin_dir), _untracked_paths(plugin_dir))


def committed_file_sizes(repository_root: Path) -> list[tuple[str, int]] | None:
    """``(path, bytes)`` of every file in the HEAD commit, or None when git fails.

    Paths are relative to the repository root; this is the tree GitHub zips
    for a commit.
    """
    output = git_output(repository_root, "ls-tree", "-r", "-z", "--long", "HEAD", strip=False)
    if output is None:
        return None
    files: list[tuple[str, int]] = []
    for record in output.split("\0"):
        meta, _, path = record.partition("\t")
        fields = meta.split()
        # Submodules are commits, not files of this repository.
        if len(fields) == 4 and fields[1] == "blob":
            files.append((path, int(fields[3])))
    return files


def strip_url_credentials(url: str) -> str:
    """Drop ``user[:password]@`` from an http(s) remote URL; other forms are returned as is."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or "@" not in parts.netloc:
        return url
    return urlunsplit(parts._replace(netloc=parts.netloc.rsplit("@", 1)[1]))


def read_repo_state(plugin_dir: Path, *, version: str) -> RepoState:
    """Describe the plugin directory's own git repository for release ``v<version>``."""
    tag = f"v{version}"
    if shutil.which("git") is None:
        return RepoState(git_available=False, is_repository=False, tag=tag)
    # Like ``publish``, the plugin directory must own its repository.
    if not (plugin_dir / ".git").exists():
        return RepoState(git_available=True, is_repository=False, tag=tag)

    # Each git process costs ~0.2 s on Windows; the four reads are independent.
    with ThreadPoolExecutor(max_workers=4) as pool:
        origin_read = pool.submit(git_output, plugin_dir, "remote", "get-url", "origin")
        status_read = pool.submit(_branch_status, plugin_dir)
        untracked_read = pool.submit(_untracked_paths, plugin_dir)
        tag_read = pool.submit(
            git_output, plugin_dir, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}"
        )
    raw_origin = origin_read.result() or None
    status = status_read.result()
    tag_commit = tag_read.result()
    dirty = _dirty_paths(status, untracked_read.result())

    origin_url = strip_url_credentials(raw_origin) if raw_origin else None
    head = status.head if status else None
    branch = status.branch if status else None
    detached = bool(head) and branch is None
    upstream = status.upstream if status and branch else None
    head_pushed: bool | None = None
    if head and upstream:
        remote, _, remote_branch = upstream.partition("/")
        head_pushed = remote == "origin" and bool(remote_branch) and status.level_with_upstream
    return RepoState(
        git_available=True,
        is_repository=True,
        tag=tag,
        origin_url=origin_url,
        github_repository=parse_github_repository_remote(origin_url) if origin_url else None,
        branch=branch,
        upstream=upstream,
        head=head,
        detached=detached,
        head_pushed=head_pushed,
        worktree_clean=None if dirty is None else not dirty,
        dirty_paths=tuple((dirty or [])[:_DIRTY_PATHS_SHOWN]),
        tag_exists=bool(tag_commit),
        tag_at_head=(tag_commit == head) if tag_commit and head else None,
    )
