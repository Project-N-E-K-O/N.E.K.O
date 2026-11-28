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

"""Task agents that change files side by side, and a leader's team (design 17.12).

A part of ``TaskAgentManager`` (it is mixed into it), kept in a file of its
own: everything here is about the git worktrees members of a project work
in (``worktrees.py`` makes and lands them), and about the team a leader
starts for one commission.

* **Who works in a worktree.** A session whose ``seat`` is ``tree``: a
  member that was added to a project beside the one that works in its
  directory, and a task agent of a leader's team that changes files. A
  worktree is made for each commission as it starts, from the project's
  directory as it stands then, and such sessions do not wait for one
  another. Where none can be made (no git repository, too many of them, the
  disk is full) a member works in the project's directory, in its turn, as
  every member did before; a team's task agent is not started.
* **Landing.** When the commission is done the host merges what was changed
  into the project's working files, committing nothing, once whoever works
  in that directory is between two steps. What clashes goes back to the
  task agent to merge, a few times; past that, or where the user is to be
  asked first, the worktree stays and the user decides (land it, discard
  it, see to it themselves).
* **A team.** ``start_team`` starts several task agents for one commission
  of a leader, which keeps the commission, waits for them with the tools a
  task agent has for those it started, and reports once. The user is asked
  once for a team that changes files. An inspector may look at each one's
  changes before they land.

What is on record of each worktree is a row of the ledger (``ledger.Tree``).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import os
import shutil
import time
from pathlib import Path
from collections.abc import AsyncIterator, Iterator
from typing import Any

from nekopower.host.ledger import ACTIVE_STATUSES, ENDED_STATUSES, TREE_STATES, TREE_STATES_LEFT, Commission, Session, Tree
from nekopower.host.permissions import OPEN_SESSION

logger = logging.getLogger(__name__)

# Why a worktree was not landed (``Tree.reason``).
REASON_CONFLICT = "conflict"
REASON_FAILED = "failed"
REASON_INSPECTION = "inspection"
REASON_ASK = "ask"
REASON_STOPPED = "stopped"
REASON_RESTART = "restart"
# What it changed was landed, but for files that are never carried over (too large, or a
# repository of their own): those are still in it.
REASON_SKIPPED = "skipped"
TREE_REASONS: tuple[str, ...] = (
    REASON_CONFLICT, REASON_FAILED, REASON_INSPECTION, REASON_ASK, REASON_STOPPED, REASON_RESTART, REASON_SKIPPED,
)

# What an inspector found (the ``inspected`` event).
CHECK_PASSED = "pass"
CHECK_FAILED = "fail"
CHECK_NONE = "none"
# (Not a verdict that is put on record: the inspector could not be stopped, and is still at it.)
CHECK_STUCK = "stuck"

# The states of a worktree whose commission is under way.
_AT_WORK: frozenset[str] = frozenset({"making", "active", "landing", "resolving"})
# The states of a worktree that is shown, and takes room among those a project may have: not
# one the user discarded, which is only waiting to be removed.
_SHOWN_STATES: tuple[str, ...] = tuple(state for state in TREE_STATES if state != "discarding")
# How often what a landing waits for is looked at again, where nothing tells of it.
_POLL_SECONDS = 0.2
# How long the runs that work in a worktree are waited for to be over before it is looked at
# for the last time; past that it is kept as it is.
_GONE_SECONDS = 15.0
# What a merge is said to have come to when files that clashed still hold conflict markers
# (``MergeResult.detail``): nothing was merged, and nothing is put into the worktree anew.
_MARKERS_LEFT = "conflict markers are left"
# How a line begins that begins one side of a clash, and how much of a file is read at a time
# when it is looked through for one.
_MARKS = (b"<<<<<<< ", b">>>>>>> ")
_MARKERS_CHUNK_BYTES = 1024 * 1024


def _has_markers(path: Path) -> bool:
    """Whether a file still holds conflict markers of a merge: a line that begins one side of a clash.

    The whole file is looked through, a piece at a time: a clash far
    into a large file is one all the same.
    """
    try:
        with open(path, "rb") as file:
            # (The start of a file is the start of a line.)
            kept = b"\n"
            while True:
                piece = file.read(_MARKERS_CHUNK_BYTES)
                if not piece:
                    return False
                data = kept + piece
                if any(end + mark in data for end in (b"\n", b"\r") for mark in _MARKS):
                    return True
                # A line's end and the beginning of a marker may lie across two pieces.
                kept = data[-len(_MARKS[0]) :]
    except OSError:
        return False


@dataclasses.dataclass
class _Done:
    """How a commission worked on in a worktree said it is done; kept while what it changed is landed."""

    text: str
    reason: str
    delivery: Any
    # How many texts its session had been sent when it said so: one sent since began another run.
    sent: int
    # Set once the run that said so is over: nothing of it is under way in the worktree.
    run_over: asyncio.Event = dataclasses.field(default_factory=asyncio.Event)


@dataclasses.dataclass
class _Quiet:
    """A landing in a project's directory: the sessions that write there stop before their next request until it is through."""

    reached: set[str] = dataclasses.field(default_factory=set)
    changed: asyncio.Event = dataclasses.field(default_factory=asyncio.Event)
    go: asyncio.Event = dataclasses.field(default_factory=asyncio.Event)


def _says_it_is(path: Path, name: str, repository: str | None) -> bool:
    """Whether a directory holds the ``.git`` file of the git worktree called ``name`` of the repository at ``repository``.

    Git writes ``gitdir: <repository>/.git/worktrees/<name>`` into a
    worktree. One of the same name of another repository is not it
    (be that one beside the repository, or one the repository lies
    in); nor is one whose repository that line does not tell (it keeps
    its git directory elsewhere), or where the record does not say
    which the repository was (``Tree.repo_root``): not known to be it,
    it is left alone.
    """
    if not repository:
        return False
    try:
        said = (path / ".git").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return False
    if not said.startswith("gitdir:"):
        return False
    pointed = Path(said[len("gitdir:") :].strip())
    if not pointed.is_absolute():
        # (Written so where git is set to: relative to where the ``.git`` file is.)
        pointed = Path(os.path.normpath(path / pointed))
    parts = pointed.parts
    if len(parts) < 4 or parts[-3:] != (".git", "worktrees", name):
        return False
    return os.path.normcase(os.path.realpath(Path(*parts[:-3]))) == os.path.normcase(os.path.realpath(repository))


def _repo_from_worktree_gitfile(path: Path) -> Path | None:
    """Return the repository root named by a worktree's ``.git`` file, or None."""
    try:
        said = (path / ".git").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not said.startswith("gitdir:"):
        return None
    pointed = Path(said[len("gitdir:") :].strip())
    if not pointed.is_absolute():
        pointed = Path(os.path.normpath(path / pointed))
    parts = pointed.parts
    if len(parts) < 4 or parts[-3:-1] != (".git", "worktrees"):
        return None
    return Path(*parts[:-3])


def _is_link(path: Path) -> bool:
    """Whether a path is a link to somewhere else (a symbolic link, or a junction), and no directory of its own."""
    if path.is_symlink():
        return True
    try:
        there = os.path.join(os.path.realpath(path.parent), path.name)
        return os.path.normcase(os.path.realpath(path)) != os.path.normcase(there)
    except OSError:
        return True


def _is_empty(path: Path) -> bool:
    """Whether a directory is there with nothing in it."""
    try:
        return not any(path.iterdir())
    except OSError:
        return False


def _remove_empty(path: Path) -> None:
    """Remove a directory that has nothing in it; one that cannot be removed (it is in use) is left."""
    try:
        os.rmdir(path)
    except OSError:
        pass


def _remove_entry(entry: Path) -> None:
    """Remove a file, a link, or a directory with all in it; what cannot be removed (it is in use) is left."""
    try:
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, True)
            if os.path.lexists(entry):
                os.rmdir(entry)  # a junction, which is not gone into, or a directory left empty
        else:
            entry.unlink()
    except OSError:
        pass


def _same_dir(first: str | None, second: str | None) -> bool:
    if not first or not second:
        return False
    return os.path.normcase(os.path.normpath(first)) == os.path.normcase(os.path.normpath(second))


def files_of(changed: Any, most: int) -> tuple[list[dict[str, Any]], int]:
    """Return the files a landing changed as they go on record, ``most`` of them, and how many more there are."""
    listed = [
        {"path": item.path, "status": item.status, "added": item.added, "deleted": item.deleted}
        for item in list(changed)[:most]
    ]
    return listed, max(0, len(changed) - most)


def files_text(files: list[dict[str, Any]], more: int, max_tokens: int) -> str:
    """Return files a landing changed in one line, for a model to read; within ``max_tokens``."""
    from config.prompts import prompts_nekopower_brain as texts
    from utils.tokenize import truncate_to_tokens

    parts: list[str] = []
    for item in files:
        if item.get("status") == "added":
            parts.append(texts.TREE_FILE_ADDED.format(path=item["path"]))
        elif item.get("status") == "deleted":
            parts.append(texts.TREE_FILE_DELETED.format(path=item["path"]))
        elif item.get("added") is None or item.get("deleted") is None:
            parts.append(texts.TREE_FILE_BINARY.format(path=item["path"]))
        else:
            parts.append(texts.TREE_FILE_CHANGED.format(path=item["path"], added=item["added"], deleted=item["deleted"]))
    said = ", ".join(parts)
    if more:
        said += texts.TREE_FILES_MORE.format(more=more)
    return truncate_to_tokens(said, max_tokens)


def outside_of(changed: Any, subpath: str) -> list[str]:
    """Return the files a landing changed that lie outside the project's directory, elsewhere in its repository.

    ``subpath`` is where the project's directory is in the repository,
    empty when it is the repository's root (then nothing is outside).
    """
    if not subpath:
        return []
    below = subpath.rstrip("/") + "/"
    return [item.path for item in changed if not str(item.path).startswith(below)]


def names_text(names: Any, most: int, max_tokens: int) -> str:
    """Return file names in one line, ``most`` by name and the rest counted; within ``max_tokens``."""
    from config.prompts import prompts_nekopower_brain as texts
    from utils.tokenize import truncate_to_tokens

    listed = [str(name) for name in names]
    said = ", ".join(listed[:most])
    if len(listed) > most:
        said += texts.TREE_FILES_MORE.format(more=len(listed) - most)
    return truncate_to_tokens(said, max_tokens)


class TreeWork:
    """What ``TaskAgentManager`` does about git worktrees and teams; see the module docstring."""

    def _init_tree_work(self) -> None:
        from nekopower.host.landing_settings import LandingStore
        from nekopower.host.worktrees import WorktreeManager

        # The one that makes and lands worktrees for this host: its locks are its own.
        self.worktrees: Any = WorktreeManager()
        # What the user set about landing (``landing_settings.py``).
        self.landing = LandingStore(self._paths.landing_json)
        # Handles of the worktrees this process made or found again, by id.
        self._tree_handles: dict[str, Any] = {}
        # The worktrees this process made, found again or took on record: those an earlier
        # process left are told from them when what is on record is gone through.
        self._trees_mine: set[str] = set()
        # By commission: the worktree it is worked on in, while it is under way.
        self._tree_of: dict[str, str] = {}
        # By session: the same, for telling whether a session writes in its project's directory.
        self._tree_sessions: dict[str, str] = {}
        # Sessions that are to work in a worktree and work in their project's directory
        # for the commission they are at: none could be made for it.
        self._direct_now: set[str] = set()
        # Sessions that wait, as they start, for their turn in a project's directory, in
        # the order they came (``_takes_its_turn``).
        self._turn_waiters: dict[str, int] = {}
        self._turn_serial = 0
        # By directory: when it was looked at, and whether a worktree can be made for it.
        self._tree_probes: dict[str, tuple[float, bool]] = {}
        # One landing at a time in a project's directory.
        self._landing_locks: dict[str, asyncio.Lock] = {}
        # By project: the landing under way in its directory (``_Quiet``).
        self._quiet: dict[str, _Quiet] = {}
        # By commission: how it said it is done, while what it changed is landed, and the task that lands it.
        self._dones: dict[str, _Done] = {}
        self._landings: dict[str, asyncio.Task[Any]] = {}
        # Worktrees the user is having landed, discarded or kept this moment, and those an earlier
        # process left that are being gone through: a commission goes on in none of them meanwhile.
        self._trees_in_hand: set[str] = set()
        # The commissions of the worktrees the user's decision is being carried out on, by worktree.
        self._decided_for: dict[str, str] = {}
        # Commissions a landing is putting down as done this moment: an end that is the landing's
        # own, and not one that left its worktree behind.
        # What is said to one of them meanwhile waits until that is written (``_nothing_more_said``);
        # so it is with one whose changes are being left for the user.
        self._landing_ends: dict[str, asyncio.Future[None]] = {}
        # How many hold each of them back this moment: the end of a run may be written within a
        # landing's hold. Kept until the last of them is through.
        self._landing_holds: dict[str, int] = {}
        # Projects that are being given another directory this moment (``project_changing``).
        self._projects_changing: set[str] = set()
        # Held while a project's worktrees are counted and one more is put on record: two that
        # start at once do not both take the last room.
        self._tree_room = asyncio.Lock()
        # How many worktrees are being made this moment, and how often those an earlier process
        # left are being gone through: neither while the other is under way, so one that is
        # being made is not taken for one that was left.
        self._trees_making = 0
        self._trees_looking = 0
        self._tree_tasks: set[asyncio.Task[Any]] = set()
        # Inspectors the host started, by their commission: what each reported, once it is over.
        self._inspections: dict[str, asyncio.Future[tuple[str, str]]] = {}
        # Commissions of leaders for which the user allowed a team that changes files.
        self._team_allowed: set[str] = set()
        # Commissions of leaders that started a team, those reminded that it stands still, and who watches.
        self._teams: set[str] = set()
        self._stall_reminded: set[str] = set()
        self._stall_watches: dict[str, asyncio.Task[Any]] = {}

    def _tree_task(self, work: Any) -> asyncio.Task[Any]:
        """Run work about a worktree as a task of its own, which shutting down stops."""
        task = asyncio.ensure_future(work)
        self._tree_tasks.add(task)
        task.add_done_callback(self._tree_tasks.discard)
        return task

    async def _stop_tree_work(self) -> None:
        """Stop what is under way about worktrees, as the host shuts down: what a landing began it puts back itself."""
        tasks = [task for task in self._tree_tasks if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for quiet in self._quiet.values():
            quiet.go.set()

    async def _note_tree_event(self, commission_id: str | None, kind: str, payload: dict[str, Any]) -> None:
        """Put something about a worktree on record with its commission; a note that cannot be written is logged."""
        if not commission_id:
            return
        try:
            await self._ledger.append_event(commission_id, kind, payload)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the note is for the record
            logger.warning("noting %s for commission %s failed", kind, commission_id, exc_info=True)

    # -- who works in a worktree ------------------------------------------

    async def tree_possible(self, project: str | None, home: str | None) -> bool:
        """Whether a git worktree would be made now for a commission of ``project``, whose directory is ``home``.

        By what git said of the directory a short while ago
        (``TREE_PROBE_SECONDS``) and by how many the project has. For
        telling task agents apart that would wait for one another; when
        one is made, git is asked anew.
        """
        from config import nekopower_settings as settings

        if not project or not home:
            return False
        key = os.path.normcase(os.path.normpath(home))
        now = time.monotonic()
        kept = self._tree_probes.get(key)
        if kept is None or now - kept[0] >= settings.TREE_PROBE_SECONDS:
            try:
                usable = bool((await self.worktrees.probe(home)).usable)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - not told: none is made
                logger.warning("whether a worktree can be made for a project's directory could not be told", exc_info=True)
                usable = False
            self._tree_probes[key] = (now, usable)
        else:
            usable = kept[1]
        if not usable:
            return False
        return len(await self._ledger.trees(project=project, states=_SHOWN_STATES)) < settings.WORKTREES_PER_PROJECT

    async def _works_in_tree(self, member: Session) -> bool:
        """Whether a member changes files in a worktree of its own, and so not in its project's directory.

        One that has one for the commission it is at; one that is to
        have one and for which one can be made, as far as that can be
        told before it starts. Not one that had to do without for the
        commission it is at.
        """
        if member.seat != "tree":
            return False
        if member.id in self._direct_now:
            # (Whatever worktree another commission of its keeps, one that rests meanwhile: the
            # one it is at writes in the project's directory.)
            return False
        if member.id in self._tree_sessions:
            return True
        return await self.tree_possible(member.project, member.workdir)

    async def seat_of_new_member(self, project: str) -> str:
        """Return the seat a new member that changes files gets in a project: ``tree`` beside one that works in its directory.

        The first member that changes files works in the project's
        directory itself; every one added while that one still takes
        work has a worktree made for each of its commissions, and so
        does not wait for it. One that only reads is nobody to wait
        for. (Of every character: the directory is the project's.)
        """
        rows = await self._ledger.find_sessions(project=project, kind="line", limit=500, commissions_each=0)
        for row in rows:
            if row.session.seat != "" or not await self._ledger.session_alive(row.session):
                continue
            try:
                writes = bool((await self._template_of(row.session)).changes_files)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one whose preset cannot be read counts as one that changes files
                writes = True
            if writes:
                return "tree"
        return ""

    async def _tree_row_of(self, commission_id: str) -> Tree | None:
        """Return the worktree a commission is worked on in: its own, or that of the commission that started it."""
        tree_id = self._tree_of.get(commission_id)
        if tree_id is None and self._tree_of:
            commission = await self._ledger.get(commission_id)
            if commission is not None and commission.parent_id:
                tree_id = self._tree_of.get(commission.parent_id)
        return await self._ledger.get_tree(tree_id) if tree_id else None

    async def _handle_of(self, row: Tree) -> Any:
        """Return the handle of a worktree on record, found again where this process has none; None when it is not there."""
        from nekopower.host.worktrees import WorktreeError

        handle = self._tree_handles.get(row.id)
        if handle is not None:
            return handle
        try:
            handle = await self.worktrees.open(row.project_dir, row.path)
        except asyncio.CancelledError:
            raise
        except (WorktreeError, ValueError, OSError) as exc:
            logger.info("worktree %s on record could not be found again: %s", row.id, exc)
            return None
        self._tree_handles[row.id] = handle
        self._trees_mine.add(row.id)
        return handle

    async def _handle_for_cleanup(self, row: Tree) -> Any:
        """Return a worktree handle for cleanup, falling back to ``row.repo_root`` when ``row.project_dir`` is gone."""
        from nekopower.host.worktrees import WorktreeError

        handle = await self._handle_of(row)
        if handle is not None:
            return handle
        if not row.repo_root or _same_dir(row.repo_root, row.project_dir):
            return None
        try:
            handle = await self.worktrees.open(row.repo_root, row.path)
        except asyncio.CancelledError:
            raise
        except (WorktreeError, ValueError, OSError) as exc:
            logger.info("worktree %s on record could not be opened via repo_root: %s", row.id, exc)
            return None
        return handle

    async def _workdir_referenced_actively(
        self,
        target_path: Path | str,
        *,
        ignore_blocked_commission_id: str | None = None,
    ) -> bool:
        """Return whether any active/in-use session, active commission, or active project references ``target_path``."""
        from nekopower.host.storage import _BUSY_SESSION_STATUSES, _path_is_or_inside

        norm_target = os.path.normcase(os.path.normpath(str(target_path)))
        for active_wd in getattr(self, "_starting_workdirs", {}).values():
            if active_wd and (
                _path_is_or_inside(active_wd, norm_target) or _path_is_or_inside(norm_target, active_wd)
            ):
                return True
        if await asyncio.to_thread(self._paths.ledger_db().is_file):
            in_use_ids_fn = getattr(self, "_in_use_session_ids", None)
            mem_in_use = tuple(sorted(in_use_ids_fn())) if callable(in_use_ids_fn) else ()
            statuses = tuple(sorted(_BUSY_SESSION_STATUSES))
            status_ph = ",".join("?" for _ in statuses)
            if mem_in_use:
                id_ph = ",".join("?" for _ in mem_in_use)
                sess_sql = (
                    f"SELECT workdir FROM sessions WHERE workdir IS NOT NULL "
                    f"AND (status IN ({status_ph}) OR id IN ({id_ph}))"
                )
                sess_params: tuple[str, ...] = (*statuses, *mem_in_use)
            else:
                sess_sql = f"SELECT workdir FROM sessions WHERE workdir IS NOT NULL AND status IN ({status_ph})"
                sess_params = statuses
            sess_workdirs = await self._ledger.run(
                lambda conn, q=sess_sql, p=sess_params: [
                    str(r["workdir"]) for r in conn.execute(q, p).fetchall() if r["workdir"]
                ]
            )
            if any(_path_is_or_inside(wd, norm_target) for wd in sess_workdirs):
                return True
            active_statuses = tuple(sorted(ACTIVE_STATUSES))
            comm_ph = ",".join("?" for _ in active_statuses)
            comm_rows = await self._ledger.run(
                lambda conn, p=active_statuses: [
                    (str(r["id"]), str(r["status"]), str(r["workdir"]))
                    for r in conn.execute(
                        f"SELECT id, status, workdir FROM commissions WHERE workdir IS NOT NULL AND status IN ({comm_ph})",
                        p,
                    ).fetchall()
                    if r["workdir"]
                ]
            )
            for cid, cstatus, cwd in comm_rows:
                if ignore_blocked_commission_id and cid == ignore_blocked_commission_id and cstatus == "blocked":
                    continue
                if _path_is_or_inside(cwd, norm_target):
                    return True
        space = getattr(self, "_space", None)
        if space is not None and hasattr(space, "list_projects_checked"):
            try:
                projects, readable = await space.list_projects_checked(include_archived=False)
            except OSError:
                return True
            if not readable:
                return True
            for proj in projects:
                if proj.workdir and _path_is_or_inside(proj.workdir, norm_target):
                    return True
        return False

    async def _repository_gone(self, row: Tree) -> bool:
        """Whether the repository a worktree was made from is not there any more (its directory went, or is no repository now).

        Git will not open the worktree again then, however often it is
        asked: what is left of it is a directory of the host's own.
        """
        from nekopower.host.worktrees import NOT_A_REPOSITORY

        if not await asyncio.to_thread(Path(row.project_dir).is_dir):
            return True
        try:
            return (await self.worktrees.probe(row.project_dir)).reason == NOT_A_REPOSITORY
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - not known: taken for one that is there
            return False

    async def _repo_root_gone(self, repo_root: str) -> bool:
        """Whether ``repo_root`` itself is gone or is no longer a git repository."""
        from nekopower.host.worktrees import NOT_A_REPOSITORY

        if not await asyncio.to_thread(Path(repo_root).is_dir):
            return True
        try:
            return (await self.worktrees.probe(repo_root)).reason == NOT_A_REPOSITORY
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - unknown state: treat repository as present so metadata cleanup retries
            return False

    async def _remove_what_is_left(self, row: Tree, *, before_delete: Any = None) -> bool | None | str:
        """Remove the directory of a worktree git no longer knows (its repository is gone).

        Returns whether it went; None for a directory that is not the
        worktree's any more, which is left where it is; ``"aborted"`` when
        ``before_delete(path)`` returned False immediately before deletion.
        Only one that still says it is that worktree is removed (the ``.git``
        file git put into it names it, and the repository it is of), or one with
        nothing in it: another directory put in its place since is not what the
        user discarded.
        """
        from utils.asyncio_executor import to_thread_through_cancellation

        base, path = self._paths.worktrees_dir(), Path(row.path)

        def remove() -> bool | str:
            if not _same_dir(str(path.parent), str(base)):
                return False
            if _is_link(path):
                # The worktree was moved elsewhere and a link to it left here: what the link leads
                # to is read, and removed, through no such link.
                return False
            if _is_empty(path):
                # All of it went, and the directory itself did not (it was in use, or the host ended
                # just then): nothing is lost with an empty directory, whoever made it. One that
                # still cannot be removed stays on record as one to remove.
                if before_delete is not None and not before_delete(path):
                    return "aborted"
                _remove_empty(path)
                return True
            if not _says_it_is(path, row.id, row.repo_root):
                return False
            from nekopower.host.worktrees import _fs, _restore_surviving_dir_times, _snapshot_dir_times

            saved_times = _snapshot_dir_times(path) if before_delete is not None else []
            if before_delete is not None and not before_delete(path):
                return "aborted"
            # The file that says whose the directory is goes last, with the directory: where
            # something of it is in use and stays, what is left still says so when this is
            # tried again (as the host next starts).
            try:
                for entry in list(path.iterdir()):
                    if entry.name != ".git":
                        _remove_entry(entry)
                if all(entry.name == ".git" for entry in path.iterdir()):
                    _remove_entry(path / ".git")
                    _remove_empty(path)
            except OSError:
                # Best-effort partial cleanup; caller checks _tree_is_gone below.
                pass
            finally:
                if saved_times and os.path.lexists(_fs(path)):
                    _restore_surviving_dir_times(saved_times)
            return True

        status = await to_thread_through_cancellation(remove)
        if status == "aborted":
            return "aborted"
        if not status:
            return None
        return await self._tree_is_gone(row)

    @staticmethod
    async def _tree_is_gone(row: Tree) -> bool:
        """Whether a worktree's directory is not there any more (and not merely not to be opened this moment)."""
        return not await asyncio.to_thread(Path(row.path).is_dir)

    async def _still_at_it_in(self, row: Tree) -> bool:
        """Whether a run of a worktree's commission, or of a task agent it started, is under way in that worktree this moment."""
        commission = await self._ledger.get(row.commission_id) if row.commission_id else None
        if commission is None or not _same_dir(commission.workdir, row.workdir):
            # (At work in another worktree of its own, or nowhere.)
            return False
        theirs = [commission.id, *(child.id for child in await self._ledger.select(parent_id=commission.id))]
        return any(self._at_it(item) for item in theirs)

    def _at_it(self, commission_id: str) -> bool:
        """Whether a commission has a run under way in a runtime (one whose session is still being opened has none)."""
        live = self._sessions.get(commission_id)
        return live is not None and live.ready and not live.idle

    async def _gone_from_runtime(self, commission_id: str, *, itself: bool = True, over: bool = False) -> bool:
        """Wait until no run is under way for the task agents a commission started, nor (``itself``) for the commission; returns whether that is so.

        ``over``: of the task agents it started, only those that are
        over on record (stopped, say, and not gone yet). One that is at
        work as it should be is not waited for: its commission goes on
        beside it, as when the result of another takes it up again.

        Before a worktree they work in is looked at for the last time:
        a tool of theirs that is finishing a write is through by then.
        No longer than ``_GONE_SECONDS``: False when one is still at it
        by then, or when the task agents it started could not be read.
        Whoever asks keeps the worktree as it is then. (A landing
        asks for the task agents alone: a run of the commission's own
        that began meanwhile is more the user said to it, which the
        landing waits for by itself, ``_stale``.)
        """
        deadline = time.monotonic() + _GONE_SECONDS
        while not self._closed:
            try:
                theirs = [
                    child.id
                    for child in await self._ledger.select(parent_id=commission_id)
                    if not over or child.status in ENDED_STATUSES
                ]
                if itself:
                    theirs.append(commission_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - not known: asked again, and not taken for gone
                logger.debug("the task agents a commission started could not be read", exc_info=True)
                theirs = None
            if theirs is not None and not any(self._at_it(item) for item in theirs):
                return True
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(_POLL_SECONDS)
        return False

    def _forget_tree(self, row: Tree, session_id: str | None = None) -> None:
        """Forget that a commission is worked on in a worktree: it is over there, or the worktree is gone.

        ``session_id`` is a session that took the worktree as its own
        and is not the one on its record (the commission went on to
        another session since): forgotten for that one as well.
        """
        if row.commission_id and self._tree_of.get(row.commission_id) == row.id:
            del self._tree_of[row.commission_id]
        for session in (row.session_id, session_id):
            if session and self._tree_sessions.get(session) == row.id:
                del self._tree_sessions[session]

    async def _remove_tree(self, row: Tree, handle: Any = None, *, discarded: bool = False) -> bool:
        """Remove a worktree and take it off the record; returns whether its directory went.

        One whose directory could not be removed (a file of it is in
        use) is taken off the record all the same: it holds nothing that
        was not landed, and is removed when what is left over is gone
        through as the host next starts. Not so one the user discards
        (``discarded``), which holds what was not landed and would be
        taken for one that was left: that one stays on record as one to
        remove (``discarding``), shown nowhere.
        """
        from nekopower.host.worktrees import WorktreeError

        self._forget_tree(row)
        gone = True
        handle = handle if handle is not None else await self._handle_of(row)
        if handle is not None:
            try:
                await self.worktrees.remove(handle)
            except asyncio.CancelledError:
                raise
            except (WorktreeError, OSError) as exc:
                logger.warning("worktree %s could not be removed: %s", row.id, exc)
                gone = False
        self._tree_handles.pop(row.id, None)
        self._trees_mine.discard(row.id)
        if discarded and not gone:
            await self._ledger.update_tree(row.id, state="discarding", reason="")
            return False
        await self._ledger.drop_tree(row.id)
        return gone

    @staticmethod
    def _evaluate_mtime(path: Path, until: float, check_mtime: Any) -> str | None:
        """Return an abort reason when ``path`` cannot be verified untouched since ``until``."""
        if check_mtime is None:
            return None
        checked = check_mtime(path)
        if isinstance(checked, tuple):
            mtime, whole = checked
            if not whole:
                return "scan_incomplete"
        else:
            mtime = checked
        if mtime is not None and mtime > until:
            return "touched"
        return None

    async def _remove_discarded(
        self,
        row: Tree,
        *,
        until: float | None = None,
        check_mtime: Any = None,
    ) -> bool:
        """Remove a ``discarding`` worktree that could not be removed earlier; returns whether it went.

        False as well where only its record went: the directory there
        is not the worktree's any more, and was left as it is. When
        ``row.reason == "expired"`` and ``check_mtime`` + ``until`` are given,
        freshness is re-checked before deletion so files modified since
        automatic expiry are spared.
        """
        from nekopower.host.storage import _classify_path_safety
        from nekopower.host.worktrees import WorktreeError

        safety = await asyncio.to_thread(_classify_path_safety, Path(row.path), self._paths.worktrees_dir())
        if safety not in ("ok", "missing"):
            logger.warning(
                "discarded worktree %s path %s failed managed-root safety check (%s); skipping",
                row.id,
                row.path,
                safety,
            )
            return False

        hold_workdir_fn = getattr(self, "hold_workdir", None)
        release_workdir_fn = getattr(self, "release_workdir", None)
        workdir_hold: str | None = None
        if callable(hold_workdir_fn):
            workdir_hold = hold_workdir_fn(row.path)
            if workdir_hold is None:
                return False
        try:
            if await self._workdir_referenced_actively(row.path, ignore_blocked_commission_id=row.commission_id):
                return False

            guard_mtime = row.reason == "expired" and check_mtime is not None and until is not None
            if guard_mtime and not await self._tree_is_gone(row):
                if await asyncio.to_thread(self._evaluate_mtime, Path(row.path), until, check_mtime) is not None:
                    return False

            def still_untouched(path: Path) -> bool:
                assert until is not None
                return self._evaluate_mtime(path, until, check_mtime) is None

            handle = await self._handle_for_cleanup(row)
            if handle is not None:
                if not guard_mtime:
                    return await self._remove_tree(row, handle, discarded=True)
                try:
                    outcome = await self.worktrees.remove_if_untouched(handle, still_untouched)
                except asyncio.CancelledError:
                    raise
                except (WorktreeError, OSError) as exc:
                    logger.warning("auto-expired worktree %s could not be removed on retry: %s", row.id, exc)
                    return False
                if outcome == "aborted":
                    return False
                self._forget_tree(row)
                self._tree_handles.pop(row.id, None)
                self._trees_mine.discard(row.id)
                await self._ledger.drop_tree(row.id)
                return outcome == "removed"
            if not await self._tree_is_gone(row):
                # A directory is there that git does not open as this worktree (half removed, or
                # another put in its place since): not known to be what was discarded, and left
                # alone, to be tried again when the host next starts. Unless the repository itself
                # is gone: git never opens it again. What still says it is that worktree is removed
                # (tried again the next time where a file of it is in use); anything else there is
                # not the host's, and only the record goes.
                repo_root = row.repo_root or row.project_dir
                if repo_root and not await self._repo_root_gone(repo_root):
                    return False
                went = await self._remove_what_is_left(row, before_delete=still_untouched if guard_mtime else None)
                if went == "aborted":
                    return False
                if went is not True:
                    if went is None:
                        self._forget_tree(row)
                        await self._ledger.drop_tree(row.id)
                    return False
            else:
                # Directory is already gone; retry cleaning up lingering git registration and refs if repo exists.
                repo_root = row.repo_root or row.project_dir
                if repo_root and not await self._repo_root_gone(repo_root):
                    try:
                        await self.worktrees.forget_gone(repo_root, row.path, row.id)
                    except asyncio.CancelledError:
                        raise
                    except (WorktreeError, OSError) as exc:
                        logger.warning("discarded worktree %s metadata could not be cleaned up yet: %s", row.id, exc)
                        return False
            self._forget_tree(row)
            await self._ledger.drop_tree(row.id)
            return True
        finally:
            if workdir_hold is not None and callable(release_workdir_fn):
                release_workdir_fn(workdir_hold)

    async def _tree_to_work_in(self, commission: Commission, kept: Session, home: str, changes: bool) -> Tree | None:
        """Return the worktree a commission that starts is worked on in, made now where it has none; None for none.

        One it was worked on in before (it is taken up again) is found
        again as it was left; it stays on record as it was left until
        the commission is at work (``_tree_at_work``), so a start that
        comes to nothing leaves it so. Not one the user sees to
        personally (``kept``): that one is the user's from then on, and
        the commission gets another. Otherwise one is made for a session
        that is to have one (``seat``) and can change files. Where none
        can be made: a member works in its project's directory, once
        nobody else changes files there; a task agent of a team is not
        started.
        """
        from config.prompts import prompts_nekopower_brain as texts

        for row in reversed(await self._ledger.trees(commission_id=commission.id)):
            tree_id = row.id
            while not self._closed:
                # What an earlier process left is being gone through, the user's decision on it is
                # being carried out, or it is being seen to after the run that left it: it is gone
                # on in once that is through, as it stands then. (Looked at once more after the
                # read: it may have been taken in hand meanwhile.)
                if tree_id in self._trees_in_hand:
                    await asyncio.sleep(_POLL_SECONDS)
                    continue
                row = await self._ledger.get_tree(tree_id)
                if tree_id not in self._trees_in_hand:
                    break
            if row is None or row.state in ("kept", "discarding"):
                # Gone since, the user's to see to personally (and left alone, then), or discarded.
                continue
            # Its own from here on, with nothing awaited since the look above: nothing else takes it in hand.
            # (And no longer one that does without: at no moment both.)
            self._tree_of[commission.id] = row.id
            self._tree_sessions[kept.id] = row.id
            self._direct_now.discard(kept.id)
            if row.state == "making":
                # Left half made by a start that was cut off: made anew.
                await self._remove_tree(row)
                self._forget_tree(row, kept.id)
                continue
            handle = await self._handle_of(row)
            if handle is None and not await self._tree_is_gone(row):
                # There, and not to be opened this moment (git did not answer): it holds what the
                # commission changed, and is not given up for a new one.
                self._forget_tree(row, kept.id)
                raise RuntimeError(f"the directory this commission is worked on in cannot be opened just now: {row.path}")
            if handle is None:
                self._forget_tree(row, kept.id)
                await self._ledger.drop_tree(row.id)
                await self._note_tree_event(commission.id, "tree_lost", {"tree": row.id, "path": row.path})
                continue
            if not await self._gone_from_runtime(commission.id, itself=False, over=True):
                # A task agent it started, and that is over, is still at it in there (it could
                # not be stopped in time when the commission was): not gone on in under it. (One
                # that is at work as it should be is no such: the commission was at work all
                # along, and is taken up again by the result of another it started.)
                self._forget_tree(row, kept.id)
                raise RuntimeError("a task agent this commission started is still at work in the directory it is worked on in")
            await asyncio.to_thread(lambda found=row: Path(found.workdir).mkdir(parents=True, exist_ok=True))
            return row
        if kept.seat != "tree" or not changes or kept.kind not in ("line", "child"):
            return None
        row, why, detail = await self._make_tree(commission, kept, home)
        if row is not None:
            return row
        await self._note_tree_event(commission.id, "tree_not_made", {"reason": why, "detail": detail})
        if kept.kind == "child":
            raise RuntimeError(texts.TEAM_MEMBER_NO_TREE.format(reason=detail or why))
        await self._takes_its_turn(commission.id, kept)
        return None

    async def _tree_at_work(self, commission_id: str, session_id: str) -> None:
        """Put the worktree a commission goes on in on record as at work in, now that the commission is at work."""
        tree_id = self._tree_of.get(commission_id)
        row = await self._ledger.get_tree(tree_id) if tree_id else None
        if row is None:
            return
        state = "resolving" if row.state == "resolving" else "active"
        await self._ledger.update_tree(row.id, state=state, reason="", session_id=session_id)

    async def _waits_in_tree(self) -> set[str]:
        """Return the commissions whose changes wait in a worktree for the user to decide on; empty where that cannot be read."""
        try:
            # (Not one the user sees to personally: its commission may be at work in another.)
            rows = await self._ledger.trees(states=("held", "unlanded"))
            # And those the user's decision is being carried out on this moment, whose worktree
            # is on record as being landed, or is gone already, with its commission not done yet.
            return {str(row.commission_id) for row in rows if row.commission_id} | set(self._decided_for.values())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - what a commission started is ended as ever
            logger.warning("the worktrees that wait for the user could not be read", exc_info=True)
            return set()

    async def _tree_not_started(self, commission_id: str) -> None:
        """A start that made or found a worktree for a commission did not start it: the worktree is seen to as for one that ended."""
        if commission_id not in self._tree_of:
            return
        commission = await self._ledger.get(commission_id)
        if commission is None or commission.status in ENDED_STATUSES:
            if commission is not None:
                await self._tree_left(commission)
            else:
                self._tree_of.pop(commission_id, None)

    async def _make_tree(self, commission: Commission, kept: Session, home: str) -> tuple[Tree | None, str, str]:
        """Make a worktree of ``home`` for a commission; returns its row, or None with why none was made."""
        while self._trees_looking and not self._closed:
            await asyncio.sleep(_POLL_SECONDS)
        # (Nothing is awaited between the look above and this.)
        self._trees_making += 1
        try:
            return await self._make_tree_now(commission, kept, home)
        finally:
            self._trees_making -= 1

    async def _make_tree_now(self, commission: Commission, kept: Session, home: str) -> tuple[Tree | None, str, str]:
        from config import nekopower_settings as settings
        from nekopower.host.paths import worktree_name
        from nekopower.host.worktrees import WorktreeError

        project = kept.project or commission.project
        started = time.perf_counter()
        try:
            probe = await self.worktrees.probe(home)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - none is made
            return None, "git_failed", str(exc)
        if not probe.usable:
            return None, str(probe.reason), probe.detail
        async with self._tree_room:
            # Counted and put on record in one go: the room one takes is not taken by another
            # that starts at the same moment.
            if len(await self._ledger.trees(project=project, states=_SHOWN_STATES)) >= settings.WORKTREES_PER_PROJECT:
                return None, "too_many", f"the project has {settings.WORKTREES_PER_PROJECT} worktrees already"
            # (One of this commission that the user sees to personally keeps its name.)
            taken = {row.id for row in await self._ledger.trees(commission_id=commission.id)}
            base = commission.id[:12]
            name = worktree_name(next(item for item in (base, *(f"{base}-{n}" for n in range(2, 100))) if item not in taken))
            path = self._paths.worktree_dir(name)
            try:
                row = await self._ledger.add_tree(
                    id=name,
                    session_id=kept.id,
                    commission_id=commission.id,
                    lanlan_name=commission.lanlan_name,
                    project=project,
                    project_dir=home,
                    path=str(path),
                    workdir=str(path / probe.subpath) if probe.subpath else str(path),
                )
            except ValueError as exc:
                return None, "name_taken", str(exc)
            self._trees_mine.add(row.id)
        try:
            # Where its session works was read a moment ago, and the project may have been
            # given another directory since: from here on the row keeps it where it is.
            again = await self._ledger.get_session(kept.id)
            if again is not None and again.workdir and kept.workdir and not _same_dir(again.workdir, kept.workdir):
                await self._ledger.drop_tree(row.id)
                self._trees_mine.discard(row.id)
                return None, "moved", "the project was given another directory meanwhile"
            handle = await self.worktrees.create(home, path)
        except (WorktreeError, ValueError, OSError) as exc:
            await self._ledger.drop_tree(row.id)
            self._trees_mine.discard(row.id)
            return None, str(getattr(exc, "reason", "") or "git_failed"), str(getattr(exc, "detail", "") or exc)
        except BaseException:
            # (``create`` leaves nothing behind when it is cut off.)
            await asyncio.shield(self._ledger.drop_tree(row.id))
            self._trees_mine.discard(row.id)
            raise
        self._tree_handles[row.id] = handle
        row = await self._ledger.update_tree(
            row.id, state="active", token=handle.token, workdir=str(handle.workdir), repo_root=str(handle.repo_root)
        ) or row
        self._tree_of[commission.id] = row.id
        self._tree_sessions[kept.id] = row.id
        self._direct_now.discard(kept.id)
        await self._note_tree_event(
            commission.id,
            "tree_made",
            {"tree": row.id, "path": row.path, "seconds": round(time.perf_counter() - started, 2)},
        )
        return row, "", ""

    async def _takes_its_turn(self, commission_id: str, kept: Session) -> None:
        """Have a member that got no worktree wait, as it starts, until nobody else changes files in its project's directory.

        It was let start beside the others because a worktree was to be
        made for it (``_note_writer``), and none could be. From here on
        it counts as one that writes in the project's directory, which
        those that start later wait for; it waits itself for those at
        work there already. Of several that wait so, the one that came
        first goes first.
        """
        project = kept.project
        if not project:
            return
        self._direct_now.add(kept.id)
        self._writes_in[kept.id] = (project, kept.id)
        self._turn_serial += 1
        mine = self._turn_serial
        self._turn_waiters[kept.id] = mine
        try:
            while not self._closed and self._held_by.get(kept.id) == commission_id:
                in_the_way = False
                for other, held in self._held_by.items():
                    theirs = self._writes_in.get(other)
                    if other == kept.id or held == commission_id or theirs is None:
                        continue
                    if theirs[0] != project or theirs[1] == kept.id:
                        continue
                    if self._turn_waiters.get(other, 0) > mine:
                        # It waits as this one does, and came later.
                        continue
                    in_the_way = True
                    break
                if not in_the_way:
                    return
                await asyncio.sleep(_POLL_SECONDS)
        finally:
            self._turn_waiters.pop(kept.id, None)

    async def _tree_said(self, commission: Commission) -> str:
        """Return what the message that opens a commission says of the worktree it is worked on in; empty for none.

        Where it is, what is not in it, that the project's directory is
        read and not written; and, of an earlier commission of the same
        task agent whose changes were not landed, where those are.
        """
        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_brain as texts
        from utils.tokenize import truncate_to_tokens

        tree_id = self._tree_of.get(commission.id)
        row = await self._ledger.get_tree(tree_id) if tree_id else None
        if row is None:
            return ""
        parts = [texts.TASK_AGENT_IN_TREE.format(tree=row.workdir, project=row.project_dir)]
        if row.session_id:
            for other in await self._ledger.trees(session_id=row.session_id, states=sorted(TREE_STATES_LEFT)):
                if other.id == row.id or not other.commission_id:
                    continue
                earlier = await self._ledger.get(other.commission_id)
                files = ""
                if other.conflicts:
                    files = texts.TASK_AGENT_EARLIER_TREE_FILES.format(
                        files=names_text(other.conflicts, settings.TREE_FILES_LISTED, settings.MEMBER_TREE_NOTE_MAX_TOKENS // 2)
                    )
                parts.append(
                    texts.TASK_AGENT_EARLIER_TREE.format(
                        title=earlier.title if earlier is not None else other.id, path=other.workdir, files=files
                    )
                )
        return truncate_to_tokens("\n".join(parts), settings.MEMBER_TREE_NOTE_MAX_TOKENS)

    # -- what may be written where ----------------------------------------

    async def _rule_on_tree_write(self, commission_id: str, path: str) -> dict[str, Any] | None:
        """Rule on a write that concerns a worktree; None when it concerns none.

        Refused: a task agent that works in a worktree writing into the
        directory of its project, which it only reads (what it changes
        gets there by being landed); and anybody writing into the
        worktree of another. (What a shell command writes is not looked
        into, as nowhere.)
        """
        from config.prompts import prompts_nekopower_brain as texts
        from nekopower.runtime.permissions import is_inside

        rows = await self._ledger.trees()
        if not rows:
            return None
        mine = await self._tree_row_of(commission_id)

        def look() -> str | None:
            target = Path(path)
            for row in rows:
                if is_inside(target, Path(row.path)):
                    return None if mine is not None and row.id == mine.id else "other"
            if mine is not None and is_inside(target, Path(mine.project_dir)):
                return "project"
            return None

        found = await asyncio.to_thread(look)
        if found == "other":
            return {"allow": False, "reason": texts.TREE_WRITE_REFUSED}
        if found == "project" and mine is not None:
            return {"allow": False, "reason": texts.TREE_PROJECT_WRITE_REFUSED.format(tree=mine.workdir)}
        return None

    async def _tree_places(self, but: str | None = None) -> list[tuple[str, str | None, str | None]]:
        """Return the worktrees on record as places sessions work in, for what is read there without asking; not those of session ``but``.

        The directory in each that stands for its project's: a worktree
        is of the whole repository, and what lies beside the project in
        it is read under the same rule as anything outside a working
        directory.
        """
        return [
            (row.session_id or row.id, row.workdir, None)
            for row in await self._ledger.trees()
            if not (but and row.session_id == but)
        ]

    # -- landing ----------------------------------------------------------

    async def _lands_first(
        self, session: Any, *, text: str, reason: str, delivery: Any = None, run_over: bool = False
    ) -> bool:
        """Have what a commission changed in its worktree landed before it is put down as done; False when it works in none.

        Called where a commission is about to be done. It stays at
        work meanwhile; how it said it is done is kept, and a task of
        its own lands the changes once the run that said so is over
        (``run_over``: it is over already).
        """
        commission_id = session.commission_id
        if commission_id not in self._tree_of or self._closed:
            return False
        done = _Done(text=text, reason=reason, delivery=delivery, sent=session.sent)
        if run_over:
            done.run_over.set()
        self._replace_done(commission_id, done)
        if commission_id not in self._landings:
            task = self._tree_task(self._land(commission_id))
            self._landings[commission_id] = task
            task.add_done_callback(
                lambda finished: self._landings.pop(commission_id, None)
                if self._landings.get(commission_id) is finished
                else None
            )
        return True

    def _replace_done(self, commission_id: str, done: _Done) -> None:
        """Keep how a commission said it is done, in place of how it said so before; whoever waited for that run is woken.

        The landing may wait for the run of the one that is replaced to
        be over, which nothing says any more: woken, it looks at the one
        that stands now (``_land``).
        """
        before = self._dones.get(commission_id)
        self._dones[commission_id] = done
        if before is not None and before is not done:
            before.run_over.set()

    def _run_over_in_tree(self, session: Any, event: dict[str, Any]) -> bool:
        """Note that a run of a commission whose changes are being landed is over; False when none are.

        The end of such a run does not end the commission: the landing
        does. A run that began after it said it is done (the user said
        more to it) has said it is done once more by ending, with its
        last words.
        """
        done = self._dones.get(session.commission_id)
        if done is None:
            return False
        if session.sent != done.sent:
            done = _Done(
                text=str(event.get("text") or "") or done.text, reason=done.reason, delivery=done.delivery, sent=session.sent
            )
            self._replace_done(session.commission_id, done)
        session.idle = True
        done.run_over.set()
        return True

    async def _land(self, commission_id: str) -> None:
        """Land what a commission changed in its worktree, once its run is over; see ``_land_once``."""
        try:
            while not self._closed:
                done = self._dones.get(commission_id)
                if done is None:
                    return
                await done.run_over.wait()
                if self._dones.get(commission_id) is not done:
                    # Said anew meanwhile (it reported once more, or went on at the user's word): that one stands.
                    continue
                if await self._land_once(commission_id, done) != "again":
                    return
                # More was said to it meanwhile and it is at work again: landed once that run is over.
                while not self._closed and self._dones.get(commission_id) is done and self._stale(commission_id, done):
                    await asyncio.sleep(_POLL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the commission is not left at work for nobody
            logger.exception("landing what commission %s changed failed", commission_id)
            done = self._dones.pop(commission_id, None)
            tree_id = self._tree_of.get(commission_id)
            row = await self._ledger.get_tree(tree_id) if tree_id else None
            commission = await self._ledger.get(commission_id)
            if row is not None and commission is not None and done is not None:
                await self._not_landed(row, commission, done, REASON_FAILED, detail="the host could not land it")
            elif commission is not None and commission.status == "working":
                # Its changes are landed (or left for the user), and how it ended could not be put on
                # record: not left at work for nobody. Nothing is merged a second time.
                await self._fail(commission_id, "what came of it could not be put on record", said="run")
                await self._wrap_up_in_tree(commission_id)

    async def _landing_end_written(self, commission_id: str) -> None:
        """Wait until no landing is writing how a commission ends this moment (``_nothing_more_said``)."""
        while (written := self._landing_ends.get(commission_id)) is not None:
            await asyncio.wait([written])

    @contextlib.contextmanager
    def _nothing_more_said(self, commission_id: str) -> Iterator[None]:
        """Keep what is said to a commission back while a landing writes how it ends: done, or left for the user.

        From here until that is written: what the user says waits
        (``tell``), and so do what a task agent it started hands back
        (``_tell_parent``), what is handed on to its session
        (``settling``, as while the end of a run is looked at), and the
        next request of a run its runtime began by itself
        (``_pause_for_landing``). Each goes by how the commission
        stands once it is written. Entered with nothing awaited since
        it was last looked at whether more was said (``_stale``).

        The end of a run that leaves a commission done outside a worktree
        holds it the same way while that is written (``_end``'s ``hold``),
        entered right after ``unless``, which is that last look. Held so more
        than once at a time, it is let go once the last of them is through.
        """
        holds = self._landing_holds.get(commission_id, 0)
        if not holds:
            self._landing_ends[commission_id] = asyncio.get_running_loop().create_future()
        self._landing_holds[commission_id] = holds + 1
        live = self._sessions.get(commission_id)
        if live is not None:
            live.settling += 1
        try:
            yield
        finally:
            if live is not None:
                live.settling -= 1
            # Held by more than one at once (or one within another): what waits goes on once the
            # last of them is through, whichever that is.
            holds = self._landing_holds.pop(commission_id) - 1
            if holds:
                self._landing_holds[commission_id] = holds
            else:
                self._landing_ends.pop(commission_id).set_result(None)

    def _stale(self, commission_id: str, done: _Done) -> bool:
        """Whether a commission went on working since it said it is done: what it changed is not landed under it."""
        if self._dones.get(commission_id) is not done:
            return True
        live = self._sessions.get(commission_id)
        return live is not None and (not live.idle or live.sent != done.sent)

    async def _land_once(self, commission_id: str, done: _Done) -> str:
        """Land what a commission changed, as it stands; returns ``again`` when it went on working meanwhile.

        In this order: an inspector looks at it, where that is asked
        for (what it finds wrong goes back to the task agent); where the
        user is asked first, it is left for the user; else it is merged
        into the project's directory, and the commission is done.
        """
        from config import nekopower_settings as settings

        tree_id = self._tree_of.get(commission_id)
        row = await self._ledger.get_tree(tree_id) if tree_id else None
        commission = await self._ledger.get(commission_id)
        if row is None or commission is None or commission.status != "working":
            self._dones.pop(commission_id, None)
            return "over"
        handle = await self._handle_of(row)
        if handle is None:
            self._dones.pop(commission_id, None)
            return await self._not_landed(row, commission, done, REASON_FAILED, detail="its worktree is gone")
        row = await self._ledger.update_tree(row.id, state="landing") or row
        if self._stale(commission_id, done):
            # More was said to it since, and it is at work on that: what it started for that is
            # not stopped, and nothing is landed under it.
            return await self._at_work_again(row)
        # A task agent it started works in the same worktree, and may still be at it though the
        # commission says it is done: stopped, as when a commission is done anywhere, and gone
        # from its runtime before what was changed is read.
        await self._end_what_it_started(commission_id)
        if not await self._gone_from_runtime(commission_id, itself=False):
            if self._stale(commission_id, done):
                return await self._at_work_again(row)
            # One of them is still at it in the worktree: nothing is merged under it.
            self._dones.pop(commission_id, None)
            return await self._not_landed(
                row, commission, done, REASON_FAILED, detail="a task agent it started could not be stopped in its worktree"
            )
        chosen = await self.landing.get()
        if chosen["inspect"] or (await self._chosen(commission_id)).get("inspect"):
            verdict, findings = await self._inspect(row, commission)
            if self._stale(commission_id, done):
                # More was said to it while it was checked (what it started for that is its own
                # to wait for): checked anew once that run is over.
                return await self._at_work_again(row)
            if verdict == CHECK_STUCK:
                # The inspector (stopped, say) is still at it in the worktree: nothing is merged under it.
                self._dones.pop(commission_id, None)
                return await self._not_landed(
                    row, commission, done, REASON_FAILED, detail="an inspector could not be stopped in its worktree"
                )
            await self._note_tree_event(
                commission_id, "inspected", {"tree": row.id, "verdict": verdict, "round": row.checks + 1}
            )
            if self._stale(commission_id, done):
                # More was said to it while that was put on record: what was found is not acted on
                # under the run that began (it is not put down as stuck, nor sent back).
                return await self._at_work_again(row)
            if verdict == CHECK_FAILED:
                if row.checks + 1 >= settings.INSPECTION_ROUNDS:
                    return await self._not_landed(
                        row, commission, done, REASON_INSPECTION, rounds=row.checks + 1, findings=findings
                    )
                row = await self._ledger.update_tree(row.id, state="active", checks=row.checks + 1) or row
                self._dones.pop(commission_id, None)
                await self._send_back(commission_id, row, findings)
                return "sent back"
        if chosen["mode"] == "ask":
            if self._stale(commission_id, done):
                # More was said to it meanwhile: not held back under the run that does that.
                return await self._at_work_again(row)
            return await self._not_landed(row, commission, done, REASON_ASK)
        result = await self._merge_or_markers(row, handle, unless=lambda: self._stale(commission_id, done))
        if result is None:
            return await self._at_work_again(row)
        return await self._landed_or_not(row, handle, commission, done, result)

    async def _at_work_again(self, row: Tree) -> str:
        """Put a worktree whose landing gives way to more the user said back on record as at work in; returns ``again``."""
        await self._ledger.update_tree(row.id, was=("landing",), state="active")
        return "again"

    async def _merge_or_markers(self, row: Tree, handle: Any, *, unless: Any = None) -> Any:
        """Merge a worktree back (``_merge_back``), unless files that clashed in it still hold conflict markers.

        A task agent that was sent back to merge what clashed and says
        it is done with the markers still there has not merged: landed
        so, the markers would be text in the project's files. That is
        told as one more clash of those files, with nothing merged.
        """
        from nekopower.host.worktrees import MergeResult

        if row.conflicts:
            root = Path(handle.path)
            left = await asyncio.to_thread(lambda: tuple(name for name in row.conflicts if _has_markers(root / name)))
            if left:
                # (More said to it while the files were read is seen where this is acted on, ``_landed_or_not``.)
                return MergeResult("conflict", conflicts=left, detail=_MARKERS_LEFT)
        return await self._merge_back(row, handle, unless=unless)

    async def _send_back(self, commission_id: str, row: Tree, findings: str) -> None:
        """Send a task agent back to its work with what an inspector found, framed as no word of the user's."""
        import secrets

        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_brain as texts
        from config.prompts.prompts_nekopower_sessions import frame
        from nekopower.host.task_agents import _Found
        from utils.tokenize import truncate_to_tokens

        nonce = secrets.token_hex(8)
        text = _Found(
            frame(
                texts.TREE_CHECK_FAILED.format(nonce=nonce, round=row.checks, rounds=settings.INSPECTION_ROUNDS),
                truncate_to_tokens(findings, settings.INSPECTION_FINDINGS_MAX_TOKENS) or "(nothing was said)",
                nonce,
            )
        )
        await self._say_to(commission_id, text, reason="an inspector found problems with what it changed", external=True)

    async def _say_to(self, commission_id: str, text: str, *, reason: str, external: bool = False) -> bool:
        """Say something of the host's to a commission that is at work: to its open session, or by taking it up again.

        ``external`` says that a model wrote what is said: what the
        user allowed the commission for its session is asked anew from
        there on, as for a note from another session.
        """
        if external:
            self._permissions.received_external_input(commission_id)
        live = self._sessions.get(commission_id)
        runtime = self._runtime_of(live) if live is not None else None
        if live is not None and runtime is not None and live.ready and not live.closing:
            try:
                if await self._send_text(runtime, live, "steer", text):
                    return True
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - taken up again instead
                logger.warning("saying something to commission %s in its session failed", commission_id, exc_info=True)
        return await self._wake_working(commission_id, text, reason=reason)

    def _writers_in(self, project: str | None) -> list[str]:
        """Return the sessions that change files in a project's directory and hold a place."""
        return [
            session_id
            for session_id, where in self._writes_in.items()
            if where[0] == project and self._held_by.get(session_id) is not None
        ]

    def _run_under_way(self, session_id: str) -> Any:
        """Return the place of a session that has a run under way, with a tool of it possibly at work; None otherwise."""
        live = self._sessions.get(self._held_by.get(session_id) or "")
        if (
            live is None
            or live.session_id != session_id
            or not live.ready
            or live.idle
            or live.closing
            or live.settling
            or live.stands is None
            or live.stands.status != "working"
            or live.stands.resting
        ):
            return None
        return live

    async def _pause_for_landing(self, session_id: str) -> None:
        """Hold a session that changes files in a project's directory before its next request while a landing is under way there.

        Asked before every model request of a run (``_cb_budget``):
        between two steps, with no tool of the run at work.
        """
        # A landing is writing how its commission ends this moment (or the end of a run is, ``_end``'s
        # ``hold``), and this is a run its runtime began by itself meanwhile (nothing else of the
        # host's begins one then): asked on once
        # that is written, so that it changes nothing in a worktree on record as one to remove.
        commission_id = self._at_work_in(session_id)
        if commission_id in self._landing_ends:
            live = self._sessions.get(commission_id)
            if live is not None:
                # A run that asks is under way, whether or not word of its start has been taken in
                # (that comes another way, and may come after): the landing sees it as more that
                # began (``_stale``), keeps the worktree, and lets it go on.
                live.idle = False
            await self._landing_end_written(commission_id)
        where = self._writes_in.get(session_id)
        quiet = self._quiet.get(where[0]) if where is not None else None
        if quiet is None or quiet.go.is_set():
            return
        quiet.reached.add(session_id)
        quiet.changed.set()
        await quiet.go.wait()

    @contextlib.asynccontextmanager
    async def _quiet_in(self, project: str | None) -> Any:
        """Wait until nothing changes files in a project's directory, and keep it so inside.

        Every session that writes there and has a run under way is let
        get to where it asks before its next request, and held there
        (``_pause_for_landing``); one in a runtime that does not ask is
        waited for until its run is over. Not waited for with a time
        limit: a command that runs for long is not written under.
        """
        quiet = _Quiet()
        if project:
            self._quiet[project] = quiet
        try:
            while project and not self._closed:
                waited_for = [
                    session_id
                    for session_id in self._writers_in(project)
                    if session_id not in quiet.reached and self._run_under_way(session_id) is not None
                ]
                if not waited_for:
                    break
                quiet.changed.clear()
                try:
                    await asyncio.wait_for(quiet.changed.wait(), timeout=_POLL_SECONDS)
                except asyncio.TimeoutError:
                    # Nothing was said to have changed: looked at again all the same.
                    continue
            yield quiet
        finally:
            quiet.go.set()
            if project and self._quiet.get(project) is quiet:
                del self._quiet[project]

    async def _merge_back(self, row: Tree, handle: Any, *, unless: Any = None) -> Any:
        """Merge what a worktree holds into its project's directory; returns what came of it, None when ``unless`` said not to.

        One landing at a time in a directory, with nothing else
        changing files there meanwhile (``_quiet_in``). Whoever is at
        work in that directory is told what was merged before it goes on.
        """
        from nekopower.host.worktrees import MergeResult

        key = os.path.normcase(os.path.normpath(row.project_dir))
        lock = self._landing_locks.setdefault(key, asyncio.Lock())
        async with lock:
            async with self._quiet_in(row.project):
                if unless is not None and unless():
                    return None
                try:
                    result = await self.worktrees.merge_back(handle)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - told as a landing that failed
                    notes = "; ".join(getattr(exc, "__notes__", ()) or ())
                    logger.warning("merging worktree %s back failed: %s", row.id, exc)
                    result = MergeResult("failed", detail=f"{exc}{'; ' + notes if notes else ''}")
                if result.status == "landed":
                    try:
                        await self._tell_beside(row, result)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - the files are merged, whoever was to hear of it
                        logger.warning("telling who works beside worktree %s of its landing failed", row.id, exc_info=True)
                return result

    async def _tell_beside(self, row: Tree, result: Any) -> None:
        """Tell the member at work in a project's directory what was just merged into it; for its information only."""
        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_brain as texts
        from nekopower.host.task_agents import _Found

        files, more = files_of(result.files, settings.TREE_FILES_LISTED)
        session = await self._ledger.get_session(row.session_id) if row.session_id else None
        said = _Found(
            texts.TREE_LANDED_BESIDE.format(
                name=session.name if session is not None else "another task agent",
                files=files_text(files, more, settings.LANDED_NOTE_MAX_TOKENS),
            )
        )
        for session_id in self._writers_in(row.project):
            if self._writes_in.get(session_id, ("", ""))[1] != session_id:
                # A task agent the member started: the member is told.
                continue
            live = self._run_under_way(session_id)
            runtime = self._runtime_of(live) if live is not None else None
            if live is None or runtime is None:
                continue
            try:
                await self._send_text(runtime, live, "steer", said)
                await self._note_tree_event(live.commission_id, "told_of_landing", {"tree": row.id, "text": str(said)})
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - for its information only
                logger.warning("telling a member what was merged into its directory failed", exc_info=True)

    async def _landed_or_not(self, row: Tree, handle: Any, commission: Commission, done: _Done, result: Any) -> str:
        """Act on what a merge came to: the commission is done, its task agent merges what clashed, or the user decides."""
        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_brain as texts
        from nekopower.host.task_agents import _Found
        from nekopower.host.worktrees import WorktreeError

        commission_id = commission.id
        if result.status in ("landed", "unchanged"):
            files, more = files_of(result.files, settings.TREE_FILES_LISTED)
            outside = outside_of(result.files, getattr(handle, "subpath", ""))
            if result.status == "landed":
                await self._note_tree_event(commission_id, "landed", self._landed_payload(row, result, outside))
            if self._stale(commission_id, done):
                # More was said to it while the merge ran, and it is at work on that in its worktree:
                # what it had changed is landed, the worktree stays, and what it changes now lands
                # when that run is over.
                await self._ledger.update_tree(row.id, state="active")
                return "again"
            told, shown = self._landed_texts(row, commission.lang, files, more, outside, result.skipped)
            text = "\n\n".join(part for part in (done.text, told) if part)
            # Done first, and its worktree seen to after: what the user says to it while its
            # directory goes is a new commission's, and no run begins in it. Not done under a
            # run that began while its end was being written (``unless``): that one goes on in
            # the worktree, which stays.
            # Nothing was said to it since it said it is done (looked at above, and nothing was
            # awaited since), and nothing more is from here until its end is written
            # (``_nothing_more_said``). Its worktree is put down as one to remove only with that
            # in place: no run begins, or goes on, in one that a host that starts would remove.
            with self._nothing_more_said(commission_id):
                try:
                    if not result.skipped:
                        # On record as one to remove from here on (what it held is landed): shown nowhere,
                        # in nobody's way, and removed as the host next starts should it end this moment.
                        await self._ledger.update_tree(row.id, state="discarding")
                    # (``unless``: a run its runtime began by itself meanwhile. It is held before its
                    # first request until this is through, and goes on in the worktree, which stays.)
                    ended = await self._end(
                        commission_id, "done", reason=done.reason, text=text,
                        shown="\n\n".join(part for part in (done.text, shown) if part) if shown else None,
                        suggested_delivery=done.delivery, result_summary=self._summary(text),
                        unless=lambda: self._stale(commission_id, done),
                    )
                except BaseException:
                    self._dones.pop(commission_id, None)
                    await self._seen_to_once_landed(row, handle, result)
                    raise
                if not ended and self._stale(commission_id, done):
                    # Back on record as one it is at work in before whoever waits goes on: that run
                    # changes nothing in a worktree that is still on record as one to remove.
                    await self._ledger.update_tree(row.id, state="active")
                    return "again"
            self._dones.pop(commission_id, None)
            await self._seen_to_once_landed(row, handle, result)
            await self._wrap_up_in_tree(commission_id)
            return "landed"
        if self._stale(commission_id, done):
            # More was said to it while the merge ran: what came of the merge is not acted on under
            # the run that does that, and it is tried anew when that run is over.
            return await self._at_work_again(row)
        if result.status == "conflict" and row.rounds < settings.LANDING_CONFLICT_ROUNDS:
            try:
                if result.detail != _MARKERS_LEFT:
                    # (Where markers are left, the worktree holds the merge as far as it went already.)
                    handle = await self.worktrees.prepare_conflicts(handle, result)
            except asyncio.CancelledError:
                raise
            except (WorktreeError, OSError) as exc:
                if self._stale(commission_id, done):
                    # More was said to it while that was tried (which may be why it could not be:
                    # the worktree changed under it): tried anew when that run is over.
                    return await self._at_work_again(row)
                notes = "; ".join(getattr(exc, "__notes__", ()) or ())
                return await self._not_landed(
                    row, commission, done, REASON_FAILED, detail=f"{exc}{'; ' + notes if notes else ''}",
                    conflicts=result.conflicts,
                )
            self._tree_handles[row.id] = handle
            row = await self._ledger.update_tree(
                row.id, state="resolving", rounds=row.rounds + 1, conflicts=result.conflicts, token=handle.token
            ) or row
            await self._note_tree_event(
                commission_id,
                "landing_conflict",
                {"tree": row.id, "conflicts": list(result.conflicts[: settings.TREE_FILES_LISTED]), "round": row.rounds},
            )
            said = _Found(
                (texts.TREE_MARKERS_LEFT if result.detail == _MARKERS_LEFT else texts.TREE_CONFLICT).format(
                    files=names_text(result.conflicts, settings.TREE_FILES_LISTED, settings.LANDED_NOTE_MAX_TOKENS),
                    round=row.rounds,
                    rounds=settings.LANDING_CONFLICT_ROUNDS,
                )
            )
            self._dones.pop(commission_id, None)
            if not await self._say_to(commission_id, said, reason="what it changed clashes with its project's directory"):
                # Nobody could be told to merge it: not left at work for nobody.
                return await self._not_landed(
                    row, commission, done, REASON_CONFLICT, rounds=row.rounds, conflicts=result.conflicts
                )
            return "resolving"
        if result.status == "conflict":
            return await self._not_landed(
                row, commission, done, REASON_CONFLICT, rounds=row.rounds, conflicts=result.conflicts
            )
        return await self._not_landed(
            row, commission, done, REASON_FAILED, detail=result.detail, left_changed=result.left_changed,
            kept_copies=result.kept_copies,
        )

    async def _seen_to_once_landed(self, row: Tree, handle: Any, result: Any) -> None:
        """Remove a worktree whose changes are landed, or keep it for what was never carried over."""
        if result.skipped:
            # What is never carried over (too large, or a repository of its own) is in the
            # worktree alone: removed with it, it would be gone. Kept, for the user to see to.
            await self._keep_for_skipped(row, result.skipped)
        else:
            await self._remove_tree(row, handle)

    @staticmethod
    def _landed_payload(row: Tree, result: Any, outside: list[str]) -> dict[str, Any]:
        """Return what goes on record of a landing: the files it changed, and those among them that tell of something."""
        from config import nekopower_settings as settings

        files, more = files_of(result.files, settings.TREE_FILES_LISTED)
        return {
            "tree": row.id, "files": files, "more": more, "lines_added": result.lines_added,
            "lines_deleted": result.lines_deleted,
            # Not carried over, being too large or a repository of their own: still in the worktree.
            "skipped": list(result.skipped[: settings.TREE_FILES_LISTED]),
            # Changed outside the project's directory, elsewhere in its repository.
            "outside": outside[: settings.TREE_FILES_LISTED],
        }

    def _landed_texts(
        self, row: Tree, lang: str | None, files: list[dict[str, Any]], more: int, outside: list[str], skipped: Any
    ) -> tuple[str, str]:
        """Return what is told of a landing after the result of its commission: to a model, and to the user in the language of the dialogue.

        Which files it changed; which of them lie outside the project's
        directory (a worktree is of the whole repository: what a command
        changed elsewhere in it landed with the rest, and is said so
        that it does not go unnoticed); and which were not carried over
        and where those are kept. Empty where there is nothing to tell.
        """
        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_brain as texts
        from nekopower.host.task_brain import _texts

        most, third = settings.TREE_FILES_LISTED, settings.LANDED_NOTE_MAX_TOKENS // 3
        words = _texts(texts.TREE_LANDED_TEXTS, lang or "en")
        told = shown = ""
        if files:
            told = texts.TREE_LANDED.format(
                project=row.project_dir, files=files_text(files, more, settings.LANDED_NOTE_MAX_TOKENS)
            )
            listed = ", ".join(str(item["path"]) for item in files) + (words["more"].format(more=more) if more else "")
            shown = words["landed"].format(files=listed)
        if outside:
            told += texts.TREE_LANDED_OUTSIDE.format(files=names_text(outside, most, third))
            shown += words["outside"].format(files=", ".join(outside[:most]))
        if skipped:
            told += (" " if told else "") + texts.TREE_LANDED_SKIPPED.format(
                files=names_text(skipped, most, third), path=row.workdir
            )
            shown += words["skipped"].format(files=", ".join(list(skipped)[:most]), path=row.workdir)
        return told, shown.strip()

    async def _keep_for_skipped(self, row: Tree, skipped: Any, *, kept: bool = False) -> Tree:
        """Keep a worktree whose changes were landed but for files that are never carried over, for the user to see to.

        ``kept``: it was the user's to see to personally, and stays that.
        """
        from config import nekopower_settings as settings

        row = await self._ledger.update_tree(
            row.id, state="kept" if kept else "unlanded", reason=REASON_SKIPPED, conflicts=skipped
        ) or row
        self._forget_tree(row)
        await self._note_tree_event(
            row.commission_id,
            "not_landed",
            {
                "tree": row.id, "path": row.workdir, "reason": REASON_SKIPPED,
                "conflicts": list(skipped)[: settings.TREE_FILES_LISTED], "rounds": 0,
            },
        )
        return row

    def _not_landed_texts(
        self, row: Tree, lang: str | None, reason: str, *, rounds: int = 0, detail: str = "",
        conflicts: Any = (), kept_copies: str = "",
    ) -> tuple[str, str]:
        """Return what is told of a worktree that was not landed: to a model, and to the user in the language of the dialogue."""
        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_brain as texts
        from nekopower.host.task_brain import _texts
        from utils.tokenize import truncate_to_tokens

        why = texts.TREE_NOT_LANDED_WHY[reason].format(rounds=rounds, detail=detail or "no reason given")
        clashed = names_text(conflicts, settings.TREE_FILES_LISTED, settings.TREE_NOT_LANDED_MAX_TOKENS // 2) if conflicts else ""
        told = texts.TREE_NOT_LANDED.format(
            why=why, path=row.workdir, files=texts.TREE_NOT_LANDED_FILES.format(files=clashed) if clashed else ""
        )
        if reason == REASON_ASK:
            # Nothing went wrong: said so, lest whoever is told (a leader, of a part of its team) has it done again.
            told = texts.TREE_HELD.format(path=row.workdir)
        words = _texts(texts.TREE_NOT_LANDED_TEXTS, lang or "en")
        shown = words[reason].format(rounds=rounds) + words["where"].format(path=row.workdir)
        if clashed:
            shown += words["files"].format(files=clashed)
        if kept_copies:
            told += texts.TREE_NOT_LANDED_COPIES.format(path=kept_copies)
            shown += words["copies"].format(path=kept_copies)
        shown += words["choices_ask" if reason == REASON_ASK else "choices"]
        return truncate_to_tokens(told, settings.TREE_NOT_LANDED_MAX_TOKENS), shown

    async def _not_landed(
        self, row: Tree, commission: Commission, done: _Done, reason: str, *, rounds: int = 0, detail: str = "",
        conflicts: Any = (), left_changed: Any = (), kept_copies: str = "", findings: str = "",
    ) -> str:
        """Leave what a commission changed in its worktree for the user to decide on, and put the commission down as stuck.

        The worktree stays as it is. The commission is told as stuck
        with why it was not landed, where the changes are and which
        files clashed, after what its task agent said of the work. In
        hand until the commission stands so: a decision of the user's
        that came in between would find a commission still at work.
        """
        tree_id, mine = row.id, row.id not in self._trees_in_hand
        self._trees_in_hand.add(tree_id)
        try:
            return await self._left_for_the_user(
                row, commission, done, reason, rounds=rounds, detail=detail, conflicts=conflicts,
                left_changed=left_changed, kept_copies=kept_copies, findings=findings,
            )
        finally:
            if mine:
                self._trees_in_hand.discard(tree_id)

    async def _left_for_the_user(
        self, row: Tree, commission: Commission, done: _Done, reason: str, *, rounds: int = 0, detail: str = "",
        conflicts: Any = (), left_changed: Any = (), kept_copies: str = "", findings: str = "",
    ) -> str:
        from config import nekopower_settings as settings

        state = "held" if reason == REASON_ASK else "unlanded"
        # Nothing more is said to it from here until it stands as stuck: steered in meanwhile, what
        # the user says would start a run that the end of this closes. Said after, it takes the
        # commission up again, as what is said to any that is stuck.
        with self._nothing_more_said(commission.id):
            # (The files that clashed last stay on record where none are named now: they are looked
            # at for markers before anything of it lands, ``_merge_or_markers``.)
            row = await self._ledger.update_tree(
                row.id, state=state, reason=reason, conflicts=tuple(conflicts) or row.conflicts
            ) or row
            self._forget_tree(row)
            self._dones.pop(commission.id, None)
            payload: dict[str, Any] = {"tree": row.id, "path": row.workdir, "reason": reason}
            if reason != REASON_ASK:
                payload.update(
                    conflicts=list(conflicts)[: settings.TREE_FILES_LISTED], rounds=rounds, detail=detail,
                    left_changed=list(left_changed)[: settings.TREE_FILES_LISTED], kept_copies=kept_copies,
                )
            await self._note_tree_event(commission.id, "landing_held" if reason == REASON_ASK else "not_landed", payload)
            told, shown = self._not_landed_texts(
                row, commission.lang, reason, rounds=rounds, detail=detail, conflicts=conflicts, kept_copies=kept_copies
            )
            said = "\n\n".join(part for part in (done.text, findings, told) if part)
            await self._end(
                commission.id,
                "blocked",
                reason="what it changed was not landed in its project's directory",
                text=said,
                shown="\n\n".join(part for part in (done.text, shown) if part),
                suggested_delivery=done.delivery,
                result_summary=self._summary(said),
            )
        await self._wrap_up_in_tree(commission.id)
        return state

    async def _wrap_up_in_tree(self, commission_id: str) -> None:
        """End what a commission that is over had started, and close its session where it is still open."""
        await self._end_what_it_started(commission_id)
        live = self._sessions.get(commission_id)
        if live is not None:
            try:
                await self._close_session(live)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - closed when it is next looked at
                logger.warning("closing the session of commission %s after its landing failed", commission_id, exc_info=True)

    def _tree_commission_changed(self, commission: Commission, previous: str | None) -> None:
        """A ledger listener: what follows, for worktrees and teams, from a commission's status changing."""
        if commission.status not in ENDED_STATUSES or previous in ENDED_STATUSES or self._closed:
            return
        self._team_allowed.discard(commission.id)
        self._teams.discard(commission.id)
        self._stall_reminded.discard(commission.id)
        watch = self._stall_watches.pop(commission.id, None)
        if watch is not None:
            watch.cancel()
        if commission.session_id:
            self._direct_now.discard(commission.session_id)
        inspection = self._inspections.get(commission.id)
        if inspection is not None and not inspection.done() and commission.status == "cancelled":
            # Stopped, which no report of its own tells of.
            inspection.set_result(("cancelled", ""))
        if commission.parent_id in self._teams and commission.parent_id not in self._stall_reminded:
            # Anew with every one that ends: a watch that looked while this one was still at work
            # does not stand for the one that looks after it.
            earlier = self._stall_watches.pop(commission.parent_id, None)
            if earlier is not None:
                earlier.cancel()
            self._stall_watches[commission.parent_id] = self._tree_task(self._watch_stall(commission.parent_id))
        if commission.id in self._tree_of and commission.id not in self._landing_ends:
            self._tree_task(self._tree_left(commission))

    async def _seen_to_after_its_run(self, row: Tree, commission: Commission) -> Tree | None:
        """Remove a worktree a run left without changes, or put it on record as not landed; returns it, None when it is removed or stands otherwise by now."""
        # Its run is being stopped, and may be finishing a write: looked at once it is gone.
        gone = await self._gone_from_runtime(commission.id)
        handle = await self._handle_of(row)
        try:
            # (Still at it: kept, whatever it holds this moment.)
            changed = not gone or (handle is not None and await self.worktrees.has_changes(handle))
            if handle is None and not await self._tree_is_gone(row):
                changed = True  # there, and not to be opened this moment: kept
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - kept: it may hold something
            logger.warning("whether worktree %s holds changes could not be told; it is kept", row.id, exc_info=True)
            changed = True
        if not changed:
            await self._remove_tree(row, handle)
            return None
        # (As it was left at work: one that stands otherwise by now is not written over.)
        return await self._ledger.update_tree(row.id, was=sorted(_AT_WORK), state="unlanded", reason=REASON_STOPPED)

    async def _tree_left(self, commission: Commission) -> None:
        """A commission ended otherwise than by its changes being landed: its worktree is removed, or kept for the user.

        Stopped, failed, or reported stuck by its task agent. What it
        changed so far stays in the worktree, which the user decides
        on (and in which it goes on when it is taken up again); a
        worktree in which nothing was changed is removed.
        """
        landing = self._landings.get(commission.id)
        if landing is not None and landing is not asyncio.current_task() and not landing.done():
            landing.cancel()
            await asyncio.gather(landing, return_exceptions=True)
        self._dones.pop(commission.id, None)
        tree_id = self._tree_of.get(commission.id)
        row = await self._ledger.get_tree(tree_id) if tree_id else None
        if row is None:
            self._tree_of.pop(commission.id, None)
            return
        self._forget_tree(row, commission.session_id)
        if row.state not in _AT_WORK:
            return
        # In hand until it is seen to, with nothing awaited since it was forgotten: a commission
        # that is taken up again meanwhile waits for this, and goes on in the worktree as this
        # leaves it (or in another, where this removes it), ``_tree_to_work_in``.
        tree_id, mine = row.id, row.id not in self._trees_in_hand
        self._trees_in_hand.add(tree_id)
        try:
            row = await self._seen_to_after_its_run(row, commission)
        finally:
            if mine:
                self._trees_in_hand.discard(tree_id)
        if row is None:
            return
        await self._note_tree_event(
            commission.id, "not_landed", {"tree": row.id, "path": row.workdir, "reason": REASON_STOPPED}
        )
        if commission.parent_id is None:
            _told, shown = self._not_landed_texts(row, commission.lang, REASON_STOPPED)
            try:
                await self._reporter.note(commission.lanlan_name, about="tree_not_landed", text=shown, commission=commission)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - it is on the task agent's card all the same
                logger.warning("telling the user of a worktree that was not landed failed", exc_info=True)

    # -- what the user decides --------------------------------------------

    async def trees_view(self, *, project: str | None = None, lanlan_name: str | None = None) -> list[dict[str, Any]]:
        """Return the worktrees on record as a page shows them, the oldest first; each with what its commission and session are called."""
        shown: list[dict[str, Any]] = []
        for row in await self._ledger.trees(project=project, lanlan_name=lanlan_name, states=_SHOWN_STATES):
            commission = await self._ledger.get(row.commission_id) if row.commission_id else None
            session = await self._ledger.get_session(row.session_id) if row.session_id else None
            shown.append(
                {
                    **row.to_json(),
                    "title": commission.title if commission is not None else "",
                    "session_name": session.name if session is not None else "",
                }
            )
        return shown

    @contextlib.asynccontextmanager
    async def _tree_in_hand(self, tree_id: str, states: Any) -> Any:
        """Take a worktree the user decides on in hand, one decision at a time; raises as the three methods below say."""
        if tree_id in self._trees_in_hand:
            raise ValueError("busy")
        # In hand before anything is awaited: a commission that is taken up again meanwhile
        # waits for this, and reads the worktree as this leaves it (``_tree_to_work_in``).
        self._trees_in_hand.add(tree_id)
        try:
            row = await self._ledger.get_tree(tree_id)
            if row is None:
                raise LookupError(tree_id)
            if row.state not in states or tree_id in self._tree_of.values():
                raise ValueError("at_work")
            if row.commission_id and await self._still_at_it_in(row):
                # Its commission was stopped and the run has not gone yet (it was kept without a
                # look for that): not landed or removed under a tool that is finishing a write.
                raise ValueError("at_work")
            if row.commission_id and self._stuck_over(row, await self._ledger.get(row.commission_id)):
                # (Not one its commission left behind and went on without: that one is at work elsewhere.)
                self._decided_for[tree_id] = row.commission_id
            yield row
        finally:
            self._trees_in_hand.discard(tree_id)
            self._decided_for.pop(tree_id, None)

    async def land_tree(self, tree_id: str) -> dict[str, Any]:
        """Land a worktree the user decides on, as the user asks: once more, or for the first time where the user is asked first.

        Merged by the host; no model is asked. Returns ``{"status",
        "files", "more", "conflicts", "resolving", "detail"}``:
        ``landed`` or ``unchanged`` (it is removed, and its commission
        done); ``conflict`` (``resolving`` says whether its commission
        went back to its task agent to merge what clashed, counted from
        the first try again; where it could not, the worktree stays as
        it was); ``failed`` (it stays, ``detail`` says why).

        Raises ``LookupError`` for one that is not on record,
        ``ValueError("at_work")`` for one whose commission is under
        way, ``ValueError("busy")`` while another decision on it is
        carried out, ``ValueError("gone")`` when its directory, or the
        repository it was made from, is not there any more (it can be
        discarded), ``ValueError("not_opened")``
        when it is there and git cannot open it this moment (asked
        again later), ``ValueError("moved")`` when its project works in
        another directory by now than the one it was made from (it is
        not merged into the old one; it can be discarded, or seen to by
        hand), and ``ValueError("busy")`` too where that cannot be told
        this moment.
        """
        async with self._tree_in_hand(tree_id, TREE_STATES_LEFT) as row:
            handle = await self._handle_of(row)
            if handle is None:
                # Gone (or its repository is: there is nothing to merge it into), or there and
                # not to be opened this moment (asked again later).
                gone = await self._tree_is_gone(row) or await self._repository_gone(row)
                raise ValueError("gone" if gone else "not_opened")
            was = row
            row = await self._ledger.update_tree(row.id, state="landing") or row
            try:
                if was.state == "kept":
                    # One the user sees to personally does not keep its project where it is. Looked
                    # at now that it is on record as being landed, which does keep it there.
                    moved = await self._project_moved(row)
                    if moved is None:
                        raise ValueError("busy")
                    if moved:
                        raise ValueError("moved")
                result = await self._merge_or_markers(row, handle)
            except BaseException:
                await asyncio.shield(self._ledger.update_tree(row.id, state=was.state, rounds=was.rounds, checks=was.checks))
                raise
            try:
                return await self._landed_at_the_users_word(tree_id, row, handle, result, was.state == "kept")
            except BaseException:
                # What follows the merge was cut off or failed: one still on record as being landed
                # is the user's to decide on again, and does not stay so until the host starts anew.
                await asyncio.shield(
                    self._ledger.update_tree(
                        row.id, was=("landing",), state="kept" if was.state == "kept" else "unlanded", reason=REASON_FAILED
                    )
                )
                raise

    async def _landed_at_the_users_word(
        self, tree_id: str, row: Tree, handle: Any, result: Any, was_kept: bool = False
    ) -> dict[str, Any]:
        """Act on what a merge the user asked for came to; returns what ``land_tree`` answers."""
        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_brain as texts
        from nekopower.host.task_agents import _Found
        from nekopower.host.worktrees import WorktreeError

        commission = await self._ledger.get(row.commission_id) if row.commission_id else None
        answer: dict[str, Any] = {
            "status": result.status, "files": [], "more": 0, "conflicts": [], "resolving": False, "detail": result.detail,
        }
        if result.status in ("landed", "unchanged"):
            files, more = files_of(result.files, settings.TREE_FILES_LISTED)
            outside = outside_of(result.files, getattr(handle, "subpath", ""))
            answer.update(
                files=files, more=more, outside=outside[: settings.TREE_FILES_LISTED],
                skipped=list(result.skipped[: settings.TREE_FILES_LISTED]),
            )
            if result.status == "landed":
                await self._note_tree_event(
                    row.commission_id, "landed", {**self._landed_payload(row, result, outside), "by": "user"}
                )
            if result.skipped:
                # Landed, but for what is never carried over: that stays where it is, with the worktree.
                await self._keep_for_skipped(row, result.skipped, kept=was_kept)
            else:
                await self._remove_tree(row, handle)
            # (Read as it is settled: its commission may have gone on in another worktree meanwhile.)
            commission = await self._ledger.get(row.commission_id) if row.commission_id else None
            if commission is not None and commission.status == "blocked" and self._stuck_over(row, commission):
                text = (
                    texts.TREE_LANDED_LATER.format(files=files_text(files, more, settings.LANDED_NOTE_MAX_TOKENS))
                    if files
                    else texts.TREE_LANDED_LATER_NOTHING
                )
                moved = await self._ledger.transition(
                    commission.id, "working", reason="the user had what it changed landed", only_from=("blocked",)
                )
                if moved.applied:
                    try:
                        await self._end(
                            commission.id, "done", reason="what it changed was landed at the user's word", text=text,
                            result_summary=self._summary(text),
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - its changes are landed; it is not left at work for nobody
                        logger.exception("commission %s could not be put down as done after its landing", commission.id)
                        await self._fail(commission.id, "what came of it could not be put on record", said="run")
                    await self._settle_after_decision(commission)
            return answer
        if result.status == "conflict":
            answer["conflicts"] = list(result.conflicts[: settings.TREE_FILES_LISTED])
            taker = await self._ledger.get_session(commission.session_id) if commission is not None else None
            if (
                commission is not None
                # (One that failed is taken up again as one that is stuck is: left by a host
                # that ended under it, or by a run that stopped on an error.)
                and commission.status in ("blocked", "failed")
                and self._stuck_over(row, commission)
                # (Not one the user sees to personally: that one is the user's to merge.)
                and not was_kept
                and taker is not None
                and await self._ledger.session_alive(taker)
            ):
                try:
                    if result.detail != _MARKERS_LEFT:
                        handle = await self.worktrees.prepare_conflicts(handle, result)
                except (WorktreeError, OSError) as exc:
                    await self._ledger.update_tree(
                        row.id, state="unlanded", reason=REASON_FAILED, conflicts=result.conflicts
                    )
                    answer.update(status="failed", detail=str(exc))
                    return answer
                self._tree_handles[row.id] = handle
                await self._ledger.update_tree(
                    row.id, state="resolving", rounds=1, conflicts=result.conflicts, token=handle.token, reason=""
                )
                await self._note_tree_event(
                    commission.id,
                    "landing_conflict",
                    {"tree": row.id, "conflicts": answer["conflicts"], "round": 1, "by": "user"},
                )
                said = _Found(
                    texts.TREE_TRY_AGAIN
                    + "\n\n"
                    + (texts.TREE_MARKERS_LEFT if result.detail == _MARKERS_LEFT else texts.TREE_CONFLICT).format(
                        files=names_text(result.conflicts, settings.TREE_FILES_LISTED, settings.LANDED_NOTE_MAX_TOKENS),
                        round=1,
                        rounds=settings.LANDING_CONFLICT_ROUNDS,
                    )
                )
                # (Out of hand from here: its commission goes on in it, ``_tree_to_work_in``.)
                self._trees_in_hand.discard(tree_id)
                if await self._take_up_again(commission.id, said):
                    answer["resolving"] = True
                    return answer
            # (One the user sees to personally stays that: its commission goes on in another.)
            await self._ledger.update_tree(
                row.id, state="kept" if was_kept else "unlanded", reason=REASON_CONFLICT, conflicts=result.conflicts
            )
            return answer
        # (One the user sees to personally stays that.)
        await self._ledger.update_tree(row.id, state="kept" if was_kept else "unlanded", reason=REASON_FAILED)
        if result.kept_copies:
            answer["kept_copies"] = result.kept_copies
        return answer

    @contextlib.asynccontextmanager
    async def project_changing(self, project: str) -> AsyncIterator[None]:
        """Entered by whoever gives a project another directory, for as long as that takes.

        Its sessions are written first and its record second. A worktree
        of it the user sees to personally is not landed in between
        (``_project_moved``): where the project has no leader on record,
        nothing else tells of a change that is half way.
        """
        self._projects_changing.add(project)
        try:
            yield
        finally:
            self._projects_changing.discard(project)

    async def _project_moved(self, row: Tree) -> bool | None:
        """Whether the project a worktree was made for works in another directory by now (or is put away); None where that cannot be told."""
        if self._space is None or not row.project:
            return False
        if row.project in self._projects_changing:
            # Being given another directory this moment (which began before this worktree was put
            # on record as being landed, or it would have been refused): not known where it ends up.
            return None
        try:
            project, readable = await self._space.read_project(row.project)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - not known: nothing is merged on a guess
            logger.debug("where project %s works could not be read", row.project, exc_info=True)
            return None
        if not readable:
            # (What stands in for a record that could not be read names no directory.)
            return None
        if project is None:
            # Put away (or gone).
            return True
        if project.workdir and not _same_dir(project.workdir, row.project_dir):
            return True
        # A change of where it works writes its sessions first and its record second: one that is
        # under way this moment shows in where its leader works already.
        try:
            leaders = await self._ledger.find_sessions(project=row.project, kind="project", commissions_each=0)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - not known, as where the record cannot be read
            logger.debug("where the leader of project %s works could not be read", row.project, exc_info=True)
            return None
        return any(
            item.session.workdir and not _same_dir(item.session.workdir, row.project_dir) for item in leaders
        )

    @staticmethod
    def _stuck_over(row: Tree, commission: Commission | None) -> bool:
        """Whether a commission is stuck over this worktree of its own, and not over another.

        A commission that went on after the user took one of its
        worktrees to see to personally has another: what the user
        decides on the first settles that worktree alone. Told by where
        the commission was last worked on.
        """
        return commission is not None and _same_dir(commission.workdir, row.workdir)

    async def _settle_after_decision(self, commission: Commission) -> None:
        """Write where the session of a commission stands that the user's decision on its worktree ended.

        No runtime had it open for that commission, which was over. A
        session another commission holds by now is that one's to say.
        """
        session_id = commission.session_id
        if not session_id or self._held_by.get(session_id) is not None:
            return
        try:
            await self._ledger.settle_session(session_id, commission.id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the status is for showing
            logger.warning("noting where a session stands failed", exc_info=True)

    async def discard_tree(self, tree_id: str) -> dict[str, Any]:
        """Remove a worktree the user decides on, with what it holds, as the user asks; returns ``{"removed": bool}``.

        Its commission, where it was stuck over it (and not over
        another worktree of its own), is put down as stopped.
        ``removed`` is False when its directory could not be removed (a
        file of it is in use): it is shown no more, and removed when
        the host next starts. False as well where its repository is
        gone and the directory there is not the worktree's any more:
        that one is left alone for good, and only the record goes.
        Raises as ``land_tree``.
        """
        async with self._tree_in_hand(tree_id, TREE_STATES_LEFT) as row:
            hold_workdir_fn = getattr(self, "hold_workdir", None)
            release_workdir_fn = getattr(self, "release_workdir", None)
            workdir_hold: str | None = None
            if callable(hold_workdir_fn):
                workdir_hold = hold_workdir_fn(row.path)
                if workdir_hold is None:
                    raise ValueError("at_work")
            try:
                if await self._workdir_referenced_actively(row.path, ignore_blocked_commission_id=row.commission_id):
                    raise ValueError("at_work")
                handle = await self._handle_for_cleanup(row)
                if handle is None and not await self._tree_is_gone(row):
                    repo_root = row.repo_root or row.project_dir
                    if repo_root and not await self._repo_root_gone(repo_root):
                        # There, and not to be opened this moment: nothing of it could be removed.
                        raise ValueError("not_opened")
                    # Its repository is gone (moved, or removed): git never opens it again. What the
                    # user discards is the directory the host made for it, where it still says it is
                    # that. One that could not be removed now (a file of it is in use) stays on record
                    # as one to remove; a directory that is not the worktree's any more is left alone,
                    # and only the record goes.
                    went = await self._remove_what_is_left(row)
                    self._forget_tree(row)
                    self._tree_handles.pop(row.id, None)
                    self._trees_mine.discard(row.id)
                    if went is False:
                        await self._ledger.update_tree(row.id, state="discarding", reason="")
                    else:
                        await self._ledger.drop_tree(row.id)
                    removed = went is True
                else:
                    removed = await self._remove_tree(row, handle, discarded=True)
            finally:
                if workdir_hold is not None and callable(release_workdir_fn):
                    release_workdir_fn(workdir_hold)
            await self._note_tree_event(row.commission_id, "tree_discarded", {"tree": row.id, "path": row.workdir})
            # (Read as it is settled: its commission may have gone on in another worktree meanwhile.)
            stuck = self._stuck_over(row, await self._ledger.get(row.commission_id) if row.commission_id else None)
            if row.commission_id and stuck:
                moved = await self._ledger.transition(
                    row.commission_id, "cancelled", reason="the user discarded what it changed", only_from=("blocked",)
                )
                if moved.applied and moved.commission is not None:
                    await self._settle_after_decision(moved.commission)
            return {"removed": removed}

    async def keep_tree(self, tree_id: str) -> dict[str, Any]:
        """Leave a worktree to the user, who sees to it personally; returns its row as a page shows it.

        It stays where it is, nothing reminds of it any more, and it no
        longer keeps the project from being given another directory.
        It can still be landed or discarded. Raises as ``land_tree``.
        """
        async with self._tree_in_hand(tree_id, ("held", "unlanded")) as row:
            kept = await self._ledger.update_tree(row.id, state="kept") or row
            await self._note_tree_event(row.commission_id, "tree_kept", {"tree": row.id, "path": row.workdir})
            return kept.to_json()

    async def retry_discarded_tree(
        self,
        tree_id: str,
        *,
        until: float | None = None,
        check_mtime: Any = None,
    ) -> bool:
        """Retry removing a single ``discarding`` worktree under ``_trees_in_hand``."""
        if tree_id in self._trees_in_hand:
            raise ValueError("busy")
        self._trees_in_hand.add(tree_id)
        try:
            row = await self._ledger.get_tree(tree_id)
            if row is None:
                raise LookupError(tree_id)
            if row.state != "discarding" or tree_id in self._tree_of.values():
                raise ValueError("at_work")
            return await self._remove_discarded(row, until=until, check_mtime=check_mtime)
        finally:
            self._trees_in_hand.discard(tree_id)

    async def retry_discarded_trees(
        self,
        *,
        include_expired: bool = True,
        only_expired: bool = False,
        until: float | None = None,
        check_mtime: Any = None,
    ) -> int:
        """Retry removing ``discarding`` worktrees left from earlier attempts; returns how many went."""
        removed = 0
        for row in await self._ledger.trees(states=("discarding",)):
            if only_expired and row.reason != "expired":
                continue
            if not include_expired and row.reason == "expired":
                continue
            if row.id in self._trees_in_hand or row.id in self._tree_of.values():
                continue
            try:
                if await self.retry_discarded_tree(row.id, until=until, check_mtime=check_mtime):
                    removed += 1
            except (LookupError, ValueError):
                continue
        return removed

    async def expire_tree(
        self,
        tree_id: str,
        *,
        until: float,
        check_mtime: Any = None,
    ) -> dict[str, Any]:
        """Automatically remove a left-over worktree older than ``until`` if untouched since then."""
        from nekopower.host.storage import _classify_path_safety
        from nekopower.host.worktrees import WorktreeError, WorktreeRemovalIncomplete

        def evaluate_mtime(path: Path) -> str | None:
            return self._evaluate_mtime(path, until, check_mtime)

        async with self._tree_in_hand(tree_id, TREE_STATES_LEFT) as row:
            if row.updated_at > until:
                return {"expired": False, "reason": "recent"}
            safety = await asyncio.to_thread(_classify_path_safety, Path(row.path), self._paths.worktrees_dir())
            if safety not in ("ok", "missing"):
                logger.warning(
                    "worktree %s path %s failed managed-root safety check (%s); skipping expiry",
                    row.id,
                    row.path,
                    safety,
                )
                return {"expired": False, "reason": "unsafe_path"}
            hold_workdir_fn = getattr(self, "hold_workdir", None)
            release_workdir_fn = getattr(self, "release_workdir", None)
            workdir_hold: str | None = None
            if callable(hold_workdir_fn):
                workdir_hold = hold_workdir_fn(row.path)
                if workdir_hold is None:
                    return {"expired": False, "reason": "in_use"}
            try:
                if await self._workdir_referenced_actively(row.path, ignore_blocked_commission_id=row.commission_id):
                    return {"expired": False, "reason": "in_use"}
                if check_mtime is not None and not await self._tree_is_gone(row):
                    pre_abort = await asyncio.to_thread(evaluate_mtime, Path(row.path))
                    if pre_abort is not None:
                        return {"expired": False, "reason": pre_abort}
                handle = await self._handle_for_cleanup(row)
                outcome_kind = "removed"
                removed = True
                if handle is None and not await self._tree_is_gone(row):
                    repo_root = row.repo_root or row.project_dir
                    if repo_root and not await self._repo_root_gone(repo_root):
                        raise ValueError("not_opened")
                    abort_gone: list[str] = []

                    def still_untouched_gone(path: Path) -> bool:
                        reason = evaluate_mtime(path)
                        if reason is not None:
                            abort_gone.append(reason)
                            return False
                        return True

                    went = await self._remove_what_is_left(
                        row, before_delete=still_untouched_gone if check_mtime is not None else None
                    )
                    if went == "aborted":
                        return {"expired": False, "reason": abort_gone[0] if abort_gone else "touched"}
                    self._forget_tree(row)
                    self._tree_handles.pop(row.id, None)
                    self._trees_mine.discard(row.id)
                    if went is True:
                        removed, outcome_kind = True, "removed"
                    elif went is False:
                        removed, outcome_kind = False, "deferred"
                    else:
                        removed, outcome_kind = False, "record_only"
                elif handle is not None:
                    abort_reason: list[str] = []

                    def still_untouched(path: Path) -> bool:
                        reason = evaluate_mtime(path)
                        if reason is not None:
                            abort_reason.append(reason)
                            return False
                        return True

                    try:
                        result = await self.worktrees.remove_if_untouched(
                            handle, still_untouched if check_mtime is not None else None
                        )
                    except asyncio.CancelledError:
                        raise
                    except WorktreeRemovalIncomplete as exc:
                        logger.warning("worktree %s could not be removed completely during expiry: %s", row.id, exc)
                        result = "deferred"
                        removed = False
                        outcome_kind = "deferred"
                    except (WorktreeError, OSError) as exc:
                        # Failed before deletion began (e.g. git index.lock, token mismatch, unknown identity):
                        # leave tree record and blocked commission untouched for a future upkeep round.
                        logger.warning("worktree %s expiry skipped before deletion: %s", row.id, exc)
                        return {"expired": False, "reason": "not_removable"}
                    if result == "aborted" or result is False:
                        return {"expired": False, "reason": abort_reason[0] if abort_reason else "touched"}
                    if result == "record_only":
                        removed = False
                        outcome_kind = "record_only"
                    self._forget_tree(row)
                    self._tree_handles.pop(row.id, None)
                    self._trees_mine.discard(row.id)
                else:
                    # Directory is already gone; drop lingering git registration/refs if repository still exists.
                    repo_root = row.repo_root or row.project_dir
                    if repo_root and not await self._repo_root_gone(repo_root):
                        try:
                            await self.worktrees.forget_gone(repo_root, row.path, row.id)
                        except asyncio.CancelledError:
                            raise
                        except (WorktreeError, OSError) as exc:
                            logger.warning("worktree %s metadata cleanup deferred: %s", row.id, exc)
                            removed = False
                            outcome_kind = "deferred"
                    self._forget_tree(row)
                    self._tree_handles.pop(row.id, None)
                    self._trees_mine.discard(row.id)
            finally:
                if workdir_hold is not None and callable(release_workdir_fn):
                    release_workdir_fn(workdir_hold)

            # Persist pending expiry settlement in space state (independent of ledger.db)
            # before cancelling any blocked commission or dropping the tree row so a
            # ledger failure or crash followed by restart still settles the commission.
            await self._record_pending_expired_tree(row.id, outcome_kind)
            await self._settle_expired_tree(row, outcome_kind, removed=removed)
            await self._clear_pending_expired_trees((row.id,))
            return {"expired": True, "removed": removed, "outcome": outcome_kind}

    async def _read_pending_expired_trees(self) -> dict[str, str]:
        """Return ``{tree_id: outcome_kind}`` for auto-expiries awaiting ledger settlement."""
        space = getattr(self, "_space", None)
        if space is None or not hasattr(space, "read_state"):
            return {}
        try:
            state = await space.read_state()
        except asyncio.CancelledError:
            raise
        except OSError:
            return {}
        raw = state.get("pending_expired_trees") if isinstance(state, dict) else None
        if not isinstance(raw, dict):
            return {}
        valid = {"removed", "deferred", "record_only"}
        return {str(k): str(v) for k, v in raw.items() if isinstance(k, str) and v in valid}

    async def _record_pending_expired_tree(self, tree_id: str, outcome_kind: str) -> None:
        """Persist a pending expiry settlement in space state."""
        space = getattr(self, "_space", None)
        if space is None or not hasattr(space, "write_state"):
            return
        try:
            current = await self._read_pending_expired_trees()
            current[tree_id] = outcome_kind
            await space.write_state(pending_expired_trees=current)
        except asyncio.CancelledError:
            raise
        except OSError:
            logger.debug("could not persist pending expiry state for worktree %s", tree_id, exc_info=True)

    async def _clear_pending_expired_trees(self, tree_ids: tuple[str, ...] | set[str]) -> None:
        """Remove settled worktree ids from persisted pending expiry state."""
        space = getattr(self, "_space", None)
        if not tree_ids or space is None or not hasattr(space, "write_state"):
            return
        try:
            current = await self._read_pending_expired_trees()
            changed = False
            for tid in tree_ids:
                if tid in current:
                    del current[tid]
                    changed = True
            if changed:
                await space.write_state(pending_expired_trees=current)
        except asyncio.CancelledError:
            raise
        except OSError:
            logger.debug("could not clear pending expiry state for worktrees", exc_info=True)

    async def _cancel_expired_commission(self, row: Tree, outcome_kind: str) -> None:
        """Cancel a commission blocked over an expired worktree and settle its session."""
        cancel_reasons = {
            "removed": "the worktree expired and was cleaned up",
            "deferred": "the worktree expired and is pending removal",
            "record_only": "the worktree expired and its record was removed",
        }
        stuck = self._stuck_over(row, await self._ledger.get(row.commission_id) if row.commission_id else None)
        if row.commission_id and stuck:
            moved = await self._ledger.transition(
                row.commission_id,
                "cancelled",
                reason=cancel_reasons.get(outcome_kind, cancel_reasons["removed"]),
                only_from=("blocked",),
            )
            if moved.applied and moved.commission is not None:
                await self._settle_after_decision(moved.commission)

    async def _settle_expired_tree(self, row: Tree, outcome_kind: str, *, removed: bool) -> None:
        """Cancel any blocked commission, record the timeline event, and update/drop the tree row."""
        await self._cancel_expired_commission(row, outcome_kind)
        await self._note_tree_event(
            row.commission_id,
            "tree_expired",
            {
                "tree": row.id,
                "path": row.workdir,
                "state": row.state,
                "removed": removed,
                "outcome": outcome_kind,
            },
        )
        if outcome_kind == "deferred":
            await self._ledger.update_tree(row.id, state="discarding", reason="expired")
        else:
            await self._ledger.drop_tree(row.id)

    async def remove_orphan_worktree(self, tree_id: str) -> dict[str, Any]:
        """Remove an untracked directory inside ``wt/`` safely, unregistering it from git when possible."""
        from nekopower.host.paths import worktree_name
        from nekopower.host.storage import _classify_path_safety, measure_dir, remove_dir_safely
        from nekopower.host.worktrees import WorktreeError
        from utils.asyncio_executor import to_thread_through_cancellation

        name = worktree_name(tree_id)
        norm_name = os.path.normcase(name)
        base = self._paths.worktrees_dir()
        target = self._paths.worktree_dir(name)

        def _same_fs_entry(left: Path, right: Path) -> bool:
            if _same_dir(str(left), str(right)):
                return True
            try:
                return os.path.samefile(left, right)
            except OSError:
                return False

        async with self._tree_room:
            if self._trees_making > 0 or self._trees_looking > 0:
                raise ValueError("busy")
            for active_id in (*self._trees_in_hand, *self._trees_mine, *self._tree_of.values()):
                if os.path.normcase(active_id) == norm_name or await asyncio.to_thread(
                    _same_fs_entry, target, self._paths.worktree_dir(active_id)
                ):
                    raise ValueError("busy")
            for row in await self._ledger.trees():
                if os.path.normcase(row.id) == norm_name or await asyncio.to_thread(
                    _same_fs_entry, target, Path(row.path)
                ):
                    raise ValueError("busy")
            self._trees_in_hand.add(name)
        try:
            # Validate base (wt/) and every segment down to target BEFORE invoking git worktree removal.
            safety = await asyncio.to_thread(_classify_path_safety, target, base)
            if safety == "missing":
                return {"removed": True, "freed_bytes": 0}
            if safety != "ok":
                raise ValueError("unsafe_path")
            hold_workdir_fn = getattr(self, "hold_workdir", None)
            release_workdir_fn = getattr(self, "release_workdir", None)
            workdir_hold: str | None = None
            if callable(hold_workdir_fn):
                workdir_hold = hold_workdir_fn(target)
                if workdir_hold is None:
                    raise ValueError("busy")
            try:
                if await self._workdir_referenced_actively(target):
                    raise ValueError("busy")
                pre_stats = await asyncio.to_thread(measure_dir, target, time.monotonic() + 0.25)
                est_bytes = pre_stats.bytes if pre_stats.exists else 0
                # Try git removal first when the directory's .git points to an existing repository.
                git_attempted = False
                repo_root = await asyncio.to_thread(_repo_from_worktree_gitfile, target)
                if repo_root is not None and await asyncio.to_thread(repo_root.is_dir):
                    git_attempted = True
                    try:
                        handle = await self.worktrees.open(repo_root, target)
                        await self.worktrees.remove(handle)
                    except asyncio.CancelledError:
                        raise
                    except (WorktreeError, ValueError, OSError):
                        # Git unregistration unavailable; fall back to direct removal below.
                        pass
                if not await asyncio.to_thread(lambda: os.path.lexists(target)):
                    return {"removed": True, "freed_bytes": est_bytes}
                git_freed = 0
                if git_attempted and pre_stats.whole:
                    mid_stats = await asyncio.to_thread(measure_dir, target, time.monotonic() + 0.25)
                    if mid_stats.whole:
                        git_freed = max(0, est_bytes - (mid_stats.bytes if mid_stats.exists else 0))
                removed, freed = await to_thread_through_cancellation(remove_dir_safely, target, base)
                if git_attempted and not pre_stats.whole and removed:
                    total_freed = max(est_bytes, freed)
                else:
                    total_freed = git_freed + freed
                return {"removed": removed, "freed_bytes": total_freed}
            finally:
                if workdir_hold is not None and callable(release_workdir_fn):
                    release_workdir_fn(workdir_hold)
        finally:
            self._trees_in_hand.discard(name)

    async def reconcile_trees(self) -> dict[str, list[str]]:
        """Go through the worktrees an earlier process left, as the host starts; returns what was removed and what is kept.

        One on record whose directory is gone is taken off the record.
        One whose commission was under way when that process ended is
        kept for the user when it holds changes, and removed when it
        holds none; so is one the user was to decide on that holds none
        any more. Then every repository a project works in is asked for
        worktrees of the host that nothing on record knows of
        (``WorktreeManager.reconcile``): removed where they hold
        nothing, put on record for the user where they do.
        """
        self._trees_looking += 1
        try:
            while self._trees_making and not self._closed:
                # One is being made, on record or about to be: gone through once it stands.
                await asyncio.sleep(_POLL_SECONDS)
            return await self._reconcile_trees_now()
        finally:
            self._trees_looking -= 1

    async def _reconcile_trees_now(self) -> dict[str, list[str]]:
        from nekopower.host.worktrees import GitError, WorktreeError, WorktreeUnavailable

        removed: list[str] = []
        kept: list[str] = []
        homes: dict[str, tuple[str, str | None]] = {}
        pending_expired = await self._read_pending_expired_trees()
        seen_tree_ids: set[str] = set()
        settled_pending: set[str] = set()
        for row in await self._ledger.trees():
            seen_tree_ids.add(row.id)
            homes.setdefault(os.path.normcase(os.path.normpath(row.project_dir)), (row.project_dir, row.project))
            if row.id in self._trees_mine or row.id in self._trees_in_hand or row.id in self._tree_of.values():
                continue
            if row.state == "discarding":
                # Auto-expired retries always wait for upkeep so freshness is re-checked
                # before any deletion (even if the directory appeared gone moments earlier).
                if row.reason == "expired":
                    if row.id in pending_expired:
                        try:
                            await self._cancel_expired_commission(row, pending_expired[row.id])
                            settled_pending.add(row.id)
                        except asyncio.CancelledError:
                            raise
                        except Exception:  # noqa: BLE001 - retry on next restart/upkeep
                            logger.warning("pending expiry commission settlement failed for %s", row.id, exc_info=True)
                    kept.append(row.id)
                    continue
                # The user discarded it: finish removal now.
                if await self._remove_discarded(row):
                    removed.append(row.id)
                continue
            # A worktree that entered auto-expiry before a crash or ledger failure: finish
            # git metadata cleanup (if directory is gone) and blocked-commission settlement
            # rather than silently dropping the row under the external-deletion branch.
            if row.id in pending_expired and row.state in TREE_STATES_LEFT:
                self._trees_in_hand.add(row.id)
                try:
                    outcome_kind = pending_expired[row.id]
                    gone = await self._tree_is_gone(row)
                    if gone:
                        repo_root = row.repo_root or row.project_dir
                        if repo_root and not await self._repo_root_gone(repo_root):
                            try:
                                await self.worktrees.forget_gone(repo_root, row.path, row.id)
                            except asyncio.CancelledError:
                                raise
                            except (WorktreeError, OSError) as exc:
                                logger.warning(
                                    "pending expired worktree %s metadata cleanup deferred: %s", row.id, exc
                                )
                                outcome_kind = "deferred"
                    self._forget_tree(row)
                    await self._settle_expired_tree(
                        row, outcome_kind, removed=(gone and outcome_kind == "removed")
                    )
                    settled_pending.add(row.id)
                    if outcome_kind == "deferred":
                        kept.append(row.id)
                    else:
                        removed.append(row.id)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - keep pending marker and row for retry
                    logger.warning("pending expiry recovery failed for worktree %s", row.id, exc_info=True)
                    kept.append(row.id)
                finally:
                    self._trees_in_hand.discard(row.id)
                continue
            # In hand from here on, with nothing awaited since the look above: a commission that is
            # taken up again meanwhile waits for this before it goes on in its worktree
            # (``_tree_to_work_in``), and finds it as this leaves it.
            self._trees_in_hand.add(row.id)
            try:
                handle = await self._handle_of(row)
                if handle is None and not await self._tree_is_gone(row):
                    # There, and not to be opened this moment (git did not answer): left on record
                    # as it is, and looked at when the host next starts.
                    kept.append(row.id)
                    continue
                if handle is None:
                    self._forget_tree(row)
                    await self._ledger.drop_tree(row.id)
                    removed.append(row.id)
                    continue
                try:
                    changed = await self.worktrees.has_changes(handle)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - kept: it may hold something
                    changed = True
                if not changed:
                    await self._remove_tree(row, handle)
                    removed.append(row.id)
                elif row.state in _AT_WORK:
                    await self._ledger.update_tree(row.id, state="unlanded", reason=REASON_RESTART)
                    await self._note_tree_event(
                        row.commission_id, "not_landed", {"tree": row.id, "path": row.workdir, "reason": REASON_RESTART}
                    )
                    kept.append(row.id)
                else:
                    kept.append(row.id)
            finally:
                self._trees_in_hand.discard(row.id)
        stale_pending = {tid for tid in pending_expired if tid not in seen_tree_ids} | settled_pending
        if stale_pending:
            await self._clear_pending_expired_trees(stale_pending)
        if self._space is not None:
            try:
                for project in await self._space.list_projects(include_archived=False):
                    if project.workdir:
                        homes.setdefault(
                            os.path.normcase(os.path.normpath(project.workdir)), (project.workdir, project.name)
                        )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - those on record are gone through all the same
                logger.warning("the projects could not be listed to look for worktrees left over", exc_info=True)
        base = self._paths.worktrees_dir()
        if not await asyncio.to_thread(base.is_dir):
            # The host never made a worktree here: nothing of its own is left anywhere, and no git is run.
            return {"removed": removed, "kept": kept}
        for home, project in homes.values():
            known = {row.id for row in await self._ledger.trees()} | self._trees_mine
            try:
                report = await self.worktrees.reconcile(home, base, known)
            except asyncio.CancelledError:
                raise
            except WorktreeUnavailable:
                continue  # no git repository, or no git: nothing of the host's is there
            except (GitError, WorktreeError, OSError) as exc:
                logger.warning("looking for worktrees left over in %s failed: %s", home, exc)
                continue
            removed.extend(report.removed)
            for path in report.kept:
                try:
                    handle = await self.worktrees.open(home, path)
                    row = await self._ledger.add_tree(
                        id=handle.id, project=project, project_dir=home, repo_root=str(handle.repo_root),
                        path=str(handle.path), workdir=str(handle.workdir), token=handle.token, state="unlanded",
                        reason=REASON_RESTART,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - left where it is
                    logger.warning("worktree %s holds changes and could not be put on record: %s", path, exc)
                    continue
                self._tree_handles[row.id] = handle
                kept.append(row.id)
            for path, why in report.failed:
                logger.warning("worktree %s was left as it is: %s", path, why)
        return {"removed": removed, "kept": kept}

    # -- an inspector before a landing ------------------------------------

    async def _inspect(self, row: Tree, commission: Commission) -> tuple[str, str]:
        """Have an inspector look at what a commission changed in its worktree; returns the verdict and what it said.

        A task agent of the inspector's preset, started by the host for
        the commission (what it uses is the commission's), which works
        in the worktree itself. Told by how it reports and not by its
        words: ``ready`` passes, ``blocked`` says something is wrong
        with the work, anything else (``failed``, stopped, not started,
        ended without a report) is no verdict, and the changes land as
        if nobody had looked.
        """
        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_brain as texts
        from utils.tokenize import truncate_to_tokens

        what = "\n".join(part for part in (commission.title, commission.dispatch_text) if part)
        try:
            child, _created = await self._ledger.create(
                lanlan_name=commission.lanlan_name,
                title=texts.TREE_CHECK_TITLE.format(title=commission.title),
                dispatch_text=texts.TREE_CHECK_BRIEF.format(
                    what=truncate_to_tokens(what, settings.INSPECTION_BRIEF_MAX_TOKENS)
                ),
                user_text=commission.user_text,
                source="task_agent",
                route="task_agent",
                parent_id=commission.id,
                name=f"inspector-{row.checks + 1}",
                project=commission.project,
                workdir=row.workdir,
                conversation_id=commission.conversation_id,
                lang=commission.lang,
                events=[("preset", {"preset": "inspector", "by": "host"})],
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - no verdict: it lands as if nobody had looked
            logger.warning("an inspector could not be filed for commission %s", commission.id, exc_info=True)
            return CHECK_NONE, ""
        heard: asyncio.Future[tuple[str, str]] = asyncio.get_running_loop().create_future()
        self._inspections[child.id] = heard
        try:
            if not await self.submit(child, candidate_plugin_ids=(), wait=False):
                return CHECK_NONE, ""
            status, said = await heard
        except asyncio.CancelledError:
            # The landing is called off: so is who looks at it.
            await asyncio.shield(self._stop_inspector(child.id))
            raise
        finally:
            self._inspections.pop(child.id, None)
        if not await self._gone_from_runtime(child.id):
            # Heard of (stopped, say) and not gone from its runtime: it may be finishing a tool in there.
            return CHECK_STUCK, said
        if status == "completed":
            # (Done by a report, or by a run that ended without one: only the first says it passes.)
            return (CHECK_PASSED if await self._ledger.has_event(child.id, "report") else CHECK_NONE), said
        if status == "blocked":
            return CHECK_FAILED, said
        return CHECK_NONE, said

    async def _stop_inspector(self, commission_id: str) -> None:
        try:
            moved = await self._ledger.transition(commission_id, "cancelled", reason="what it was to check is not landed now")
            if moved.applied:
                await self.abort(commission_id)
        except Exception:  # noqa: BLE001 - it ends with the commission it was started for
            logger.warning("stopping an inspector failed", exc_info=True)

    def _heard_of_inspection(self, commission: Commission, status: str, text: str) -> bool:
        """Take what an inspector the host started reported; False for any other task agent, whose parent is told."""
        heard = self._inspections.get(commission.id)
        if heard is None:
            return False
        if not heard.done():
            heard.set_result((status, text))
        return True

    # -- a leader's team --------------------------------------------------

    async def _chosen(self, commission_id: str) -> dict[str, Any]:
        """Return what a task agent was started with by whoever started it (the ``preset`` event of its commission); empty for none."""
        from nekopower.host.task_agents import _PRESET_ON_RECORD_WITHIN

        for event in await self._ledger.events(commission_id, limit=_PRESET_ON_RECORD_WITHIN):
            if event.type == "preset":
                return dict(event.payload)
        return {}

    async def _cb_start_team(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Start several task agents for the commission a leader is at, each with a part of it (the ``start_team`` tool).

        The leader keeps the commission. Each part is started as a task
        agent of its own, as ``subagent`` starts one, of the preset
        named (a worker when none is): one that changes files in a
        worktree made for it, whose changes the host lands when it is
        done, and one that reads in the project's directory. The user
        is asked once for a team that changes files (``OPEN_SESSION``,
        with who is to be started); allowed, the rest of this
        commission's team is not asked about again.
        """
        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_brain as texts
        from nekopower.host.task_agents import _STILL_WORKING
        from nekopower.host.templates import TemplateError, model_name
        from utils.tokenize import truncate_to_tokens

        commission_id = self._at_work_in(session_id)
        live = self._sessions.get(commission_id)
        commission = await self._ledger.get(commission_id)
        leader = await self._ledger.get_session(session_id)
        if (
            live is None
            or commission is None
            or leader is None
            or leader.kind != "project"
            or not leader.project
            or commission.parent_id is not None
            or commission.session_id != leader.id
        ):
            raise RuntimeError(texts.START_TEAM_NOT_A_LEADER)
        if live.handing_over is not None:
            raise RuntimeError(texts.HANDED_OVER_ALREADY)
        if commission.status != "working":
            raise RuntimeError("the commission is not at work; no team is started")
        wanted = payload.get("parts")
        if not isinstance(wanted, list) or not wanted or not all(isinstance(part, dict) for part in wanted):
            raise RuntimeError(texts.START_TEAM_NO_PARTS)
        over = await self._template_of(leader)
        parts: list[dict[str, Any]] = []
        for number, part in enumerate(wanted, 1):
            description = " ".join(str(part.get("description") or "").split())
            brief = truncate_to_tokens(str(part.get("brief") or ""), settings.TEAM_PART_BRIEF_MAX_TOKENS)
            if not description or not brief.strip():
                raise RuntimeError(texts.START_TEAM_PART_INCOMPLETE.format(number=number))
            preset = await self._preset_wanted(commission, part.get("preset")) or "worker"
            try:
                template = (await asyncio.to_thread(self.templates.resolve, preset, leader.project)).under(over)
            except TemplateError as exc:
                raise RuntimeError(f"the preset {preset!r} cannot be used: {exc}") from exc
            parts.append(
                {
                    "name": part.get("name"), "description": description, "brief": brief, "preset": preset,
                    "writes": bool(template.changes_files),
                }
            )
        working = len(await self._ledger.select(parent_id=commission_id, statuses=_STILL_WORKING))
        if working + len(parts) > settings.TEAM_MAX_WORKING:
            raise RuntimeError(texts.START_TEAM_TOO_MANY.format(most=settings.TEAM_MAX_WORKING, working=working))
        writers = [part for part in parts if part["writes"]]
        home = leader.workdir or commission.workdir
        if writers:
            probe = await self.worktrees.probe(home) if home else None
            if probe is None or not probe.usable:
                why = (probe.detail or str(probe.reason)) if probe is not None else "the project has no directory"
                raise RuntimeError(texts.START_TEAM_NO_TREES.format(reason=why))
            there = len(await self._ledger.trees(project=leader.project, states=_SHOWN_STATES))
            if there + len(writers) > settings.WORKTREES_PER_PROJECT:
                raise RuntimeError(
                    texts.START_TEAM_NO_ROOM.format(
                        there=there, most=settings.WORKTREES_PER_PROJECT, room=max(0, settings.WORKTREES_PER_PROJECT - there)
                    )
                )
            if commission_id not in self._team_allowed:
                members = [
                    truncate_to_tokens(
                        f"{model_name(part['preset'])}: {part['description']}", settings.TEAM_ASK_MEMBERS_MAX_TOKENS
                    )
                    for part in writers
                ]
                decision = await self._permissions.check(
                    commission_id,
                    OPEN_SESSION,
                    detail={
                        "tool": "start_team",
                        "title": "; ".join(members),
                        "members": members,
                        # The same, apart: for a page that names a preset in its own words.
                        "parts": [
                            {
                                "preset": part["preset"],
                                "description": truncate_to_tokens(
                                    part["description"], settings.TEAM_ASK_MEMBERS_MAX_TOKENS
                                ),
                            }
                            for part in writers
                        ],
                        "count": len(writers),
                        "tree": True,
                    },
                )
                if not decision.allow:
                    raise RuntimeError(texts.START_TEAM_NOT_ALLOWED.format(reason=decision.reason or "declined"))
                # The user may have taken a while: looked at once more.
                live = self._sessions.get(commission_id)
                commission = await self._ledger.get(commission_id)
                if live is None or live.closing or commission is None or commission.status != "working":
                    raise RuntimeError("the commission is not at work any more; no team is started")
                if live.handing_over is not None:
                    raise RuntimeError(texts.HANDED_OVER_ALREADY)
                self._team_allowed.add(commission_id)
        inspect = bool(payload.get("inspect"))
        taken: list[dict[str, Any]] = []
        refused: list[tuple[str, str]] = []
        for part in parts:
            chosen: dict[str, Any] = {"team": True}
            if part["writes"]:
                chosen["seat"] = "tree"
                if inspect:
                    chosen["inspect"] = True
            try:
                name, child = await self._start_child(
                    commission, name=part["name"], description=part["description"], prompt=part["brief"],
                    preset=part["preset"], workdir=home, chosen=chosen,
                )
            except RuntimeError as exc:
                refused.append((str(part["name"] or part["description"]), str(exc)))
                continue
            taken.append({"name": name, "preset": part["preset"], "commission_id": child.id, "writes": part["writes"]})
        # Taken is not started: one for which no worktree could be made after all is not one of the team.
        without = await self._got_no_tree(
            [item["commission_id"] for item in taken if item["writes"]], settings.TEAM_START_WAIT_SECONDS
        )
        started = [item for item in taken if item["commission_id"] not in without]
        refused.extend((item["name"], without[item["commission_id"]]) for item in taken if item["commission_id"] in without)
        if started:
            self._teams.add(commission_id)
            await self._note_tree_event(commission_id, "team_formed", {"members": started, "inspect": inspect})
        if not started:
            raise RuntimeError("; ".join(f"{name}: {why}" for name, why in refused) or "no task agent was started")
        message = texts.START_TEAM_DONE.format(names=", ".join(item["name"] for item in started))
        if refused:
            message += texts.START_TEAM_NOT_STARTED.format(
                names=", ".join(name for name, _why in refused), reason=refused[0][1]
            )
        return {"message": message, "started": [item["name"] for item in started]}

    async def _got_no_tree(self, commission_ids: list[str], seconds: float) -> dict[str, str]:
        """Wait until each of a team's task agents that change files has its worktree or is over; return why, for those that got none.

        No longer than ``seconds``: one still being started by then
        counts as started, and its parent hears of it as of any that
        fails.
        """
        deadline = time.monotonic() + seconds
        without: dict[str, str] = {}
        waiting = list(commission_ids)
        while waiting and not self._closed:
            for item in list(waiting):
                if item in self._tree_of:
                    waiting.remove(item)
                    continue
                stands = await self._ledger.get(item)
                if stands is None or stands.status in ENDED_STATUSES:
                    waiting.remove(item)
                    if stands is not None and not await self._ledger.has_event(item, "tree_made"):
                        # Over with no worktree ever made for it: none could be made, or its start
                        # came to nothing before that. (One that was at work and is done has had one.)
                        without[item] = stands.status_reason or "no worktree could be made for it"
            if not waiting or time.monotonic() >= deadline:
                break
            await asyncio.sleep(_POLL_SECONDS)
        return without

    async def _team_still_at_work(self, session_id: str, payload: dict[str, Any]) -> None:
        """Raise when a leader reports a commission done while task agents of the team it started for it are still at work.

        A commission that is done has what it started stopped: reported
        done too early, a part that is still being worked on would be
        stopped half way, with its changes not in the project, under a
        report that says it is all there. The leader waits for them, or
        stops the ones it no longer wants, and reports then. (Giving up,
        ``blocked`` or ``failed``, is let through: that stops them.)
        """
        from config.prompts import prompts_nekopower_brain as texts
        from nekopower.host.task_agents import _STILL_WORKING

        commission_id = self._at_work_in(session_id)
        if payload.get("status") != "ready":
            return
        if commission_id not in self._teams and not await self._ledger.has_event(commission_id, "team_formed"):
            # (By what is on record as well: a host started since knows of no team it started.)
            return
        working = await self._ledger.select(parent_id=commission_id, statuses=_STILL_WORKING, oldest_first=True)
        if working:
            raise RuntimeError(
                texts.TEAM_STILL_AT_WORK.format(
                    count=len(working), names=", ".join(member.name or member.id[:8] for member in working)
                )
            )

    async def _watch_stall(self, commission_id: str) -> None:
        """Remind a leader, once, that every task agent it started for a commission is over and the commission stands still.

        A task agent's end is told to the leader as it comes, and wakes
        it; this is for where it was not (one was stopped by the user,
        say, which no report tells of). Looked at a while after one of
        them ended: by rule, with no model asked. The reminder gives
        the leader a turn.
        """
        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_brain as texts
        from nekopower.host.task_agents import _STILL_WORKING, _Found
        from utils.tokenize import truncate_to_tokens

        try:
            await asyncio.sleep(settings.TEAM_STALL_SECONDS)
            if self._closed:
                return
            commission = await self._ledger.get(commission_id)
            if commission is None or commission.status != "working" or commission.resting:
                return
            if await self._ledger.select(parent_id=commission_id, statuses=_STILL_WORKING):
                return
            live = self._sessions.get(commission_id)
            if live is not None and (not live.ready or not live.idle or live.settling or live.awaiting_prompt or live.closing):
                return
            if commission_id in self._queue or commission_id in self._requeueing or commission_id in self._taking_up:
                return
            members = await self._ledger.select(parent_id=commission_id, oldest_first=True)
            listed = ", ".join(
                texts.TEAM_STALLED_MEMBER.format(name=member.name or member.id[:8], outcome=member.status)
                for member in members
            )
            said = _Found(truncate_to_tokens(texts.TEAM_STALLED.format(members=listed), settings.TEAM_STALL_NOTE_MAX_TOKENS))
            self._stall_reminded.add(commission_id)
            if await self._say_to(commission_id, said, reason="every task agent it started is over"):
                await self._note_tree_event(commission_id, "stall_reminded", {"members": len(members)})
        finally:
            if self._stall_watches.get(commission_id) is asyncio.current_task():
                del self._stall_watches[commission_id]
