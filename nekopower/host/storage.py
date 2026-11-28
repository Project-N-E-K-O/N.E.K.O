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

"""Disk space inspection, retention settings, and cleanup for NekoPower.

Manages what accumulates under ``<data root>/nekopower/``:

* ``wt/<id>/``: git worktrees side sessions work in (tracked in the ledger or
  orphaned directories left behind on disk).
* ``task_agents/projects/<project>/sessions/<id>/work/`` and
  ``task_agents/projects/<project>/work/``: host-owned working directories.
* ``task_agents/projects/<project>/sessions/<id>/{transcript.jsonl,system.json,outputs/}``:
  session records and spilled large tool outputs.
* ``task_agent_space/``, ``dsh/``, ``browser_profile/``: reported for overview
  and opened in the system file manager; never deleted from here.

Safety invariants:

1. No user-chosen directory outside ``<data root>/nekopower/task_agents`` or
   ``<data root>/nekopower/wt`` is ever listed for cleanup or deleted.
2. Symlinks and Windows directory junctions are never traversed during
   measurement or deletion; deleting a link removes the link itself, never
   what it points to.
3. Active sessions, busy projects, active worktrees, and working directories
   still referenced by active projects or live sessions are never deleted.
4. Automatic cleanup during ``SpaceKeeper.tidy()`` uses two-reading clock-jump
   confirmation and checks file modification times so recently edited files
   are spared.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import stat
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nekopower.host.ledger import ACTIVE_STATUSES, TREE_STATES_LEFT
from nekopower.host.paths import (
    SYSTEM_SNAPSHOT_NAME,
    TRANSCRIPT_NAME,
    HostPaths,
    safe_segment,
    worktree_name,
)

logger = logging.getLogger(__name__)

_FILE_VERSION = 1
_OUTPUTS_NAME = "outputs"
_WORK_NAME = "work"
_BUSY_SESSION_STATUSES: frozenset[str] = frozenset({"running", "waiting", "compacting"})
_WORK_DIRS_DUE_KEY = "work_dirs_due_until"
_WORK_DIRS_SEEN_KEY = "work_dirs_due_seen_at"
_WORKTREES_DUE_KEY = "worktrees_due_until"
_WORKTREES_SEEN_KEY = "worktrees_due_seen_at"
_MAX_CLEAN_IDS = 500


# -- filesystem safety helpers ------------------------------------------------


def _fs(path: Path) -> str:
    """Return a path string with the Windows long-path prefix when needed."""
    raw = os.path.abspath(str(path))
    if sys.platform != "win32" or raw.startswith("\\\\?\\"):
        return raw
    if raw.startswith("\\\\"):
        return "\\\\?\\UNC\\" + raw[2:]
    return "\\\\?\\" + raw


def _is_reparse_link(path: Path, st: os.stat_result | None = None) -> bool:
    """Return whether ``path`` is a symlink or a Windows junction/mount point."""
    try:
        info = st if st is not None else os.lstat(_fs(path))
    except OSError:
        return True
    if stat.S_ISLNK(info.st_mode):
        return True
    if sys.platform == "win32":
        tag = getattr(info, "st_reparse_tag", 0)
        symlink_tag = getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C)
        mount_tag = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
        if tag in (symlink_tag, mount_tag):
            return True
    return False


def _classify_path_safety(target: Path, allowed_root: Path) -> str:
    """Classify ``target`` relative to ``allowed_root`` without following links.

    Returns:
    * ``"ok"`` when ``target`` exists, lies strictly inside ``allowed_root``,
      and neither ``allowed_root`` nor any component down to ``target`` is a
      symlink or Windows junction/mount point.
    * ``"missing"`` when ``target`` (or an intermediate directory) does not
      exist and every existing prefix from ``allowed_root`` downward is a
      regular directory (not a link).
    * ``"unsafe"`` when ``target`` escapes ``allowed_root``, equals
      ``allowed_root``, traverses a link/junction, or cannot be inspected.
    """
    abs_root = os.path.abspath(str(allowed_root))
    abs_target = os.path.abspath(str(target))
    norm_root = os.path.normcase(abs_root)
    norm_target = os.path.normcase(abs_target)
    prefix = norm_root if norm_root.endswith(os.sep) else norm_root + os.sep
    if norm_target == norm_root or not norm_target.startswith(prefix):
        return "unsafe"
    rel_str = abs_target[len(prefix) :]
    rel_parts = Path(rel_str).parts
    if not rel_parts or any(part in ("", ".", "..") for part in rel_parts):
        return "unsafe"

    cursor = Path(abs_root)
    try:
        root_st = os.lstat(_fs(cursor))
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unsafe"
    if _is_reparse_link(cursor, root_st) or not stat.S_ISDIR(root_st.st_mode):
        return "unsafe"

    for idx, part in enumerate(rel_parts):
        cursor = cursor / part
        try:
            st = os.lstat(_fs(cursor))
        except FileNotFoundError:
            return "missing"
        except OSError:
            return "unsafe"
        if _is_reparse_link(cursor, st):
            return "unsafe"
        if idx < len(rel_parts) - 1 and not stat.S_ISDIR(st.st_mode):
            return "unsafe"

    try:
        real_root = os.path.normcase(os.path.realpath(abs_root))
        real_parent = os.path.normcase(os.path.realpath(os.path.dirname(abs_target)))
        real_target = os.path.normcase(os.path.realpath(abs_target))
    except OSError:
        return "unsafe"
    if real_target == real_root:
        return "unsafe"
    real_prefix = real_root if real_root.endswith(os.sep) else real_root + os.sep
    if real_parent != real_root and not real_parent.startswith(real_prefix):
        return "unsafe"
    expected = os.path.normcase(os.path.join(real_parent, os.path.basename(abs_target)))
    return "ok" if real_target == expected else "unsafe"


def _is_link_or_escaped(target: Path, allowed_root: Path) -> bool:
    """Return True when ``target`` is missing, a link, traverses a link, or lies outside ``allowed_root``."""
    return _classify_path_safety(target, allowed_root) != "ok"


def _same_path(first: str | Path | None, second: str | Path | None) -> bool:
    if not first or not second:
        return False
    return os.path.normcase(os.path.normpath(str(first))) == os.path.normcase(os.path.normpath(str(second)))


def _path_is_or_inside(child: str | Path | None, parent: str | Path | None) -> bool:
    """Return whether ``child`` equals ``parent`` or lies inside it (normalized, no disk follow)."""
    if not child or not parent:
        return False
    c = os.path.normcase(os.path.normpath(str(child)))
    p = os.path.normcase(os.path.normpath(str(parent)))
    return c == p or c.startswith(p + os.sep)


@dataclass(frozen=True, slots=True)
class DirStats:
    """Measured size and metadata of one directory without following links."""

    exists: bool
    bytes: int
    whole: bool
    file_count: int
    newest_mtime: float | None


def measure_dir(path: Path, deadline: float, *, skip_names: frozenset[str] = frozenset()) -> DirStats:
    """Blocking: measure ``path`` up to ``deadline`` without following symlinks or junctions."""
    try:
        root_stat = os.lstat(_fs(path))
    except FileNotFoundError:
        return DirStats(exists=False, bytes=0, whole=True, file_count=0, newest_mtime=None)
    except OSError:
        return DirStats(exists=False, bytes=0, whole=False, file_count=0, newest_mtime=None)
    if _is_reparse_link(path, root_stat) or not stat.S_ISDIR(root_stat.st_mode):
        return DirStats(exists=False, bytes=0, whole=True, file_count=0, newest_mtime=None)

    total = 0
    files = 0
    newest = float(root_stat.st_mtime)
    whole = True
    stack: list[Path] = [path]
    while stack:
        if time.monotonic() > deadline:
            whole = False
            break
        current = stack.pop()
        try:
            with os.scandir(_fs(current)) as entries:
                for entry in entries:
                    if time.monotonic() > deadline:
                        whole = False
                        break
                    if entry.name in skip_names:
                        continue
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        whole = False
                        continue
                    if st.st_mtime > newest:
                        newest = float(st.st_mtime)
                    child_path = current / entry.name
                    if _is_reparse_link(child_path, st):
                        files += 1
                        continue
                    if stat.S_ISDIR(st.st_mode):
                        stack.append(child_path)
                    else:
                        files += 1
                        total += int(st.st_size)
        except OSError:
            whole = False
    return DirStats(exists=True, bytes=total, whole=whole, file_count=files, newest_mtime=newest)


def newest_mtime_of(
    path: Path, budget_seconds: float = 0.25, *, skip_git: bool = False
) -> tuple[float | None, bool]:
    """Blocking: return ``(newest_mtime, scan_complete)`` for ``path`` within ``budget_seconds``.

    ``scan_complete`` is False whenever the scan timed out or hit unreadable
    entries; callers performing automatic cleanup must refuse to delete when
    ``scan_complete`` is False so recently edited nested files are never lost.
    """
    skip = frozenset({".git"}) if skip_git else frozenset()
    stats = measure_dir(path, time.monotonic() + budget_seconds, skip_names=skip)
    if not stats.exists:
        return None, stats.whole
    return stats.newest_mtime, stats.whole


def _clear_readonly(fs_path: str) -> None:
    with contextlib.suppress(OSError):
        os.chmod(fs_path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)


def remove_dir_safely(target: Path, allowed_root: Path) -> tuple[bool, int]:
    """Blocking: recursively remove ``target`` inside ``allowed_root`` without following links.

    Returns ``(removed, freed_bytes)``. ``removed`` is True when ``target`` no
    longer exists on disk; False when some entry could not be deleted (e.g.
    held open by another process on Windows). Raises ``ValueError`` when
    ``target`` is a link or lies outside ``allowed_root``.
    """
    safety = _classify_path_safety(target, allowed_root)
    if safety == "missing":
        return True, 0
    if safety != "ok":
        raise ValueError("unsafe_path")

    freed = 0
    failed = False

    def _delete_tree(directory: Path) -> None:
        nonlocal freed, failed
        fs_dir = _fs(directory)
        try:
            with os.scandir(fs_dir) as entries:
                items = list(entries)
        except FileNotFoundError:
            return
        except OSError:
            failed = True
            return
        for entry in items:
            child = directory / entry.name
            fs_child = _fs(child)
            try:
                st = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError:
                failed = True
                continue
            is_link = _is_reparse_link(child, st)
            if is_link:
                # Never descend into symlinks or Windows junctions: remove only the link itself.
                try:
                    if stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode):
                        os.rmdir(fs_child)
                    else:
                        os.unlink(fs_child)
                except FileNotFoundError:
                    # Link already disappeared concurrently.
                    pass
                except OSError:
                    _clear_readonly(fs_child)
                    try:
                        if stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode):
                            os.rmdir(fs_child)
                        else:
                            os.unlink(fs_child)
                    except OSError:
                        failed = True
                continue
            if stat.S_ISDIR(st.st_mode):
                _delete_tree(child)
            else:
                size = int(st.st_size)
                try:
                    os.unlink(fs_child)
                    freed += size
                except FileNotFoundError:
                    # File already removed concurrently.
                    pass
                except OSError:
                    _clear_readonly(fs_child)
                    try:
                        os.unlink(fs_child)
                        freed += size
                    except FileNotFoundError:
                        # File disappeared while clearing read-only bit.
                        pass
                    except OSError:
                        failed = True
        try:
            os.rmdir(fs_dir)
        except FileNotFoundError:
            # Directory already removed concurrently.
            pass
        except OSError:
            _clear_readonly(fs_dir)
            try:
                os.rmdir(fs_dir)
            except FileNotFoundError:
                # Directory disappeared while clearing read-only bit.
                pass
            except OSError:
                failed = True

    _delete_tree(target)
    still_there = os.path.lexists(_fs(target))
    return (not still_there and not failed), freed


def _prune_empty_parents(session_dir: Path, projects_root: Path) -> None:
    """Blocking: remove ``session_dir`` (and empty ``sessions/`` parent) if completely empty."""
    for candidate in (session_dir, session_dir.parent):
        try:
            if _is_link_or_escaped(candidate, projects_root):
                return
            os.rmdir(_fs(candidate))
        except OSError:
            return


def _disk_usage_of(path: Path) -> dict[str, int] | None:
    probe = Path(os.path.abspath(str(path)))
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return None
    return {"total": int(usage.total), "used": int(usage.used), "free": int(usage.free)}


def _confirmed_cutoff(
    state: dict[str, Any],
    now: float,
    due: float,
    *,
    until_key: str,
    seen_key: str,
) -> tuple[float | None, bool]:
    """Two-reading clock-jump confirmation matching ``SpaceKeeper._confirmed_line``."""
    from config.nekopower_settings import SPACE_UPKEEP_INTERVAL_SECONDS

    noted, seen_at = state.get(until_key), state.get(seen_key)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (noted, seen_at)):
        return None, True
    if seen_at > now:
        return None, True
    if seen_at > now - SPACE_UPKEEP_INTERVAL_SECONDS:
        return None, False
    return min(float(noted), due), True


# -- retention settings store -------------------------------------------------


def _default_settings() -> dict[str, Any]:
    from config.nekopower_settings import SPACE_WORK_DIR_KEPT_DAYS, SPACE_WORKTREE_KEPT_DAYS

    return {
        "auto_clean_work_dirs": True,
        "work_dir_kept_days": int(SPACE_WORK_DIR_KEPT_DAYS),
        "auto_clean_worktrees": True,
        "worktree_kept_days": int(SPACE_WORKTREE_KEPT_DAYS),
    }


def _days_bounds() -> tuple[int, int]:
    from config.nekopower_settings import STORAGE_RETENTION_MAX_DAYS, STORAGE_RETENTION_MIN_DAYS

    return int(STORAGE_RETENTION_MIN_DAYS), int(STORAGE_RETENTION_MAX_DAYS)


def _valid_days(value: Any) -> bool:
    least, most = _days_bounds()
    return isinstance(value, int) and not isinstance(value, bool) and least <= value <= most


def _note_write_failed(writing: "asyncio.Future[dict[str, Any]]") -> None:
    if not writing.cancelled() and writing.exception() is not None:
        logger.warning("writing the storage settings failed: %s", type(writing.exception()).__name__)


def _safe_unreadable_settings() -> dict[str, Any]:
    """Fail-closed settings used when ``storage.json`` exists but cannot be read."""
    values = _default_settings()
    values["auto_clean_work_dirs"] = False
    values["auto_clean_worktrees"] = False
    return values


class StorageSettingsStore:
    """User-configured automatic cleanup settings kept in ``storage.json``."""

    def __init__(self, path: Path | Callable[[], Path]) -> None:
        self._path_source = path
        self._lock = asyncio.Lock()
        self._kept: dict[str, Any] | None = None

    def _path(self) -> Path:
        source = self._path_source
        return Path(source() if callable(source) else source)

    def _read_disk(self) -> tuple[dict[str, Any], bool]:
        """Blocking: return ``(values, readable)``; ``readable`` is False on read/parse error."""
        found = _default_settings()
        try:
            raw = json.loads(self._path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return found, True
        except (OSError, ValueError) as exc:
            logger.warning(
                "storage settings cannot be read (%s); automatic cleanup disabled until readable",
                type(exc).__name__,
            )
            return _safe_unreadable_settings(), False
        if not isinstance(raw, dict):
            logger.warning("storage settings file does not contain a JSON object; automatic cleanup disabled")
            return _safe_unreadable_settings(), False
        if isinstance(raw.get("auto_clean_work_dirs"), bool):
            found["auto_clean_work_dirs"] = raw["auto_clean_work_dirs"]
        if _valid_days(raw.get("work_dir_kept_days")):
            found["work_dir_kept_days"] = int(raw["work_dir_kept_days"])
        if isinstance(raw.get("auto_clean_worktrees"), bool):
            found["auto_clean_worktrees"] = raw["auto_clean_worktrees"]
        if _valid_days(raw.get("worktree_kept_days")):
            found["worktree_kept_days"] = int(raw["worktree_kept_days"])
        return found, True

    def _write(self, values: dict[str, Any], path: Path) -> None:
        from utils.file_utils import atomic_write_json

        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, {"version": _FILE_VERSION, **values})

    async def get(self) -> dict[str, Any]:
        """Return the active settings dictionary.

        When ``storage.json`` exists but cannot be read, returns fail-closed
        settings with automatic cleanup disabled and does not cache them so
        subsequent calls re-read once the file recovers.
        """
        if self._kept is not None:
            return dict(self._kept)
        async with self._lock:
            if self._kept is not None:
                return dict(self._kept)
            values, readable = await asyncio.to_thread(self._read_disk)
            if readable:
                self._kept = values
            return dict(values)

    async def describe(self) -> dict[str, Any]:
        """Return settings with defaults, bounds, and transcript retention for the UI."""
        from config.nekopower_settings import SPACE_TRANSCRIPT_KEPT_SECONDS

        values = await self.get()
        least, most = _days_bounds()
        return {
            **values,
            "transcript_kept_days": max(1, round(SPACE_TRANSCRIPT_KEPT_SECONDS / 86400.0)),
            "min_days": least,
            "max_days": most,
            "defaults": _default_settings(),
        }

    async def put(
        self,
        *,
        auto_clean_work_dirs: Any = None,
        work_dir_kept_days: Any = None,
        auto_clean_worktrees: Any = None,
        worktree_kept_days: Any = None,
    ) -> dict[str, Any]:
        """Update any provided fields and persist them atomically."""
        if auto_clean_work_dirs is not None and not isinstance(auto_clean_work_dirs, bool):
            raise ValueError("bad_flag")
        if auto_clean_worktrees is not None and not isinstance(auto_clean_worktrees, bool):
            raise ValueError("bad_flag")
        if work_dir_kept_days is not None and not _valid_days(work_dir_kept_days):
            raise ValueError("out_of_range")
        if worktree_kept_days is not None and not _valid_days(worktree_kept_days):
            raise ValueError("out_of_range")
        writing = asyncio.ensure_future(
            self._put(auto_clean_work_dirs, work_dir_kept_days, auto_clean_worktrees, worktree_kept_days)
        )
        writing.add_done_callback(_note_write_failed)
        await asyncio.shield(writing)
        return await self.describe()

    async def _put(
        self,
        auto_clean_work_dirs: bool | None,
        work_dir_kept_days: int | None,
        auto_clean_worktrees: bool | None,
        worktree_kept_days: int | None,
    ) -> dict[str, Any]:
        async with self._lock:
            # Always base saves on current on-disk contents; refuse to overwrite an unreadable file.
            values, readable = await asyncio.to_thread(self._read_disk)
            if not readable:
                raise OSError("storage settings file is unreadable")
            if auto_clean_work_dirs is not None:
                values["auto_clean_work_dirs"] = auto_clean_work_dirs
            if work_dir_kept_days is not None:
                values["work_dir_kept_days"] = int(work_dir_kept_days)
            if auto_clean_worktrees is not None:
                values["auto_clean_worktrees"] = auto_clean_worktrees
            if worktree_kept_days is not None:
                values["worktree_kept_days"] = int(worktree_kept_days)
            await asyncio.to_thread(self._write, values, self._path())
            self._kept = values
            return dict(values)


# -- work dir id encoding -----------------------------------------------------


def parse_work_dir_id(item_id: Any) -> tuple[str, str, str | None]:
    """Parse and validate a work_dir item id into ``(scope, project, session_id)``.

    Accepted forms:
    * ``session:<project>/<session_id>``
    * ``project:<project>``

    Raises ``ValueError`` for anything else or any unsafe path segment.
    """
    if not isinstance(item_id, str) or not item_id:
        raise ValueError("bad_work_dir")
    if item_id.startswith("session:"):
        rest = item_id[len("session:") :]
        if "/" not in rest:
            raise ValueError("bad_work_dir")
        project, session_id = rest.split("/", 1)
        if "/" in session_id:
            raise ValueError("bad_work_dir")
        return "session", safe_segment(project, "project"), safe_segment(session_id, "session id")
    if item_id.startswith("project:"):
        project = item_id[len("project:") :]
        if "/" in project:
            raise ValueError("bad_work_dir")
        return "project", safe_segment(project, "project"), None
    raise ValueError("bad_work_dir")


def format_work_dir_id(scope: str, project: str, session_id: str | None = None) -> str:
    if scope == "session":
        assert session_id is not None
        return f"session:{project}/{session_id}"
    return f"project:{project}"


# -- main storage manager -----------------------------------------------------


class StorageManager:
    """Inspects and cleans disk space owned by the NekoPower host."""

    def __init__(
        self,
        *,
        paths: HostPaths,
        ledger: Any,
        space: Any,
        task_agents: Any,
        project_lock: asyncio.Lock,
        project_busy: Callable[[str], Awaitable[str | None]],
        places: Callable[[], list[dict[str, Any]]],
        space_keeper: Any = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._paths = paths
        self._ledger = ledger
        self._space = space
        self._task_agents = task_agents
        self._project_lock = project_lock
        self._project_busy = project_busy
        self._places = places
        self._space_keeper = space_keeper
        self._clock = clock
        self.settings = StorageSettingsStore(paths.storage_json)

    def attach_space_keeper(self, space_keeper: Any) -> None:
        self._space_keeper = space_keeper

    # -- references & busy checks -----------------------------------------

    async def _read_projects_checked(
        self, *, include_archived: bool
    ) -> tuple[dict[str, Any], set[str] | None, bool]:
        """Read projects while verifying each active project's ``project.json`` is readable.

        ``TaskAgentSpace.list_projects()`` returns stub records with ``workdir=None``
        when an individual ``project.json`` fails to read. Calling ``read_project()``
        on each active project lets callers distinguish a real ``workdir=None`` from
        an unreadable record and fail closed.
        """
        projects_by_name: dict[str, Any] = {}
        active_project_workdirs: set[str] = set()
        if not await asyncio.to_thread(self._paths.space().root.is_dir):
            return projects_by_name, active_project_workdirs, True
        try:
            listed = await self._space.list_projects(include_archived=include_archived)
        except OSError:
            logger.warning("listing projects for storage check failed", exc_info=True)
            return projects_by_name, None, False
        read_project = getattr(self._space, "read_project", None)
        projects_readable = True
        for proj in listed:
            if proj.archive_year is None:
                record = proj
                if callable(read_project):
                    try:
                        checked, ok = await read_project(proj.name)
                    except OSError:
                        projects_readable = False
                        logger.warning("reading project %s for storage check failed", proj.name, exc_info=True)
                        continue
                    if not ok:
                        projects_readable = False
                        logger.warning("project %s record is unreadable; failing closed for storage cleanup", proj.name)
                        continue
                    if checked is None:
                        continue
                    record = checked
                projects_by_name[record.name] = record
                if record.workdir:
                    active_project_workdirs.add(os.path.normcase(os.path.normpath(record.workdir)))
            elif proj.name not in projects_by_name:
                projects_by_name[proj.name] = proj
        return projects_by_name, (active_project_workdirs if projects_readable else None), projects_readable

    async def _ledger_snapshot(
        self,
    ) -> tuple[dict[str, Any], set[str], set[str], set[str], dict[str, Any], list[Any], bool, bool]:
        """Read sessions, live workdirs, active project workdirs, projects, and trees in one pass."""
        sessions_by_id: dict[str, Any] = {}
        active_sessions: set[str] = set()
        live_workdirs: set[str] = set()

        has_ledger = await asyncio.to_thread(self._paths.ledger_db().is_file)
        if has_ledger:
            all_sessions = await self._ledger.find_sessions(limit=10_000, commissions_each=1)
            for row in all_sessions:
                session = row.session
                sessions_by_id[session.id] = row
                if session.status in _BUSY_SESSION_STATUSES or self._task_agents._in_use(session.id):
                    active_sessions.add(session.id)
                    if session.workdir:
                        live_workdirs.add(os.path.normcase(os.path.normpath(session.workdir)))
            under_way = await self._ledger.select(statuses=tuple(ACTIVE_STATUSES), limit=2000)
            for commission in under_way:
                if commission.session_id:
                    active_sessions.add(commission.session_id)
                if commission.workdir:
                    live_workdirs.add(os.path.normcase(os.path.normpath(commission.workdir)))
            trees = await self._ledger.trees()
            for row in trees:
                for backing in (row.repo_root, row.project_dir, row.path, row.workdir):
                    if backing:
                        live_workdirs.add(os.path.normcase(os.path.normpath(backing)))
        else:
            trees = []

        projects_by_name, maybe_proj_workdirs, projects_readable = await self._read_projects_checked(
            include_archived=True
        )
        active_project_workdirs = maybe_proj_workdirs if maybe_proj_workdirs is not None else set()

        return (
            sessions_by_id,
            active_sessions,
            live_workdirs,
            active_project_workdirs,
            projects_by_name,
            trees,
            has_ledger,
            projects_readable,
        )

    async def _all_session_rows(self) -> list[tuple[str, str | None, str, str | None]]:
        """Return ``(id, project, status, workdir)`` for every session in the ledger without row-limit truncation."""
        return await self._ledger.run(
            lambda conn: [
                (
                    str(row["id"]),
                    str(row["project"]) if row["project"] is not None else None,
                    str(row["status"]),
                    str(row["workdir"]) if row["workdir"] is not None else None,
                )
                for row in conn.execute("SELECT id, project, status, workdir FROM sessions").fetchall()
            ]
        )

    async def _active_commission_refs(self) -> list[tuple[str | None, str | None]]:
        """Return ``(session_id, workdir)`` for every active commission in the ledger without truncation."""
        statuses = tuple(ACTIVE_STATUSES)
        placeholders = ",".join("?" for _ in statuses)
        return await self._ledger.run(
            lambda conn: [
                (
                    str(row["session_id"]) if row["session_id"] is not None else None,
                    str(row["workdir"]) if row["workdir"] is not None else None,
                )
                for row in conn.execute(
                    f"SELECT session_id, workdir FROM commissions WHERE status IN ({placeholders})",
                    statuses,
                ).fetchall()
            ]
        )

    async def _busy_workdirs(
        self,
        *,
        exclude_session_id: str | None = None,
        exclude_session_ids: set[str] | frozenset[str] = frozenset(),
    ) -> tuple[set[str], set[str], set[str] | None]:
        """Return ``(active_session_ids, live_workdirs, active_project_workdirs)``.

        When ``exclude_session_id`` or ``exclude_session_ids`` is given, those
        sessions' own in-memory file holds (``hold_files``) are excluded from
        ``_in_use`` so cleanup does not mistake its own deletion lock for an
        active user; their ledger statuses and other sessions sharing the
        directory are still checked.

        ``active_project_workdirs`` is ``None`` if listing active projects
        failed with ``OSError``, so callers depending on project-reference
        checks fail closed rather than assuming no references exist.
        """
        excluded = set(exclude_session_ids)
        if exclude_session_id is not None:
            excluded.add(exclude_session_id)
        active_sessions: set[str] = set()
        live_workdirs: set[str] = set()
        active_project_workdirs: set[str] | None = set()
        if await asyncio.to_thread(self._paths.ledger_db().is_file):
            for sid, _project, status, workdir in await self._all_session_rows():
                in_use = status in _BUSY_SESSION_STATUSES or (
                    sid not in excluded and self._task_agents._in_use(sid)
                )
                if in_use:
                    active_sessions.add(sid)
                    if workdir:
                        live_workdirs.add(os.path.normcase(os.path.normpath(workdir)))
            for comm_sid, comm_workdir in await self._active_commission_refs():
                if comm_sid:
                    active_sessions.add(comm_sid)
                if comm_workdir:
                    live_workdirs.add(os.path.normcase(os.path.normpath(comm_workdir)))
            for row in await self._ledger.trees():
                for backing in (row.repo_root, row.project_dir, row.path, row.workdir):
                    if backing:
                        live_workdirs.add(os.path.normcase(os.path.normpath(backing)))
        _projects, active_project_workdirs, _readable = await self._read_projects_checked(include_archived=False)
        return active_sessions, live_workdirs, active_project_workdirs

    # -- overview ---------------------------------------------------------

    async def overview(self) -> dict[str, Any]:
        """Return a complete disk space inventory for the Disk Space Management Panel."""
        from config.nekopower_settings import (
            SPACE_TRANSCRIPT_KEPT_SECONDS,
            STORAGE_ITEM_BUDGET_SECONDS,
            STORAGE_MAX_ITEMS,
            STORAGE_SCAN_BUDGET_SECONDS,
        )

        now = self._clock()
        policy = await self.settings.describe()
        work_dir_cutoff = now - policy["work_dir_kept_days"] * 86400.0
        worktree_cutoff = now - policy["worktree_kept_days"] * 86400.0
        transcript_cutoff = now - SPACE_TRANSCRIPT_KEPT_SECONDS

        (
            sessions_by_id,
            active_sessions,
            live_workdirs,
            active_project_workdirs,
            projects_by_name,
            trees,
            has_ledger,
            projects_readable,
        ) = await self._ledger_snapshot()
        busy_projects: set[str] = set()
        for proj_name, proj in projects_by_name.items():
            if proj.archive_year is None:
                try:
                    if await self._project_busy(proj_name) is not None:
                        busy_projects.add(proj_name)
                except Exception:  # noqa: BLE001 - treat unreadable as busy
                    busy_projects.add(proj_name)

        # Also gather commission titles for tracked trees.
        tree_meta: dict[str, tuple[str, str]] = {}
        for row in trees:
            title = ""
            session_name = ""
            if row.commission_id and has_ledger:
                commission = await self._ledger.get(row.commission_id)
                if commission is not None:
                    title = commission.title
            if row.session_id and row.session_id in sessions_by_id:
                session_name = sessions_by_id[row.session_id].session.name
            tree_meta[row.id] = (title, session_name)

        # Active worktree ids from in-memory manager state.
        trees_at_work = set(self._task_agents._tree_of.values()) | set(self._task_agents._trees_in_hand)
        still_at_it: set[str] = set()
        for row in trees:
            if row.id in trees_at_work or row.state not in (*TREE_STATES_LEFT, "discarding"):
                still_at_it.add(row.id)
            elif row.commission_id and await self._task_agents._still_at_it_in(row):
                still_at_it.add(row.id)

        # Forks map: source_session_id -> uncleared forks, latest activity, and busy forks.
        uncleared_fork_sources: set[str] = set()
        fork_latest_active: dict[str, float] = {}
        busy_fork_sources: set[str] = set()
        for srow in sessions_by_id.values():
            s = srow.session
            if s.kind == "fork" and s.forked_from and s.status != "cleared":
                uncleared_fork_sources.add(s.forked_from)
                prev = fork_latest_active.get(s.forked_from)
                if prev is None or s.last_active_at > prev:
                    fork_latest_active[s.forked_from] = s.last_active_at
                if s.id in active_sessions:
                    busy_fork_sources.add(s.forked_from)

        root = self._paths.root()
        places_spec = self._places()

        def scan() -> dict[str, Any]:
            deadline = time.monotonic() + STORAGE_SCAN_BUDGET_SECONDS

            def item_deadline() -> float:
                return min(deadline, time.monotonic() + STORAGE_ITEM_BUDGET_SECONDS)

            # 1. Scan worktrees (tracked + orphan dirs in wt/)
            wt_root = self._paths.worktrees_dir()
            wt_root_exists = wt_root.is_dir() and not _is_reparse_link(wt_root)
            wt_items: list[dict[str, Any]] = []
            wt_total_bytes = 0
            wt_whole = True
            wt_reclaimable = 0
            seen_wt_ids: set[str] = set()

            for row in trees:
                seen_wt_ids.add(row.id)
                wt_path = Path(row.path)
                stats = measure_dir(wt_path, item_deadline())
                wt_whole = wt_whole and stats.whole
                if stats.exists:
                    wt_total_bytes += stats.bytes
                in_use = row.id in still_at_it or row.state not in (*TREE_STATES_LEFT, "discarding")
                can_clean = not in_use
                can_land = row.state in TREE_STATES_LEFT and row.reason != "skipped" and stats.exists and not in_use
                effective_time = max(
                    (t for t in (row.updated_at, stats.newest_mtime) if t is not None),
                    default=row.updated_at,
                )
                stale = can_clean and (
                    row.state == "discarding"
                    or (stats.whole and (not stats.exists or effective_time <= worktree_cutoff))
                )
                if can_clean and stats.exists:
                    wt_reclaimable += stats.bytes
                title, session_name = tree_meta.get(row.id, ("", ""))
                wt_items.append(
                    {
                        "id": row.id,
                        "kind": "tracked",
                        "project": row.project,
                        "title": title,
                        "session_name": session_name,
                        "session_id": row.session_id,
                        "commission_id": row.commission_id,
                        "state": row.state,
                        "reason": row.reason,
                        "conflicts": list(row.conflicts),
                        "path": row.path,
                        "workdir": row.workdir,
                        "exists": stats.exists,
                        "bytes": stats.bytes if stats.exists else 0,
                        "bytes_whole": stats.whole,
                        "created_at": row.created_at,
                        "updated_at": effective_time,
                        "can_clean": can_clean,
                        "can_land": can_land,
                        "stale": stale,
                    }
                )

            if wt_root_exists:
                try:
                    with os.scandir(_fs(wt_root)) as entries:
                        for entry in entries:
                            name = entry.name
                            if name in seen_wt_ids:
                                continue
                            child = wt_root / name
                            if _is_reparse_link(child):
                                continue
                            try:
                                is_dir = entry.is_dir(follow_symlinks=False)
                            except OSError:
                                continue
                            if not is_dir:
                                continue
                            try:
                                worktree_name(name)
                            except ValueError:
                                continue
                            stats = measure_dir(child, item_deadline())
                            wt_whole = wt_whole and stats.whole
                            wt_total_bytes += stats.bytes
                            busy_orphan = name in self._task_agents._trees_mine or name in self._task_agents._trees_in_hand
                            can_clean = not busy_orphan
                            if can_clean:
                                wt_reclaimable += stats.bytes
                            wt_items.append(
                                {
                                    "id": name,
                                    "kind": "orphan",
                                    "project": None,
                                    "title": "",
                                    "session_name": "",
                                    "session_id": None,
                                    "commission_id": None,
                                    "state": "orphan",
                                    "reason": "",
                                    "conflicts": [],
                                    "path": str(child),
                                    "workdir": str(child),
                                    "exists": True,
                                    "bytes": stats.bytes,
                                    "bytes_whole": stats.whole,
                                    "created_at": None,
                                    "updated_at": stats.newest_mtime,
                                    "can_clean": can_clean,
                                    "can_land": False,
                                    "stale": can_clean and stats.whole,
                                }
                            )
                except OSError:
                    wt_whole = False

            # 2. Scan task_agents/projects/* for work dirs + transcripts/outputs
            ta_root = root / "task_agents"
            ta_exists = ta_root.is_dir() and not _is_reparse_link(ta_root)
            projects_root = ta_root / "projects"
            work_dir_items: list[dict[str, Any]] = []
            work_dirs_bytes = 0
            work_dirs_whole = True
            work_dirs_reclaimable = 0

            transcript_bytes = 0
            transcript_whole = True
            transcript_sessions = 0
            expired_transcript_count = 0
            expired_transcript_bytes = 0
            outputs_count = 0
            outputs_bytes = 0
            transcript_reclaimable = 0
            ta_other_bytes = 0
            ta_other_whole = True

            def _measure_unclassified(directory: Path, skip: frozenset[str]) -> None:
                nonlocal ta_other_bytes, ta_other_whole
                if time.monotonic() > deadline:
                    ta_other_whole = False
                    return
                try:
                    with os.scandir(_fs(directory)) as extra_entries:
                        for extra in extra_entries:
                            if extra.name in skip:
                                continue
                            if time.monotonic() > deadline:
                                ta_other_whole = False
                                break
                            child = directory / extra.name
                            try:
                                st = extra.stat(follow_symlinks=False)
                            except OSError:
                                ta_other_whole = False
                                continue
                            if _is_reparse_link(child, st):
                                continue
                            if stat.S_ISDIR(st.st_mode):
                                extra_stats = measure_dir(child, item_deadline())
                                ta_other_bytes += extra_stats.bytes
                                ta_other_whole = ta_other_whole and extra_stats.whole
                            elif stat.S_ISREG(st.st_mode):
                                ta_other_bytes += int(st.st_size)
                except OSError:
                    ta_other_whole = False

            if ta_exists:
                _measure_unclassified(ta_root, frozenset({"projects"}))

            if ta_exists and projects_root.is_dir() and not _is_reparse_link(projects_root):
                try:
                    with os.scandir(_fs(projects_root)) as project_entries:
                        proj_dirs = list(project_entries)
                except OSError:
                    proj_dirs = []
                    work_dirs_whole = False
                    transcript_whole = False
                    ta_other_whole = False

                for proj_entry in proj_dirs:
                    proj_name = proj_entry.name
                    proj_path = projects_root / proj_name
                    if _is_reparse_link(proj_path):
                        continue
                    try:
                        st_proj = proj_entry.stat(follow_symlinks=False)
                    except OSError:
                        ta_other_whole = False
                        continue
                    if not stat.S_ISDIR(st_proj.st_mode):
                        if stat.S_ISREG(st_proj.st_mode):
                            ta_other_bytes += int(st_proj.st_size)
                        continue
                    try:
                        safe_segment(proj_name, "project")
                    except ValueError:
                        extra_stats = measure_dir(proj_path, item_deadline())
                        ta_other_bytes += extra_stats.bytes
                        ta_other_whole = ta_other_whole and extra_stats.whole
                        continue

                    _measure_unclassified(proj_path, frozenset({_WORK_NAME, "sessions"}))

                    proj_record = projects_by_name.get(proj_name)
                    # Project-level default work dir: task_agents/projects/<project>/work
                    proj_work = proj_path / _WORK_NAME
                    if proj_work.is_dir() and not _is_reparse_link(proj_work):
                        stats = measure_dir(proj_work, item_deadline())
                        work_dirs_whole = work_dirs_whole and stats.whole
                        work_dirs_bytes += stats.bytes
                        norm_pw = os.path.normcase(os.path.normpath(str(proj_work)))
                        in_use = proj_name in busy_projects or any(
                            _path_is_or_inside(wd, norm_pw) for wd in live_workdirs
                        )
                        is_archived = proj_record is not None and proj_record.archive_year is not None
                        status_label = "project_archived" if is_archived else "project_active"
                        referenced_proj = not is_archived and (
                            not projects_readable
                            or any(_path_is_or_inside(pwd, norm_pw) for pwd in active_project_workdirs)
                        )
                        can_clean = not in_use and not referenced_proj and projects_readable
                        if can_clean:
                            work_dirs_reclaimable += stats.bytes
                        last_at = max(
                            (
                                t
                                for t in (
                                    proj_record.last_commission_at if proj_record else None,
                                    stats.newest_mtime,
                                )
                                if t
                            ),
                            default=stats.newest_mtime,
                        )
                        work_dir_items.append(
                            {
                                "id": format_work_dir_id("project", proj_name),
                                "scope": "project",
                                "project": proj_name,
                                "session_id": None,
                                "session_name": proj_name,
                                "title": proj_record.description if proj_record else "",
                                "session_status": status_label,
                                "path": str(proj_work),
                                "bytes": stats.bytes,
                                "bytes_whole": stats.whole,
                                "file_count": stats.file_count,
                                "empty": stats.whole and stats.file_count == 0,
                                "last_active_at": last_at,
                                "in_use": in_use,
                                "can_clean": can_clean,
                                "stale": False,
                                "referenced_by_project": referenced_proj,
                            }
                        )

                    # Session directories: task_agents/projects/<project>/sessions/<session_id>/
                    sessions_dir = proj_path / "sessions"
                    if not sessions_dir.is_dir() or _is_reparse_link(sessions_dir):
                        continue
                    try:
                        with os.scandir(_fs(sessions_dir)) as sess_entries:
                            sess_list = list(sess_entries)
                    except OSError:
                        work_dirs_whole = False
                        transcript_whole = False
                        ta_other_whole = False
                        continue

                    for sess_entry in sess_list:
                        sess_id = sess_entry.name
                        sess_path = sessions_dir / sess_id
                        if _is_reparse_link(sess_path):
                            continue
                        try:
                            st_sess = sess_entry.stat(follow_symlinks=False)
                        except OSError:
                            ta_other_whole = False
                            continue
                        if not stat.S_ISDIR(st_sess.st_mode):
                            if stat.S_ISREG(st_sess.st_mode):
                                ta_other_bytes += int(st_sess.st_size)
                            continue
                        try:
                            safe_segment(sess_id, "session id")
                        except ValueError:
                            extra_stats = measure_dir(sess_path, item_deadline())
                            ta_other_bytes += extra_stats.bytes
                            ta_other_whole = ta_other_whole and extra_stats.whole
                            continue

                        srow = sessions_by_id.get(sess_id)
                        session = srow.session if srow is not None else None
                        latest_comm = srow.commissions[0] if (srow is not None and srow.commissions) else None
                        session_active = sess_id in active_sessions

                        # Measure session work/ directory if present
                        work_path = sess_path / _WORK_NAME
                        if work_path.is_dir() and not _is_reparse_link(work_path):
                            stats = measure_dir(work_path, item_deadline())
                            work_dirs_whole = work_dirs_whole and stats.whole
                            work_dirs_bytes += stats.bytes
                            norm_wp = os.path.normcase(os.path.normpath(str(work_path)))
                            referenced_live = any(_path_is_or_inside(wd, norm_wp) for wd in live_workdirs)
                            referenced_proj = not projects_readable or any(
                                _path_is_or_inside(pwd, norm_wp) for pwd in active_project_workdirs
                            )
                            in_use = session_active or referenced_live or sess_id in busy_fork_sources
                            can_clean = not in_use and not referenced_proj and projects_readable
                            last_at = max(
                                (
                                    t
                                    for t in (
                                        session.last_active_at if session else None,
                                        fork_latest_active.get(sess_id),
                                        stats.newest_mtime,
                                    )
                                    if t
                                ),
                                default=stats.newest_mtime,
                            )
                            is_empty = stats.whole and stats.file_count == 0
                            stale = (
                                can_clean
                                and not referenced_proj
                                and stats.whole
                                and (
                                    is_empty
                                    or session is None
                                    or (last_at is not None and last_at <= work_dir_cutoff)
                                )
                            )
                            if can_clean:
                                work_dirs_reclaimable += stats.bytes
                            work_dir_items.append(
                                {
                                    "id": format_work_dir_id("session", proj_name, sess_id),
                                    "scope": "session",
                                    "project": proj_name,
                                    "session_id": sess_id,
                                    "session_name": session.name if session else "",
                                    "title": (
                                        (session.title if session and session.title else "")
                                        or (latest_comm.title if latest_comm else "")
                                    ),
                                    "session_status": session.status if session else "orphan",
                                    "path": str(work_path),
                                    "bytes": stats.bytes,
                                    "bytes_whole": stats.whole,
                                    "file_count": stats.file_count,
                                    "empty": is_empty,
                                    "last_active_at": last_at,
                                    "in_use": in_use,
                                    "can_clean": can_clean,
                                    "stale": stale,
                                    "referenced_by_project": referenced_proj,
                                }
                            )

                        # Measure transcript.jsonl, system.json, and outputs/
                        sess_record_bytes = 0
                        sess_outputs_bytes = 0
                        has_record = False
                        has_outputs = False
                        for fname in (TRANSCRIPT_NAME, SYSTEM_SNAPSHOT_NAME):
                            fpath = sess_path / fname
                            try:
                                fst = os.lstat(_fs(fpath))
                                if stat.S_ISREG(fst.st_mode):
                                    sess_record_bytes += int(fst.st_size)
                                    has_record = True
                            except OSError:
                                # Missing or unreadable optional record file is skipped.
                                pass
                        out_dir = sess_path / _OUTPUTS_NAME
                        if out_dir.is_dir() and not _is_reparse_link(out_dir):
                            ostats = measure_dir(out_dir, item_deadline())
                            transcript_whole = transcript_whole and ostats.whole
                            sess_outputs_bytes = ostats.bytes
                            has_outputs = True

                        # Account for other session files (e.g. dsh-session.json) without double-counting.
                        _measure_unclassified(
                            sess_path,
                            frozenset({_WORK_NAME, _OUTPUTS_NAME, TRANSCRIPT_NAME, SYSTEM_SNAPSHOT_NAME}),
                        )

                        combined = sess_record_bytes + sess_outputs_bytes
                        if has_record or has_outputs:
                            transcript_sessions += 1
                            transcript_bytes += combined
                            is_expired = (
                                not session_active
                                and session is not None
                                and session.status != "cleared"
                                and session.last_active_at <= transcript_cutoff
                            )
                            is_cleanable_outputs = (
                                has_outputs
                                and not session_active
                                and sess_id not in uncleared_fork_sources
                            )
                            if is_expired:
                                expired_transcript_count += 1
                                expired_transcript_bytes += combined
                            if is_cleanable_outputs:
                                outputs_count += 1
                                outputs_bytes += sess_outputs_bytes
                            if is_expired:
                                transcript_reclaimable += combined
                            elif is_cleanable_outputs:
                                transcript_reclaimable += sess_outputs_bytes

            # 3. Measure remaining places (space, dsh, browser_profile) + root files
            place_measurements: dict[str, tuple[bool, int | None, bool]] = {}
            ta_total = work_dirs_bytes + transcript_bytes + ta_other_bytes
            place_measurements["task_agents"] = (
                ta_exists,
                ta_total if ta_exists else None,
                work_dirs_whole and transcript_whole and ta_other_whole,
            )
            place_measurements["worktrees"] = (wt_root_exists, wt_total_bytes if wt_root_exists else None, wt_whole)

            other_place_bytes: dict[str, tuple[bool, int, bool]] = {}
            for place in places_spec:
                key = place["key"]
                if key in place_measurements:
                    continue
                p = Path(place["path"])
                stats = measure_dir(p, deadline)
                place_measurements[key] = (stats.exists, stats.bytes if stats.exists else None, stats.whole)
                other_place_bytes[key] = (stats.exists, stats.bytes, stats.whole)

            # Top-level files in <root> (ledger.db, json configs)
            root_other_bytes = 0
            root_other_whole = True
            if root.is_dir() and not _is_reparse_link(root):
                try:
                    with os.scandir(_fs(root)) as entries:
                        for entry in entries:
                            try:
                                st = entry.stat(follow_symlinks=False)
                                if stat.S_ISREG(st.st_mode):
                                    root_other_bytes += int(st.st_size)
                            except OSError:
                                root_other_whole = False
                                continue
                except OSError:
                    root_other_whole = False

            # Sort items: cleanable first so protected/in-use entries cannot hide
            # smaller cleanable items behind STORAGE_MAX_ITEMS, then largest, then oldest.
            wt_items.sort(
                key=lambda item: (not item["can_clean"], -(item["bytes"] or 0), item["updated_at"] or 0.0, item["id"])
            )
            work_dir_items.sort(
                key=lambda item: (
                    not item["can_clean"],
                    -(item["bytes"] or 0),
                    item["last_active_at"] or 0.0,
                    item["id"],
                )
            )

            wt_more = max(0, len(wt_items) - STORAGE_MAX_ITEMS)
            wd_more = max(0, len(work_dir_items) - STORAGE_MAX_ITEMS)

            places_out = [
                {
                    **place,
                    "exists": place_measurements[place["key"]][0],
                    "bytes": place_measurements[place["key"]][1],
                    "bytes_whole": place_measurements[place["key"]][2],
                }
                for place in places_spec
            ]

            space_exists, space_bytes, space_whole = other_place_bytes.get("space", (False, 0, True))
            dsh_exists, dsh_bytes, dsh_whole = other_place_bytes.get("dsh", (False, 0, True))
            browser_exists, browser_bytes, browser_whole = other_place_bytes.get("browser_profile", (False, 0, True))

            categories = [
                {
                    "key": "worktrees",
                    "bytes": wt_total_bytes,
                    "bytes_whole": wt_whole,
                    "count": len(wt_items),
                    "reclaimable_bytes": wt_reclaimable,
                    "cleanable": True,
                },
                {
                    "key": "work_dirs",
                    "bytes": work_dirs_bytes,
                    "bytes_whole": work_dirs_whole,
                    "count": len(work_dir_items),
                    "reclaimable_bytes": work_dirs_reclaimable,
                    "cleanable": True,
                },
                {
                    "key": "transcripts",
                    "bytes": transcript_bytes,
                    "bytes_whole": transcript_whole,
                    "count": transcript_sessions,
                    "reclaimable_bytes": transcript_reclaimable,
                    "cleanable": True,
                },
                {
                    "key": "space",
                    "bytes": space_bytes if space_exists else 0,
                    "bytes_whole": space_whole,
                    "count": 0,
                    "reclaimable_bytes": 0,
                    "cleanable": False,
                },
                {
                    "key": "dsh",
                    "bytes": dsh_bytes if dsh_exists else 0,
                    "bytes_whole": dsh_whole,
                    "count": 0,
                    "reclaimable_bytes": 0,
                    "cleanable": False,
                },
                {
                    "key": "browser_profile",
                    "bytes": browser_bytes if browser_exists else 0,
                    "bytes_whole": browser_whole,
                    "count": 0,
                    "reclaimable_bytes": 0,
                    "cleanable": False,
                },
                {
                    "key": "other",
                    "bytes": root_other_bytes + ta_other_bytes,
                    "bytes_whole": root_other_whole and ta_other_whole,
                    "count": 0,
                    "reclaimable_bytes": 0,
                    "cleanable": False,
                },
            ]
            total_bytes = sum(cat["bytes"] for cat in categories)
            total_whole = all(cat["bytes_whole"] for cat in categories)
            reclaimable_bytes = wt_reclaimable + work_dirs_reclaimable + transcript_reclaimable

            return {
                "disk": _disk_usage_of(root),
                "total_bytes": total_bytes,
                "total_whole": total_whole,
                "reclaimable_bytes": reclaimable_bytes,
                "categories": categories,
                "places": places_out,
                "worktrees": wt_items[:STORAGE_MAX_ITEMS],
                "worktrees_more": wt_more,
                "work_dirs": work_dir_items[:STORAGE_MAX_ITEMS],
                "work_dirs_more": wd_more,
                "transcripts": {
                    "bytes": transcript_bytes,
                    "bytes_whole": transcript_whole,
                    "sessions": transcript_sessions,
                    "expired_count": expired_transcript_count,
                    "expired_bytes": expired_transcript_bytes,
                    "outputs_count": outputs_count,
                    "outputs_bytes": outputs_bytes,
                },
                "settings": policy,
            }

        return await asyncio.to_thread(scan)

    # -- open path resolution (strictly by validated id, never raw path) --

    async def resolve_open_path(self, kind: str, item_id: str) -> Path:
        """Resolve an item id to a directory path that may be opened in the file manager."""
        if kind == "place":
            path = next((Path(place["path"]) for place in self._places() if place["key"] == item_id), None)
            if path is None:
                raise ValueError("bad_place")
            if not await asyncio.to_thread(path.is_dir):
                raise FileNotFoundError("place_missing")
            return path
        if kind == "work_dir":
            scope, project, session_id = parse_work_dir_id(item_id)
            root = self._paths.root() / "task_agents"
            target = (
                self._paths.work_dir(session_id, project)
                if scope == "session" and session_id is not None
                else self._paths.project_work_dir(project)
            )
            safety = await asyncio.to_thread(_classify_path_safety, target, root)
            if safety == "missing":
                raise FileNotFoundError("place_missing")
            if safety != "ok":
                raise ValueError("bad_work_dir")
            if not await asyncio.to_thread(target.is_dir):
                raise FileNotFoundError("place_missing")
            return target
        if kind == "worktree":
            name = worktree_name(item_id)
            if await asyncio.to_thread(self._paths.ledger_db().is_file):
                row = await self._ledger.get_tree(name)
                if row is not None:
                    target = Path(row.workdir or row.path)
                    if not await asyncio.to_thread(target.is_dir):
                        raise FileNotFoundError("place_missing")
                    return target
            target = self._paths.worktree_dir(name)
            safety = await asyncio.to_thread(_classify_path_safety, target, self._paths.worktrees_dir())
            if safety == "missing":
                raise FileNotFoundError("place_missing")
            if safety != "ok":
                raise ValueError("no_worktree")
            if not await asyncio.to_thread(target.is_dir):
                raise FileNotFoundError("place_missing")
            return target
        raise ValueError("bad_kind")

    # -- manual cleanup: working directories ------------------------------

    async def clean_work_dirs(self, ids: list[str]) -> dict[str, Any]:
        """Clean the specified host-owned working directories, refusing any that are in use or project-referenced."""
        if not isinstance(ids, list) or not ids or len(ids) > _MAX_CLEAN_IDS:
            raise ValueError("bad_ids")
        unique_ids = list(dict.fromkeys(ids))
        parsed = [(item_id, parse_work_dir_id(item_id)) for item_id in unique_ids]

        cleaned: list[str] = []
        failed: list[dict[str, str]] = []
        freed_total = 0

        for item_id, (scope, project, session_id) in parsed:
            outcome, freed = await self._clean_one_work_dir(
                scope, project, session_id, skip_project_referenced=True
            )
            freed_total += freed
            if outcome == "cleaned":
                cleaned.append(item_id)
            else:
                failed.append({"id": item_id, "code": outcome})
        return {"cleaned": cleaned, "freed_bytes": freed_total, "failed": failed}

    async def _clean_one_work_dir(
        self,
        scope: str,
        project: str,
        session_id: str | None,
        *,
        until: float | None = None,
        skip_project_referenced: bool = False,
    ) -> tuple[str, int]:
        """Remove one validated host-owned ``work/`` directory.

        Returns ``(outcome, freed_bytes)`` where ``outcome`` is ``cleaned``,
        ``not_found``, ``in_use``, ``recent``, ``scan_incomplete``,
        ``not_removed``, or ``unsafe_path``.
        """
        from utils.asyncio_executor import to_thread_through_cancellation

        ta_root = self._paths.root() / "task_agents"
        projects_root = ta_root / "projects"

        if scope == "project":
            target = self._paths.project_work_dir(project)
            norm_target = os.path.normcase(os.path.normpath(str(target)))
            families = getattr(self._task_agents, "families", None)
            placing_lock = getattr(families, "placing", None)
            placing_cm = placing_lock if placing_lock is not None else contextlib.nullcontext()
            # Acquire _project_lock before families.placing (matching service.open_session / derive_leader)
            # and hold all existing sessions of the project so no commission can start into target mid-delete.
            async with self._project_lock, placing_cm:
                if await self._project_busy(project) is not None:
                    return "in_use", 0
                held_sids: list[str] = []
                try:
                    if await asyncio.to_thread(self._paths.ledger_db().is_file):
                        for sid, sess_proj, _status, sess_workdir in await self._all_session_rows():
                            if sess_proj == project or (
                                sess_workdir and _path_is_or_inside(sess_workdir, norm_target)
                            ):
                                if not self._task_agents.hold_files(sid, drop_idle_timer=False):
                                    return "in_use", 0
                                held_sids.append(sid)
                    if await self._project_busy(project) is not None:
                        return "in_use", 0
                    _active_ids, live_workdirs, active_project_workdirs = await self._busy_workdirs(
                        exclude_session_ids=frozenset(held_sids)
                    )
                    if any(_path_is_or_inside(wd, norm_target) for wd in live_workdirs):
                        return "in_use", 0
                    if skip_project_referenced and (
                        active_project_workdirs is None
                        or any(_path_is_or_inside(pwd, norm_target) for pwd in active_project_workdirs)
                    ):
                        return "in_use", 0
                    safety = await asyncio.to_thread(_classify_path_safety, target, ta_root)
                    if safety == "missing":
                        return "not_found", 0
                    if safety != "ok":
                        return "unsafe_path", 0
                    try:
                        removed, freed = await to_thread_through_cancellation(remove_dir_safely, target, ta_root)
                    except ValueError:
                        return "unsafe_path", 0
                    return ("cleaned" if removed else "not_removed"), freed
                finally:
                    for sid in held_sids:
                        self._task_agents.release_files(sid)

        assert session_id is not None
        target = self._paths.work_dir(session_id, project)
        norm_target = os.path.normcase(os.path.normpath(str(target)))
        held_sids: list[str] = []
        if not self._task_agents.hold_files(session_id, drop_idle_timer=False):
            return "in_use", 0
        held_sids.append(session_id)
        try:
            has_ledger = await asyncio.to_thread(self._paths.ledger_db().is_file)
            if has_ledger:
                # Hold every fork of this session and every other session whose workdir
                # is inside target so none can start concurrently while target is deleted.
                extra_sids: set[str] = set()
                for copy in await self._ledger.forks_of(session_id):
                    extra_sids.add(copy.id)
                for sid, _sess_proj, _status, sess_workdir in await self._all_session_rows():
                    if sid != session_id and sess_workdir and _path_is_or_inside(sess_workdir, norm_target):
                        extra_sids.add(sid)
                for sid in sorted(extra_sids - {session_id}):
                    if not self._task_agents.hold_files(sid, drop_idle_timer=False):
                        return "in_use", 0
                    held_sids.append(sid)

                session = await self._ledger.get_session(session_id)
                if session is not None:
                    if session.status in _BUSY_SESSION_STATUSES:
                        return "in_use", 0
                    if await self._ledger.select(session_id=session_id, statuses=ACTIVE_STATUSES, limit=1):
                        return "in_use", 0
                    if until is not None and session.last_active_at > until:
                        return "recent", 0
                    for copy in await self._ledger.forks_of(session_id):
                        if copy.status in _BUSY_SESSION_STATUSES:
                            return "in_use", 0
                        if await self._ledger.select(session_id=copy.id, statuses=ACTIVE_STATUSES, limit=1):
                            return "in_use", 0
                        if until is not None and copy.status != "cleared" and copy.last_active_at > until:
                            return "recent", 0
            _active_ids, live_workdirs, active_project_workdirs = await self._busy_workdirs(
                exclude_session_ids=frozenset(held_sids)
            )
            if any(_path_is_or_inside(wd, norm_target) for wd in live_workdirs):
                return "in_use", 0
            if skip_project_referenced and (
                active_project_workdirs is None
                or any(_path_is_or_inside(pwd, norm_target) for pwd in active_project_workdirs)
            ):
                return "in_use", 0
            safety = await asyncio.to_thread(_classify_path_safety, target, ta_root)
            if safety == "missing":
                return "not_found", 0
            if safety != "ok":
                return "unsafe_path", 0
            if until is not None:
                mtime, whole = await asyncio.to_thread(newest_mtime_of, target, 0.25)
                if not whole:
                    return "scan_incomplete", 0
                if mtime is not None and mtime > until:
                    return "recent", 0
            try:
                removed, freed = await to_thread_through_cancellation(remove_dir_safely, target, ta_root)
            except ValueError:
                return "unsafe_path", 0
            if removed:
                await asyncio.to_thread(_prune_empty_parents, target.parent, projects_root)
                return "cleaned", freed
            return "not_removed", freed
        finally:
            for sid in held_sids:
                self._task_agents.release_files(sid)

    # -- manual cleanup: worktrees ----------------------------------------

    async def clean_worktrees(self, ids: list[str]) -> dict[str, Any]:
        """Discard tracked left-over worktrees, retry ``discarding`` ones, or remove orphans."""
        if not isinstance(ids, list) or not ids or len(ids) > _MAX_CLEAN_IDS:
            raise ValueError("bad_ids")
        unique_ids = list(dict.fromkeys(ids))
        validated = [worktree_name(item_id) for item_id in unique_ids]

        cleaned: list[str] = []
        deferred: list[str] = []
        failed: list[dict[str, str]] = []
        freed_total = 0

        for tree_id in validated:
            row = await self._ledger.get_tree(tree_id) if await asyncio.to_thread(self._paths.ledger_db().is_file) else None
            path_to_measure = Path(row.path) if row is not None else self._paths.worktree_dir(tree_id)
            pre = await asyncio.to_thread(measure_dir, path_to_measure, time.monotonic() + 0.25)
            est_bytes = pre.bytes if pre.exists else 0

            if row is not None:
                if row.state == "discarding":
                    try:
                        removed = await self._task_agents.retry_discarded_tree(tree_id)
                    except LookupError:
                        cleaned.append(tree_id)
                        continue
                    except ValueError as exc:
                        failed.append({"id": tree_id, "code": str(exc)})
                        continue
                    if removed:
                        cleaned.append(tree_id)
                        freed_total += est_bytes
                    else:
                        deferred.append(tree_id)
                    continue
                try:
                    outcome = await self._task_agents.discard_tree(tree_id)
                except LookupError:
                    failed.append({"id": tree_id, "code": "no_worktree"})
                    continue
                except ValueError as exc:
                    failed.append({"id": tree_id, "code": str(exc)})
                    continue
                if outcome.get("removed"):
                    cleaned.append(tree_id)
                    freed_total += est_bytes
                else:
                    deferred.append(tree_id)
            else:
                try:
                    outcome = await self._task_agents.remove_orphan_worktree(tree_id)
                except ValueError as exc:
                    failed.append({"id": tree_id, "code": str(exc)})
                    continue
                if outcome.get("removed"):
                    cleaned.append(tree_id)
                    freed_total += int(outcome.get("freed_bytes") or est_bytes)
                else:
                    deferred.append(tree_id)

        return {"cleaned": cleaned, "deferred": deferred, "freed_bytes": freed_total, "failed": failed}

    # -- manual cleanup: transcripts & outputs ----------------------------

    async def clean_transcripts(self, mode: str = "expired") -> dict[str, Any]:
        """Clean expired session transcripts (``expired``) or inactive tool output caches (``outputs``)."""
        if mode not in ("expired", "outputs"):
            raise ValueError("bad_mode")
        if self._space_keeper is None:
            return {"cleaned": 0, "freed_bytes": 0, "skipped": 0}
        if mode == "expired":
            return await self._space_keeper.clean_expired_sessions_now()
        return await self._space_keeper.clean_inactive_outputs_now()

    # -- automatic cleanup (called from SpaceKeeper.tidy) -----------------

    async def reap_work_dirs(self, now: float, state: dict[str, Any]) -> None:
        """Automatically remove old idle session ``work/`` directories during upkeep."""
        policy = await self.settings.get()
        if not policy["auto_clean_work_dirs"]:
            return
        due = now - policy["work_dir_kept_days"] * 86400.0
        until, note = _confirmed_cutoff(
            state, now, due, until_key=_WORK_DIRS_DUE_KEY, seen_key=_WORK_DIRS_SEEN_KEY
        )
        if note:
            await self._space.write_state(**{_WORK_DIRS_DUE_KEY: due, _WORK_DIRS_SEEN_KEY: now})
        if until is None:
            return

        projects_root = self._paths.root() / "task_agents" / "projects"
        candidates = await asyncio.to_thread(self._find_session_work_dirs, projects_root)
        if not candidates:
            return
        removed = 0
        for project, session_id in candidates:
            if not (await self.settings.get())["auto_clean_work_dirs"]:
                break
            outcome, _freed = await self._clean_one_work_dir(
                "session", project, session_id, until=until, skip_project_referenced=True
            )
            if outcome == "cleaned":
                removed += 1
        if removed:
            logger.info("task agent storage: automatically cleaned %d expired working director(y/ies)", removed)

    @staticmethod
    def _find_session_work_dirs(projects_root: Path) -> list[tuple[str, str]]:
        """Blocking: list ``(project, session_id)`` pairs whose ``work/`` directory exists."""
        if not projects_root.is_dir() or _is_reparse_link(projects_root):
            return []
        found: list[tuple[str, str]] = []
        try:
            with os.scandir(_fs(projects_root)) as proj_entries:
                for proj_entry in proj_entries:
                    proj_name = proj_entry.name
                    proj_path = projects_root / proj_name
                    if _is_reparse_link(proj_path):
                        continue
                    try:
                        if not proj_entry.is_dir(follow_symlinks=False):
                            continue
                        safe_segment(proj_name, "project")
                    except (OSError, ValueError):
                        continue
                    sessions_dir = proj_path / "sessions"
                    if not sessions_dir.is_dir() or _is_reparse_link(sessions_dir):
                        continue
                    try:
                        with os.scandir(_fs(sessions_dir)) as sess_entries:
                            for sess_entry in sess_entries:
                                sess_id = sess_entry.name
                                sess_path = sessions_dir / sess_id
                                if _is_reparse_link(sess_path):
                                    continue
                                try:
                                    if not sess_entry.is_dir(follow_symlinks=False):
                                        continue
                                    safe_segment(sess_id, "session id")
                                except (OSError, ValueError):
                                    continue
                                work_path = sess_path / _WORK_NAME
                                if work_path.is_dir() and not _is_reparse_link(work_path):
                                    found.append((proj_name, sess_id))
                    except OSError:
                        continue
        except OSError:
            return found
        return found

    async def reap_worktrees(self, now: float, state: dict[str, Any]) -> None:
        """Retry ``discarding`` worktrees and automatically expire old left-over worktrees."""
        # Always retry worktrees the user already explicitly discarded.
        retried = await self._task_agents.retry_discarded_trees()
        if retried:
            logger.info("task agent storage: finished removing %d previously discarded worktree(s)", retried)

        policy = await self.settings.get()
        if not policy["auto_clean_worktrees"]:
            return
        due = now - policy["worktree_kept_days"] * 86400.0
        until, note = _confirmed_cutoff(
            state, now, due, until_key=_WORKTREES_DUE_KEY, seen_key=_WORKTREES_SEEN_KEY
        )
        if note:
            await self._space.write_state(**{_WORKTREES_DUE_KEY: due, _WORKTREES_SEEN_KEY: now})
        if until is None:
            return

        if not await asyncio.to_thread(self._paths.ledger_db().is_file):
            return
        rows = await self._ledger.trees(states=tuple(sorted(TREE_STATES_LEFT)))
        expired = 0
        for row in rows:
            if not (await self.settings.get())["auto_clean_worktrees"]:
                break
            if row.updated_at > until:
                continue
            try:
                outcome = await self._task_agents.expire_tree(
                    row.id,
                    until=until,
                    check_mtime=lambda path: newest_mtime_of(path, 0.25, skip_git=True),
                )
            except (LookupError, ValueError):
                continue
            if outcome.get("expired"):
                expired += 1
        if expired:
            logger.info("task agent storage: automatically expired %d stale worktree(s)", expired)
