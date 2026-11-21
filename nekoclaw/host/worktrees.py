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

* write objects, and refs below ``refs/nekoclaw/``;
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

Nothing calls this module yet; a later change wires it to side sessions.
"""

from __future__ import annotations

import asyncio
import contextlib
import filecmp
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

import config.nekoclaw_settings as settings
from nekoclaw.host.paths import worktree_name
from nekoclaw.host.repo_docs import repository_root

REF_PREFIX = "refs/nekoclaw/worktrees/"
"""Refs this module keeps a worktree's commits under: ``<prefix><id>/start``,
where the next landing merges from (where the worktree started, then what last
landed, or the project snapshot conflicts were prepared against)."""

IDENTITY = {
    "GIT_AUTHOR_NAME": "NekoClaw",
    "GIT_AUTHOR_EMAIL": "nekoclaw@localhost",
    "GIT_COMMITTER_NAME": "NekoClaw",
    "GIT_COMMITTER_EMAIL": "nekoclaw@localhost",
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
# The merge drivers git has itself; any other is a program the user configured.
_BUILT_IN_MERGE = frozenset({b"unspecified", b"set", b"unset", b"text", b"binary", b"union"})

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
    (``REF_PREFIX``), so an older handle does no harm.
    """

    id: str
    repo_root: Path
    path: Path
    subpath: str
    start: str

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
    """Where a new worktree starts. ``CurrentState`` now; a later change adds a remote branch."""

    async def commit(self, manager: WorktreeManager, repo_root: Path) -> str:
        raise NotImplementedError


class CurrentState(Start):
    """Start from what the project directory holds now, uncommitted changes and new files included."""

    async def commit(self, manager: WorktreeManager, repo_root: Path) -> str:
        return (await manager._snapshot(repo_root, "nekoclaw: worktree start")).commit


@dataclass(frozen=True)
class FromRef(Start):
    """Start from a commit already in the repository, named by a ref or an object name. Nothing is fetched."""

    ref: str

    async def commit(self, manager: WorktreeManager, repo_root: Path) -> str:
        commit = await manager._resolve_commit(repo_root, self.ref)
        listed = await manager._git(repo_root, "ls-tree", "-r", "-z", commit)
        entries = [entry.split(b"\t", 1) for entry in listed.stdout.split(b"\0") if entry]
        if any(info.startswith(b"160000 ") for info, _name in entries):
            raise WorktreeUnavailable(SUBMODULES, f"{self.ref} has submodules")
        programs = await manager._programs(repo_root, [name for _info, name in entries], source=commit)
        if programs:
            raise WorktreeUnavailable(programs[0], f"in {self.ref}: {programs[1]}")
        return commit


# -- how a result lands -------------------------------------------------------


class Landing:
    """How a worktree's result reaches the project. ``WorkingFiles`` now; a later change adds a pull request."""

    async def land(self, manager: WorktreeManager, worktree: Worktree, result: Snapshot) -> MergeResult:
        raise NotImplementedError


class WorkingFiles(Landing):
    """Merge the result into the project directory's working files, committing nothing."""

    async def land(self, manager: WorktreeManager, worktree: Worktree, result: Snapshot) -> MergeResult:
        return await manager._land_on_working_files(worktree, result)


# -- the manager --------------------------------------------------------------


class WorktreeManager:
    """Makes, lands and removes worktrees; one per host.

    Snapshots, landings and every change to a repository's worktrees are
    done one at a time per repository (a lock per repository root, on the
    event loop the manager is used on).
    """

    def __init__(self, git: str = "git") -> None:
        self._git_program = git
        # A directory that does not exist: no hook of the user's runs.
        self._no_hooks = str(Path(tempfile.gettempdir()) / f"nekoclaw-no-hooks-{uuid.uuid4().hex}")
        self._version: tuple[int, int, int] | None | Literal[False] = False
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_keys: dict[str, str] = {}
        self._making: set[str] = set()

    # -- public ----------------------------------------------------------------

    async def probe(self, directory: Path | str) -> Probe:
        """Return whether a worktree can be made for ``directory``; never raises for a directory that cannot."""
        version = await self.git_version()
        if version is None:
            return Probe(GIT_MISSING, f"{self._git_program!r} could not be run")
        minimum = tuple(settings.WORKTREE_GIT_MIN_VERSION)
        if version < minimum:
            return Probe(
                GIT_TOO_OLD,
                f"git {'.'.join(map(str, version))} is older than {'.'.join(map(str, minimum))}",
                git_version=version,
            )
        return await self._probe_repository(Path(directory), version)

    async def snapshot(self, directory: Path | str) -> Snapshot:
        """Write what the repository ``directory`` is in holds now as a commit; changes nothing the user sees."""
        found = await self._usable(directory)
        assert found.repo_root is not None
        async with await self._lock(found.repo_root):
            return await self._snapshot(found.repo_root, "nekoclaw: snapshot")

    async def create(self, project_dir: Path | str, path: Path | str, start: Start | None = None) -> Worktree:
        """Make a worktree of the repository ``project_dir`` is in at ``path`` and return it.

        ``path`` must not exist yet (an empty directory is accepted) and must
        lie outside the repository; its last part becomes the worktree's id.
        Raises WorktreeUnavailable when the directory does not qualify or the
        disk is too full, WorktreeError when ``path`` cannot be used, GitError
        when git fails; nothing is left behind then.
        """
        found = await self._usable(project_dir)
        assert found.repo_root is not None
        repo_root = found.repo_root
        target = await asyncio.to_thread(_absolute, Path(path))
        worktree_id = _worktree_id(target)
        await asyncio.to_thread(_check_target, target, repo_root)
        ref = _start_ref(worktree_id)
        # Two repositories have two locks: one making at a target at a time.
        claim = os.path.normcase(str(target))
        if claim in self._making:
            raise WorktreeError(f"a worktree is being made at {target}")
        self._making.add(claim)
        try:
            async with await self._lock(repo_root):
                return await self._create(repo_root, found.subpath, target, worktree_id, ref, start or CurrentState())
        finally:
            self._making.discard(claim)

    async def _create(
        self, repo_root: Path, subpath: str, target: Path, worktree_id: str, ref: str, start: Start
    ) -> Worktree:
        if await self._ref_exists(repo_root, ref):
            raise WorktreeError(f"a worktree named {worktree_id!r} already exists in {repo_root}")
        commit = await start.commit(self, repo_root)
        disk = await self.check_disk(target, await self._checkout_size(repo_root, commit))
        if not disk.enough:
            raise WorktreeUnavailable(DISK_SPACE, f"{disk.free} bytes free, {disk.needed} needed")
        existed = await asyncio.to_thread(target.exists)
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
        except BaseException:
            with contextlib.suppress(Exception):
                await self._forget(repo_root, target, worktree_id, remove_directory=not existed)
                if existed:
                    await asyncio.to_thread(target.mkdir, exist_ok=True)
            raise
        return Worktree(worktree_id, repo_root, target, subpath, commit)

    async def open(self, project_dir: Path | str, path: Path | str) -> Worktree:
        """Return the worktree at ``path`` made earlier for ``project_dir``, after a restart for instance.

        The project need not qualify for a new worktree now (a merge may be
        under way); it must be in the repository the worktree belongs to.
        Raises ValueError for a name no worktree can have.
        """
        found = await self._usable(project_dir, qualify=False)
        assert found.repo_root is not None
        target = await asyncio.to_thread(_absolute, Path(path))
        worktree_id = _worktree_id(target)
        registered = any(_same_path(known, target) for known in await self._registered(found.repo_root))
        if not registered or not await asyncio.to_thread(target.is_dir):
            raise WorktreeError(f"{target} is not a worktree of {found.repo_root}")
        start = await self._resolve_commit(found.repo_root, _start_ref(worktree_id))
        return Worktree(worktree_id, found.repo_root, target, found.subpath, start)

    async def merge_back(self, worktree: Worktree, landing: Landing | None = None) -> MergeResult:
        """Land what was done in ``worktree``; see ``MergeResult`` for the outcomes. Raises GitError when git fails."""
        async with await self._lock(worktree.repo_root):
            start = await self._start(worktree)
            result = await self._snapshot(worktree.path, "nekoclaw: worktree result", start)
            return await (landing or WorkingFiles()).land(self, worktree, result)

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
        async with await self._lock(worktree.repo_root):
            start = await self._start(worktree)
            if start != conflict.base:
                raise WorktreeError("the worktree landed or was prepared since this merge was tried; merge again")
            now = await self._snapshot(worktree.path, "nekoclaw: worktree result", start)
            if now.tree != conflict.result_tree:
                raise WorktreeError("the worktree changed after the merge was tried; merge again")
            if now.skipped:
                listed = await self._git(worktree.path, "ls-tree", "-r", "-z", "--name-only", conflict.merged_tree)
                paths = [_decode(name) for name in listed.stdout.split(b"\0") if name]
                hit = [
                    name
                    for name in now.skipped
                    if any(path == name.rstrip("/") or path.startswith(name.rstrip("/") + "/") for path in paths)
                ]
                if hit:
                    raise WorktreeError(
                        f"preparing would overwrite what only the worktree has (left out as too large, or a "
                        f"repository of its own): {', '.join(hit)}"
                    )
            half_done = await self._commit_tree(
                worktree.path, conflict.merged_tree, conflict.current, "nekoclaw: conflicts to resolve"
            )
            try:
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
                await self._git(worktree.repo_root, "update-ref", _start_ref(worktree.id), conflict.current)
            except BaseException as error:
                # Its files as they were (the result snapshot holds them) and
                # its start as it was, so the same conflict can be prepared
                # again: one piece no further cancellation cuts.
                async def put_back() -> None:
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
                        error.add_note(f"the worktree could not be put back: {_failure('checkout', back.stderr)}")
                    await self._put_start_back(worktree, start, error)

                putting = asyncio.ensure_future(put_back())
                cancelled = await _settle(putting)
                if putting.exception() is not None:
                    error.add_note(f"putting the worktree back failed: {putting.exception()}")
                if cancelled and not isinstance(error, asyncio.CancelledError):
                    raise asyncio.CancelledError from error
                raise
        return replace(worktree, start=conflict.current)

    async def has_changes(self, worktree: Worktree) -> bool:
        """Return whether the worktree holds anything not landed yet; False when its directory is gone."""
        if not await asyncio.to_thread(worktree.path.is_dir):
            return False
        async with await self._lock(worktree.repo_root):
            return await self._has_changes(worktree.repo_root, worktree.path, worktree.id, worktree.start)

    async def remove(self, worktree: Worktree) -> None:
        """Remove the worktree, its registration and its refs, whatever it holds; fine when already gone.

        A directory at its path the repository has no registration for is
        left alone: it is not known to be this worktree.
        """
        async with await self._lock(worktree.repo_root):
            await self._forget(worktree.repo_root, worktree.path, worktree.id, remove_directory=False)

    async def reconcile(
        self, project_dir: Path | str, base_dir: Path | str, known: Iterable[str]
    ) -> ReconcileReport:
        """Remove what this module left behind for ``project_dir``'s repository that nothing knows of.

        Considers the repository's worktrees directly in ``base_dir`` and its
        refs below ``REF_PREFIX``. One whose id is not in ``known`` is
        removed when it holds no changes (or its directory is gone) and kept
        and reported when it does. Directories in ``base_dir`` this
        repository does not know are left alone: they may be another's. The
        project need not qualify for a new worktree now.
        """
        found = await self._usable(project_dir, qualify=False)
        assert found.repo_root is not None
        repo_root = found.repo_root
        known_ids = set(known)
        base = await asyncio.to_thread(_absolute, Path(base_dir))
        report = ReconcileReport()
        async with await self._lock(repo_root):
            registered = await self._registered(repo_root)
            # A ref is left alone while a worktree of that name exists anywhere.
            registered_ids = {path.name for path in registered}
            for path in registered:
                if not _same_path(path.parent, base) or path.name in known_ids:
                    continue
                start = await self._try_resolve(repo_root, _start_ref(path.name))
                if start is None:
                    continue  # no ref of ours: not known to be one of ours
                try:
                    if await asyncio.to_thread(path.is_dir) and await self._has_changes(
                        repo_root, path, path.name, start
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
        """Return the version of git, or None when it cannot be run. Asked once."""
        if self._version is False:
            try:
                run = await self._run(None, ["--version"], timeout=settings.WORKTREE_GIT_QUERY_SECONDS)
            except (OSError, GitError):
                self._version = None
            else:
                self._version = parse_git_version(run.decode())
        return self._version

    # -- probing ---------------------------------------------------------------

    async def _usable(self, directory: Path | str, *, qualify: bool = True) -> Probe:
        """Return the probe of ``directory``, raising WorktreeUnavailable when it is not usable.

        With ``qualify`` False only the repository is looked for (git runs,
        the directory is in a work tree, not a bare one): enough to find or
        clean up worktrees made earlier, whatever state the project is in now.
        """
        if qualify:
            found = await self.probe(directory)
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

    async def _probe_repository(self, directory: Path, version: tuple[int, int, int], qualify: bool = True) -> Probe:
        def unusable(reason: str, detail: str = "", root: Path | None = None, subpath: str = "") -> Probe:
            return Probe(reason, detail, root, subpath, version)

        resolved, root = await asyncio.to_thread(_find_root, directory)
        if not await asyncio.to_thread(resolved.is_dir):
            return unusable(NOT_A_REPOSITORY, f"{directory} is not a directory")
        if root is None:
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
            return Probe(None, "", top, subpath, version)
        try:
            found = await self._qualify(top, subpath, Path(git_dir))
        except GitError as error:
            return unusable(GIT_FAILED, str(error), top, subpath)
        return found if found is not None else Probe(None, "", top, subpath, version)

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
        # Objects missing from a partial clone are fetched when read; git
        # before 2.44 does not let that be turned off (GIT_NO_LAZY_FETCH).
        extension = await self._git(top, "config", "--get", "extensions.partialClone", check=False)
        promisors = await self._git(
            top, "config", "--type=bool", "--get-regexp", r"^remote\..*\.promisor$", check=False
        )
        if extension.decode().strip() or any(line.endswith(" true") for line in promisors.lines()):
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

    async def _programs(self, root: Path, names: list[bytes], source: str | None = None) -> tuple[str, str] | None:
        """Return why git would run a program the user configured on these paths, or None.

        That is a content filter (``filter`` attribute: Git LFS, git-crypt,
        ...) or a merge driver other than git's own (``merge`` attribute, or
        ``merge.default``). A snapshot, a merge or a checkout would run it;
        the host does not run the user's programs, and what one makes in a
        worktree need not match the project. With ``source`` the attributes
        are read from that tree, as a checkout of it would. Returns a reason
        key and what it applies to.
        """
        default = await self._git(root, "config", "--get", "merge.default", check=False)
        driver = default.stdout.strip()
        if driver and driver not in _BUILT_IN_MERGE:
            return MERGE_DRIVERS, f"merge.default is {_decode(driver)}"
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
            if attribute == b"filter" and value not in (b"unspecified", b"unset"):
                return CONTENT_FILTERS, f"{_decode(path)} goes through a content filter (Git LFS, git-crypt, ...)"
            if attribute == b"merge" and value not in _BUILT_IN_MERGE:
                return MERGE_DRIVERS, f"{_decode(path)} is merged by the program {_decode(value)}"
        return None

    # -- snapshots -------------------------------------------------------------

    async def _snapshot(self, root: Path, message: str, start: str | None = None) -> Snapshot:
        """Write what ``root`` holds as a commit (see ``Snapshot``).

        A file ``start`` (a worktree's start) has is taken whatever its size:
        it is the worktree's own, and leaving it out would read as deleting it.
        """
        head = await self._try_resolve(root, "HEAD")
        scratch = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="nekoclaw-index-"))
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
            others = await self._git(root, "ls-files", "-z", "--others", "--exclude-standard", env=env)
            names = [_decode(name) for name in others.stdout.split(b"\0") if name]
            # A repository of its own inside (listed with a trailing slash)
            # would be recorded as a submodule link and its files lost.
            nested = [name for name in names if name.endswith("/")]
            files = [name for name in names if not name.endswith("/")]
            large = await asyncio.to_thread(_too_large, root, files, settings.WORKTREE_UNTRACKED_MAX_BYTES)
            if large and start:
                known = await self._git(root, "ls-tree", "-r", "-z", "--name-only", start)
                had = {_decode(name) for name in known.stdout.split(b"\0") if name}
                large = [name for name in large if name not in had]
            skipped = nested + large
            specs = ["."] + [f":(exclude,literal,top){name.rstrip('/')}" for name in skipped]
            # What is to be added is checked again each time: a filter may
            # have been set up, or a file it applies to made, since.
            cached = await self._git(root, "ls-files", "-z", "--cached", env=env)
            adding = [name for name in cached.stdout.split(b"\0") if name]
            adding += [name.encode("utf-8", "surrogateescape") for name in files if name not in skipped]
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
            tree = (await self._git(root, "write-tree", env=env)).lines()[0]
        finally:
            await asyncio.to_thread(shutil.rmtree, scratch, True)
        commit = await self._commit_tree(root, tree, head, message)
        return Snapshot(commit, tree, tuple(skipped))

    async def _marked(self, root: Path, env: Mapping[str, str]) -> list[bytes]:
        """Return the paths of an index's entries marked assume-unchanged or skip-worktree."""
        listed = await self._git(root, "ls-files", "-z", "-v", env=env, timeout=settings.WORKTREE_GIT_WORK_SECONDS)
        return [
            entry[2:] for entry in listed.stdout.split(b"\0") if entry and (entry[:1].islower() or entry[:1] == b"S")
        ]

    async def _commit_tree(self, root: Path, tree: str, parent: str | None, message: str) -> str:
        args = ["commit-tree", "-m", message]
        if parent:
            args += ["-p", parent]
        return (await self._git(root, *args, tree, env=IDENTITY)).lines()[0]

    async def _has_changes(self, repo_root: Path, path: Path, worktree_id: str, start: str) -> bool:
        start = await self._try_resolve(repo_root, _start_ref(worktree_id)) or start
        now = await self._snapshot(path, "nekoclaw: worktree result", start)
        # Files left out of the snapshot (large, or a repository of its own)
        # were made in the worktree: the project's are never copied into it.
        return bool(now.skipped) or now.tree != await self._resolve_commit(path, f"{start}^{{tree}}", kind="tree")

    # -- landing on the working files ------------------------------------------

    async def _land_on_working_files(self, worktree: Worktree, result: Snapshot) -> MergeResult:
        root = worktree.repo_root
        base = await self._start(worktree)
        # The user's files may hold conflict markers of their own then: no
        # landing on top of them.
        git_dir = (await self._git(root, "rev-parse", "--absolute-git-dir")).lines()[0]
        operation = await asyncio.to_thread(_operation_in_progress, Path(git_dir))
        if operation:
            return MergeResult(
                "failed",
                detail=f"a {operation} is in progress in the project; land when it is done",
                skipped=result.skipped,
                result=result.commit,
                result_tree=result.tree,
                base=base,
            )
        current = await self._snapshot(root, "nekoclaw: project before landing")
        merged = await self._git(
            root,
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
        backup = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="nekoclaw-landing-"))
        keep = False
        try:
            try:
                saved = await asyncio.to_thread(_back_up, root, [change.path for change in files], backup)
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
                    raise asyncio.CancelledError from error
                raise
            restoring = asyncio.ensure_future(asyncio.to_thread(_restore, root, saved))
            cancelled = await _settle(restoring)
            left = restoring.result()
            keep = bool(left)
            if cancelled:
                raise asyncio.CancelledError
            return MergeResult(
                "failed", detail=detail, left_changed=tuple(left), kept_copies=str(backup) if keep else "", **counted
            )
        finally:
            if not keep:
                await asyncio.to_thread(shutil.rmtree, backup, True)

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
        scratch = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="nekoclaw-index-"))
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
            await asyncio.to_thread(shutil.rmtree, scratch, True)
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

    async def _forget(self, repo_root: Path, path: Path, worktree_id: str, *, remove_directory: bool) -> None:
        """Drop a worktree's registration, its directory when registered (or ``remove_directory``), and its refs."""
        registered = any(_same_path(known, path) for known in await self._registered(repo_root))
        common = Path((await self._git(repo_root, "rev-parse", "--path-format=absolute", "--git-common-dir")).lines()[0])
        removed_by_git = False
        if registered:
            removed = await self._git(
                repo_root,
                "worktree",
                "remove",
                "--force",
                str(path),
                check=False,
                timeout=settings.WORKTREE_GIT_WORK_SECONDS,
            )
            removed_by_git = removed.returncode == 0
        # Ours: registered, made by this call, or (git dropped the
        # registration before it gave up) its .git file still points here.
        if registered or remove_directory or await asyncio.to_thread(_points_into, path, common):
            await asyncio.to_thread(_remove_tree, path)
            if await asyncio.to_thread(os.path.lexists, _fs(path)):
                raise WorktreeError(f"{path} could not be removed entirely; its registration and refs are kept")
        if registered and not removed_by_git:
            await asyncio.to_thread(_drop_registration, common, path)
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
        where = os.path.normcase(str(repo_root))
        key = self._lock_keys.get(where)
        if key is None:
            common = (await self._git(repo_root, "rev-parse", "--path-format=absolute", "--git-common-dir")).lines()[0]
            key = self._lock_keys[where] = os.path.normcase(os.path.abspath(common))
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
        extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=None if cwd is None else str(cwd),
            stdin=asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
            **extra,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(input), timeout)
        except asyncio.TimeoutError:
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
        return [line for line in self.decode().splitlines() if line]


def parse_git_version(text: str) -> tuple[int, int, int] | None:
    """Return the version in ``git --version``'s output (``git version 2.49.0.windows.1``)."""
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def _worktree_id(target: Path) -> str:
    """Return the id a worktree at ``target`` has: its directory's name (``paths.worktree_name``)."""
    return worktree_name(target.name)


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
        except BaseException:  # the future's own failure; the caller reads it
            break
    return cancelled


async def _stop(process: asyncio.subprocess.Process) -> None:
    """Kill ``process`` and read what is left of its output, which is what lets it be reaped, to the end."""
    with contextlib.suppress(ProcessLookupError):
        process.kill()
    with contextlib.suppress(Exception):
        await _to_the_end(process.communicate())


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
    except OSError:
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
    def clear_read_only(function, failed, _info) -> None:  # type: ignore[no-untyped-def]
        with contextlib.suppress(OSError):
            os.chmod(failed, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            function(failed)

    shutil.rmtree(_fs(path), onerror=clear_read_only)


def _points_into(path: Path, common_dir: Path) -> bool:
    """Return whether ``path`` holds a worktree of the repository whose git directory is ``common_dir``."""
    try:
        text = (path / ".git").read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not text.startswith("gitdir:"):
        return False
    pointed = Path(text[len("gitdir:") :].strip())
    return _same_path(pointed.parent, common_dir / "worktrees")


def _drop_registration(common_dir: Path, path: Path) -> None:
    """Remove the registration of a worktree of ours git would not remove, matched by where it was."""
    admin = common_dir / "worktrees"
    if not admin.is_dir():  # git removed it before it gave up
        return
    for entry in admin.iterdir():
        try:
            pointed = (entry / "gitdir").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if _same_path(Path(pointed).parent, path):
            _remove_tree(entry)


@dataclass(frozen=True)
class _Saved:
    name: str
    copy: Path | None  # None: the file did not exist before
    made_dirs: tuple[str, ...] = ()


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
        made = []
        parent = (root / name).parent
        while not os.path.lexists(_fs(parent)) and _relative(parent, root):
            made.append(_relative(parent, root) or "")
            parent = parent.parent
        saved.append(_Saved(name, None, tuple(made)))
    (backup / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
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
