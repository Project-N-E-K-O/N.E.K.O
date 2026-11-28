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

"""Project lifecycle, registration, matching, description, and archiving mixin.

Method-only mixin: every instance attribute is initialized in
``TaskAgentSpace.__init__`` (``nekopower.host.space.manager``).
"""

from __future__ import annotations

import json
import stat

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from nekopower.host.ledger import Commission
from nekopower.host.paths import DEFAULT_PROJECT, SpacePaths, safe_segment

from .models import (
    logger,
    PROJECT_ACTIVE,
    PROJECT_ARCHIVED,
    _PROJECT_FILE_VERSION,
    redact,
    DESCRIPTION_AUTHORS,
    Project,
    _LeftAside,
    _WorkdirClearing,
)
from .markdown import _one_line, _plain_lines, _date, normalize_name, name_occurs, _index_line
from .fs import _is_project

# Late-binding read point for symbols that tests rebind on
# ``nekopower.host.space`` via ``monkeypatch.setattr`` / ``patch``. Do NOT
# from-import those names here: a from-import snapshots the value at
# import time and the facade patch would silently miss this module.
from nekopower.host import space as _space_facade


class ProjectsMixin:
    """Project listing, creation, workdir tracking, archiving, and restoration methods."""

    # -- projects ---------------------------------------------------------

    def _project_from(self, directory: Path, *, archive_year: str | None = None) -> Project:
        """Read a project's file, to show the project; a directory without a readable one is a project of its name.

        Not for a record that is to be written back: see ``_project_to_change``.
        """
        data, _readable = self._project_data(directory)
        return self._project_of(data, directory, archive_year)

    def _project_to_change(self, directory: Path) -> tuple[Project, bool]:
        """Read a project's file once, to change it and write it back. Returns it and whether there is a file.

        Raises OSError when the file is there and cannot be read. What a
        failed read gives are defaults; written back, they would replace
        the project's working directory and description with nothing. And
        reading again before the write proves nothing: a file that could
        not be read a moment ago and can be now would be written over with
        the defaults of the first read.
        """
        data, readable = self._project_data(directory)
        if not readable:
            raise OSError(f"the file of project {directory.name} cannot be read; it is left as it is")
        return self._project_of(data, directory, None), data is not None

    @staticmethod
    def _project_of(data: dict[str, Any] | None, directory: Path, archive_year: str | None) -> Project:
        base = Project(name=directory.name, archive_year=archive_year, directory=directory.name)
        if archive_year is not None:
            base = replace(base, status=PROJECT_ARCHIVED)
        if data is None:
            return base

        def number(key: str, default: float = 0.0) -> float:
            value = data.get(key)
            return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default

        archived_at = data.get("archived_at")
        pending = data.get("pending_archive")
        return Project(
            # A project in use is called what its directory is called, which
            # is how it is made and how it is found: a name changed by hand
            # in its file would be one nothing finds it by, and renaming the
            # directory renames the project. An archived one can be in a
            # directory with a number after its name, so its file says.
            name=str(data.get("name") or directory.name) if archive_year is not None else directory.name,
            description=_one_line(data.get("description")),
            workdir=str(data["workdir"]) if isinstance(data.get("workdir"), str) and data["workdir"] else None,
            description_by=str(data["description_by"]) if data.get("description_by") in DESCRIPTION_AUTHORS else "",
            status=PROJECT_ARCHIVED if archive_year is not None else PROJECT_ACTIVE,
            created_at=number("created_at"),
            last_commission_at=number("last_commission_at"),
            commissions=int(number("commissions")),
            archived_at=float(archived_at) if isinstance(archived_at, (int, float)) and archive_year is not None else None,
            archive_year=archive_year,
            archive_attempts=int(number("archive_attempts")),
            directory=directory.name,
            pending_archive=pending if isinstance(pending, dict) else None,
        )

    @staticmethod
    def _project_data(directory: Path) -> tuple[dict[str, Any] | None, bool]:
        """Return what a project's file holds, and whether the file could be read.

        ``(None, True)`` when there is no file. ``(None, False)`` when there
        is one and it cannot be read or is not what this writes: the
        project then shows by its directory's name alone, and its file is
        not written over (``_project_to_change``).
        """
        try:
            # With or without the mark some editors put at the beginning of a
            # file they save: the user may have changed the description there.
            data = json.loads((directory / SpacePaths.PROJECT_FILE).read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return None, True
        except (OSError, ValueError, RecursionError):
            return None, False
        return (data, True) if isinstance(data, dict) else (None, False)

    def _put_project(self, directory: Path, project: Project) -> None:
        """Write a project's file. The project was read with ``_project_to_change``, or is new."""
        from utils.file_utils import atomic_write_json

        data = project.to_json()
        data.pop("archive_year", None)
        data.pop("directory", None)
        if data.get("pending_archive") is None:
            data.pop("pending_archive", None)
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write_json(directory / SpacePaths.PROJECT_FILE, {"version": _PROJECT_FILE_VERSION, **data}, ensure_ascii=False, indent=2)

    def _active_projects(self) -> list[Project]:
        try:
            directories = sorted(item for item in self._paths().projects_dir().iterdir() if _is_project(item))
        except OSError:
            return []
        return [self._project_from(item) for item in directories]

    def _archived_projects(self, *, skip_corrupt: bool = False) -> list[Project]:
        projects: list[Project] = []
        try:
            years = sorted(item for item in self._paths().archive_dir().iterdir() if item.is_dir())
        except OSError:
            return []
        for year in years:
            try:
                directories = sorted(item for item in year.iterdir() if _is_project(item))
            except OSError:
                continue
            for item in directories:
                if skip_corrupt:
                    data, ok = self._project_data(item)
                    if not ok:
                        continue
                    try:
                        projects.append(self._project_of(data, item, year.name))
                    except (OSError, ValueError, TypeError, OverflowError):
                        continue
                else:
                    projects.append(self._project_from(item, archive_year=year.name))
        return projects

    def _directory_of(self, project: Project) -> Path:
        paths = self._paths()
        if project.archive_year is not None:
            return paths.archived_dir(project.archive_year, project.directory or project.name)
        if project.directory:
            # The directory it was read from, whatever it is called.
            return paths.projects_dir() / project.directory
        return paths.project_dir(project.name)

    def _revive(self, project: str | None) -> None:
        """Bring a project back from the archive when something is about to be written under it.

        A project is archived only while nothing is under way in it, but a
        commission can still finish, or file a lesson, a moment after its
        project was moved. Written to a new directory of the same name,
        that would split the project in two.
        """
        if project is None or project == DEFAULT_PROJECT or self._paths().project_dir(project).is_dir():
            return
        archived = self._archived_of(project)
        if archived is not None:
            self._restore(archived)

    def _archived_of(self, name: str) -> Project | None:
        """Return the archived project of a name; of several, the one archived last.

        A name can be in the archive more than once: a project of the name
        was begun anew after an earlier one was archived, or a move to
        another volume was cut off after its copy had its name. The one to
        bring back is the one that was in use last.
        """
        wanted = normalize_name(name)
        found = [item for item in self._archived_projects() if normalize_name(item.name) == wanted]
        return max(found, key=lambda item: (item.archived_at or 0.0, item.last_commission_at), default=None)

    async def list_projects(self, *, include_archived: bool = True) -> list[Project]:
        """Return the projects, the archived ones last."""

        def work() -> list[Project]:
            projects = self._active_projects()
            if include_archived:
                projects.extend(self._archived_projects())
            return projects

        return await self._read(work)

    async def list_projects_checked(
        self, *, include_archived: bool = True, under_write_lock: bool = False
    ) -> tuple[list[Project], bool]:
        """Return projects and whether active directory enumeration and every active record succeeded."""

        def work() -> tuple[list[Project], bool]:
            projects_dir = self._paths().projects_dir()
            try:
                entries = sorted(projects_dir.iterdir())
            except FileNotFoundError:
                entries = []
            except OSError:
                return [], False
            active: list[Project] = []
            readable = True
            for item in entries:
                try:
                    st = item.stat()
                except FileNotFoundError:
                    continue
                except OSError:
                    readable = False
                    continue
                if not stat.S_ISDIR(st.st_mode) or item.name.startswith("."):
                    continue
                data, ok = self._project_data(item)
                if not ok:
                    readable = False
                try:
                    active.append(self._project_of(data, item, None))
                except (ValueError, TypeError, OverflowError):
                    readable = False
                    continue
            projects = list(active)
            if include_archived:
                projects.extend(self._archived_projects(skip_corrupt=True))
            return projects, readable

        if under_write_lock:
            return await self._write(work)
        return await self._read(work)

    async def get_project(self, name: str) -> Project | None:
        """Return a project that is not archived, or None."""

        def work() -> Project | None:
            directory = self._paths().project_dir(name)
            return self._project_from(directory) if directory.is_dir() else None

        return await self._read(work)

    async def set_project_workdir(self, name: str, workdir: str) -> Project | None:
        """Write where a project's files are, as the user asks. Returns the project, or None for one that is not there.

        Its record and its line in the index of the space (which says
        where its files are). Not for an archived project: that is named
        with its year, and nothing works in it. Whether the directory is
        one a task agent may work in, and whether anything is at work in
        the project, is for the caller to see to (``NekoPowerHost.set_project_workdir``).
        """
        safe_segment(name, "project")

        def work() -> Project | None:
            paths = self._paths()
            directory = paths.project_dir(name)
            if not directory.is_dir():
                return None
            project, known = self._project_to_change(directory)
            if not known:
                return None
            if workdir and self._workdir_clearing is not None and self._workdir_clearing(workdir):
                raise _WorkdirClearing(workdir)
            changed = replace(project, workdir=workdir)
            self._put_project(directory, changed)
            if name != DEFAULT_PROJECT:
                try:
                    line, target = self._project_line(changed)
                    self._index_set(paths.index(None), line, target)
                except OSError:
                    # The project works there all the same; its line says the old place till it is written again.
                    logger.warning("a project's working directory was changed, but its line in the index was not", exc_info=True)
            return changed

        return await self._write(work)

    async def read_project(self, name: str) -> tuple[Project | None, bool]:
        """Return a project that is not archived, or None, and whether its record could be read.

        ``get_project`` shows a project whose file cannot be read just now
        by its directory's name alone, with nothing said of what it is
        for. This tells that from a project nothing was ever written of:
        what is put in the place of words that were not read would lose
        them.
        """

        def work() -> tuple[Project | None, bool]:
            directory = self._paths().project_dir(name)
            if not directory.is_dir():
                return None, True
            data, readable = self._project_data(directory)
            return self._project_of(data, directory, None), readable

        return await self._read(work)

    def _project_line(self, project: Project) -> tuple[str, str]:
        paths = self._paths()
        target = f"projects/{project.name}/{paths.INDEX_NAME}"
        parts = [project.description] if project.description else []
        if project.workdir:
            parts.append(f"files in {project.workdir}")
        return _index_line(f"project: {project.name}", target, "; ".join(parts)), target

    def _ensure_project(
        self,
        name: str,
        *,
        description: str = "",
        workdir: str | None = None,
        at: float | None = None,
        count: bool = True,
        description_by: str = "creator",
    ) -> Project:
        """Return the project of a name, making it when it is not there.

        With ``count`` a commission is counted for it and its idle time
        starts anew. ``description`` is for a project that has none, and
        ``description_by`` says who wrote it.
        """
        from config.nekopower_settings import SPACE_PROJECT_DESCRIPTION_MAX_TOKENS

        paths = self._paths()
        now = self._clock() if at is None else at
        directory = paths.project_dir(name)
        # Read once, and not at all filed under when its record cannot be
        # read: a commission must not start without where its files are.
        project, known = self._project_to_change(directory)
        if not known:
            project = Project(name=name, created_at=now)
        effective_new_wd = None if project.workdir else workdir
        if effective_new_wd and self._workdir_clearing is not None and self._workdir_clearing(effective_new_wd):
            raise _WorkdirClearing(effective_new_wd)
        given = "" if project.description else _space_facade._cut(_one_line(redact(description)), SPACE_PROJECT_DESCRIPTION_MAX_TOKENS)
        project = replace(
            project,
            name=name,
            description=project.description or given,
            description_by=description_by if given else project.description_by,
            workdir=project.workdir or workdir,
            created_at=project.created_at or now,
            last_commission_at=max(project.last_commission_at, now) if count else project.last_commission_at,
            commissions=project.commissions + (1 if count else 0),
            status=PROJECT_ACTIVE,
            archived_at=None,
            archive_year=None,
            archive_attempts=0,
            # A summary kept is of the project before this commission, even
            # when the commission ended before the one the project had last.
            pending_archive=None if count else project.pending_archive,
        )
        self._put_project(directory, project)
        if not known and name != DEFAULT_PROJECT:
            line, target = self._project_line(project)
            self._index_add(paths.index(None), line, target)
        return project

    async def touch_project(
        self,
        name: str,
        *,
        description: str = "",
        workdir: str | None = None,
        at: float | None = None,
        count: bool = True,
    ) -> Project:
        """Return a project, making it when it is new; with ``count`` a commission is counted for it."""
        safe_segment(name, "project")
        return await self._write(
            lambda: self._ensure_project(name, description=description, workdir=workdir, at=at, count=count)
        )

    def _match(self, texts: tuple[str, ...]) -> Project | None:
        """Return the project whose name occurs in one of the texts; the longest name wins."""
        from config.nekopower_settings import SPACE_PROJECT_MATCH_MIN_CHARS

        haystacks = [text for text in texts if text]
        if not haystacks:
            return None
        best: tuple[tuple[int, int, float], Project] | None = None
        for project in (*self._active_projects(), *self._archived_projects()):
            if project.name == DEFAULT_PROJECT:
                continue
            needle = normalize_name(project.name)
            if len(needle) < SPACE_PROJECT_MATCH_MIN_CHARS:
                continue
            if not any(name_occurs(project.name, text) for text in haystacks):
                continue
            rank = (len(needle), 1 if project.status == PROJECT_ACTIVE else 0, project.last_commission_at)
            if best is None or rank > best[0]:
                best = (rank, project)
        if best is None:
            return None
        project = best[1]
        if project.status == PROJECT_ARCHIVED:
            # Of several archived projects of the name, the one archived last.
            return self._archived_of(project.name) or project
        return project

    def _restore(self, project: Project) -> Project:
        """Move an archived project back among the projects."""
        paths = self._paths()
        source = self._directory_of(project)
        target = paths.project_dir(project.name)
        if target.exists():
            # A project of the name is there already: that one is used, and
            # the archived one stays where it is.
            return self._project_from(target)
        disk_data, _readable = self._project_data(source)
        disk_workdir = (
            str(disk_data["workdir"])
            if isinstance(disk_data, dict) and isinstance(disk_data.get("workdir"), str) and disk_data["workdir"]
            else None
        )
        for candidate in (project.workdir, disk_workdir):
            if candidate and self._workdir_clearing is not None and self._workdir_clearing(candidate):
                raise _WorkdirClearing(candidate)
        target.parent.mkdir(parents=True, exist_ok=True)
        _space_facade._move(source, target)
        try:
            # As its file has it now, not as it was listed a while ago.
            current, _known = self._project_to_change(target)
        except OSError:
            # Its record cannot be read: it is back, and its file as it was.
            logger.warning("the file of a project brought back cannot be read; it is left as it is")
            restored = replace(project, status=PROJECT_ACTIVE, archived_at=None, archive_year=None, directory=target.name)
        else:
            if current.workdir and self._workdir_clearing is not None and self._workdir_clearing(current.workdir):
                try:
                    _space_facade._move(target, source)
                except OSError:
                    logger.warning(
                        "a restored project pointed at a workdir being cleared and could not be moved back; clearing its workdir reference",
                        exc_info=True,
                    )
                    current = replace(current, workdir=None)
                else:
                    raise _WorkdirClearing(current.workdir)
            restored = replace(
                current,
                name=current.name if _known else project.name,
                status=PROJECT_ACTIVE,
                archived_at=None,
                archive_year=None,
                archive_attempts=0,
                directory=target.name,
                pending_archive=None,
            )
            self._put_project(target, restored)
        try:
            line, link = self._project_line(restored)
            # Whichever of its files the line of the archived project points
            # at: its summary, its index when it has none, or another one
            # the user chose.
            self._index_swap(
                paths.index(None), f"archive/{project.archive_year}/{source.name}/", line, link, below=True
            )
        except OSError:
            # The project is back all the same, and is used.
            logger.warning("a project was brought back, but its line in the index of the space was not changed", exc_info=True)
        return restored

    async def use_project(self, name: str) -> None:
        """Note that a commission of a project is being taken up, before its notes are read.

        The project is brought back when it was archived, and its idle time
        starts anew. This runs under the lock archiving moves a directory
        under, and archiving looks at that time there: a project is either
        archived before this (and brought back by it) or not archived at
        all, never moved away under a commission that just started on it.
        A commission taken up again is counted for the project already.
        """
        if name == DEFAULT_PROJECT:
            return
        safe_segment(name, "project")

        def work() -> None:
            self._revive(name)
            directory = self._paths().project_dir(name)
            try:
                project, known = self._project_to_change(directory)
            except OSError:
                logger.warning("the file of a project in use cannot be read; it is left as it is")
                return
            now = self._clock()
            if known and now > project.last_commission_at:
                self._put_project(directory, replace(project, last_commission_at=now))

        await self._write(work)

    async def open_project(
        self,
        name: str,
        *,
        description: str = "",
        workdir: str | None = None,
        at: float | None = None,
        unless_archived: bool = False,
        unless_new: bool = False,
        description_by: str = "creator",
    ) -> Project | None:
        """Return the project of a name for a commission filed under it now.

        ``description`` is for a project that has none yet, and
        ``description_by`` says who wrote it (``Project.description_by``).

        A project of the name, however the name is spelled, is used; one
        that is not there is made, or, with ``unless_new``, not made and
        None is returned; the commission is counted for it. One that is
        archived is moved back, or, with ``unless_archived``, left where it
        is and None is returned. Looking and what follows are one step under
        the lock archiving runs under, so a project archived a moment ago is
        seen as archived.
        """
        safe_segment(name, "project")

        def work() -> Project | None:
            wanted = normalize_name(name)
            active = next((item for item in self._active_projects() if normalize_name(item.name) == wanted), None)
            if active is not None:
                return self._ensure_project(
                    active.name, description=description, workdir=workdir, at=at, description_by=description_by
                )
            archived = self._archived_of(name)
            if archived is not None:
                if unless_archived:
                    return None
                return self._ensure_project(self._restore(archived).name, at=at)
            if unless_new:
                return None
            return self._ensure_project(
                name, description=description, workdir=workdir, at=at, description_by=description_by
            )

        return await self._write(work)

    async def describe_project(self, name: str, description: str, *, by: str) -> Project | None:
        """Write what a project is for, and who says so; returns the project, or None when nothing was written.

        ``by`` is one of ``DESCRIPTION_AUTHORS``. What the user wrote is
        the user's: only ``by="user"`` writes over it. Nothing is written
        for a project that is not there (archived ones are not brought
        back for this), for ``_daily``, for an empty description, or when
        the project's record cannot be read. Its line in the index of all
        projects is written anew with it.
        """
        from config.nekopower_settings import SPACE_PROJECT_DESCRIPTION_MAX_TOKENS

        if by not in DESCRIPTION_AUTHORS:
            raise ValueError(f"unknown author of a description {by!r}")
        safe_segment(name, "project")
        text = _space_facade._cut(_one_line(redact(description)), SPACE_PROJECT_DESCRIPTION_MAX_TOKENS)
        if not text or name == DEFAULT_PROJECT:
            return None

        def work() -> Project | None:
            paths = self._paths()
            directory = paths.project_dir(name)
            try:
                # (A project that is not there has no record: nothing is made of it here.)
                project, known = self._project_to_change(directory)
            except OSError:
                logger.warning("the file of a project cannot be read; what it is for is left as it is")
                return None
            if not known or (project.description_by == "user" and by != "user"):
                return None
            changed = replace(project, description=text, description_by=by)
            self._put_project(directory, changed)
            try:
                line, target = self._project_line(changed)
                self._index_set(paths.index(None), line, target)
            except OSError:
                # The record has it; the line follows the next time it is written.
                logger.warning("the line of a project in the index of the space was not changed", exc_info=True)
            return changed

        return await self._write(work)

    async def assign_project(self, commission: Commission) -> Project | None:
        """Return the project a commission that names none belongs to, by what was dispatched.

        A project whose name occurs in what she dispatched, or in what the
        user said, takes the commission; one that is archived is moved back
        first. None when no name occurs: the commission is one of ``_daily``.
        """
        texts = (commission.dispatch_text, commission.user_text)

        def work() -> Project | None:
            project = self._match(texts)
            if project is None:
                return None
            if project.status == PROJECT_ARCHIVED:
                project = self._restore(project)
            return self._ensure_project(project.name)

        return await self._write(work)

    # -- archiving --------------------------------------------------------

    def _archive(
        self,
        name: str,
        summary: str,
        description: str,
        unless_touched_since: float | None = None,
        commissions: int | None = None,
    ) -> Project | None:
        """Move a project to the archive, with its summary. Returns it, or None when it was not moved.

        With ``unless_touched_since``, a project a commission was filed under
        after that time stays where it is; with ``commissions``, so does one
        that has another number of commissions by now (one that ended
        earlier can be filed under it late, and leaves the time as it was).
        Filing and this run under the same lock, so the look and the move
        are one step.

        A project that cannot be moved (on Windows, while a file in it is
        held open) stays where it is. A summary that came with it is kept
        in the project's record (``Project.kept_summary``), and a later try
        that brings none uses it: the summary was asked of a model, so a
        project that cannot be moved is not asked about again in every
        round, and what the model wrote is not thrown away.
        """
        paths = self._paths()
        source = paths.project_dir(name)
        if not source.is_dir():
            return None
        try:
            project, _known = self._project_to_change(source)
            readable = True
        except OSError:
            # Moved all the same when the caller says so; its file goes along as it is.
            project, readable = self._project_from(source), False
        if unless_touched_since is not None and (not readable or project.last_commission_at > unless_touched_since):
            return None
        if commissions is not None and (not readable or project.commissions != commissions):
            return None
        if not summary.strip():
            kept = project.kept_summary()
            if kept is not None:
                summary, description = kept[0], description or kept[1]
        now = self._clock()
        year = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y")
        root = paths.archive_dir() / year
        archived = replace(
            project,
            description=project.description or _one_line(description),
            status=PROJECT_ARCHIVED,
            archived_at=now,
            archive_year=year,
            pending_archive=None,
        )
        # The summary this try goes by. It stays in the record until the
        # directory has moved: stopped in between, the project is still
        # among the projects and its summary is not asked for again.
        using: dict[str, Any] | None = {
            "for": project.last_commission_at,
            "summary": redact(summary).strip(),
            "line": _one_line(redact(description)),
        }
        if not using["summary"]:
            using = None
        ahead = False
        try:
            written = redact(summary).strip() + "\n" if summary.strip() else None
            # The summary of an earlier archiving is neither written over
            # nor left under the name of this one: nothing of the user's
            # space is removed by itself.
            aside = self._set_old_summary_aside(source, unless=written)
            if written is not None:
                if not aside:
                    raise OSError("the summary the project was archived with before could not be set aside")
                self._put_text(source / paths.SUMMARY_NAME, written)
            root.mkdir(parents=True, exist_ok=True)
            target = root / source.name
            number = 2
            while target.exists():
                target = root / f"{source.name}-{number}"
                number += 1
            if readable:
                # Its record says when it was archived before it is moved,
                # so a process stopped right after the move leaves a record
                # that is whole: by that time the project is told from an
                # older one of its name in the archive. While it is still
                # among the projects, that time in its record means nothing.
                self._put_project(source, replace(archived, pending_archive=using))
                ahead = True
            _space_facade._move(source, target)
        except _LeftAside:
            # Not where it was. Whatever has its name now is another thing:
            # nothing is written there.
            logger.warning("a project could not be archived and is not where it was", exc_info=True)
            return None
        except OSError as exc:
            logger.warning("a project could not be moved to the archive (%s); it stays where it is", type(exc).__name__)
            back = replace(project, pending_archive=using) if using is not None else project
            # Not when the directory is not there: a move that could not be
            # undone left it under another name, and a record written now
            # would make an empty project of its name.
            if readable and source.is_dir() and (ahead or back != project):
                # As it was, with a summary that came to nothing kept for the next try.
                try:
                    self._put_project(source, back)
                except OSError:
                    logger.warning("the record of a project that could not be archived could not be put back", exc_info=True)
            return None
        archived = replace(archived, directory=target.name)
        if readable and using is not None:
            # Moved: the summary is in ``SUMMARY.md`` and has done its work.
            try:
                self._put_project(target, archived)
            except OSError:
                logger.warning("the record of an archived project still holds the summary it was archived with", exc_info=True)
        _line, link = self._project_line(project)
        try:
            # At its summary. One archived without a summary (no model, or
            # none that gave one) has no such file: its line points at its
            # index, as the line of a project in use does, so that whoever
            # follows it comes to the notes that were kept. Told by the
            # summary of this archiving, not by the file: a project that
            # was brought back may still have the one it was archived
            # with before. One that has no index either (nothing was ever
            # filed under it) is pointed at as a directory: what there is
            # of it, its reviews or its rules, is in there.
            if summary.strip():
                pointed = paths.SUMMARY_NAME
            elif (target / paths.INDEX_NAME).is_file():
                pointed = paths.INDEX_NAME
            else:
                pointed = ""
            archive_link = f"archive/{year}/{target.name}/{pointed}"
            self._index_swap(
                paths.index(None),
                link,
                _index_line(f"archived project: {name}", archive_link, _one_line(description) or archived.description),
                archive_link,
            )
        except OSError:
            # The project is archived all the same; it is not said to have failed.
            logger.warning("a project was archived, but its line in the index of the space was not changed", exc_info=True)
        return archived

    @staticmethod
    def _set_old_summary_aside(directory: Path, *, unless: str | None = None) -> bool:
        """Give the summary a project was archived with before another name. Returns whether its name is free now.

        A project that was brought back keeps the summary it was archived
        with then. Left under that name it would pass for the summary of
        this archiving, or be written over by it; under a name with its
        date it says what it is, and stays. ``unless`` is the summary of
        this archiving: a file that holds just that was written by an
        earlier try at it that could not move the project, and is no
        summary of before.
        """
        old = directory / SpacePaths.SUMMARY_NAME
        try:
            written = _date(old.stat().st_mtime)
        except OSError:
            # None there.
            return True
        if unless is not None:
            try:
                # As it is kept: with the line ends a text is written with.
                if _plain_lines(old.read_text(encoding="utf-8-sig")) == _plain_lines(unless):
                    return True
            except (OSError, UnicodeError):
                # Not told apart from one of before: kept as one.
                pass
        aside = old.with_name(f"{old.stem}-{written}{old.suffix}")
        number = 2
        while aside.exists():
            aside = old.with_name(f"{old.stem}-{written}-{number}{old.suffix}")
            number += 1
        try:
            old.rename(aside)
        except OSError:
            # Held by another program. It stays; a project archived without
            # a summary is archived all the same and its line does not point
            # at it, one archived with a summary waits for the next round.
            logger.warning("the summary a project was archived with before could not be given another name", exc_info=True)
            return False
        return True

    async def archive_project(
        self,
        name: str,
        *,
        summary: str = "",
        description: str = "",
        unless_touched_since: float | None = None,
        commissions: int | None = None,
    ) -> Project | None:
        """Archive a project; see ``_archive``."""
        if name == DEFAULT_PROJECT:
            return None
        return await self._write(
            lambda: self._archive(name, summary, description, unless_touched_since, commissions)
        )

    async def note_archive_attempt(self, name: str) -> int:
        """Count a summary that could not be written for a project; returns the count."""

        def work() -> int:
            directory = self._paths().project_dir(name)
            if not directory.is_dir():
                return 0
            try:
                project, _known = self._project_to_change(directory)
            except OSError:
                return 0
            project = replace(project, archive_attempts=project.archive_attempts + 1)
            self._put_project(directory, project)
            return project.archive_attempts

        return await self._write(work)
