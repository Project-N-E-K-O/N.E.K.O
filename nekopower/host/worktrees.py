# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Git worktrees a side session works in, and landing what it did (design 3.3, 17.8).

A side session does not write the user's project directory. It works in a
git worktree made from what the project directory holds now, and when it is
done the host lands its changes on the project's working files: a three-way
merge of where the worktree started, the project as it is now and the
worktree's result. Nothing is committed and no branch is made; new files
land as ordinary untracked files.

What this module may do to the user's repository, and nothing else:

* write objects, and refs below ``refs/nekopower/``;
* ``git worktree add`` / ``remove``, and remove the registration of a
  worktree of its own whose directory is gone;
* change working files, only when landing, and only the ones the merge
  changes; a landing either changes all of them or, as far as the files
  allow, puts every one back as it was (``MergeResult``).

Its branches, index, stash, configuration and hooks are never touched:
snapshots use a temporary index file, every git call points ``core.hooksPath``
at a directory that does not exist, turns off the file system monitor and
automatic housekeeping, never asks for credentials and never fetches. The
one command that changes a checkout (``checkout``, in ``prepare_conflicts``)
runs in the host's own worktree.

``TaskAgentManager`` has one manager of these for the host
(``tree_work.py``): it makes a worktree for each commission of a member that
changes files beside others, and lands what the member did.
"""

from __future__ import annotations

import asyncio
import contextlib
import filecmp
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

import config.nekopower_settings as settings
from nekopower.host import _copy_trees
from nekopower.host.paths import worktree_name
from nekopower.host.repo_docs import repository_root

REF_PREFIX = "refs/nekopower/worktrees/"
"""Refs this module keeps a worktree's commits under: ``<prefix><id>/start``,
where the next landing merges from (where the worktree started, then what last
landed, or the project snapshot conflicts were prepared against)."""

_FETCH_REF_PREFIX = "refs/nekopower/fetch/"
_PROTECTED_BRANCHES = frozenset({"main", "master"})
_SYNTHETIC_SNAPSHOT_MESSAGES = frozenset(
    {
        "nekopower: worktree start",
        "nekopower: worktree result",
        "nekopower: project before landing",
        "nekopower: snapshot",
        "nekopower: conflicts to resolve",
    }
)

IDENTITY = {
    "GIT_AUTHOR_NAME": "NekoPower",
    "GIT_AUTHOR_EMAIL": "nekopower@localhost",
    "GIT_COMMITTER_NAME": "NekoPower",
    "GIT_COMMITTER_EMAIL": "nekopower@localhost",
}

# Reasons ``probe`` gives for a directory a worktree cannot be made from.
GIT_MISSING = "git_missing"
GIT_TOO_OLD = "git_too_old"
GIT_FAILED = "git_failed"
NOT_A_REPOSITORY = "not_a_repository"
BARE_REPOSITORY = "bare_repository"
IGNORED_DIRECTORY = "ignored_directory"
SUBMODULES = "submodules"
OPERATION_IN_PROGRESS = "operation_in_progress"
NESTED_REPOSITORY = "nested_repository"
CONTENT_FILTERS = "content_filters"
MERGE_DRIVERS = "merge_drivers"
PARTIAL_CLONE = "partial_clone"
SPARSE_CHECKOUT = "sparse_checkout"
DIRECTORY_TOO_LARGE = "directory_too_large"
# ``create`` only.
DISK_SPACE = "disk_space"

# Files in a git directory that say an operation stopped half way.
_IN_PROGRESS = (
    ("MERGE_HEAD", "merge"),
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
    ("BISECT_LOG", "bisect"),
)

# Environment variables that would point git at another repository, index or
# configuration than the one asked for.
_REDIRECTING_ENV = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_PREFIX",
        "GIT_CONFIG",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
        "GIT_GRAFT_FILE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_QUARANTINE_PATH",
        "GIT_INTERNAL_SUPER_PREFIX",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        # These change how the pathspecs given below are read.
        "GIT_LITERAL_PATHSPECS",
        "GIT_GLOB_PATHSPECS",
        "GIT_NOGLOB_PATHSPECS",
        "GIT_ICASE_PATHSPECS",
        # These change the patch a landing writes, or where attributes
        # (line endings, filters) are read from.
        "GIT_DIFF_OPTS",
        "GIT_ATTR_SOURCE",
    }
)



class WorktreeError(Exception):
    """A worktree operation could not be done; nothing it was about to change was changed."""


class GitError(WorktreeError):
    """A git command failed or did not finish in time."""

    def __init__(self, args: Iterable[str], returncode: int | None, stderr: bytes) -> None:
        self.git_args = tuple(args)
        self.returncode = returncode
        self.stderr = stderr.decode("utf-8", "replace").strip()
        what = "timed out" if returncode is None else f"exited with {returncode}"
        super().__init__(f"git {' '.join(self.git_args[:2])} {what}: {self.stderr[:500]}")


class WorktreeUnavailable(WorktreeError):
    """No worktree can be made: ``reason`` is one of the reason keys above."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True)
class Probe:
    """Whether a worktree can be made for a directory.

    ``reason`` is None when it can, else one of the reason keys; ``detail``
    says more for a person. ``repo_root`` is the root of the repository's
    working tree and ``subpath`` the directory relative to it, with ``/``
    separators and ``""`` for the root itself; both are set whenever the
    repository was found.
    """

    reason: str | None
    detail: str = ""
    repo_root: Path | None = None
    subpath: str = ""
    git_version: tuple[int, int, int] | None = None
    kind: Literal["git", "copy"] | None = None

    @property
    def usable(self) -> bool:
        return self.reason is None


@dataclass(frozen=True)
class Snapshot:
    """What a working tree held, written as a commit nothing points to but a ref of ours (or nothing).

    ``skipped`` lists the files not under version control that were left out
    because each is larger than ``WORKTREE_UNTRACKED_MAX_BYTES``.
    """

    commit: str
    tree: str
    skipped: tuple[str, ...] = ()


@dataclass(frozen=True)
class Worktree:
    """A worktree this module made.

    ``id`` is the name of its directory; ``repo_root`` the user's repository
    it was made from; ``subpath`` where the project directory is inside it;
    ``start`` the commit the next landing merges from when the handle was
    made; landings move it on, and what they read is the ref it is kept in
    (``REF_PREFIX``), so an older handle does no harm there. ``token`` names
    the worktree made then: a handle kept past its removal does not remove
    another made since at the same path.
    """

    id: str
    repo_root: Path
    path: Path
    subpath: str
    start: str
    token: str = ""
    kind: Literal["git", "copy"] = "git"
    linked_dependencies: tuple[str, ...] = ()

    @property
    def workdir(self) -> Path:
        """Return the directory that stands for the project directory inside the worktree."""
        return self.path / self.subpath if self.subpath else self.path


@dataclass(frozen=True)
class FileChange:
    """A file a landing changed in the project directory; line counts are None for a binary file."""

    path: str
    status: Literal["added", "modified", "deleted"]
    added: int | None
    deleted: int | None


@dataclass(frozen=True)
class MergeResult:
    """What a landing did.

    ``status``:

    * ``landed``: every file in ``files`` was changed in the project
      directory and checked afterwards;
    * ``unchanged``: there was nothing to land;
    * ``conflict``: nothing was changed; ``conflicts`` lists the files that
      both sides changed in ways that do not merge, ``merged_tree`` and
      ``current`` are what ``prepare_conflicts`` needs;
    * ``failed``: the changes could not be written or did not read back as
      merged (``detail`` says which). Every file the attempt changed was put
      back, except the ones in ``left_changed``, which are as the failed
      attempt left them; their copies from before are then kept in
      ``kept_copies`` (``manifest.json`` there says which is which).

    ``skipped`` lists files in the worktree that were not carried over
    because they are not under version control and too large, or are a
    repository of their own. ``base`` is the start the merge was from.
    """

    status: Literal["landed", "unchanged", "conflict", "failed"]
    files: tuple[FileChange, ...] = ()
    lines_added: int = 0
    lines_deleted: int = 0
    conflicts: tuple[str, ...] = ()
    detail: str = ""
    left_changed: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    current: str = ""
    result: str = ""
    result_tree: str = ""
    merged_tree: str = ""
    base: str = ""
    kept_copies: str = ""
    branch: str = ""
    commit: str = ""
    remote: str = ""
    pr_url: str = ""
    pr_number: int | None = None


@dataclass
class ReconcileReport:
    """What ``reconcile`` did.

    ``removed``: worktrees removed; ``kept``: worktrees kept because they
    hold changes; ``failed``: worktrees that could not be looked at or
    removed, with why (left as they are); ``refs_removed``: refs of no
    worktree, removed.
    """

    removed: list[str] = field(default_factory=list)
    kept: list[Path] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)
    refs_removed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DiskCheck:
    """Free space where a worktree would go, against what it needs."""

    free: int
    needed: int

    @property
    def enough(self) -> bool:
        return self.free >= self.needed


# -- where a worktree starts --------------------------------------------------


class Start:
    """Where a new worktree starts: ``CurrentState``, ``FromRef``, or ``RemoteDefaultBranch``."""

    async def commit(self, manager: WorktreeManager, repo_root: Path) -> str:
        raise NotImplementedError


class CurrentState(Start):
    """Start from what the project directory holds now, uncommitted changes and new files included."""

    async def commit(self, manager: WorktreeManager, repo_root: Path) -> str:
        return (await manager._snapshot(repo_root, "nekopower: worktree start")).commit


@dataclass(frozen=True)
class FromRef(Start):
    """Start from a commit already in the repository, named by a ref or an object name. Nothing is fetched."""

    ref: str

    async def commit(self, manager: WorktreeManager, repo_root: Path) -> str:
        commit = await manager._resolve_commit(repo_root, self.ref)
        await manager._validate_commit_tree(repo_root, commit, self.ref)
        return commit


@dataclass(frozen=True)
class RemoteDefaultBranch(Start):
    """Start from a remote's default branch (or a named branch on it), fetching first when asked.

    Fetches into a temporary ref under ``refs/nekopower/fetch/`` and deletes
    that ref right away, so the user's local branches, index, and
    remote-tracking refs are never modified.
    """

    remote: str = "origin"
    branch: str | None = None
    fetch: bool = True

    async def commit(self, manager: WorktreeManager, repo_root: Path) -> str:
        commit, label = await manager._resolve_remote_start(repo_root, self.remote, self.branch, fetch=self.fetch)
        await manager._validate_commit_tree(repo_root, commit, label)
        return commit


# -- how a result lands -------------------------------------------------------


@dataclass(frozen=True)
class PRRequest:
    """Details passed to a pull-request creator when landing a worktree as a PR."""

    repo_root: Path
    remote: str
    branch: str
    base_branch: str
    title: str
    body: str
    draft: bool
    commit: str
    files: tuple[FileChange, ...] = ()


@dataclass(frozen=True)
class PRInfo:
    """Pull request created or updated by ``PullRequest`` landing."""

    url: str
    number: int | None = None


PRCreator = Callable[[PRRequest], Awaitable[PRInfo]]


class Landing:
    """How a worktree's result reaches the project: ``WorkingFiles``, ``BranchCommit``, or ``PullRequest``."""

    async def land(self, manager: WorktreeManager, worktree: Worktree, result: Snapshot) -> MergeResult:
        raise NotImplementedError


class WorkingFiles(Landing):
    """Merge the result into the project directory's working files, committing nothing."""

    async def land(self, manager: WorktreeManager, worktree: Worktree, result: Snapshot) -> MergeResult:
        return await manager._land_on_working_files(worktree, result)


@dataclass(frozen=True)
class BranchCommit(Landing):
    """Commit the worktree's changes onto a branch, optionally pushing it to a remote.

    Never updates the repository's currently checked-out branch, another
    worktree's checked-out branch, or a default branch; never force-pushes;
    and leaves the project's working files and index untouched.
    """

    branch: str = ""
    message: str = ""
    remote: str | None = None
    base_branch: str | None = None

    async def land(self, manager: WorktreeManager, worktree: Worktree, result: Snapshot) -> MergeResult:
        return await manager._land_on_branch(
            worktree,
            result,
            branch=self.branch,
            message=self.message,
            remote=self.remote,
            base_branch=self.base_branch,
        )


@dataclass(frozen=True)
class PullRequest(Landing):
    """Commit to a delivery branch, push it, and open a pull request (draft by default).

    Never pushes to a default or base branch, never force-pushes, and never
    merges or enables auto-merge. Pass ``create_pr`` to plug in a GitHub
    plugin or custom forge handler; otherwise uses the ``gh`` CLI.
    """

    title: str
    body: str = ""
    branch: str = ""
    base_branch: str | None = None
    remote: str = "origin"
    draft: bool = True
    commit_message: str = ""
    gh: str = "gh"
    create_pr: PRCreator | None = None

    async def land(self, manager: WorktreeManager, worktree: Worktree, result: Snapshot) -> MergeResult:
        return await manager._land_as_pr(worktree, result, self)


# -- the manager --------------------------------------------------------------


class WorktreeManager:
    """Makes, lands and removes worktrees; one per host.

    Snapshots, landings and every change to a repository's worktrees are
    done one at a time per repository (a lock per repository, kept by this
    manager on the event loop it is used on). Two managers, or two
    processes, do not wait for each other: one host has one manager.
    """

    def __init__(
        self,
        git: str = "git",
        *,
        allow_non_git: bool = True,
        link_dependencies: bool | None = None,
    ) -> None:
        self._git_program = git
        self._allow_non_git = allow_non_git
        self._link_dependencies = link_dependencies
        # A directory that does not exist: no hook of the user's runs.
        self._no_hooks = str(Path(tempfile.gettempdir()) / f"nekopower-no-hooks-{uuid.uuid4().hex}")
        self._version: tuple[int, int, int] | None | Literal[False] = False
        self._locks: dict[str, asyncio.Lock] = {}
        self._making: set[str] = set()

    # -- public ----------------------------------------------------------------

    async def probe(self, directory: Path | str, *, allow_non_git: bool | None = None) -> Probe:
        """Return whether a worktree or non-git working copy can be made for ``directory``; never raises."""
        use_copy = self._allow_non_git if allow_non_git is None else allow_non_git
        resolved, root = await asyncio.to_thread(_find_root, Path(directory))
        if not await asyncio.to_thread(resolved.is_dir):
            return Probe(NOT_A_REPOSITORY, f"{directory} is not a directory")
        version = await self.git_version()
        if root is not None:
            if version is None:
                return Probe(GIT_MISSING, f"{self._git_program!r} could not be run")
            minimum = tuple(settings.WORKTREE_GIT_MIN_VERSION)
            if version < minimum:
                return Probe(
                    GIT_TOO_OLD,
                    f"git {'.'.join(map(str, version))} is older than {'.'.join(map(str, minimum))}",
                    git_version=version,
                )
            return await self._probe_repository(Path(directory), version, resolved=resolved, root=root)
        # No .git found within search depth: check for a bare repository before copy fallback.
        if version is not None and await self._is_bare(resolved):
            return Probe(BARE_REPOSITORY, f"{resolved} is a bare repository", git_version=version)
        if not use_copy:
            if version is None:
                return Probe(GIT_MISSING, f"{self._git_program!r} could not be run")
            minimum = tuple(settings.WORKTREE_GIT_MIN_VERSION)
            if version < minimum:
                return Probe(
                    GIT_TOO_OLD,
                    f"git {'.'.join(map(str, version))} is older than {'.'.join(map(str, minimum))}",
                    git_version=version,
                )
            return Probe(NOT_A_REPOSITORY, f"{directory} is in no git repository", git_version=version)
        return await self._probe_copy_directory(resolved, version)

    async def remote_default_branch(self, directory: Path | str, remote: str = "origin", *, fetch: bool = False) -> str:
        """Return the default branch name of ``remote`` for the repository ``directory`` is in."""
        found = await self._usable(directory, qualify=False, allow_non_git=False)
        assert found.repo_root is not None
        return await self._discover_remote_default_branch(found.repo_root, remote, fetch=fetch)

    async def snapshot(self, directory: Path | str) -> Snapshot:
        """Write what the repository ``directory`` is in holds now as a commit; changes nothing the user sees."""
        found = await self._usable(directory, allow_non_git=False)
        assert found.repo_root is not None
        async with await self._lock(found.repo_root):
            return await self._snapshot(found.repo_root, "nekopower: snapshot")

    async def create(
        self,
        project_dir: Path | str,
        path: Path | str,
        start: Start | None = None,
        *,
        link_dependencies: bool | None = None,
    ) -> Worktree:
        """Make a worktree (or non-git working copy) of ``project_dir`` at ``path`` and return it.

        ``path`` must not exist yet (an empty directory is accepted) and must
        lie outside the repository/project; its last part becomes the worktree's id.
        Raises WorktreeUnavailable when the directory does not qualify or the
        disk is too full, WorktreeError when ``path`` cannot be used (or made)
        or the start has no such directory as ``project_dir``, GitError when
        git fails; nothing is left behind then.
        """
        git_only_start = start is not None and not isinstance(start, CurrentState)
        found = await self._usable(project_dir, allow_non_git=False if git_only_start else None)
        should_link = (
            (self._link_dependencies if self._link_dependencies is not None else settings.WORKTREE_DEPENDENCY_LINK_ENABLED)
            if link_dependencies is None
            else link_dependencies
        )
        target = await asyncio.to_thread(_absolute, Path(path))
        worktree_id = _worktree_id(target)
        if found.kind == "copy":
            project_root = await asyncio.to_thread(_absolute, Path(project_dir))
            await asyncio.to_thread(_check_target, target, project_root)
            claim = os.path.normcase(str(target))
            if claim in self._making:
                raise WorktreeError(f"a worktree is being made at {target}")
            self._making.add(claim)
            try:
                async with self._copy_lock(project_root):
                    return await self._create_copy(
                        project_root, target, worktree_id, link_dependencies=should_link
                    )
            finally:
                self._making.discard(claim)
        assert found.repo_root is not None
        repo_root = found.repo_root
        await asyncio.to_thread(_check_target, target, repo_root)
        ref = _start_ref(worktree_id)
        # Two repositories have two locks: one making at a target at a time.
        claim = os.path.normcase(str(target))
        if claim in self._making:
            raise WorktreeError(f"a worktree is being made at {target}")
        self._making.add(claim)
        try:
            async with await self._lock(repo_root):
                return await self._create(
                    repo_root,
                    found.subpath,
                    target,
                    worktree_id,
                    ref,
                    start or CurrentState(),
                    link_dependencies=should_link,
                )
        finally:
            self._making.discard(claim)

    async def _create(
        self,
        repo_root: Path,
        subpath: str,
        target: Path,
        worktree_id: str,
        ref: str,
        start: Start,
        *,
        link_dependencies: bool = True,
    ) -> Worktree:
        if await self._ref_exists(repo_root, ref):
            raise WorktreeError(f"a worktree named {worktree_id!r} already exists in {repo_root}")
        # One of the user's, its directory gone: cleaning up after a failed
        # make would take its registration for this one's.
        if any(_same_path(known, target) for known in await self._registered(repo_root)):
            raise WorktreeError(f"{target} is registered as a worktree of {repo_root} already")
        commit = await start.commit(self, repo_root)
        if subpath:  # the project's directory, which the worktree is worked in
            kind = await self._git(repo_root, "cat-file", "-t", f"{commit}:{subpath}", check=False)
            if kind.lines()[:1] != ["tree"]:
                raise WorktreeError(f"{commit} has no directory {subpath}: the worktree would have nowhere to work")
        disk = await self.check_disk(target, await self._checkout_size(repo_root, commit))
        if not disk.enough:
            raise WorktreeUnavailable(DISK_SPACE, f"{disk.free} bytes free, {disk.needed} needed")
        # Made here, at once (not merely found missing): a directory another
        # put there meanwhile is not taken for this one when cleaning up.
        # Waited for to the end; made, then cancelled, it goes again.
        claiming = asyncio.ensure_future(asyncio.to_thread(_claim_directory, target))
        cancelled = await _settle(claiming)
        failed = claiming.exception()
        if failed is not None:
            if cancelled:
                raise asyncio.CancelledError from failed
            raise WorktreeError(f"the worktree's directory {target} could not be made: {failed}") from failed
        if cancelled:
            if claiming.result():
                await _to_the_end(asyncio.to_thread(_unclaim_directory, target))
            raise asyncio.CancelledError
        existed = not claiming.result()
        token = uuid.uuid4().hex
        linked: tuple[str, ...] = ()
        try:
            await self._git(repo_root, "update-ref", ref, commit)
            await self._git(
                repo_root,
                "worktree",
                "add",
                "--detach",
                str(target),
                commit,
                timeout=settings.WORKTREE_GIT_WORK_SECONDS,
            )
            # Which directory it is, beside its registration: a removal
            # that stopped half way (its .git gone) is told from a
            # directory put there after the worktree went.
            admin = Path((await self._git(target, "rev-parse", "--absolute-git-dir")).lines()[0])
            # Waited for to the end: a late write could land on the
            # registration of another made by the same name after cleanup.
            await _to_the_end(asyncio.to_thread(_note_directory, admin, target, token))
            if link_dependencies and settings.WORKTREE_DEPENDENCY_DIRS:
                candidates = await self._find_ignored_dependency_dirs(repo_root)
                if candidates:
                    linked = await _to_the_end(
                        asyncio.to_thread(
                            _copy_trees.link_dependency_paths,
                            repo_root,
                            target,
                            candidates,
                            limit=settings.WORKTREE_DEPENDENCY_LINK_MAX,
                        )
                    )
                    if linked:
                        await _to_the_end(asyncio.to_thread(_note_links, admin, linked))
        except BaseException as error:
            # Nothing left behind, done to the end even when cancelled again.
            async def clean() -> None:
                try:
                    if linked:
                        await asyncio.to_thread(_copy_trees.unlink_dependencies, target, linked)
                    await self._forget(repo_root, target, worktree_id, remove_directory=not existed)
                    if existed:
                        await asyncio.to_thread(target.mkdir, exist_ok=True)
                except Exception as failed:
                    error.add_note(
                        f"cleaning up fell short ({failed}); {ref} or {target} may be left: remove() or reconcile()"
                    )

            cancelled = await _settle(asyncio.ensure_future(clean()))
            if cancelled and not isinstance(error, asyncio.CancelledError):
                raise _cancelled_after(error) from error
            raise
        return Worktree(worktree_id, repo_root, target, subpath, commit, token, kind="git", linked_dependencies=linked)

    async def open(self, project_dir: Path | str, path: Path | str) -> Worktree:
        """Return the worktree or non-git copy at ``path`` made earlier for ``project_dir``, after a restart for instance.

        The project need not qualify for a new worktree now (a merge may be
        under way); it must be in the repository (or match the directory) the
        worktree belongs to. Raises ValueError for a name no worktree can have.
        """
        target = await asyncio.to_thread(_absolute, Path(path))
        worktree_id = _worktree_id(target)
        resolved_project = await asyncio.to_thread(_absolute, Path(project_dir))
        copy_meta = await asyncio.to_thread(_copy_trees.read_copy_meta, target)
        if copy_meta is not None:
            if not await asyncio.to_thread(resolved_project.is_dir):
                raise WorktreeError(f"{project_dir} is not a directory")
            if not _copy_trees.same_path(str(copy_meta.get("project_dir") or ""), resolved_project):
                raise WorktreeError(f"{target} is not a working copy of {project_dir}")
            async with self._copy_lock(resolved_project):
                meta = await asyncio.to_thread(_copy_trees.read_copy_meta, target)
                if meta is None or not await asyncio.to_thread(target.is_dir):
                    raise WorktreeError(f"{target} is not a working copy of {project_dir}")
                now_id = await asyncio.to_thread(_directory_identity, target)
                if _copy_trees.copy_matches_identity(target, meta, now_id) == "theirs":
                    raise WorktreeError(f"{target} is not the working copy originally made for {project_dir}")
                return Worktree(
                    worktree_id,
                    resolved_project,
                    target,
                    "",
                    str(meta.get("start") or ""),
                    str(meta.get("token") or ""),
                    kind="copy",
                    linked_dependencies=tuple(str(item) for item in (meta.get("links") or ())),
                )
        found = await self._usable(project_dir, qualify=False, allow_non_git=False)
        assert found.repo_root is not None
        # Under the lock: one being made (or cleaned up after failing) is
        # seen whole or not at all.
        async with await self._lock(found.repo_root):
            registered = any(_same_path(known, target) for known in await self._registered(found.repo_root))
            common = Path(
                (await self._git(found.repo_root, "rev-parse", "--path-format=absolute", "--git-common-dir")).lines()[0]
            )
            if not registered:
                # Its registration may have gone (removed by hand); its .git
                # file still says whose it is.
                registered = await asyncio.to_thread(_points_into, target, common)
            if not registered or not await asyncio.to_thread(target.is_dir):
                raise WorktreeError(f"{target} is not a worktree of {found.repo_root}")
            start = await self._resolve_commit(found.repo_root, _start_ref(worktree_id))
            token = await asyncio.to_thread(_noted_token, common, target) or ""
            links = await asyncio.to_thread(_noted_links, common, target)
        return Worktree(
            worktree_id, found.repo_root, target, found.subpath, start, token, kind="git", linked_dependencies=links
        )

    async def merge_back(self, worktree: Worktree, landing: Landing | None = None) -> MergeResult:
        """Land what was done in ``worktree``; see ``MergeResult`` for the outcomes. Raises GitError when git fails."""
        if worktree.kind == "copy":
            if landing is not None and not isinstance(landing, WorkingFiles):
                return MergeResult("failed", detail="branch and pull request landing require a git repository")
            async with self._copy_lock(worktree.repo_root):
                return await self._land_copy(worktree)
        async with await self._lock(worktree.repo_root):
            # Its own conflict markers would land as text.
            unfinished = await self._unfinished(worktree.path)
            if unfinished:
                return MergeResult("failed", detail=f"{unfinished} in the worktree; finish or abort it first")
            # Made one since the worktree was: merging would read objects it
            # may lack, and fetch them.
            if await self._partial_clone(worktree.repo_root):
                return MergeResult(
                    "failed", detail="the repository has become a partial clone: merging could fetch from its remote"
                )
            start = await self._start(worktree)
            links = await self._worktree_links(worktree.repo_root, worktree.path, worktree.linked_dependencies)
            if links:
                result = await self._snapshot(worktree.path, "nekopower: worktree result", start, linked=links)
            else:
                result = await self._snapshot(worktree.path, "nekopower: worktree result", start)
            return await (landing or WorkingFiles()).land(self, worktree, result)

    async def _touched_by_preparing(self, root: Path, commits: list[str | None]) -> list[str]:
        """Return the paths that differ between any two neighbours of ``commits`` (None ones left out)."""
        named = [commit for commit in commits if commit]
        touched: set[str] = set()
        for older, newer in zip(named, named[1:]):
            if older == newer:
                continue
            listed = await self._git(
                root, "diff-tree", "-r", "-z", "--name-only", "--no-renames", older, newer,
                timeout=settings.WORKTREE_GIT_WORK_SECONDS,
            )
            touched.update(_decode(name) for name in listed.stdout.split(b"\0") if name)
        return sorted(touched)

    async def _unfinished(self, root: Path) -> str | None:
        """Return what git operation is half done in ``root`` (or that its index has unmerged entries), or None."""
        git_dir = (await self._git(root, "rev-parse", "--absolute-git-dir")).lines()[0]
        operation = await asyncio.to_thread(_operation_in_progress, Path(git_dir))
        if operation:
            return f"a {operation} is in progress"
        unmerged = await self._git(root, "ls-files", "-z", "--unmerged")
        return "unmerged files are" if unmerged.stdout else None

    async def _put_start_back(self, worktree: Worktree, start: str, error: BaseException) -> None:
        """Set a worktree's start ref back to ``start`` even when cancelled meanwhile; say so on ``error`` if it fails."""
        back = await _to_the_end(
            self._git(worktree.repo_root, "update-ref", _start_ref(worktree.id), start, check=False)
        )
        if not back.ok:
            error.add_note(
                f"the start of worktree {worktree.id} could not be set back to {start} "
                f"({_failure('update-ref', back.stderr)}); merging it again would take what was not landed for landed"
            )

    async def _start(self, worktree: Worktree) -> str:
        """Return where the next landing of ``worktree`` merges from: its ref, else the handle's."""
        return await self._try_resolve(worktree.repo_root, _start_ref(worktree.id)) or worktree.start

    async def prepare_conflicts(self, worktree: Worktree, conflict: MergeResult) -> Worktree:
        """Put the half-done merge of a ``conflict`` result into the worktree, with conflict markers.

        The worktree then holds the merge as far as it went, and its start
        becomes the project snapshot that merge was against, so that the
        next ``merge_back`` lands only what is resolved from here on. Raises
        WorktreeError when the worktree changed since the merge was tried.
        Returns the worktree with its new start.
        """
        if conflict.status != "conflict":
            raise WorktreeError("only a conflicting merge can be prepared")
        if worktree.kind == "copy":
            async with self._copy_lock(worktree.repo_root):
                return await self._prepare_copy_conflicts(worktree, conflict)
        async with await self._lock(worktree.repo_root):
            start = await self._start(worktree)
            if start != conflict.base:
                raise WorktreeError("the worktree landed or was prepared since this merge was tried; merge again")
            # A checkout would end what is half done there (a merge's
            # MERGE_HEAD), which putting back cannot restore.
            unfinished = await self._unfinished(worktree.path)
            if unfinished:
                raise WorktreeError(f"{unfinished} in the worktree; finish or abort that, then merge again")
            links = await self._worktree_links(worktree.repo_root, worktree.path, worktree.linked_dependencies)
            active = await asyncio.to_thread(_copy_trees.active_links, worktree.path, links)
            if active:
                now = await self._snapshot(worktree.path, "nekopower: worktree result", start, linked=active)
            else:
                now = await self._snapshot(worktree.path, "nekopower: worktree result", start)
            if now.tree != conflict.result_tree:
                raise WorktreeError("the worktree changed after the merge was tried; merge again")
            # Checking the conflicts out replaces the index: a version only
            # staged there would be gone.
            if await self._staged_only(worktree.path, start, now.tree):
                raise WorktreeError(
                    "the worktree's index holds a version its files do not (staged, then changed in the file); "
                    "commit or unstage it, then merge again"
                )
            listed = await self._git(worktree.path, "ls-tree", "-r", "-z", "--name-only", conflict.merged_tree)
            names = [name for name in listed.stdout.split(b"\0") if name]
            # A filter and a file it applies to may meet only in the merge; a
            # checkout would run it.
            programs = await self._programs(worktree.path, names, source=conflict.merged_tree)
            if programs:
                raise WorktreeUnavailable(*programs)
            # The checkout writes anew what the merge has and the worktree's
            # snapshot has not. Whatever stands at such a path, or a file
            # where it needs a directory, is the worktree's alone (ignored,
            # left out as too large, a repository of its own): it would be
            # overwritten, so nothing is checked out.
            listed_held = await self._git(worktree.path, "ls-tree", "-r", "-z", "--name-only", now.tree)
            held = {_decode(name) for name in listed_held.stdout.split(b"\0") if name}
            merged_names = {_decode(name) for name in names}
            written = sorted(merged_names - held)
            nested = {name.rstrip("/") for name in now.skipped if name.endswith("/")}
            hit = await asyncio.to_thread(_in_the_way, worktree.path, written, held, nested, set(active))
            if hit:
                raise WorktreeError(
                    f"preparing would overwrite what only the worktree has (ignored, too large, or a "
                    f"repository of its own): {', '.join(hit)}"
                )
            half_done = await self._commit_tree(
                worktree.path, conflict.merged_tree, conflict.current, "nekopower: conflicts to resolve"
            )
            # What its own HEAD and index were (staged work included), to put
            # back as they were should preparing fail.
            own_head = await self._try_resolve(worktree.path, "HEAD")
            # The branch it was on, if any: preparing detaches from it.
            on_branch = await self._git(
                worktree.path, "symbolic-ref", "--quiet", "HEAD", check=False,
                timeout=settings.WORKTREE_GIT_QUERY_SECONDS,
            )
            own_branch = on_branch.lines()[0] if on_branch.ok and on_branch.lines() else None
            index = Path(
                (await self._git(worktree.path, "rev-parse", "--path-format=absolute", "--git-path", "index")).lines()[0]
            )
            saved = await _temporary_directory("nekopower-index-")
            # The bytes of every file the checkouts may write: checking the
            # result out again gives back its content, not its bytes (line
            # endings an eol rule converts, for one).
            try:
                touched = await self._touched_by_preparing(
                    worktree.path, [start, own_head, conflict.result, half_done]
                )
                # And what differs from the index only in bytes git reads
                # away (line endings an eol rule converts): no tree shows
                # it, but the forced checkout writes it all the same.
                # diff-files only reads the index.
                dirty = await self._git(
                    worktree.path, "diff-files", "--name-only", "-z", timeout=settings.WORKTREE_GIT_WORK_SECONDS
                )
                touched = sorted({*touched, *(_decode(name) for name in dirty.stdout.split(b"\0") if name)})
                (saved / "files").mkdir()
                # Waited for to the end: what a copy still writes could not be
                # removed below (Windows), and would be left unreported.
                files_saved = await _to_the_end(
                    asyncio.to_thread(_back_up, worktree.path, touched, saved / "files")
                )
                had_index = await _to_the_end(asyncio.to_thread(_copy_index, index, saved / "index"))
            except BaseException:
                await _to_the_end(asyncio.to_thread(_remove_tree, saved))
                raise
            keep_saved = False
            relinked: tuple[str, ...] = active
            try:
                # Unlink dependency junctions/symlinks before forced checkout so git never traverses them.
                if active:
                    await _to_the_end(asyncio.to_thread(_copy_trees.unlink_dependencies, worktree.path, active))
                # The host's own worktree, not the user's checkout.
                await self._git(
                    worktree.path,
                    "checkout",
                    "--force",
                    "--detach",
                    "--no-recurse-submodules",
                    half_done,
                    timeout=settings.WORKTREE_GIT_WORK_SECONDS,
                )
                # What the merge deletes, among what the snapshot held, goes
                # too: a checkout leaves a file its index never had (one
                # landed from here earlier). The result snapshot puts it back
                # should preparing fail below.
                # Waited for to the end: putting back must not race it.
                await _to_the_end(asyncio.to_thread(_remove_files, worktree.path, sorted(held - merged_names)))
                if active:
                    relinked = await _to_the_end(
                        asyncio.to_thread(
                            _copy_trees.link_dependency_paths,
                            worktree.repo_root,
                            worktree.path,
                            active,
                            limit=settings.WORKTREE_DEPENDENCY_LINK_MAX,
                        )
                    )
                    admin = Path((await self._git(worktree.path, "rev-parse", "--absolute-git-dir")).lines()[0])
                    await _to_the_end(asyncio.to_thread(_note_links, admin, relinked))
                await self._git(worktree.repo_root, "update-ref", _start_ref(worktree.id), conflict.current)
            except BaseException as error:
                # Its files as they were (the result snapshot holds them) and
                # its start as it was, so the same conflict can be prepared
                # again: one piece no further cancellation cuts.
                async def put_back() -> None:
                    nonlocal keep_saved
                    # Each step is tried whatever the one before did (a git
                    # that times out or cannot start included); what fails
                    # is said, and the index's copy is kept.
                    if active:
                        with contextlib.suppress(Exception):
                            await asyncio.to_thread(_copy_trees.unlink_dependencies, worktree.path, active)
                    try:
                        back = await self._git(
                            worktree.path,
                            "checkout",
                            "--force",
                            "--detach",
                            "--no-recurse-submodules",
                            conflict.result,
                            check=False,
                            timeout=settings.WORKTREE_GIT_WORK_SECONDS,
                        )
                        if not back.ok:
                            error.add_note(
                                f"the worktree could not be put back: {_failure('checkout', back.stderr)}"
                            )
                    except Exception as failed:
                        error.add_note(f"the worktree could not be put back: {failed}")
                    try:
                        left = await asyncio.to_thread(_restore, worktree.path, files_saved)
                    except Exception as failed:
                        left = [f"({failed})"]
                    if left:
                        keep_saved = True
                        error.add_note(
                            f"files of the worktree could not be put back byte for byte: {', '.join(left)}; "
                            f"what they were is kept in {saved / 'files'} (see manifest.json)"
                        )
                    if active:
                        try:
                            restored_links = await asyncio.to_thread(
                                _copy_trees.link_dependency_paths,
                                worktree.repo_root,
                                worktree.path,
                                active,
                                limit=settings.WORKTREE_DEPENDENCY_LINK_MAX,
                            )
                            admin = Path((await self._git(worktree.path, "rev-parse", "--absolute-git-dir")).lines()[0])
                            await asyncio.to_thread(_note_links, admin, restored_links)
                            missing_links = [rel for rel in active if rel not in restored_links]
                            if missing_links:
                                error.add_note(
                                    f"dependency links could not be restored: {', '.join(missing_links)}"
                                )
                        except Exception as failed:
                            error.add_note(f"dependency links could not be restored: {failed}")
                    # Back on its branch (which preparing never moved), or at
                    # its commit when it was detached.
                    was = own_branch or own_head
                    if was:
                        try:
                            if own_branch:
                                head = await self._git(
                                    worktree.path, "symbolic-ref", "HEAD", own_branch, check=False
                                )
                            else:
                                head = await self._git(
                                    worktree.path, "update-ref", "--no-deref", "HEAD", own_head, check=False
                                )
                            if not head.ok:
                                how = "symbolic-ref" if own_branch else "update-ref"
                                error.add_note(f"the worktree's HEAD could not be put back to {was}: "
                                               f"{_failure(how, head.stderr)}")
                        except Exception as failed:
                            error.add_note(f"the worktree's HEAD could not be put back to {was}: {failed}")
                    if had_index:
                        try:
                            await asyncio.to_thread(_put_index_back, saved / "index", index)
                        except Exception as failed:
                            keep_saved = True
                            error.add_note(
                                f"the worktree's index could not be put back ({failed}); "
                                f"what it was is kept in {saved / 'index'}"
                            )
                    else:  # it had none: the checkouts made one
                        try:
                            await asyncio.to_thread(_remove_index, index)
                        except Exception as failed:
                            error.add_note(f"the index the checkout made could not be removed from {index}: {failed}")
                    try:
                        await self._put_start_back(worktree, start, error)
                    except Exception as failed:
                        error.add_note(f"the start of worktree {worktree.id} could not be set back to {start}: {failed}")

                putting = asyncio.ensure_future(put_back())
                cancelled = await _settle(putting)
                if putting.exception() is not None:
                    error.add_note(f"putting the worktree back failed: {putting.exception()}")
                if cancelled and not isinstance(error, asyncio.CancelledError):
                    raise _cancelled_after(error) from error
                raise
            finally:
                # Not waited for: once prepared, no cancellation can come in
                # between that and the result.
                if not keep_saved:
                    asyncio.get_running_loop().run_in_executor(None, _remove_tree, saved)
        return replace(worktree, start=conflict.current, linked_dependencies=relinked)

    async def has_changes(self, worktree: Worktree) -> bool:
        """Return whether the worktree holds anything not landed yet; False when its directory is gone."""
        if worktree.kind == "copy":
            async with self._copy_lock(worktree.repo_root):
                if not await asyncio.to_thread(worktree.path.is_dir):
                    return False
                return await asyncio.to_thread(
                    _copy_trees.copy_has_changes,
                    worktree.path,
                    dependency_dirs=settings.WORKTREE_DEPENDENCY_DIRS,
                    max_file_bytes=settings.WORKTREE_UNTRACKED_MAX_BYTES,
                )
        async with await self._lock(worktree.repo_root):
            # Under the lock: a removal queued before this one is done by now.
            if not await asyncio.to_thread(worktree.path.is_dir):
                return False
            links = await self._worktree_links(worktree.repo_root, worktree.path, worktree.linked_dependencies)
            return await self._has_changes(worktree.repo_root, worktree.path, worktree.id, worktree.start, linked=links)

    async def has_pending_delivery(self, worktree: Worktree) -> bool:
        """Return whether a git worktree has a pushed branch awaiting retryable PR creation.

        Propagates git or filesystem read errors so cleanup paths stay fail-closed and keep the worktree.
        """
        if worktree.kind == "copy":
            return False
        async with await self._lock(worktree.repo_root):
            if not await asyncio.to_thread(worktree.path.is_dir):
                return False
            common = Path(
                (await self._git(worktree.repo_root, "rev-parse", "--path-format=absolute", "--git-common-dir")).lines()[0]
            )
            delivery = await asyncio.to_thread(_noted_delivery, common, worktree.path, strict=True)
            return bool(delivery.get("pr_pending"))

    async def remove(self, worktree: Worktree) -> None:
        """Remove the worktree, its registration and its refs, whatever it holds; fine when already gone.

        A directory at its path the repository has no registration for,
        or known to be another put there after the worktree went, is left
        alone. One whose ``.git`` went and that cannot be told from such
        another (where the file system gives no birth time) is left too,
        with its registration and refs, and WorktreeError says so.
        """
        if worktree.kind == "copy":
            async with self._copy_lock(worktree.repo_root):
                await self._remove_copy(worktree.path, token=worktree.token)
            return
        async with await self._lock(worktree.repo_root):
            await self._forget(
                worktree.repo_root, worktree.path, worktree.id, remove_directory=False, token=worktree.token
            )

    async def reconcile(
        self, project_dir: Path | str, base_dir: Path | str, known: Iterable[str]
    ) -> ReconcileReport:
        """Remove what this module left behind for ``project_dir`` that nothing knows of.

        Handles both git worktrees (when ``project_dir`` is in a git repository)
        and non-git working copies in ``base_dir``. Directories not known to
        belong to ``project_dir`` are left alone.
        """
        known_ids = set(known)
        base = await asyncio.to_thread(_absolute, Path(base_dir))
        resolved_project = await asyncio.to_thread(_absolute, Path(project_dir))
        report = ReconcileReport()

        # Reconcile non-git working copies for this project_dir first.
        async with self._copy_lock(resolved_project):
            await self._reconcile_copies(resolved_project, base, known_ids, report)

        try:
            found = await self._usable(project_dir, qualify=False, allow_non_git=False)
        except WorktreeUnavailable:
            if await asyncio.to_thread(resolved_project.is_dir) and self._allow_non_git:
                return report
            raise
        assert found.repo_root is not None
        repo_root = found.repo_root
        async with await self._lock(repo_root):
            registered = await self._registered(repo_root)
            # A ref is left alone while a worktree of that name exists anywhere.
            registered_ids = {path.name for path in registered}
            common = Path((await self._git(repo_root, "rev-parse", "--path-format=absolute", "--git-common-dir")).lines()[0])
            for path in registered:
                if not _same_path(path.parent, base) or path.name in known_ids:
                    continue
                try:
                    start = await self._try_resolve(repo_root, _start_ref(path.name))
                    if start is None:
                        continue  # no ref of ours: not known to be one of ours
                    links = await asyncio.to_thread(_noted_links, common, path)
                    if await asyncio.to_thread(path.is_dir) and await self._has_changes(
                        repo_root, path, path.name, start, linked=links
                    ):
                        report.kept.append(path)
                        continue
                    await self._forget(repo_root, path, path.name, remove_directory=False)
                except (WorktreeError, OSError) as error:
                    report.failed.append((path, str(error)))
                    continue
                report.removed.append(path.name)
            for worktree_id in await self._ref_ids(repo_root):
                if worktree_id in known_ids or worktree_id in registered_ids:
                    continue
                left_over = base / worktree_id
                if await asyncio.to_thread(_points_into, left_over, common):
                    # Its registration went (a removal git gave up half way),
                    # its directory did not: the ref is how it is found again.
                    report.failed.append((left_over, "its registration is gone but its directory is not; remove it"))
                    continue
                try:
                    await self._delete_refs(repo_root, worktree_id)
                except WorktreeError as error:
                    report.failed.append((Path(f"{REF_PREFIX}{worktree_id}"), str(error)))
                    continue
                report.refs_removed.append(worktree_id)
        return report

    async def check_disk(self, path: Path | str, checkout_bytes: int) -> DiskCheck:
        """Return the free space where ``path`` would be made against what a checkout of ``checkout_bytes`` needs."""
        free = await asyncio.to_thread(_free_space, Path(path))
        needed = max(int(settings.WORKTREE_FREE_SPACE_FACTOR * checkout_bytes), settings.WORKTREE_MIN_FREE_BYTES)
        return DiskCheck(free, needed)

    async def git_version(self) -> tuple[int, int, int] | None:
        """Return the version of git, or None when it cannot be run.

        An answer is remembered, and so is git not being there at all; a
        failure that may pass (a timeout on a busy machine) is not.
        """
        if self._version is False:
            try:
                run = await self._run(None, ["--version"], timeout=settings.WORKTREE_GIT_QUERY_SECONDS)
            except GitError as error:
                if isinstance(error.__cause__, FileNotFoundError):
                    self._version = None
                return None
            self._version = parse_git_version(run.decode())
        return self._version

    # -- probing ---------------------------------------------------------------

    async def _usable(
        self,
        directory: Path | str,
        *,
        qualify: bool = True,
        allow_non_git: bool | None = None,
    ) -> Probe:
        """Return the probe of ``directory``, raising WorktreeUnavailable when it is not usable.

        With ``qualify`` False only the repository is looked for (git runs,
        the directory is in a work tree, not a bare one): enough to find or
        clean up worktrees made earlier, whatever state the project is in now.
        """
        if qualify:
            found = await self.probe(directory, allow_non_git=allow_non_git)
        else:
            version = await self.git_version()
            found = (
                Probe(GIT_MISSING, f"{self._git_program!r} could not be run")
                if version is None
                else await self._probe_repository(Path(directory), version, qualify=False)
            )
        if not found.usable:
            raise WorktreeUnavailable(str(found.reason), found.detail)
        return found

    async def _probe_repository(
        self,
        directory: Path,
        version: tuple[int, int, int],
        qualify: bool = True,
        *,
        resolved: Path | None = None,
        root: Path | None = None,
    ) -> Probe:
        def unusable(reason: str, detail: str = "", root_path: Path | None = None, subpath: str = "") -> Probe:
            return Probe(reason, detail, root_path, subpath, version)

        if resolved is None:
            resolved, root = await asyncio.to_thread(_find_root, directory)
        if not await asyncio.to_thread(resolved.is_dir):
            return unusable(NOT_A_REPOSITORY, f"{directory} is not a directory")
        if root is None:
            # A bare repository has no .git entry to find: git is asked.
            if await self._is_bare(resolved):
                return unusable(BARE_REPOSITORY, f"{resolved} is a bare repository")
            return unusable(NOT_A_REPOSITORY, f"{directory} is in no git repository")
        try:
            facts = await self._git(
                root, "rev-parse", "--path-format=absolute", "--is-bare-repository", "--absolute-git-dir", "--git-common-dir"
            )
            bare, git_dir, common_dir = facts.lines()[:3]
            if bare == "true":
                return unusable(BARE_REPOSITORY, f"{root} is a bare repository")
            top = Path((await self._git(root, "rev-parse", "--show-toplevel")).lines()[0])
        except (GitError, ValueError) as error:
            return unusable(GIT_FAILED, str(error))
        subpath = _relative(resolved, top)
        if subpath is None:
            return unusable(NOT_A_REPOSITORY, f"{directory} is outside {top}")
        if not qualify:
            return Probe(None, "", top, subpath, version, kind="git")
        try:
            found = await self._qualify(top, subpath, Path(git_dir))
        except GitError as error:
            return unusable(GIT_FAILED, str(error), top, subpath)
        return found if found is not None else Probe(None, "", top, subpath, version, kind="git")

    async def _probe_copy_directory(
        self, resolved: Path, version: tuple[int, int, int] | None
    ) -> Probe:
        """Validate a non-git directory for copy-based parallel working copies."""
        try:
            await asyncio.to_thread(
                _copy_trees.scan_copy_directory,
                resolved,
                dependency_dirs=settings.WORKTREE_DEPENDENCY_DIRS,
                max_file_bytes=settings.WORKTREE_UNTRACKED_MAX_BYTES,
                max_files=settings.WORKTREE_COPY_MAX_FILES,
                max_total_bytes=settings.WORKTREE_COPY_MAX_BYTES,
                hash_files=False,
            )
        except _copy_trees.CopyTooLarge as exc:
            return Probe(DIRECTORY_TOO_LARGE, str(exc), git_version=version)
        except OSError as exc:
            return Probe(NOT_A_REPOSITORY, f"{resolved} cannot be read: {exc}", git_version=version)
        return Probe(None, "", repo_root=None, subpath="", git_version=version, kind="copy")

    async def _validate_commit_tree(self, repo_root: Path, commit: str, label: str) -> None:
        """Ensure ``commit`` contains no submodules, content filters, or custom merge drivers."""
        listed = await self._git(repo_root, "ls-tree", "-r", "-z", commit)
        entries = [entry.split(b"\t", 1) for entry in listed.stdout.split(b"\0") if entry]
        if any(info.startswith(b"160000 ") for info, _name in entries):
            raise WorktreeUnavailable(SUBMODULES, f"{label} has submodules")
        programs = await self._programs(repo_root, [name for _info, name in entries], source=commit)
        if programs:
            raise WorktreeUnavailable(programs[0], f"in {label}: {programs[1]}")

    async def _ensure_remote(self, repo_root: Path, remote: str) -> None:
        name = remote.strip()
        if not name or name.startswith("-"):
            raise WorktreeError(f"invalid remote name: {remote!r}")
        found = await self._git(
            repo_root, "remote", "get-url", "--", name, check=False, timeout=settings.WORKTREE_GIT_QUERY_SECONDS
        )
        if not found.ok:
            raise WorktreeError(f"remote {name!r} is not configured in {repo_root}")

    async def _validate_branch_name(self, repo_root: Path, branch: str) -> str:
        cleaned = branch.strip()
        if not cleaned or cleaned.startswith("-") or any(ch.isspace() for ch in cleaned):
            raise WorktreeError(f"invalid branch name: {branch!r}")
        checked = await self._git(
            repo_root, "check-ref-format", "--branch", cleaned, check=False, timeout=settings.WORKTREE_GIT_QUERY_SECONDS
        )
        if not checked.ok:
            raise WorktreeError(f"invalid branch name: {branch!r}")
        return cleaned

    async def _discover_remote_url_default_branch(self, repo_root: Path, target: str) -> str | None:
        """Query ``target`` (remote name or URL) via ``git ls-remote`` and return its default branch name if found."""
        queried = await self._git(
            repo_root,
            "ls-remote",
            "--symref",
            "--",
            target,
            "HEAD",
            timeout=settings.WORKTREE_PR_TIMEOUT_SECONDS,
        )
        head_oid = ""
        for line in queried.lines():
            if line.startswith("ref: ") and "\t" in line:
                ref_part, sym = line[len("ref: ") :].split("\t", 1)
                if sym.strip() == "HEAD" and ref_part.startswith("refs/heads/"):
                    return ref_part[len("refs/heads/") :]
            elif "\t" in line:
                oid_part, ref_name = line.split("\t", 1)
                if ref_name.strip() == "HEAD":
                    head_oid = oid_part.strip()
        if head_oid:
            heads = await self._git(
                repo_root,
                "ls-remote",
                "--heads",
                "--",
                target,
                timeout=settings.WORKTREE_PR_TIMEOUT_SECONDS,
            )
            matched = [
                ref_name[len("refs/heads/") :]
                for line in heads.lines()
                if "\t" in line
                for oid_part, ref_name in (line.split("\t", 1),)
                if oid_part.strip() == head_oid and ref_name.startswith("refs/heads/")
            ]
            for preferred in ("main", "master"):
                if preferred in matched:
                    return preferred
            if len(matched) == 1:
                return matched[0]
        return None

    async def _discover_remote_default_branch(self, repo_root: Path, remote: str, *, fetch: bool) -> str:
        await self._ensure_remote(repo_root, remote)
        prefix = f"refs/remotes/{remote}/"
        if fetch:
            discovered = await self._discover_remote_url_default_branch(repo_root, remote)
            if discovered:
                return discovered
        local_sym = await self._git(
            repo_root,
            "symbolic-ref",
            "--quiet",
            f"refs/remotes/{remote}/HEAD",
            check=False,
            timeout=settings.WORKTREE_GIT_QUERY_SECONDS,
        )
        if local_sym.ok and local_sym.lines():
            target = local_sym.lines()[0]
            if target.startswith(prefix):
                return target[len(prefix) :]
        for candidate in ("main", "master"):
            if await self._try_resolve(repo_root, f"{prefix}{candidate}") is not None:
                return candidate
        raise WorktreeError(f"could not determine the default branch of remote {remote!r}")

    async def _resolve_remote_start(
        self, repo_root: Path, remote: str, branch: str | None, *, fetch: bool
    ) -> tuple[str, str]:
        await self._ensure_remote(repo_root, remote)
        if branch is not None:
            resolved_branch = await self._validate_branch_name(repo_root, branch)
        else:
            resolved_branch = await self._discover_remote_default_branch(repo_root, remote, fetch=fetch)
        label = f"{remote}/{resolved_branch}"
        if not fetch:
            commit = await self._resolve_commit(repo_root, f"refs/remotes/{remote}/{resolved_branch}")
            return commit, label
        tmp_ref = f"{_FETCH_REF_PREFIX}{uuid.uuid4().hex}"
        try:
            await self._git(
                repo_root,
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                "--refmap=",
                "--quiet",
                "--",
                remote,
                f"+refs/heads/{resolved_branch}:{tmp_ref}",
                timeout=settings.WORKTREE_PR_TIMEOUT_SECONDS,
            )
            commit = await self._resolve_commit(repo_root, tmp_ref)
        finally:
            await _to_the_end(self._git(repo_root, "update-ref", "-d", tmp_ref, check=False))
        return commit, label

    async def _find_ignored_dependency_dirs(self, repo_root: Path) -> list[str]:
        """Return git-ignored dependency directory paths under ``repo_root`` without recursing inside them."""
        dep_names = frozenset(settings.WORKTREE_DEPENDENCY_DIRS)
        if not dep_names:
            return []
        listed = await self._git(
            repo_root,
            "ls-files",
            "-z",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--directory",
            "--no-empty-directory",
            check=False,
            timeout=settings.WORKTREE_GIT_WORK_SECONDS,
        )
        if not listed.ok:
            return []
        found: list[str] = []
        for raw in listed.stdout.split(b"\0"):
            if not raw:
                continue
            rel = _decode(raw).rstrip("/")
            if not rel:
                continue
            if rel.rsplit("/", 1)[-1] in dep_names:
                found.append(rel)
        return sorted(set(found))

    async def _worktree_links(
        self, repo_root: Path, worktree_path: Path, hinted: Iterable[str] = ()
    ) -> tuple[str, ...]:
        """Return recorded dependency links for a git worktree (from admin note and handle)."""
        combined = set(hinted)
        with contextlib.suppress(GitError, OSError):
            common = Path(
                (await self._git(repo_root, "rev-parse", "--path-format=absolute", "--git-common-dir")).lines()[0]
            )
            noted = await asyncio.to_thread(_noted_links, common, worktree_path)
            combined.update(noted)
        return tuple(sorted(combined))

    async def _is_bare(self, directory: Path) -> bool:
        try:
            asked = await self._git(
                directory, "rev-parse", "--is-bare-repository", check=False,
                timeout=settings.WORKTREE_GIT_QUERY_SECONDS,
            )
        except GitError:
            return False
        return asked.ok and asked.lines()[:1] == ["true"]

    async def _partial_clone(self, root: Path) -> bool:
        """Return whether the repository is a partial clone.

        Objects missing from one would be fetched when read: every git the
        host runs is told not to (GIT_NO_LAZY_FETCH), and a partial clone is
        not taken on, so nothing is left wanting an object it lacks.
        """
        extension = await self._git(root, "config", "--get", "extensions.partialClone", check=False)
        promisors = await self._git(
            root, "config", "--type=bool", "--get-regexp", r"^remote\..*\.promisor$", check=False
        )
        return bool(extension.decode().strip()) or any(line.endswith(" true") for line in promisors.lines())

    async def _qualify(self, top: Path, subpath: str, git_dir: Path) -> Probe | None:
        """Return why a repository's work tree does not qualify, or None when it does."""

        def unusable(reason: str, detail: str) -> Probe:
            return Probe(reason, detail, top, subpath, self._version or None)

        if subpath.split("/", 1)[0] == ".git":
            return unusable(NOT_A_REPOSITORY, f"{subpath} is inside the repository's git directory")
        if subpath and (await self._git(top, "check-ignore", "-q", "--", subpath, check=False)).returncode == 0:
            return unusable(IGNORED_DIRECTORY, f"{subpath} is ignored by the repository")
        operation = await asyncio.to_thread(_operation_in_progress, git_dir)
        if operation:
            return unusable(OPERATION_IN_PROGRESS, f"a {operation} is in progress")
        sparse = await self._git(top, "config", "--type=bool", "--get", "core.sparseCheckout", check=False)
        if sparse.decode().strip() == "true":
            return unusable(SPARSE_CHECKOUT, "the repository uses a sparse checkout")
        if await self._partial_clone(top):
            return unusable(PARTIAL_CLONE, "the repository is a partial clone")
        if await self._has_submodules(top):
            return unusable(SUBMODULES, "the repository has submodules")
        listed = await self._git(
            top, "ls-files", "-z", "--cached", "--others", "--exclude-standard", timeout=settings.WORKTREE_GIT_WORK_SECONDS
        )
        names = [name for name in listed.stdout.split(b"\0") if name]
        # A repository inside the project that is neither ignored nor a
        # submodule: git would record it as a submodule link, and a worktree
        # would hold an empty directory where it is.
        nested = [_decode(name) for name in names if name.endswith(b"/")]
        if nested:
            return unusable(NESTED_REPOSITORY, f"{nested[0]} is a repository of its own")
        programs = await self._programs(top, names)
        if programs:
            return unusable(*programs)
        return None

    async def _has_submodules(self, root: Path) -> bool:
        if await asyncio.to_thread((root / ".gitmodules").exists):
            return True
        staged = await self._git(root, "ls-files", "-z", "--stage")
        return any(entry.startswith(b"160000 ") for entry in staged.stdout.split(b"\0"))

    async def _programs(
        self, root: Path, names: list[bytes], source: str | None = None, filters: bool = True
    ) -> tuple[str, str] | None:
        """Return why git would run a program the user configured on these paths, or None.

        That is a content filter (``filter`` attribute: Git LFS, git-crypt,
        ...) or a merge driver configured as a program (``merge`` attribute,
        or ``merge.default``, naming a ``merge.<name>.driver``). A snapshot, a merge or a checkout would run it;
        the host does not run the user's programs, and what one makes in a
        worktree need not match the project. With ``source`` the attributes
        are read from that tree, as a checkout of it would. With ``filters``
        False only merge drivers are asked about. Returns a reason key and
        what it applies to.
        """
        # git runs a program only for a name a driver is configured for (one
        # configured under a built-in name is found before git's own); for any
        # other name it merges as text.
        # Any merge.<name>.<key> makes <name> a driver of the user's, even one
        # with no command (git then stops the merge rather than merge as text).
        # Names only, NUL-separated: a name may hold spaces ([merge "my drv"]).
        configured = await self._git(
            root, "config", "-z", "--name-only", "--get-regexp", r"^merge\..*\.[^.]+$", check=False
        )
        # The name may be empty ([merge ""]) or hold spaces anywhere.
        keys = [key for key in configured.stdout.split(b"\0") if key.startswith(b"merge.") and key.count(b".") >= 2]
        theirs = {key[len(b"merge.") : key.rindex(b".")] for key in keys}
        default = await self._git(root, "config", "-z", "--get", "merge.default", check=False)
        driver = default.stdout[:-1] if default.stdout.endswith(b"\0") else default.stdout
        if default.returncode == 0 and driver in theirs:
            return MERGE_DRIVERS, f"merge.default is {_decode(driver)}"
        # A filter named unspecified, unset or set reads, in check-attr's
        # output, the same as no filter at all.
        ambiguous = await self._git(
            root, "config", "-z", "--name-only", "--get-regexp", r"^filter\.(unspecified|unset|set)\.", check=False
        )
        if filters and ambiguous.stdout.strip(b"\0"):
            return CONTENT_FILTERS, "a content filter is configured under a name check-attr cannot tell from none"
        checked = await self._git(
            root,
            "check-attr",
            *([f"--source={source}"] if source else []),
            "--stdin",
            "-z",
            "filter",
            "merge",
            input=b"".join(name + b"\0" for name in names),
            timeout=settings.WORKTREE_GIT_WORK_SECONDS,
        )
        fields = checked.stdout.split(b"\0")
        for index in range(0, len(fields) - 2, 3):
            path, attribute, value = fields[index : index + 3]
            if filters and attribute == b"filter" and value not in (b"unspecified", b"unset"):
                return CONTENT_FILTERS, f"{_decode(path)} goes through a content filter (Git LFS, git-crypt, ...)"
            if attribute == b"merge" and value in theirs:
                return MERGE_DRIVERS, f"{_decode(path)} is merged by the program {_decode(value)}"
        return None

    # -- snapshots -------------------------------------------------------------

    async def _snapshot(
        self, root: Path, message: str, start: str | None = None, *, linked: Iterable[str] = ()
    ) -> Snapshot:
        """Write what ``root`` holds as a commit (see ``Snapshot``).

        A file ``start`` (a worktree's start) has is taken whatever its size:
        it is the worktree's own, and leaving it out would read as deleting it.
        Active host-created dependency links in ``linked`` are excluded even on
        POSIX where a trailing-slash ignore rule does not match a symlink.
        """
        head = await self._try_resolve(root, "HEAD")
        active_dep_links = (
            await asyncio.to_thread(_copy_trees.active_links, root, linked) if linked else ()
        )
        dep_link_set = set(active_dep_links)
        scratch = await _temporary_directory("nekopower-index-")
        try:
            env = {"GIT_INDEX_FILE": str(scratch / "index")}
            # A copy of the real index keeps what git knows of each file's
            # size and time, so only changed files are read again; the real
            # one is only read. Entries marked to be left alone
            # (assume-unchanged, skip-worktree) would hide changes from
            # ``add``: the copy's marks are cleared.
            real = (await self._git(root, "rev-parse", "--path-format=absolute", "--git-path", "index")).lines()[0]
            if await asyncio.to_thread(_copy_index, Path(real), scratch / "index"):
                marked = await self._marked(root, env)
                if marked:
                    for flag in ("--no-assume-unchanged", "--no-skip-worktree"):
                        await self._git(
                            root,
                            "update-index",
                            flag,
                            "-z",
                            "--stdin",
                            env=env,
                            input=b"".join(name + b"\0" for name in marked),
                            timeout=settings.WORKTREE_GIT_WORK_SECONDS,
                        )
            else:
                await self._git(root, "read-tree", *([head] if head else ["--empty"]), env=env)
            def _is_dep_link_path(path_name: str) -> bool:
                if not dep_link_set:
                    return False
                clean = path_name.rstrip("/")
                return clean in dep_link_set or any(up in dep_link_set for up in _copy_trees._parents(clean))

            cached = await self._git(root, "ls-files", "-z", "--cached", env=env)
            cached_names = [name for name in cached.stdout.split(b"\0") if name]
            if dep_link_set:
                staged_dep_links = [_decode(name) for name in cached_names if _is_dep_link_path(_decode(name))]
                if staged_dep_links:
                    await self._git(
                        root,
                        "rm",
                        "--cached",
                        "--force",
                        "--ignore-unmatch",
                        "--pathspec-from-file=-",
                        "--pathspec-file-nul",
                        env=env,
                        input=_nul_join(f":(literal,top){name.rstrip('/')}" for name in staged_dep_links),
                        timeout=settings.WORKTREE_GIT_WORK_SECONDS,
                    )
                    cached_names = [name for name in cached_names if not _is_dep_link_path(_decode(name))]
            others = await self._git(root, "ls-files", "-z", "--others", "--exclude-standard", env=env)
            raw_others = [_decode(name) for name in others.stdout.split(b"\0") if name]
            unignored_dep_links = [name for name in raw_others if _is_dep_link_path(name)]
            names = [name for name in raw_others if not _is_dep_link_path(name)]
            exclude_dep_specs: list[str] = []
            if unignored_dep_links:
                hit_roots = sorted(
                    dep
                    for dep in dep_link_set
                    if any(item.rstrip("/") == dep or item.startswith(dep + "/") for item in unignored_dep_links)
                )
                ignored_roots: set[str] = set()
                if hit_roots:
                    chk = await self._git(
                        root,
                        "check-ignore",
                        "-z",
                        "--stdin",
                        env=env,
                        input=_nul_join(hit_roots),
                        check=False,
                        timeout=settings.WORKTREE_GIT_WORK_SECONDS,
                    )
                    ignored_roots = {_decode(p).rstrip("/") for p in chk.stdout.split(b"\0") if p}
                for dep in hit_roots:
                    if dep not in ignored_roots:
                        exclude_dep_specs.append(dep)
                    else:
                        exclude_dep_specs.extend(
                            item for item in unignored_dep_links if item.rstrip("/") == dep or item.startswith(dep + "/")
                        )
            # A repository of its own inside (listed with a trailing slash)
            # would be recorded as a submodule link and its files lost.
            nested = [name for name in names if name.endswith("/")]
            files = [name for name in names if not name.endswith("/")]
            large = await asyncio.to_thread(_too_large, root, files, settings.WORKTREE_UNTRACKED_MAX_BYTES)
            kept: list[str] = []
            if start:
                known = await self._git(root, "ls-tree", "-r", "-z", "--name-only", start)
                had = {_decode(name) for name in known.stdout.split(b"\0") if name}
                # The start's files are the worktree's own: whatever their
                # size, and even when ignored since, they are taken while
                # they are there; left out, they would read as deleted.
                large = [name for name in large if name not in had]
                seen = {_decode(name) for name in cached_names} | set(files)
                kept = [
                    name
                    for name in await asyncio.to_thread(_present, root, sorted(had - seen))
                    if not _is_dep_link_path(name)
                ]
            skipped = nested + large
            excluded = [*skipped, *exclude_dep_specs]
            specs = ["."] + [f":(exclude,literal,top){name.rstrip('/')}" for name in excluded]
            # What is to be added is checked again each time: a filter may
            # have been set up, or a file it applies to made, since.
            adding = list(cached_names)
            adding += [name.encode("utf-8", "surrogateescape") for name in [*files, *kept] if name not in excluded]
            programs = await self._programs(root, adding)
            if programs:
                raise WorktreeUnavailable(*programs)
            await self._git(
                root,
                "add",
                "--all",
                "--pathspec-from-file=-",
                "--pathspec-file-nul",
                env=env,
                input=_nul_join(specs),
                timeout=settings.WORKTREE_GIT_WORK_SECONDS,
            )
            if kept:
                await self._git(
                    root,
                    "add",
                    "--force",
                    "--pathspec-from-file=-",
                    "--pathspec-file-nul",
                    env=env,
                    input=_nul_join(f":(literal,top){name}" for name in kept),
                    timeout=settings.WORKTREE_GIT_WORK_SECONDS,
                )
            # A repository staged as a submodule link since the worktree was
            # made: its files would be lost, landed as an empty directory.
            staged = await self._git(root, "ls-files", "-z", "--stage", env=env)
            links = [entry.split(b"\t", 1)[-1] for entry in staged.stdout.split(b"\0") if entry.startswith(b"160000 ")]
            if links:
                raise WorktreeUnavailable(SUBMODULES, f"{_decode(links[0])} is a submodule link")
            tree = (await self._git(root, "write-tree", env=env)).lines()[0]
        finally:
            await asyncio.to_thread(_remove_tree, scratch)
        commit = await self._commit_tree(root, tree, head, message)
        return Snapshot(commit, tree, tuple(skipped))

    async def _marked(self, root: Path, env: Mapping[str, str]) -> list[bytes]:
        """Return the paths of an index's entries marked assume-unchanged or skip-worktree."""
        listed = await self._git(root, "ls-files", "-z", "-v", env=env, timeout=settings.WORKTREE_GIT_WORK_SECONDS)
        return [
            entry[2:] for entry in listed.stdout.split(b"\0") if entry and (entry[:1].islower() or entry[:1] == b"S")
        ]

    async def _commit_tree(
        self, root: Path, tree: str, parent: str | None, message: str, *, env: Mapping[str, str] | None = None
    ) -> str:
        args = ["commit-tree", "-m", message]
        if parent:
            args += ["-p", parent]
        return (await self._git(root, *args, tree, env=env or IDENTITY)).lines()[0]

    async def _has_changes(
        self, repo_root: Path, path: Path, worktree_id: str, start: str, *, linked: Iterable[str] = ()
    ) -> bool:
        start = await self._try_resolve(repo_root, _start_ref(worktree_id)) or start
        linked_tuple = tuple(linked)
        if linked_tuple:
            now = await self._snapshot(path, "nekopower: worktree result", start, linked=linked_tuple)
        else:
            now = await self._snapshot(path, "nekopower: worktree result", start)
        # Files left out of the snapshot (large, or a repository of its own)
        # were made in the worktree: the project's are never copied into it.
        if now.skipped or now.tree != await self._resolve_commit(path, f"{start}^{{tree}}", kind="tree"):
            return True
        return await self._staged_only(path, start)

    async def _staged_only(self, path: Path, start: str, files_tree: str | None = None) -> bool:
        """Return whether the worktree's own index holds what neither its HEAD, its start nor its files have.

        A snapshot is taken of its files, not of what was staged: a version
        staged and then undone in the file would go with the worktree, or be
        replaced when conflicts are checked out. ``files_tree`` is left out
        where its files are its start.
        """
        index = Path((await self._git(path, "rev-parse", "--path-format=absolute", "--git-path", "index")).lines()[0])
        if not await asyncio.to_thread(index.exists):
            return False
        written = await self._git(path, "write-tree", check=False, timeout=settings.WORKTREE_GIT_WORK_SECONDS)
        if not written.ok:  # unmerged entries, for one: something is half done there
            return True
        staged = written.lines()[0]
        head = await self._try_resolve(path, "HEAD^{tree}")
        differing: set[bytes] | None = None
        for other in (start, head, files_tree):
            if other is None:
                continue
            # Only what the index holds: a path it no longer has (git rm
            # --cached) loses nothing, the file being where it was.
            listed = await self._git(
                path, "diff-tree", "-r", "-z", "--name-only", "--no-renames", "--diff-filter=a", staged, other,
                timeout=settings.WORKTREE_GIT_WORK_SECONDS,
            )
            names = {name for name in listed.stdout.split(b"\0") if name}
            differing = names if differing is None else differing & names
            if not differing:
                return False
        return bool(differing)

    # -- landing on a branch / opening a pull request --------------------------

    async def _checked_out_branches(self, repo_root: Path) -> set[str]:
        """Return branch names (without ``refs/heads/``) currently checked out in any worktree of ``repo_root``."""
        listed = await self._git(repo_root, "worktree", "list", "--porcelain", "-z")
        checked: set[str] = set()
        prefix = b"branch refs/heads/"
        for entry in listed.stdout.split(b"\0"):
            if entry.startswith(prefix):
                checked.add(_decode(entry[len(prefix) :]))
        head = await self._git(
            repo_root, "symbolic-ref", "--quiet", "HEAD", check=False, timeout=settings.WORKTREE_GIT_QUERY_SECONDS
        )
        if head.ok and head.lines() and head.lines()[0].startswith("refs/heads/"):
            checked.add(head.lines()[0][len("refs/heads/") :])
        return checked

    async def _commit_identity(self, repo_root: Path) -> dict[str, str]:
        """Return git author/committer env from repo config if set, else the default NekoPower identity."""
        name = await self._git(
            repo_root, "config", "--get", "user.name", check=False, timeout=settings.WORKTREE_GIT_QUERY_SECONDS
        )
        email = await self._git(
            repo_root, "config", "--get", "user.email", check=False, timeout=settings.WORKTREE_GIT_QUERY_SECONDS
        )
        user_name = name.lines()[0].strip() if name.ok and name.lines() else ""
        user_email = email.lines()[0].strip() if email.ok and email.lines() else ""
        if user_name and user_email:
            return {
                "GIT_AUTHOR_NAME": user_name,
                "GIT_AUTHOR_EMAIL": user_email,
                "GIT_COMMITTER_NAME": user_name,
                "GIT_COMMITTER_EMAIL": user_email,
            }
        return dict(IDENTITY)

    async def _real_base_commit(self, root: Path, commit: str) -> str | None:
        """Walk past host-created synthetic snapshot commits to the first real repository commit."""
        current = commit
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            info = await self._git(
                root,
                "show",
                "-s",
                "--format=%ae%x00%ce%x00%s%x00%P",
                current,
                check=False,
                timeout=settings.WORKTREE_GIT_QUERY_SECONDS,
            )
            if not info.ok:
                return None
            parts = info.decode().rstrip("\r\n").split("\x00")
            if len(parts) < 4:
                return current
            author_email, committer_email, subject, parents_text = parts[0], parts[1], parts[2], parts[3]
            is_synthetic = (
                author_email == IDENTITY["GIT_AUTHOR_EMAIL"]
                and committer_email == IDENTITY["GIT_COMMITTER_EMAIL"]
                and subject in _SYNTHETIC_SNAPSHOT_MESSAGES
            )
            if not is_synthetic:
                return current
            parents = parents_text.split()
            if not parents:
                return None
            current = parents[0]
        return None

    async def _resolve_push_target(self, root: Path, remote_name: str) -> tuple[str, bool]:
        """Return ``(push_url, differs_from_fetch)`` for ``remote_name``, rejecting multiple push URLs."""
        push_urls_run = await self._git(
            root, "remote", "get-url", "--push", "--all", "--", remote_name, check=False
        )
        push_urls = list(dict.fromkeys(line.strip() for line in push_urls_run.lines() if line.strip()))
        if len(push_urls) > 1:
            raise WorktreeError(
                f"remote {remote_name!r} has multiple push URLs configured; refusing to push"
            )
        fetch_url_run = await self._git(root, "remote", "get-url", "--", remote_name, check=False)
        fetch_url = fetch_url_run.lines()[0].strip() if fetch_url_run.ok and fetch_url_run.lines() else ""
        if push_urls:
            return push_urls[0], push_urls[0] != fetch_url
        return fetch_url, False

    async def _land_on_branch(
        self,
        worktree: Worktree,
        result: Snapshot,
        *,
        branch: str = "",
        message: str = "",
        remote: str | None = None,
        base_branch: str | None = None,
        mark_pr_pending: bool = False,
    ) -> MergeResult:
        root = worktree.repo_root
        base = await self._start(worktree)
        raw_branch = branch.strip() or f"nekopower/{worktree.id}"
        try:
            target_branch = await self._validate_branch_name(root, raw_branch)
        except WorktreeError as exc:
            return MergeResult(
                "failed",
                detail=str(exc),
                skipped=result.skipped,
                result=result.commit,
                result_tree=result.tree,
                base=base,
            )

        protected = set(_PROTECTED_BRANCHES)
        if base_branch:
            protected.add(base_branch.strip())
        remote_name = remote.strip() if isinstance(remote, str) and remote.strip() else ""
        if remote_name:
            try:
                await self._ensure_remote(root, remote_name)
                # Inspect actual push target(s): reject multi-pushurl remotes and protect the push
                # destination's live default branch even when pushurl differs from fetch url.
                push_url, differs_from_fetch = await self._resolve_push_target(root, remote_name)
                if differs_from_fetch and push_url:
                    push_default = await self._discover_remote_url_default_branch(root, push_url)
                    if not push_default:
                        raise WorktreeError(
                            f"could not determine the default branch of push destination {push_url!r}"
                        )
                    protected.add(push_default)
                else:
                    protected.add(await self._discover_remote_default_branch(root, remote_name, fetch=True))
            except (WorktreeError, GitError) as exc:
                return MergeResult(
                    "failed",
                    detail=str(exc),
                    skipped=result.skipped,
                    result=result.commit,
                    result_tree=result.tree,
                    base=base,
                    branch=target_branch,
                    remote=remote_name,
                )
        else:
            # Best-effort cached default-branch protection for local-only commits.
            with contextlib.suppress(WorktreeError):
                protected.add(await self._discover_remote_default_branch(root, "origin", fetch=False))
        branch_cmp = target_branch.lower() if sys.platform == "win32" else target_branch
        protected_cmp = {name.lower() for name in protected} if sys.platform == "win32" else protected
        if branch_cmp in protected_cmp or target_branch.lower() in _PROTECTED_BRANCHES:
            return MergeResult(
                "failed",
                detail=f"refusing to commit or push to protected default branch {target_branch!r}",
                skipped=result.skipped,
                result=result.commit,
                result_tree=result.tree,
                base=base,
                branch=target_branch,
                remote=remote_name,
            )
        checked_out = await self._checked_out_branches(root)
        checked_cmp = {name.lower() for name in checked_out} if sys.platform == "win32" else checked_out
        if branch_cmp in checked_cmp:
            return MergeResult(
                "failed",
                detail=f"refusing to update branch {target_branch!r} while it is checked out in a worktree",
                skipped=result.skipped,
                result=result.commit,
                result_tree=result.tree,
                base=base,
                branch=target_branch,
                remote=remote_name,
            )

        common = Path((await self._git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).lines()[0])
        delivery = await asyncio.to_thread(_noted_delivery, common, worktree.path)
        ref_name = f"refs/heads/{target_branch}"
        existing_tip = await self._try_resolve(root, ref_name)
        owns_existing = existing_tip is not None and (
            delivery.get("branch") == target_branch
            or (
                delivery.get("pending_branch") == target_branch
                and delivery.get("pending_commit") == existing_tip
            )
        )
        owns_branch = existing_tip is None or owns_existing
        if not owns_branch:
            return MergeResult(
                "failed",
                detail=f"branch {target_branch!r} already exists and does not belong to this worktree",
                skipped=result.skipped,
                result=result.commit,
                result_tree=result.tree,
                base=base,
                branch=target_branch,
                remote=remote_name,
            )

        async def _finish_existing_without_new_commit(
            *,
            current_commit: str,
            merged_tree_oid: str,
            advance_start_to_result: bool,
        ) -> MergeResult:
            same_owned = owns_existing
            needs_push = (
                bool(remote_name)
                and same_owned
                and (
                    not delivery.get("pushed")
                    or delivery.get("remote") != remote_name
                    or str(delivery.get("pushed_commit") or "") != existing_tip
                )
            )
            if needs_push and existing_tip is not None:
                pushed = await self._git(
                    root,
                    "push",
                    "--no-verify",
                    "--",
                    remote_name,
                    f"{ref_name}:{ref_name}",
                    check=False,
                    timeout=settings.WORKTREE_PR_TIMEOUT_SECONDS,
                )
                if not pushed.ok:
                    return MergeResult(
                        "failed",
                        detail=_failure(f"pushing branch {target_branch} to {remote_name}", pushed.stderr),
                        skipped=result.skipped,
                        current=existing_tip,
                        result=result.commit,
                        result_tree=result.tree,
                        merged_tree=merged_tree_oid,
                        base=base,
                        branch=target_branch,
                        commit=existing_tip,
                        remote=remote_name,
                    )
                if advance_start_to_result:
                    await self._git(root, "update-ref", _start_ref(worktree.id), result.commit)
                updated_delivery: dict[str, Any] = {
                    "branch": target_branch,
                    "commit": existing_tip,
                    "remote": remote_name,
                    "pushed": True,
                    "pushed_commit": existing_tip,
                    "base_branch": str(base_branch or delivery.get("base_branch") or ""),
                    "pr_pending": True if mark_pr_pending else bool(delivery.get("pr_pending")),
                    "pr_url": str(delivery.get("pr_url") or ""),
                    "pr_number": delivery.get("pr_number"),
                    "prs": dict(delivery.get("prs") or {}) if isinstance(delivery.get("prs"), dict) else {},
                }
                await _to_the_end(asyncio.to_thread(_note_delivery, common, worktree.path, updated_delivery))
                return MergeResult(
                    "landed",
                    skipped=result.skipped,
                    current=existing_tip,
                    result=result.commit,
                    result_tree=result.tree,
                    merged_tree=merged_tree_oid,
                    base=base,
                    branch=target_branch,
                    commit=existing_tip,
                    remote=remote_name,
                )
            if advance_start_to_result:
                await self._git(root, "update-ref", _start_ref(worktree.id), result.commit)
            return MergeResult(
                "unchanged",
                skipped=result.skipped,
                current=current_commit,
                result=result.commit,
                result_tree=result.tree,
                merged_tree=merged_tree_oid,
                base=base,
                branch=target_branch,
                commit=existing_tip or "",
                remote=remote_name,
            )

        base_tree = await self._resolve_commit(root, f"{base}^{{tree}}", kind="tree")
        if result.tree == base_tree:
            return await _finish_existing_without_new_commit(
                current_commit=existing_tip or base,
                merged_tree_oid=result.tree,
                advance_start_to_result=False,
            )

        target_base = existing_tip
        if target_base is None:
            # Never build a branch or PR directly on a synthetic CurrentState snapshot commit:
            # doing so would commit and push the user's uncommitted edits and untracked files.
            target_base = await self._real_base_commit(root, base)
            if target_base is None:
                return MergeResult(
                    "failed",
                    detail=(
                        "cannot deliver to a branch or pull request from a synthetic snapshot with no real base "
                        "commit; commit first or start from a ref or remote default branch"
                    ),
                    skipped=result.skipped,
                    current=base,
                    result=result.commit,
                    result_tree=result.tree,
                    base=base,
                    branch=target_branch,
                    remote=remote_name,
                )

        did_three_way = target_base != base
        if not did_three_way:
            parent_commit = target_base
            old_tree = base_tree
            target_tree = result.tree
        else:
            # Either the branch advanced since base or base is a synthetic working-tree snapshot:
            # apply only base..result onto target_base via a 3-way merge.
            changed: set[bytes] = set()
            for side in (target_base, result.commit):
                listed = await self._git(
                    root,
                    "diff-tree",
                    "-r",
                    "-z",
                    "--name-only",
                    "--no-renames",
                    base,
                    side,
                    timeout=settings.WORKTREE_GIT_WORK_SECONDS,
                )
                changed.update(name for name in listed.stdout.split(b"\0") if name)
            if changed:
                renormalize = await self._git(root, "config", "--type=bool", "--get", "merge.renormalize", check=False)
                filters = renormalize.lines()[:1] == ["true"]
                for source in (base, target_base, result.commit, None):
                    programs = await self._programs(root, sorted(changed), source=source, filters=filters)
                    if programs:
                        raise WorktreeUnavailable(*programs)
            merged = await self._git(
                root,
                "-c",
                "merge.directoryRenames=false",
                "merge-tree",
                "--write-tree",
                "-z",
                "--name-only",
                "--no-messages",
                "--merge-base",
                base,
                target_base,
                result.commit,
                check=False,
                timeout=settings.WORKTREE_GIT_WORK_SECONDS,
            )
            if merged.returncode not in (0, 1):
                raise GitError(["merge-tree"], merged.returncode, merged.stderr)
            parts = [_decode(part) for part in merged.stdout.split(b"\0") if part]
            merged_tree = parts[0]
            if merged.returncode == 1:
                return MergeResult(
                    "conflict",
                    conflicts=tuple(dict.fromkeys(parts[1:])),
                    skipped=result.skipped,
                    current=target_base,
                    result=result.commit,
                    result_tree=result.tree,
                    merged_tree=merged_tree,
                    base=base,
                    branch=target_branch,
                    remote=remote_name,
                )
            target_base_tree = await self._resolve_commit(root, f"{target_base}^{{tree}}", kind="tree")
            if merged_tree == target_base_tree:
                # If the owned branch already holds these changes locally but has not yet been
                # pushed to the requested remote, complete remote delivery before advancing start.
                return await _finish_existing_without_new_commit(
                    current_commit=target_base,
                    merged_tree_oid=merged_tree,
                    advance_start_to_result=True,
                )
            parent_commit = target_base
            old_tree = target_base_tree
            target_tree = merged_tree

        files = await self._changes(root, old_tree, target_tree)
        counted = dict(
            files=files,
            lines_added=sum(change.added or 0 for change in files),
            lines_deleted=sum(change.deleted or 0 for change in files),
            skipped=result.skipped,
            current=target_base,
            result=result.commit,
            result_tree=result.tree,
            merged_tree=target_tree,
            base=base,
            branch=target_branch,
            remote=remote_name,
        )
        commit_message = message.strip() or f"nekopower: changes from {worktree.id}"
        identity = await self._commit_identity(root)
        new_commit = await self._commit_tree(root, target_tree, parent_commit, commit_message, env=identity)
        expected_old = existing_tip if existing_tip is not None else ""
        # Persist verifiable delivery intent BEFORE mutating refs/heads so that if the final
        # delivery note write fails after branch update/push, retrying can still prove ownership.
        intent_delivery: dict[str, Any] = {
            **delivery,
            "pending_branch": target_branch,
            "pending_commit": new_commit,
        }
        try:
            await _to_the_end(asyncio.to_thread(_note_delivery, common, worktree.path, intent_delivery))
        except OSError as exc:
            return MergeResult(
                "failed",
                detail=f"recording delivery intent failed: {exc}",
                **counted,
            )
        updated = await self._git(root, "update-ref", ref_name, new_commit, expected_old, check=False)
        if not updated.ok:
            with contextlib.suppress(OSError):
                await _to_the_end(asyncio.to_thread(_note_delivery, common, worktree.path, delivery))
            return MergeResult(
                "failed",
                detail=_failure(f"updating branch {target_branch}", updated.stderr),
                **counted,
            )

        async def rollback_branch() -> None:
            if existing_tip is None:
                await _to_the_end(self._git(root, "update-ref", "-d", ref_name, new_commit, check=False))
            else:
                await _to_the_end(self._git(root, "update-ref", ref_name, existing_tip, new_commit, check=False))
            with contextlib.suppress(OSError):
                await _to_the_end(asyncio.to_thread(_note_delivery, common, worktree.path, delivery))

        if remote_name:
            try:
                pushed = await self._git(
                    root,
                    "push",
                    "--no-verify",
                    "--",
                    remote_name,
                    f"{ref_name}:{ref_name}",
                    check=False,
                    timeout=settings.WORKTREE_PR_TIMEOUT_SECONDS,
                )
            except BaseException:
                await rollback_branch()
                raise
            if not pushed.ok:
                await rollback_branch()
                return MergeResult(
                    "failed",
                    detail=_failure(f"pushing branch {target_branch} to {remote_name}", pushed.stderr),
                    **counted,
                )

        # After a 3-way merge onto a branch that held extra changes not present in this
        # worktree, keep the worktree's baseline at result.commit (matching its files) so a
        # later delivery does not treat those branch-side additions as deletions.
        next_start = result.commit if did_three_way else new_commit
        await self._git(root, "update-ref", _start_ref(worktree.id), next_start)
        same_branch = delivery.get("branch") == target_branch
        updated_delivery: dict[str, Any] = {
            "branch": target_branch,
            "commit": new_commit,
            "remote": remote_name,
            "pushed": bool(remote_name) or (same_branch and bool(delivery.get("pushed"))),
            "pushed_commit": new_commit if remote_name else (str(delivery.get("pushed_commit") or "") if same_branch else ""),
            "base_branch": str(base_branch or (delivery.get("base_branch") if same_branch else "") or ""),
            "pr_pending": True if mark_pr_pending else (bool(delivery.get("pr_pending")) if same_branch else False),
            "pr_url": str(delivery.get("pr_url") or "") if same_branch else "",
            "pr_number": delivery.get("pr_number") if same_branch else None,
            "prs": dict(delivery.get("prs") or {}) if same_branch and isinstance(delivery.get("prs"), dict) else {},
        }
        await _to_the_end(asyncio.to_thread(_note_delivery, common, worktree.path, updated_delivery))
        return MergeResult("landed", commit=new_commit, **counted)

    async def _land_as_pr(self, worktree: Worktree, result: Snapshot, pr: PullRequest) -> MergeResult:
        root = worktree.repo_root
        base = await self._start(worktree)
        title = pr.title.strip()
        if not title:
            return MergeResult(
                "failed",
                detail="pull request title must not be empty",
                skipped=result.skipped,
                result=result.commit,
                result_tree=result.tree,
                base=base,
            )
        remote_name = pr.remote.strip() or "origin"
        try:
            await self._ensure_remote(root, remote_name)
        except WorktreeError as exc:
            return MergeResult(
                "failed",
                detail=str(exc),
                skipped=result.skipped,
                result=result.commit,
                result_tree=result.tree,
                base=base,
                remote=remote_name,
            )

        if pr.base_branch:
            try:
                base_branch = await self._validate_branch_name(root, pr.base_branch)
            except WorktreeError as exc:
                return MergeResult(
                    "failed",
                    detail=str(exc),
                    skipped=result.skipped,
                    result=result.commit,
                    result_tree=result.tree,
                    base=base,
                    remote=remote_name,
                )
        else:
            # Resolve default base branch from the actual push destination (when pushurl differs
            # from fetch URL) so the PR base matches the repository _create_pr_with_gh targets.
            try:
                push_url, differs_from_fetch = await self._resolve_push_target(root, remote_name)
                if differs_from_fetch and push_url:
                    discovered = await self._discover_remote_url_default_branch(root, push_url)
                    if not discovered:
                        raise WorktreeError(
                            f"could not determine the default branch of push destination {push_url!r}"
                        )
                    base_branch = discovered
                else:
                    try:
                        base_branch = await self._discover_remote_default_branch(root, remote_name, fetch=True)
                    except (WorktreeError, GitError):
                        base_branch = await self._discover_remote_default_branch(root, remote_name, fetch=False)
            except WorktreeError as exc:
                return MergeResult(
                    "failed",
                    detail=str(exc),
                    skipped=result.skipped,
                    result=result.commit,
                    result_tree=result.tree,
                    base=base,
                    remote=remote_name,
                )

        target_branch = pr.branch.strip() or f"nekopower/{worktree.id}"
        common = Path((await self._git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).lines()[0])
        delivery = await asyncio.to_thread(_noted_delivery, common, worktree.path)
        pr_key = f"{remote_name}\0{base_branch}"
        saved_prs = dict(delivery.get("prs") or {}) if delivery.get("branch") == target_branch and isinstance(delivery.get("prs"), dict) else {}
        matched_pr = saved_prs.get(pr_key) if isinstance(saved_prs.get(pr_key), dict) else None
        if matched_pr is None and (
            delivery.get("branch") == target_branch
            and delivery.get("remote") == remote_name
            and delivery.get("base_branch") == base_branch
            and delivery.get("pr_url")
        ):
            matched_pr = {"url": str(delivery.get("pr_url") or ""), "number": delivery.get("pr_number")}
        existing_pr_url = str(matched_pr.get("url") or "") if matched_pr else ""
        existing_pr_number = matched_pr.get("number") if matched_pr else None

        branch_result = await self._land_on_branch(
            worktree,
            result,
            branch=target_branch,
            message=pr.commit_message or title,
            remote=remote_name,
            base_branch=base_branch,
            mark_pr_pending=not bool(existing_pr_url),
        )
        if branch_result.status in ("conflict", "failed"):
            return branch_result
        # Re-read delivery in case _land_on_branch just pushed an already-committed branch.
        delivery = await asyncio.to_thread(_noted_delivery, common, worktree.path)
        if branch_result.status == "unchanged":
            if existing_pr_url:
                return replace(branch_result, pr_url=existing_pr_url, pr_number=existing_pr_number)
            # Retry PR creation if a previous push to this remote succeeded and only PR creation failed.
            if not (
                delivery.get("branch") == target_branch
                and delivery.get("remote") == remote_name
                and delivery.get("pushed")
                and delivery.get("commit")
            ):
                return branch_result
            commit_sha = str(delivery["commit"])
        else:
            commit_sha = branch_result.commit

        if existing_pr_url:
            return replace(branch_result, pr_url=existing_pr_url, pr_number=existing_pr_number)

        # Durably record pending PR creation BEFORE awaiting the PR callback/CLI so cancellation
        # or crash mid-request still preserves the worktree for retry on restart.
        pending_pr_delivery: dict[str, Any] = {
            **delivery,
            "branch": branch_result.branch,
            "commit": commit_sha,
            "remote": remote_name,
            "base_branch": base_branch,
            "pushed": True,
            "pushed_commit": commit_sha,
            "pr_pending": True,
            "prs": saved_prs,
        }
        with contextlib.suppress(OSError):
            await _to_the_end(asyncio.to_thread(_note_delivery, common, worktree.path, pending_pr_delivery))

        request = PRRequest(
            repo_root=root,
            remote=remote_name,
            branch=branch_result.branch,
            base_branch=base_branch,
            title=title,
            body=pr.body,
            draft=pr.draft,
            commit=commit_sha,
            files=branch_result.files,
        )
        try:
            info = (
                await pr.create_pr(request)
                if pr.create_pr is not None
                else await self._create_pr_with_gh(pr.gh, request)
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - report clear landing failure while preserving pushed branch
            return replace(
                branch_result,
                status="failed",
                commit=commit_sha,
                detail=f"branch {branch_result.branch!r} was pushed to {remote_name!r}, but creating the pull request failed: {exc}",
            )

        saved_prs[pr_key] = {"url": info.url, "number": info.number}
        updated_delivery: dict[str, Any] = {
            "branch": branch_result.branch,
            "commit": commit_sha,
            "remote": remote_name,
            "base_branch": base_branch,
            "pushed": True,
            "pushed_commit": commit_sha,
            "pr_pending": False,
            "pr_url": info.url,
            "pr_number": info.number,
            "prs": saved_prs,
        }
        await _to_the_end(asyncio.to_thread(_note_delivery, common, worktree.path, updated_delivery))
        return replace(branch_result, status="landed", commit=commit_sha, pr_url=info.url, pr_number=info.number)

    @staticmethod
    def _github_repo_from_remote_url(url: str) -> str | None:
        """Parse a git remote URL into a ``gh --repo`` selector (``OWNER/REPO`` or ``HOST/OWNER/REPO``)."""
        raw = url.strip()
        if not raw:
            return None
        # SCP-like syntax: git@github.com:owner/repo.git
        scp = re.match(r"^(?:[^@/\s]+@)?([A-Za-z0-9._-]+):([^/\s]+/[^/\s]+?)(?:\.git)?/?$", raw)
        if scp:
            host, path = scp.group(1), scp.group(2)
            # Exclude Windows drive paths like C:\foo or C:/foo.
            if len(host) == 1 and host.isalpha():
                return None
            return path if host.lower() == "github.com" else f"{host}/{path}"
        # URL syntax: https://github.com/owner/repo.git or ssh://git@host/owner/repo.git
        url_match = re.match(
            r"^(?:https?|ssh)://(?:[^@/\s]+@)?([A-Za-z0-9._-]+)(?::\d+)?/([^/\s]+/[^/\s]+?)(?:\.git)?/?$",
            raw,
        )
        if url_match:
            host, path = url_match.group(1), url_match.group(2)
            return path if host.lower() == "github.com" else f"{host}/{path}"
        return None

    async def _create_pr_with_gh(self, gh_program: str, request: PRRequest) -> PRInfo:
        command = [
            gh_program,
            "pr",
            "create",
        ]
        remote_url_run = await self._git(
            request.repo_root, "remote", "get-url", "--push", "--", request.remote, check=False
        )
        if not remote_url_run.ok:
            remote_url_run = await self._git(
                request.repo_root, "remote", "get-url", "--", request.remote, check=False
            )
        repo_selector = (
            self._github_repo_from_remote_url(remote_url_run.decode())
            if remote_url_run.ok
            else None
        )
        if repo_selector:
            command.extend(["--repo", repo_selector])
        command.extend([
            "--head",
            request.branch,
            "--base",
            request.base_branch,
            "--title",
            request.title,
            "--body-file",
            "-",
        ])
        if request.draft:
            command.append("--draft")
        extra: dict[str, object] = (
            {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
        )
        environment = {key: value for key, value in os.environ.items() if key not in _REDIRECTING_ENV}
        environment.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", GH_PROMPT_DISABLED="1")
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(request.repo_root),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
                **extra,
            )
        except OSError as error:
            raise WorktreeError(f"{gh_program!r} could not be started: {error}") from error
        try:
            async with asyncio.timeout(settings.WORKTREE_PR_TIMEOUT_SECONDS):
                stdout, stderr = await process.communicate(request.body.encode("utf-8"))
        except TimeoutError:
            await _stop(process)
            raise WorktreeError(f"{gh_program} pr create timed out after {settings.WORKTREE_PR_TIMEOUT_SECONDS}s") from None
        except BaseException:
            await _stop(process)
            raise
        if process.returncode != 0:
            raise WorktreeError(_failure(f"{gh_program} pr create", stderr or stdout))
        text = stdout.decode("utf-8", "replace").strip()
        url = ""
        for line in reversed(text.splitlines()):
            stripped = line.strip()
            if stripped.startswith(("http://", "https://")):
                url = stripped
                break
        if not url:
            match = re.search(r"https?://\S+", text)
            url = match.group(0) if match else text
        num_match = re.search(r"/pull/(\d+)(?:$|[/?#])", url)
        number = int(num_match.group(1)) if num_match else None
        return PRInfo(url=url, number=number)

    # -- non-git working copies ------------------------------------------------

    @staticmethod
    async def _run_copy_worker(
        fn: Callable[..., Any], *args: Any, stop: threading.Event | None = None, **kwargs: Any
    ) -> Any:
        """Run ``fn`` in a worker thread and never return on cancellation until the thread exits.

        ``asyncio.to_thread`` wraps ``run_in_executor`` without shielding it, so cancelling the
        await can mark the outer task done while the worker thread is still mutating files.
        Shielding the executor future and waiting until it finishes guarantees rollback/cleanup
        and lock release never race with an in-flight worker thread.
        """
        loop = asyncio.get_running_loop()

        def runner() -> Any:
            if stop is not None and stop.is_set():
                raise _copy_trees.CopyCancelled("copy operation cancelled before start")
            return fn(*args, **kwargs)

        fut = loop.run_in_executor(None, runner)
        try:
            return await asyncio.shield(fut)
        except BaseException:
            if stop is not None:
                stop.set()
            while not fut.done():
                try:
                    await asyncio.shield(fut)
                except asyncio.CancelledError:
                    # Keep waiting until the in-flight worker thread finishes so rollback/unlock never races it.
                    continue
                except Exception:
                    break
            raise

    def _copy_lock(self, project_dir: Path) -> asyncio.Lock:
        key = f"copy:{os.path.normcase(os.path.abspath(str(project_dir)))}"
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    async def _create_copy(
        self,
        project_dir: Path,
        target: Path,
        worktree_id: str,
        *,
        link_dependencies: bool = True,
    ) -> Worktree:
        meta_dir = _copy_trees.copy_meta_dir(target)
        if await asyncio.to_thread(meta_dir.exists):
            raise WorktreeError(f"a working copy named {worktree_id!r} already exists for {target}")
        try:
            probe_scan = await asyncio.to_thread(
                _copy_trees.scan_copy_directory,
                project_dir,
                dependency_dirs=settings.WORKTREE_DEPENDENCY_DIRS,
                max_file_bytes=settings.WORKTREE_UNTRACKED_MAX_BYTES,
                max_files=settings.WORKTREE_COPY_MAX_FILES,
                max_total_bytes=settings.WORKTREE_COPY_MAX_BYTES,
                hash_files=False,
            )
        except _copy_trees.CopyTooLarge as exc:
            raise WorktreeUnavailable(DIRECTORY_TOO_LARGE, str(exc)) from exc
        except OSError as exc:
            raise WorktreeError(f"could not read project directory {project_dir}: {exc}") from exc
        # Both the working copy and its baseline blobs live on target's volume.
        disk = await self.check_disk(target, probe_scan.total_bytes * 2)
        if not disk.enough:
            raise WorktreeUnavailable(DISK_SPACE, f"{disk.free} bytes free, {disk.needed} needed")

        token = uuid.uuid4().hex
        stop = threading.Event()
        claiming = asyncio.ensure_future(asyncio.to_thread(_claim_copy_target, target, token))
        cancelled = await _settle(claiming)
        failed = claiming.exception()
        if failed is not None:
            if cancelled:
                raise asyncio.CancelledError from failed
            if isinstance(failed, WorktreeError):
                raise failed
            raise WorktreeError(f"the working copy directory {target} could not be made: {failed}") from failed
        created, claimed_identity, claimed_xattr, claimed_stat = claiming.result()
        existed = not created
        if cancelled:
            if created and await _to_the_end(
                asyncio.to_thread(_claimed_target_matches, target, claimed_identity, claimed_xattr, token, claimed_stat)
            ):
                await _to_the_end(asyncio.to_thread(_unclaim_directory, target))
            raise asyncio.CancelledError
        try:
            start_digest, linked, _scan = await self._run_copy_worker(
                _copy_trees.populate_copy,
                project_dir,
                target,
                stop=stop,
                token=token,
                dependency_dirs=settings.WORKTREE_DEPENDENCY_DIRS,
                max_file_bytes=settings.WORKTREE_UNTRACKED_MAX_BYTES,
                max_files=settings.WORKTREE_COPY_MAX_FILES,
                max_total_bytes=settings.WORKTREE_COPY_MAX_BYTES,
                link_dependencies=link_dependencies,
                link_max=settings.WORKTREE_DEPENDENCY_LINK_MAX,
                identity=claimed_identity,
                cancel_event=stop,
            )
        except BaseException as error:
            stop.set()

            async def clean_copy() -> None:
                try:
                    can_clean_target = await asyncio.to_thread(
                        _claimed_target_matches, target, claimed_identity, claimed_xattr, token, claimed_stat
                    )
                    if can_clean_target:
                        await asyncio.to_thread(_remove_tree, target)
                        if existed:
                            await asyncio.to_thread(target.mkdir, exist_ok=True)
                    await asyncio.to_thread(_remove_tree, meta_dir)
                except Exception as clean_err:
                    error.add_note(f"cleaning up copy fell short ({clean_err}); {target} may be left")

            cancelled = await _settle(asyncio.ensure_future(clean_copy()))
            if cancelled and not isinstance(error, asyncio.CancelledError):
                raise _cancelled_after(error) from error
            if isinstance(error, _copy_trees.CopyTooLarge):
                raise WorktreeUnavailable(DIRECTORY_TOO_LARGE, str(error)) from error
            raise
        return Worktree(
            worktree_id, project_dir, target, "", start_digest, token, kind="copy", linked_dependencies=linked
        )

    async def _land_copy(self, worktree: Worktree) -> MergeResult:
        project_dir = worktree.repo_root
        if not await asyncio.to_thread(project_dir.is_dir):
            return MergeResult("failed", detail=f"project directory {project_dir} no longer exists")
        mismatch = await asyncio.to_thread(_verify_copy_handle, worktree)
        if mismatch is not None:
            return MergeResult("failed", detail=mismatch)
        compute_stop = threading.Event()
        computed = await self._run_copy_worker(
            _copy_trees.compute_copy_merge,
            project_dir,
            worktree.path,
            stop=compute_stop,
            dependency_dirs=settings.WORKTREE_DEPENDENCY_DIRS,
            max_file_bytes=settings.WORKTREE_UNTRACKED_MAX_BYTES,
            cancel_event=compute_stop,
        )
        status = computed["status"]
        if status == "failed":
            return MergeResult("failed", detail=computed.get("detail", "merge failed"))
        common = dict(
            skipped=computed["skipped"],
            current=computed["current"],
            result=computed["result"],
            result_tree=computed["result_tree"],
            merged_tree=computed["merged_tree"],
            base=computed["base"],
        )
        if status == "conflict":
            return MergeResult("conflict", conflicts=computed["conflicts"], **common)
        if status == "unchanged":
            await _to_the_end(asyncio.to_thread(_copy_trees.advance_copy_baseline, worktree.path, computed["result_scan"]))
            return MergeResult("unchanged", **common)

        raw_changes = computed["changes"]
        files = tuple(FileChange(path, kind, added, deleted) for path, kind, added, deleted in raw_changes)
        counted = dict(
            files=files,
            lines_added=sum(change.added or 0 for change in files),
            lines_deleted=sum(change.deleted or 0 for change in files),
            **common,
        )
        expected_current = computed.get("expected_current") or {}
        # Recheck immediately before backup so we never copy through or write through a project junction/symlink.
        drifted_before = await asyncio.to_thread(_copy_trees.verify_project_unchanged, project_dir, expected_current)
        if drifted_before:
            return MergeResult(
                "failed",
                detail=f"project files changed or traverse a directory link before landing ({', '.join(drifted_before[:5])}); merge again",
                **counted,
            )
        backup = await _temporary_directory("nekopower-landing-")
        keep = False
        try:
            try:
                saved = await _to_the_end(
                    asyncio.to_thread(_back_up, project_dir, [change.path for change in files], backup)
                )
            except OSError as error:
                return MergeResult("failed", detail=f"the files to change could not be copied aside: {error}", **counted)
            # Verify no affected project file changed during backup before overwriting project files.
            drifted_after = await asyncio.to_thread(_copy_trees.verify_project_unchanged, project_dir, expected_current)
            if drifted_after:
                return MergeResult(
                    "failed",
                    detail=f"project files changed while preparing to land ({', '.join(drifted_after[:5])}); merge again",
                    **counted,
                )
            stop = threading.Event()
            landed_ok = False
            try:
                await self._run_copy_worker(
                    _copy_trees.apply_copy_changes,
                    project_dir,
                    raw_changes,
                    computed["merged_entries"],
                    computed["merged_contents"],
                    stop=stop,
                    cancel_event=stop,
                )
                ok = await self._run_copy_worker(
                    _copy_trees.verify_copy_changes,
                    project_dir,
                    raw_changes,
                    computed["merged_entries"],
                    computed["merged_contents"],
                )
                if not ok:
                    detail = "the files did not read back as merged"
                else:
                    landed_ok = True
            except BaseException as error:
                stop.set()
                if isinstance(error, OSError):
                    detail = f"writing the changes failed: {error}"
                else:
                    putting = asyncio.ensure_future(asyncio.to_thread(_restore, project_dir, saved))
                    cancelled = await _settle(putting)
                    if putting.exception() is not None:
                        keep = True
                        error.add_note(f"putting back failed ({putting.exception()}); copies are in {backup}")
                    elif putting.result():
                        keep = True
                        error.add_note(f"not put back: {', '.join(putting.result())}; their copies are in {backup}")
                    if cancelled and not isinstance(error, asyncio.CancelledError):
                        raise _cancelled_after(error) from error
                    raise
            if landed_ok:
                # Commit point: merged files are already verified in project_dir. Run the baseline
                # update to completion outside the rollback handler so cancellation never reverts
                # project files while leaving the copy baseline advanced.
                await _to_the_end(
                    asyncio.to_thread(_copy_trees.advance_copy_baseline, worktree.path, computed["result_scan"])
                )
                return MergeResult("landed", **counted)
            restoring = asyncio.ensure_future(asyncio.to_thread(_restore, project_dir, saved))
            cancelled = await _settle(restoring)
            left = restoring.result()
            keep = bool(left)
            if cancelled:
                stopped = asyncio.CancelledError()
                stopped.add_note(f"the landing failed ({detail}) and its files were put back")
                if left:
                    stopped.add_note(f"not put back: {', '.join(left)}; their copies are in {backup}")
                raise stopped
            return MergeResult(
                "failed", detail=detail, left_changed=tuple(left), kept_copies=str(backup) if keep else "", **counted
            )
        finally:
            if not keep:
                asyncio.get_running_loop().run_in_executor(None, _remove_tree, backup)

    async def _prepare_copy_conflicts(self, worktree: Worktree, conflict: MergeResult) -> Worktree:
        mismatch = await asyncio.to_thread(_verify_copy_handle, worktree)
        if mismatch is not None:
            raise WorktreeError(mismatch)
        stop = threading.Event()
        try:
            new_start, _conflicts = await self._run_copy_worker(
                _copy_trees.prepare_copy_conflicts_sync,
                worktree.path,
                stop=stop,
                expected_base=conflict.base,
                expected_result=conflict.result_tree,
                merge_id=conflict.merged_tree,
                dependency_dirs=settings.WORKTREE_DEPENDENCY_DIRS,
                max_file_bytes=settings.WORKTREE_UNTRACKED_MAX_BYTES,
                cancel_event=stop,
            )
        except ValueError as exc:
            raise WorktreeError(str(exc)) from exc
        return replace(worktree, start=new_start)

    async def _remove_copy(self, path: Path, *, token: str = "") -> None:
        meta_dir = _copy_trees.copy_meta_dir(path)
        meta = await asyncio.to_thread(_copy_trees.read_copy_meta, path)
        if meta is None:
            # No metadata: nothing owned by us to delete.
            if await asyncio.to_thread(meta_dir.exists):
                await _to_the_end(asyncio.to_thread(_remove_tree, meta_dir))
            return
        if token and meta.get("token") and meta["token"] != token:
            raise WorktreeError(
                f"{path} holds a worktree made after this handle's; it is left: open() it for a handle of its own"
            )
        exists_now = await asyncio.to_thread(os.path.lexists, _fs(path))
        if exists_now:
            now_id = await asyncio.to_thread(_directory_identity, path)
            whose = _copy_trees.copy_matches_identity(path, meta, now_id)
            if whose == "theirs":
                await _to_the_end(asyncio.to_thread(_remove_tree, meta_dir))
                return
            if whose == "unknown":
                raise WorktreeError(
                    f"{path} is no longer known to be this working copy (directory identity unavailable); "
                    f"move it somewhere safe before removing"
                )
            recorded_links = tuple(str(item) for item in (meta.get("links") or ()))
            if recorded_links:
                await _to_the_end(asyncio.to_thread(_copy_trees.unlink_dependencies, path, recorded_links))
            await _to_the_end(asyncio.to_thread(_remove_tree, path))
            if await asyncio.to_thread(os.path.lexists, _fs(path)):
                raise WorktreeError(f"{path} could not be removed entirely; its metadata is kept")
        await _to_the_end(asyncio.to_thread(_remove_tree, meta_dir))

    async def _reconcile_copies(
        self, project_dir: Path, base: Path, known_ids: set[str], report: ReconcileReport
    ) -> None:
        metas = await asyncio.to_thread(_copy_trees.list_copy_metas_for_project, project_dir, base)
        for worktree_id, copy_path, meta in metas:
            if worktree_id in known_ids:
                continue
            try:
                if await asyncio.to_thread(copy_path.is_dir):
                    changed = await asyncio.to_thread(
                        _copy_trees.copy_has_changes,
                        copy_path,
                        dependency_dirs=settings.WORKTREE_DEPENDENCY_DIRS,
                        max_file_bytes=settings.WORKTREE_UNTRACKED_MAX_BYTES,
                    )
                    if changed:
                        report.kept.append(copy_path)
                        continue
                await self._remove_copy(copy_path, token=str(meta.get("token") or ""))
            except (WorktreeError, OSError) as error:
                report.failed.append((copy_path, str(error)))
                continue
            report.removed.append(worktree_id)

    # -- landing on the working files ------------------------------------------

    async def _land_on_working_files(self, worktree: Worktree, result: Snapshot) -> MergeResult:
        root = worktree.repo_root
        base = await self._start(worktree)
        # The user's files may hold conflict markers of their own then: no
        # landing on top of them.
        unfinished = await self._unfinished(root)  # an operation, or a stash pop's unmerged entries
        if unfinished:
            return MergeResult(
                "failed",
                detail=f"{unfinished} in the project; land when that is done",
                skipped=result.skipped,
                result=result.commit,
                result_tree=result.tree,
                base=base,
            )
        # A large file landed earlier is the project's now too: kept, not
        # taken for deleted on this side either.
        current = await self._snapshot(root, "nekopower: project before landing", base)
        # merge-tree runs a merge driver as it merges, before the merged tree
        # can be looked at: a path and the rule naming a driver for it may
        # each come from a different side (renamed on one, given a rule on
        # the other). Every path either side changed is asked about under
        # the base's, each side's and the working files' attributes first.
        changed: set[bytes] = set()
        for side in (current.commit, result.commit):
            listed = await self._git(
                root, "diff-tree", "-r", "-z", "--name-only", "--no-renames", base, side,
                timeout=settings.WORKTREE_GIT_WORK_SECONDS,
            )
            changed.update(name for name in listed.stdout.split(b"\0") if name)
        if changed:
            # It runs content filters only to renormalize (merge.renormalize).
            renormalize = await self._git(root, "config", "--type=bool", "--get", "merge.renormalize", check=False)
            filters = renormalize.lines()[:1] == ["true"]
            for source in (base, current.commit, result.commit, None):
                programs = await self._programs(root, sorted(changed), source=source, filters=filters)
                if programs:
                    raise WorktreeUnavailable(*programs)
        merged = await self._git(
            root,
            # No directory renames guessed: a file one side added to a
            # directory the other renamed would be merged at a path neither
            # side has, which the paths asked about above leave out.
            "-c",
            "merge.directoryRenames=false",
            "merge-tree",
            "--write-tree",
            "-z",
            "--name-only",
            "--no-messages",
            "--merge-base",
            base,
            current.commit,
            result.commit,
            check=False,
            timeout=settings.WORKTREE_GIT_WORK_SECONDS,
        )
        if merged.returncode not in (0, 1):
            raise GitError(["merge-tree"], merged.returncode, merged.stderr)
        parts = [_decode(part) for part in merged.stdout.split(b"\0") if part]
        merged_tree = parts[0]
        common = dict(
            skipped=result.skipped,
            current=current.commit,
            result=result.commit,
            result_tree=result.tree,
            merged_tree=merged_tree,
            base=base,
        )
        if merged.returncode == 1:
            return MergeResult("conflict", conflicts=tuple(dict.fromkeys(parts[1:])), **common)
        if merged_tree == current.tree:
            # What the worktree holds is in the project: the next landing
            # merges from here, or it would meet these changes again.
            await self._git(root, "update-ref", _start_ref(worktree.id), result.commit)
            return MergeResult("unchanged", **common)
        files = await self._changes(root, current.tree, merged_tree)
        # A filter and a file it applies to may meet only in the merge (one
        # from each side); writing them would run it.
        programs = await self._programs(
            root,
            [change.path.encode("utf-8", "surrogateescape") for change in files if change.status != "deleted"],
            source=merged_tree,
        )
        if programs:
            raise WorktreeUnavailable(*programs)
        counted = dict(
            files=files,
            lines_added=sum(change.added or 0 for change in files),
            lines_deleted=sum(change.deleted or 0 for change in files),
            **common,
        )
        patch = await self._git(
            root,
            "diff-tree",
            "-r",
            "-p",
            "--binary",
            "--full-index",
            "--no-renames",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            current.tree,
            merged_tree,
            timeout=settings.WORKTREE_GIT_WORK_SECONDS,
        )
        checked = await self._git(root, *_APPLY, "--check", input=patch.stdout, check=False)
        if checked.returncode != 0:
            return MergeResult("failed", detail=_failure("the changes do not apply", checked.stderr), **counted)
        backup = await _temporary_directory("nekopower-landing-")
        keep = False
        try:
            try:
                # Waited for to the end: what a copy still writes could not
                # be removed below (Windows), and would be left unreported.
                saved = await _to_the_end(
                    asyncio.to_thread(_back_up, root, [change.path for change in files], backup)
                )
            except OSError as error:
                return MergeResult("failed", detail=f"the files to change could not be copied aside: {error}", **counted)
            try:
                applied = await self._apply(root, patch.stdout)
                if applied.returncode != 0:
                    detail = _failure("writing the changes failed", applied.stderr)
                elif not await self._reads_back(root, merged_tree, files):
                    detail = "the files did not read back as merged"
                else:
                    await self._git(root, "update-ref", _start_ref(worktree.id), result.commit)
                    return MergeResult("landed", **counted)
            except BaseException as error:
                # Cancelled, timed out or git failed after writing may have
                # begun: the files go back, and the start too (it may have
                # moved on already), as one piece no further cancellation
                # cuts, before the error goes on.
                async def put_back() -> list[str]:
                    left = await asyncio.to_thread(_restore, root, saved)
                    await self._put_start_back(worktree, base, error)
                    return left

                putting = asyncio.ensure_future(put_back())
                cancelled = await _settle(putting)
                if putting.exception() is not None:
                    keep = True
                    error.add_note(f"putting back failed ({putting.exception()}); copies are in {backup}")
                elif putting.result():
                    keep = True
                    error.add_note(f"not put back: {', '.join(putting.result())}; their copies are in {backup}")
                if cancelled and not isinstance(error, asyncio.CancelledError):
                    raise _cancelled_after(error) from error
                raise
            restoring = asyncio.ensure_future(asyncio.to_thread(_restore, root, saved))
            cancelled = await _settle(restoring)
            left = restoring.result()
            keep = bool(left)
            if cancelled:
                stopped = asyncio.CancelledError()
                stopped.add_note(f"the landing failed ({detail}) and its files were put back")
                if left:
                    stopped.add_note(f"not put back: {', '.join(left)}; their copies are in {backup}")
                raise stopped
            return MergeResult(
                "failed", detail=detail, left_changed=tuple(left), kept_copies=str(backup) if keep else "", **counted
            )
        finally:
            if not keep:
                # Not waited for: once landed, no cancellation can come in
                # between the landing and its result.
                asyncio.get_running_loop().run_in_executor(None, _remove_tree, backup)

    async def _apply(self, root: Path, patch: bytes) -> _Run:
        return await self._git(
            root, *_APPLY, input=patch, check=False, timeout=settings.WORKTREE_GIT_WORK_SECONDS
        )

    async def _reads_back(self, root: Path, merged_tree: str, files: tuple[FileChange, ...]) -> bool:
        """Return whether the changed files, read as git reads them, are what the merge made."""
        gone = [change.path for change in files if change.status == "deleted"]
        if await asyncio.to_thread(_any_file, root, gone):
            return False
        present = [change.path for change in files if change.status != "deleted"]
        if not present:
            return True
        scratch = await _temporary_directory("nekopower-index-")
        try:
            env = {"GIT_INDEX_FILE": str(scratch / "index")}
            await self._git(root, "read-tree", merged_tree, env=env)
            await self._git(
                root,
                "add",
                "--all",
                "--pathspec-from-file=-",
                "--pathspec-file-nul",
                env=env,
                input=_nul_join(f":(literal,top){name}" for name in present),
                timeout=settings.WORKTREE_GIT_WORK_SECONDS,
            )
            tree = (await self._git(root, "write-tree", env=env)).lines()[0]
        finally:
            await asyncio.to_thread(_remove_tree, scratch)
        return tree == merged_tree

    async def _changes(self, root: Path, old: str, new: str) -> tuple[FileChange, ...]:
        statuses = (await self._git(root, "diff-tree", "-r", "-z", "--no-renames", "--name-status", old, new)).stdout
        fields = [_decode(part) for part in statuses.split(b"\0") if part]
        kinds = {fields[index + 1]: fields[index] for index in range(0, len(fields) - 1, 2)}
        counts = (await self._git(root, "diff-tree", "-r", "-z", "--no-renames", "--numstat", old, new)).stdout
        lines: dict[str, tuple[int | None, int | None]] = {}
        for entry in counts.split(b"\0"):
            if not entry:
                continue
            added, deleted, name = entry.split(b"\t", 2)
            lines[_decode(name)] = (_count(added), _count(deleted))
        names = {"A": "added", "D": "deleted"}
        return tuple(
            FileChange(name, names.get(kind[:1], "modified"), *lines.get(name, (None, None)))  # type: ignore[arg-type]
            for name, kind in kinds.items()
        )

    # -- worktrees and refs ----------------------------------------------------

    async def _registered(self, repo_root: Path) -> list[Path]:
        """Return the repository's linked worktrees; never its main checkout (always listed first)."""
        listed = await self._git(repo_root, "worktree", "list", "--porcelain", "-z")
        paths = [
            Path(_decode(entry[len(b"worktree ") :]))
            for entry in listed.stdout.split(b"\0")
            if entry.startswith(b"worktree ")
        ]
        return paths[1:]

    async def _forget(
        self, repo_root: Path, path: Path, worktree_id: str, *, remove_directory: bool, token: str = ""
    ) -> None:
        """Drop a worktree's registration, its directory when registered (or ``remove_directory``), and its refs."""
        listed = await self._registered(repo_root)
        registered = any(_same_path(known, path) for known in listed)
        # Another registered worktree of the same name owns the refs.
        theirs = any(known.name == worktree_id and not _same_path(known, path) for known in listed)
        common = Path((await self._git(repo_root, "rev-parse", "--path-format=absolute", "--git-common-dir")).lines()[0])
        if token and registered:
            noted = await asyncio.to_thread(_noted_token, common, path)
            if noted and noted != token:
                raise WorktreeError(
                    f"{path} holds a worktree made after this handle's; it is left: open() it for a handle of its own"
                )
        whose = await asyncio.to_thread(_whose, path, common) if registered and not remove_directory else "ours"
        if whose == "theirs":
            # Still listed, but what is there now is not that worktree (its
            # .git does not point back here, and it is not the directory
            # the worktree was made in): only the stale registration and
            # the refs go.
            await _to_the_end(asyncio.to_thread(_drop_registration, common, path))
            if not theirs:
                await self._delete_refs(repo_root, worktree_id)
            return
        if whose == "unknown":
            raise WorktreeError(
                f"{path} is no longer known to be this worktree (its .git is gone, and which directory it was "
                f"made in was not noted); it is left, and so are its registration and refs. It may be "
                f"another's: move it somewhere safe, then remove the worktree again"
            )
        # Not `git worktree remove`: when it cannot remove every file it
        # drops the registration all the same (and with it the note of
        # which directory it was), so a second try would not know the rest.
        # The directory goes first; the registration only once it is gone.
        # Ours: registered (and known to be ours above), made by this call,
        # or its .git file still points here.
        if registered or remove_directory or await asyncio.to_thread(_points_into, path, common):
            # Explicitly unlink dependency junctions/symlinks first so targets are never touched.
            noted_links = await asyncio.to_thread(_noted_links, common, path)
            if noted_links:
                await _to_the_end(asyncio.to_thread(_copy_trees.unlink_dependencies, path, noted_links))
            # Waited for to the end: the lock is not let go while it runs.
            await _to_the_end(asyncio.to_thread(_remove_tree, path))
            if await asyncio.to_thread(os.path.lexists, _fs(path)):
                raise WorktreeError(f"{path} could not be removed entirely; its registration and refs are kept")
        if registered:
            await _to_the_end(asyncio.to_thread(_drop_registration, common, path))
        if not theirs:
            await self._delete_refs(repo_root, worktree_id)

    async def _delete_refs(self, repo_root: Path, worktree_id: str) -> None:
        listed = await self._git(repo_root, "for-each-ref", "--format=%(refname)", f"{REF_PREFIX}{worktree_id}/")
        commands = "".join(f"delete {ref}\n" for ref in listed.lines()).encode()
        await self._git(repo_root, "update-ref", "--stdin", input=commands)

    async def _ref_ids(self, repo_root: Path) -> list[str]:
        listed = await self._git(repo_root, "for-each-ref", "--format=%(refname)", REF_PREFIX)
        return sorted({ref[len(REF_PREFIX) :].split("/", 1)[0] for ref in listed.lines()})

    async def _ref_exists(self, repo_root: Path, ref: str) -> bool:
        return await self._try_resolve(repo_root, ref) is not None

    async def _resolve_commit(self, root: Path, name: str, kind: str = "commit") -> str:
        found = await self._try_resolve(root, name if kind != "commit" else f"{name}^{{commit}}")
        if found is None:
            raise WorktreeError(f"{name!r} names no {kind} in {root}")
        return found

    async def _try_resolve(self, root: Path, name: str) -> str | None:
        found = await self._git(
            root,
            "rev-parse",
            "--verify",
            "--quiet",
            "--end-of-options",
            name,
            check=False,
            timeout=settings.WORKTREE_GIT_QUERY_SECONDS,
        )
        lines = found.lines()
        return lines[0] if lines else None

    async def _checkout_size(self, root: Path, commit: str) -> int:
        listed = await self._git(root, "ls-tree", "-r", "-l", "-z", commit, timeout=settings.WORKTREE_GIT_WORK_SECONDS)
        total = 0
        for entry in listed.stdout.split(b"\0"):
            size = entry.split(b"\t", 1)[0].split()[-1:] if entry else []
            if size and size[0].isdigit():
                total += int(size[0])
        return total

    # -- running git -----------------------------------------------------------

    async def _lock(self, repo_root: Path) -> asyncio.Lock:
        """Return the lock of the repository ``repo_root`` is a checkout of (its common git directory)."""
        # Asked each time: a checkout can be made one of another repository.
        common = (await self._git(repo_root, "rev-parse", "--path-format=absolute", "--git-common-dir")).lines()[0]
        key = os.path.normcase(os.path.abspath(common))
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    async def _git(
        self,
        cwd: Path,
        *args: str,
        env: Mapping[str, str] | None = None,
        input: bytes | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> _Run:
        run = await self._run(cwd, list(args), env=env, input=input, timeout=timeout or settings.WORKTREE_GIT_WORK_SECONDS)
        if check and not run.ok:
            raise GitError(args, run.returncode, run.stderr)
        return run

    async def _run(
        self,
        cwd: Path | None,
        args: list[str],
        *,
        env: Mapping[str, str] | None = None,
        input: bytes | None = None,
        timeout: float,
    ) -> _Run:
        command = [
            self._git_program,
            "-c", f"core.hooksPath={self._no_hooks}",
            "-c", "core.fsmonitor=false",
            "-c", "core.longpaths=true",
            "-c", "core.safecrlf=false",
            # A temporary index written split would leave a shared part
            # in the repository's git directory.
            "-c", "core.splitIndex=false",
            "-c", "gc.auto=0",
            "-c", "maintenance.auto=false",
            *args,
        ]  # fmt: skip
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in _REDIRECTING_ENV
        }
        environment.update(
            GIT_TERMINAL_PROMPT="0",
            GCM_INTERACTIVE="never",
            GIT_OPTIONAL_LOCKS="0",
            GIT_NO_LAZY_FETCH="1",
        )
        environment.update(env or {})
        # A group of its own, so that what git starts can be killed with it.
        extra: dict[str, object] = (
            {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=None if cwd is None else str(cwd),
                stdin=asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
                **extra,
            )
        except OSError as error:  # no git, no such directory, no handles left...
            raise GitError(args, -1, f"could not be started: {error}".encode()) from error
        try:
            # asyncio.timeout, not wait_for: in 3.11 wait_for can return the
            # finished command's result and drop a cancellation that came
            # in meanwhile.
            async with asyncio.timeout(timeout):
                stdout, stderr = await process.communicate(input)
        except TimeoutError:
            await _stop(process)
            raise GitError(args, None, f"no answer within {timeout} seconds".encode()) from None
        except BaseException:
            await _stop(process)
            raise
        return _Run(process.returncode, stdout, stderr)  # type: ignore[arg-type]


_APPLY = ("apply", "-p1", "--whitespace=nowarn", "--binary")


@dataclass(frozen=True)
class _Run:
    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def decode(self) -> str:
        return self.stdout.decode("utf-8", "surrogateescape")

    def lines(self) -> list[str]:
        """Return the output's lines; only a line feed ends one (a path may hold U+2028 and the like)."""
        return [line.rstrip("\r") for line in self.decode().split("\n") if line.rstrip("\r")]


def parse_git_version(text: str) -> tuple[int, int, int] | None:
    """Return the version in ``git --version``'s output (``git version 2.49.0.windows.1``)."""
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def _worktree_id(target: Path) -> str:
    """Return the id a worktree at ``target`` has: its directory's name (``paths.worktree_name``)."""
    return worktree_name(target.name)


def _in_the_way(
    root: Path,
    written: list[str],
    held: set[str],
    nested: set[str],
    ignore_links: set[str] | None = None,
) -> list[str]:
    """Return what under ``root`` that is the worktree's alone a checkout writing ``written`` would overwrite.

    ``held`` is what the worktree's snapshot has: the checkout replaces that
    as it should. Overwritten are a file at one of those paths, a directory
    there holding anything ``held`` has not, a file not ``held`` where one
    needs a directory, and anything inside a repository of its own
    (``nested``, directories, or any with a ``.git`` of its own). Host-created
    dependency links listed in ``ignore_links`` are unlinked before checkout
    and therefore do not block it.
    """
    ignored = ignore_links or set()
    hit: set[str] = set()
    for path in written:
        if path in ignored:
            continue
        full = _fs(root / path)
        if _is_file_or_link(full) or (os.path.isdir(full) and _holds_unsaved(root, path, held)):
            hit.add(path)
        for up in _parents(path):
            if up in ignored:
                continue
            # One in an ignored directory is not in ``nested`` (the
            # snapshot never looked there): its .git tells.
            if up in nested or os.path.lexists(_fs(root / up / ".git")):
                hit.add(up + "/")
            elif up not in held and _is_file_or_link(_fs(root / up)):
                hit.add(up)
    return sorted(hit)


def _put_index_back(copy: Path, index: Path) -> None:
    """Put a worktree's index file back from its copy."""
    shutil.copy2(copy, index)


def _is_existing_empty_dir(path: Path) -> bool:
    """Return whether ``path`` is an existing real (non-link) empty directory."""
    raw = _fs(path)
    if not os.path.isdir(raw) or _copy_trees.is_dir_link(path):
        return False
    try:
        with os.scandir(raw) as it:
            return next(it, None) is None
    except OSError:
        return False


def _nonlink_dir_stat(path: Path) -> tuple[int, int] | None:
    """Return ``(st_dev, st_ino)`` if ``path`` is a real non-link directory with a nonzero inode, else None."""
    raw = _fs(path)
    if _copy_trees.is_dir_link(path):
        return None
    try:
        st = os.lstat(raw)
    except OSError:
        return None
    if not stat.S_ISDIR(st.st_mode) or not st.st_ino:
        return None
    return (st.st_dev, st.st_ino)


def _claim_copy_target(
    target: Path, token: str
) -> tuple[bool, dict[str, int] | None, bool, tuple[int, int] | None]:
    """Claim ``target`` for a working copy and record its identity credentials before returning."""
    created = _claim_directory(target)
    if not created and not _is_existing_empty_dir(target):
        raise WorktreeError(f"{target} already exists and is not an empty directory")
    try:
        identity = _directory_identity(target)
    except OSError:
        identity = None
    xattr_ok = False
    dir_stat: tuple[int, int] | None = None
    if identity is None:
        xattr_ok = _copy_trees._set_copy_xattr(target, token)
        if not xattr_ok:
            dir_stat = _nonlink_dir_stat(target)
    return created, identity, xattr_ok, dir_stat


def _claimed_target_matches(
    target: Path,
    claimed_identity: dict[str, int] | None,
    claimed_xattr: bool,
    token: str,
    claimed_stat: tuple[int, int] | None,
) -> bool:
    """Return whether ``target`` still matches the identity recorded when it was claimed."""
    if claimed_identity is not None:
        try:
            return _directory_identity(target) == claimed_identity
        except OSError:
            return False
    if claimed_xattr:
        return _copy_trees._get_copy_xattr(target) == token
    if claimed_stat is not None:
        return _nonlink_dir_stat(target) == claimed_stat
    return False


def _verify_copy_handle(worktree: Worktree) -> str | None:
    """Verify on-disk copy metadata, project binding, token, and directory identity match ``worktree``."""
    meta = _copy_trees.read_copy_meta(worktree.path)
    if meta is None:
        return f"{worktree.path} has no working-copy metadata"
    if meta.get("id") != worktree.id:
        return f"{worktree.path} belongs to a different working copy ({meta.get('id')!r})"
    if not _copy_trees.same_path(str(meta.get("project_dir") or ""), worktree.repo_root):
        return f"{worktree.path} belongs to a different project directory"
    if worktree.token and meta.get("token") and meta["token"] != worktree.token:
        return f"{worktree.path} holds a working copy made after this handle's; open() it again"
    if not os.path.isdir(_fs(worktree.path)) or _copy_trees.is_dir_link(worktree.path):
        return f"{worktree.path} is not a working-copy directory"
    whose = _copy_trees.copy_matches_identity(worktree.path, meta, _directory_identity(worktree.path))
    if whose == "theirs" or (whose != "ours" and (isinstance(meta.get("identity"), dict) or meta.get("xattr") is True)):
        return f"{worktree.path} is not the working copy originally created for this handle"
    return None


def _claim_directory(target: Path) -> bool:
    """Make the directory a worktree goes in; return False when one was there already (empty, as it may be)."""
    os.makedirs(_fs(target.parent), exist_ok=True)
    try:
        os.mkdir(_fs(target))
    except FileExistsError:
        return False
    return True


def _unclaim_directory(target: Path) -> None:
    """Remove the (still empty) directory ``_claim_directory`` made; fine when it is gone."""
    with contextlib.suppress(FileNotFoundError):
        os.rmdir(_fs(target))


def _remove_index(index: Path) -> None:
    """Remove an index file that was not there before; fine when it is not there now."""
    with contextlib.suppress(FileNotFoundError):
        os.remove(index)


def _remove_files(root: Path, names: list[str]) -> None:
    """Remove those of ``names`` under ``root`` that are files or links, through real directories only.

    A name whose parent is now a link (or no directory) is left alone: the
    path leads elsewhere, maybe out of the worktree.
    """
    for name in names:
        if any(os.path.islink(_fs(root / up)) or not os.path.isdir(_fs(root / up)) for up in _parents(name)):
            continue
        target = _fs(root / name)
        if _is_file_or_link(target):
            os.remove(target)


def _holds_unsaved(root: Path, directory: str, held: set[str]) -> bool:
    """Return whether ``directory`` under ``root`` has a file or link that ``held`` has not."""
    top = _fs(root / directory)
    for current, dirs, files in os.walk(top):
        names = files + [name for name in dirs if os.path.islink(os.path.join(current, name))]
        for name in names:
            relative = os.path.relpath(os.path.join(current, name), top).replace(os.sep, "/")
            if f"{directory}/{relative}" not in held:
                return True
    return False


def _parents(path: str) -> list[str]:
    """Return the directories ``path`` is in, outermost first: ``a/b/c`` gives ``a`` and ``a/b``."""
    parts = path.split("/")
    return ["/".join(parts[:index]) for index in range(1, len(parts))]


def _start_ref(worktree_id: str) -> str:
    return f"{REF_PREFIX}{worktree_id}/start"


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def _nul_join(specs: Iterable[str]) -> bytes:
    return b"".join(spec.encode("utf-8", "surrogateescape") + b"\0" for spec in specs)


def _count(raw: bytes) -> int | None:
    return int(raw) if raw.isdigit() else None


def _failure(what: str, stderr: bytes) -> str:
    return f"{what}: {stderr.decode('utf-8', 'replace').strip()[:500]}"


def _cancelled_after(error: BaseException) -> asyncio.CancelledError:
    """Return the cancellation to raise in place of ``error``, carrying its notes (what was not put back)."""
    stopped = asyncio.CancelledError()
    for note in getattr(error, "__notes__", ()):
        stopped.add_note(note)
    return stopped


async def _temporary_directory(prefix: str) -> Path:
    """Make a temporary directory; cancelled meanwhile, it is removed again once made."""
    making = asyncio.ensure_future(asyncio.to_thread(tempfile.mkdtemp, prefix=prefix))
    if await _settle(making):
        if making.exception() is None:
            await _to_the_end(asyncio.to_thread(_remove_tree, making.result()))
        raise asyncio.CancelledError
    return Path(making.result())


async def _to_the_end(awaitable):  # type: ignore[no-untyped-def]
    """Await ``awaitable`` to the end, even when cancelled meanwhile; re-raise the cancellation after."""
    future = asyncio.ensure_future(awaitable)
    if await _settle(future):
        raise asyncio.CancelledError
    return future.result()


async def _settle(future: asyncio.Future) -> bool:  # type: ignore[type-arg]
    """Wait until ``future`` is done, cancelled or not meanwhile; return whether a cancellation came."""
    cancelled = False
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:  # the future's own failure; the caller reads it
            break
    return cancelled


async def _stop(process: asyncio.subprocess.Process) -> None:
    """Kill ``process`` and what it started, then read what is left of its output (which lets it be reaped).

    One piece no cancellation cuts, re-raising it after; the reading takes
    ``WORKTREE_GIT_STOP_SECONDS`` at most: a process that still holds the
    pipes then is left behind, its pipes closed.
    """

    async def finish() -> None:
        # POSIX: the group id stays git's while any member lives, reaped or
        # not (and killing an empty group does nothing). Windows: only while
        # git is not reaped is its process id surely still its own.
        if sys.platform != "win32" or process.returncode is None:
            await asyncio.to_thread(_kill_tree, process.pid)
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        try:
            await asyncio.wait_for(process.communicate(), settings.WORKTREE_GIT_STOP_SECONDS)
        except Exception:
            # Left behind: its pipes are let go of rather than waited on. The
            # transport is not public on Process, but closing it is what does it.
            transport = getattr(process, "_transport", None)
            if transport is not None:
                transport.close()

    await _to_the_end(finish())


def _kill_tree(pid: int) -> None:
    """Kill a process and every process it started.

    On POSIX its process group (git is started as one); on Windows its
    descendants, found by psutil, which leaves out a process whose id was
    reused. On Windows called only for a process not reaped yet.
    """
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            os.killpg(pid, signal.SIGKILL)  # type: ignore[attr-defined]
        return
    import psutil

    try:
        parent = psutil.Process(pid)
        family = [*parent.children(recursive=True), parent]
    except psutil.Error:
        return
    for member in family:
        with contextlib.suppress(psutil.Error):
            member.kill()


# -- file system helpers, run in a worker thread --------------------------------


def _fs(path: Path | str) -> str:
    """Return ``path`` in a form Windows opens past 260 characters; unchanged elsewhere."""
    text = os.path.abspath(str(path))
    if sys.platform != "win32" or text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def _absolute(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError):  # RuntimeError: links that loop (Python before 3.13)
        return Path(os.path.abspath(path))


def _find_root(directory: Path) -> tuple[Path, Path | None]:
    resolved = _absolute(directory)
    root = repository_root(resolved)
    try:
        return resolved, root if (root / ".git").exists() else None
    except OSError:
        return resolved, None


def _relative(path: Path, root: Path) -> str | None:
    try:
        relative = os.path.relpath(path, root)
    except ValueError:  # another drive
        return None
    if relative == ".":
        return ""
    relative = relative.replace(os.sep, "/")
    if relative == ".." or relative.startswith("../"):
        return None
    return relative


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _operation_in_progress(git_dir: Path) -> str | None:
    for name, operation in _IN_PROGRESS:
        if (git_dir / name).exists():
            return operation
    return None


def _copy_index(real: Path, copy: Path) -> bool:
    """Copy an index file with its times (git compares them with the files'); False when there is none."""
    try:
        shutil.copy2(real, copy)
    except FileNotFoundError:
        return False
    return True


def _present(root: Path, names: list[str]) -> list[str]:
    """Return those of ``names`` that are files or links under ``root``."""
    return [name for name in names if _is_file_or_link(_fs(root / name))]


def _any_file(root: Path, names: list[str]) -> bool:
    """Return whether any of ``names`` is a file or a link (a directory there may hold what replaced it)."""
    return any(_is_file_or_link(_fs(root / name)) for name in names)


def _is_file_or_link(path: str) -> bool:
    return os.path.islink(path) or os.path.isfile(path)


def _too_large(root: Path, names: list[str], limit: int) -> list[str]:
    large = []
    for name in names:
        try:
            info = os.lstat(_fs(root / name))
        except OSError:
            continue
        if info.st_size > limit:
            large.append(name)
    return large


def _check_target(target: Path, repo_root: Path) -> None:
    if _relative(target, repo_root) is not None:
        raise WorktreeError(f"{target} is inside the repository {repo_root}")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise WorktreeError(f"{target} already exists")


def _free_space(path: Path) -> int:
    probe = _absolute(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def _remove_tree(path: Path) -> None:
    _copy_trees.remove_tree_safe(path)


def _points_into(path: Path, common_dir: Path) -> bool:
    """Return whether ``path`` holds a worktree of the repository whose git directory is ``common_dir``."""
    try:
        text = (path / ".git").read_text(encoding="utf-8", errors="surrogateescape").strip()
    except OSError:
        return False
    if not text.startswith("gitdir:"):
        return False
    pointed = Path(text[len("gitdir:") :].strip())
    if not pointed.is_absolute():  # relative to where the .git file is
        pointed = path / pointed
    return _same_path(pointed.parent, common_dir / "worktrees")


def _whose(path: Path, common_dir: Path) -> Literal["ours", "theirs", "unknown"]:
    """Return whether what is at ``path`` is the worktree registered there.

    "ours" when nothing is there, its ``.git`` points back, or it is the
    very directory noted when the worktree was made (a removal that stopped
    half way, or a ``.git`` removed by hand); "theirs" when it is known to
    be another; "unknown" when nothing tells (no note: no birth time to go
    by, a reusable inode number alone could name a directory made since).
    """
    if not os.path.lexists(_fs(path)) or _points_into(path, common_dir):
        return "ours"
    return _noted_directory(path, common_dir)


_DIRECTORY_NOTE = "nekopower-directory"
_LINKS_NOTE = "nekopower-links.json"
_DELIVERY_NOTE = "nekopower-delivery.json"


def _note_links(admin: Path, links: Iterable[str]) -> None:
    _copy_trees._atomic_write_json(admin / _LINKS_NOTE, sorted(set(links)))


def _noted_links(common_dir: Path, path: Path) -> tuple[str, ...]:
    entry = _registration_of(common_dir, path)
    if entry is None:
        return ()
    try:
        raw = json.loads((entry / _LINKS_NOTE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return tuple(str(item) for item in raw if isinstance(item, str)) if isinstance(raw, list) else ()


def _note_delivery(common_dir: Path, path: Path, payload: Mapping[str, Any]) -> None:
    entry = _registration_of(common_dir, path)
    if entry is None:
        return
    _copy_trees._atomic_write_json(entry / _DELIVERY_NOTE, dict(payload))


def _noted_delivery(common_dir: Path, path: Path, *, strict: bool = False) -> dict[str, Any]:
    entry = _registration_of(common_dir, path)
    if entry is None:
        return {}
    note = entry / _DELIVERY_NOTE
    try:
        raw = json.loads(note.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        if strict:
            raise
        return {}
    if not isinstance(raw, dict):
        if strict:
            raise ValueError(f"delivery note {note} is not a JSON object")
        return {}
    return raw


def _directory_identity(path: Path) -> dict[str, int] | None:
    """Return what tells this directory from one made later at the same path, or None when nothing does.

    An inode number is reused once its directory is gone: it counts only
    together with when the directory was made.
    """
    found = os.stat(_fs(path))
    birth = _birth_of(found)
    if not found.st_ino or not birth:
        return None
    return {"dev": found.st_dev, "ino": found.st_ino, "birth": birth}


def _birth_of(found: os.stat_result, platform: str = sys.platform) -> int | None:
    """Return when a file was made, in nanoseconds, or None where the file system does not say."""
    birth = getattr(found, "st_birthtime_ns", None)
    if birth:
        return int(birth)
    if platform == "win32":  # there st_ctime is when it was made (before 3.12 the only name for it)
        return int(found.st_ctime_ns) or None
    seconds = getattr(found, "st_birthtime", None)  # macOS and the BSDs before 3.12
    return int(seconds * 1_000_000_000) if seconds else None


def _note_directory(admin: Path, path: Path, token: str) -> None:
    note: dict[str, object] = {"token": token}
    note.update(_directory_identity(path) or {})
    (admin / _DIRECTORY_NOTE).write_text(json.dumps(note), encoding="utf-8")


def _noted_token(common_dir: Path, path: Path) -> str | None:
    """Return the token noted for the worktree registered at ``path``, or None."""
    entry = _registration_of(common_dir, path)
    if entry is None:
        return None
    try:
        noted = json.loads((entry / _DIRECTORY_NOTE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    token = noted.get("token") if isinstance(noted, dict) else None
    return token if isinstance(token, str) else None


def _noted_directory(path: Path, common_dir: Path) -> Literal["ours", "theirs", "unknown"]:
    entry = _registration_of(common_dir, path)
    if entry is None:
        return "unknown"
    try:
        noted = json.loads((entry / _DIRECTORY_NOTE).read_text(encoding="utf-8"))
        now = _directory_identity(path)
    except (OSError, ValueError):
        return "unknown"
    keys = {"dev", "ino", "birth"}
    if not isinstance(noted, dict) or not keys <= set(noted) or now is None:
        return "unknown"
    return "ours" if all(noted[key] == now[key] for key in keys) else "theirs"


def _registration_of(common_dir: Path, path: Path) -> Path | None:
    """Return the registration directory of the worktree at ``path``, matched by where it was, or None."""
    admin = common_dir / "worktrees"
    if not admin.is_dir():
        return None
    for entry in admin.iterdir():
        try:
            pointed = (entry / "gitdir").read_text(encoding="utf-8", errors="surrogateescape").strip()
        except OSError:
            continue
        where = Path(pointed)
        if not where.is_absolute():  # relative to the registration
            where = entry / where
        if _same_path(where.parent, path):
            return entry
    return None


def _drop_registration(common_dir: Path, path: Path) -> None:
    """Remove the registration of a worktree of ours git would not remove, matched by where it was."""
    admin = common_dir / "worktrees"
    if not admin.is_dir():  # git removed it before it gave up
        return
    for entry in admin.iterdir():
        try:
            pointed = (entry / "gitdir").read_text(encoding="utf-8", errors="surrogateescape").strip()
        except OSError:
            continue
        where = Path(pointed)
        if not where.is_absolute():  # relative to the registration
            where = entry / where
        if _same_path(where.parent, path):
            _remove_tree(entry)
            if os.path.lexists(_fs(entry)):
                raise WorktreeError(f"the registration {entry} could not be removed entirely; the refs are kept")


@dataclass(frozen=True)
class _Saved:
    name: str
    copy: Path | None  # None: the file did not exist before
    made_dirs: tuple[str, ...] = ()
    was_directory: bool = False  # an (empty) directory was where the file goes


def _back_up(root: Path, names: list[str], backup: Path) -> list[_Saved]:
    saved = []
    manifest: dict[str, str] = {}
    for index, name in enumerate(names):
        source = _fs(root / name)
        # A directory where a file is to go is put back through its own files.
        if _is_file_or_link(source):
            copy = backup / str(index)
            shutil.copy2(source, _fs(copy), follow_symlinks=False)
            saved.append(_Saved(name, copy))
            manifest[str(index)] = name
            continue
        if os.path.isdir(source) and not os.path.islink(source):
            saved.append(_Saved(name, None, was_directory=True))
            continue
        made = []
        parent = (root / name).parent
        while not os.path.lexists(_fs(parent)) and _relative(parent, root):
            made.append(_relative(parent, root) or "")
            parent = parent.parent
        saved.append(_Saved(name, None, tuple(made)))
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return saved


def _restore(root: Path, saved: list[_Saved]) -> list[str]:
    """Put every saved file back as it was; return the ones that could not be."""
    left: list[str] = []
    for entry in saved:
        if entry.copy is None:
            target = _fs(root / entry.name)
            try:
                if _is_file_or_link(target):
                    os.remove(target)
            except OSError:
                left.append(entry.name)
    made = sorted({name for entry in saved for name in entry.made_dirs}, key=lambda name: name.count("/"), reverse=True)
    for name in made:
        with contextlib.suppress(OSError):
            os.rmdir(_fs(root / name))
    for entry in saved:
        if entry.copy is not None and not _restore_one(root, entry):
            left.append(entry.name)
        elif entry.was_directory:
            try:
                os.makedirs(_fs(root / entry.name), exist_ok=True)
            except OSError:
                left.append(entry.name)
    return sorted(set(left))


def _restore_one(root: Path, entry: _Saved) -> bool:
    assert entry.copy is not None
    target = _fs(root / entry.name)
    copy = _fs(entry.copy)
    try:
        # Untouched by the failed write (it may be the file that stopped it,
        # still locked): nothing to put back.
        if (
            os.path.isfile(target)
            and not os.path.islink(target)
            and not os.path.islink(copy)
            and filecmp.cmp(target, copy, shallow=False)
            and stat.S_IMODE(os.lstat(target).st_mode) == stat.S_IMODE(os.lstat(copy).st_mode)
        ):
            return True
        # Whatever else stands there goes first: a directory, or a link that
        # copying would follow, or that a link would not replace.
        if os.path.lexists(target) and (os.path.islink(target) or os.path.islink(copy) or not os.path.isfile(target)):
            if os.path.isdir(target) and not os.path.islink(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(copy, target, follow_symlinks=False)
    except OSError:
        return False
    return True
