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

"""The task agent space: what task agents keep for later commissions.

One per user, shared by every task agent, kept as plain text below
``<data root>/nekoclaw/task_agent_space/`` (the layout is in
``paths.SpacePaths``). It follows the way Claude Code keeps its memory:

* ``AGENTS.md`` - rules, written by the user. The host never writes them.
* ``MEMORY.md`` - an index, one line per entry, handed to a task agent when it
  starts. The entries themselves are read when they are needed.
* ``memory/*.md`` - one entry per file, of four kinds: ``lesson`` (a reusable
  finding), ``reflection`` (what went wrong and what to do instead),
  ``reference`` (where something is found), ``progress`` (how a project
  stands). Corrections and approvals from the user are task preferences
  (``preferences.py``) and are not kept here.

There is one such set for the space as a whole and one for every project;
``_daily`` is the project of commissions that belong to none. A project nothing
was commissioned for in a while is moved to ``archive/`` whole, and is moved
back when a commission names it again.

Only the host writes here. Every write goes through one lock and replaces the
file atomically, so two task agents finishing at the same time cannot lose
each other's lines. What the user edited by hand is kept: an index is changed
one line at a time, and an entry that is added to gets a dated section at its
end while what it said before stays as it was.

Nothing here runs at startup and nothing asks a model. The reviews written
after a commission, and archiving, are in ``space_keeper.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from nekoclaw.host.ledger import Commission
from nekoclaw.host.paths import DEFAULT_PROJECT, SpacePaths, safe_segment

logger = logging.getLogger(__name__)

T = TypeVar("T")

ENTRY_TYPES: tuple[str, ...] = ("lesson", "reflection", "reference", "progress")
# What ``note_lesson`` calls the kinds, as the tool's schema has them.
_DEFAULT_TYPE = "lesson"
PROJECT_ACTIVE = "active"
PROJECT_ARCHIVED = "archived"
_PROJECT_FILE_VERSION = 1
_STATE_FILE_VERSION = 1
_SLUG_MAX_CHARS = 60
_WRITE_ATTEMPTS = 3
_WRITE_RETRY_SECONDS = 0.05
# Characters that cannot be in a file name, white space, and what Markdown
# would read as part of a link.
_SLUG_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\s()\[\]#%]+')
_FRONT_MATTER_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s?(.*)$")
_WHITESPACE = re.compile(r"\s+")


def redact(text: str) -> str:
    """Return ``text`` as it is kept in the space.

    Everything that is written into a review or an entry passes through
    here. Nothing is masked today, as nothing is masked in the ledger; if
    keys, phone numbers and the like are to be masked before they are kept
    for good, this is the one place to do it.
    """
    return text


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Entry:
    """One entry: a file in a ``memory`` directory."""

    # The file's name without ``.md``.
    id: str
    # The project it belongs to; None for an entry of the space as a whole.
    project: str | None
    title: str
    description: str
    type: str
    created: str
    updated: str
    # The commissions it came from.
    sources: tuple[str, ...] = ()
    # The text below the front matter; empty in a listing.
    content: str = ""

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["sources"] = list(self.sources)
        return data


@dataclass(frozen=True, slots=True)
class Project:
    """One project: ``project.json`` of its directory."""

    name: str
    description: str = ""
    # Where its files are: the working directory of the commission it began with.
    workdir: str | None = None
    status: str = PROJECT_ACTIVE
    created_at: float = 0.0
    # When a commission was last filed under it; what archiving goes by.
    last_commission_at: float = 0.0
    commissions: int = 0
    archived_at: float | None = None
    # The year directory it is archived under.
    archive_year: str | None = None
    # How many times a summary for archiving it could not be written.
    archive_attempts: int = 0
    # The name of its directory. An archived project is addressed by this and
    # its year: a second project of a name archived in one year is kept in a
    # directory named after it with a number.
    directory: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Review:
    """The review of one commission: a file in a project's ``reviews`` directory."""

    commission_id: str
    project: str
    # The year directory when the project is archived, else None.
    archive_year: str | None
    title: str
    status: str
    ended: str
    # The ids of the entries the review filed.
    entries: tuple[str, ...] = ()
    content: str = ""

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["entries"] = list(self.entries)
        return data


@dataclass(slots=True)
class _Document:
    """A text file with front matter, taken apart."""

    # Key and value of every line of the front matter, in order.
    meta: list[tuple[str, str]] = field(default_factory=list)
    body: str = ""
    # False when the file has no front matter this module can read.
    has_meta: bool = False

    def get(self, key: str, default: str = "") -> str:
        for name, value in self.meta:
            if name == key:
                return value
        return default

    def set(self, key: str, value: str) -> None:
        for index, (name, _old) in enumerate(self.meta):
            if name == key:
                self.meta[index] = (key, value)
                return
        self.meta.append((key, value))


def _one_line(text: Any) -> str:
    return _WHITESPACE.sub(" ", str(text or "")).strip()


def _cut(text: Any, max_tokens: int) -> str:
    from utils.tokenize import truncate_to_tokens

    return truncate_to_tokens(str(text or "").strip(), max_tokens).strip()


def _parse_document(text: str) -> _Document:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return _Document(body=text)
    meta: list[tuple[str, str]] = []
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            body = "\n".join(lines[index + 1 :])
            return _Document(meta=meta, body=body.lstrip("\n"), has_meta=True)
        match = _FRONT_MATTER_KEY.match(line)
        if match is None:
            if line.strip():
                # Not what this module writes: left alone as plain text.
                return _Document(body=text)
            continue
        meta.append((match.group(1), match.group(2).strip()))
    return _Document(body=text)


def _render_document(document: _Document) -> str:
    if not document.has_meta:
        return document.body
    head = "\n".join(f"{key}: {value}" for key, value in document.meta)
    return f"---\n{head}\n---\n\n{document.body.strip()}\n"


def _date(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")


def _datetime(timestamp: float | None) -> str:
    if timestamp is None:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(title: str) -> str:
    """Return a file name (without extension) for a title.

    Letters of any script are kept, so a title in Chinese gives a name in
    Chinese. Raises ValueError when nothing usable is left.
    """
    value = _SLUG_UNSAFE.sub("-", str(title or "").strip()).strip("-. ").lower()
    value = value[:_SLUG_MAX_CHARS].strip("-. ")
    return safe_segment(value, "entry name")


def normalize_name(text: str) -> str:
    """Return a name as it is compared: case folded, white space removed."""
    return _WHITESPACE.sub("", str(text or "")).casefold()


def _spaced_letter(char: str) -> bool:
    """Whether a character is a letter or digit of a script that puts spaces between words."""
    from nekoclaw.host.selection import _PAIRED_CHAR_RE

    return bool(char) and (char.isalnum() or char == "_") and not _PAIRED_CHAR_RE.fullmatch(char)


def name_occurs(name: str, text: str) -> bool:
    """Whether a project's name occurs in a sentence.

    Case and the amount of white space do not matter. In scripts that put
    spaces between words the name has to stand as words of its own: a
    project called ``app`` is not named by ``happy``. Chinese, Japanese and
    Korean text has no such boundaries, and there the name may stand anywhere.
    """
    needle = " ".join(str(name or "").split()).casefold()
    haystack = " ".join(str(text or "").split()).casefold()
    if not needle:
        return False
    start = haystack.find(needle)
    while start >= 0:
        end = start + len(needle)
        before = haystack[start - 1] if start > 0 else ""
        after = haystack[end] if end < len(haystack) else ""
        joined_before = _spaced_letter(needle[0]) and _spaced_letter(before)
        joined_after = _spaced_letter(needle[-1]) and _spaced_letter(after)
        if not joined_before and not joined_after:
            return True
        start = haystack.find(needle, start + 1)
    return False


def _entry_type(value: Any) -> str:
    kind = str(value or "").strip().lower()
    return kind if kind in ENTRY_TYPES else _DEFAULT_TYPE


def _index_target(scope: str) -> str:
    return f"]({scope})"


def _index_line(title: str, target: str, description: str) -> str:
    title = _one_line(title).replace("[", "(").replace("]", ")")
    line = f"- [{title}]({target})"
    description = _one_line(description)
    return f"{line} — {description}" if description else line


class SpaceError(RuntimeError):
    """Something asked of the space cannot be done: an unknown entry, an unusable name."""


# ---------------------------------------------------------------------------
# The space
# ---------------------------------------------------------------------------


class TaskAgentSpace:
    """Reads and writes the task agent space. See the module docstring.

    ``paths`` is called in a worker thread (resolving the data root reads
    configuration). The methods without a leading underscore are coroutines
    and may be called from the event loop; the ones with it block.
    """

    def __init__(
        self,
        paths: Callable[[], SpacePaths],
        *,
        ledger: Any = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._paths_source = paths
        self._ledger = ledger
        self._clock = clock
        self._lock: asyncio.Lock | None = None

    # -- plumbing ---------------------------------------------------------

    def _paths(self) -> SpacePaths:
        return self._paths_source()

    async def _read(self, work: Callable[..., T], *args: Any) -> T:
        return await asyncio.to_thread(work, *args)

    async def _write(self, work: Callable[..., T], *args: Any) -> T:
        """Run blocking work that changes files, one piece at a time.

        A caller that is cancelled still waits for the work, so the lock is
        not given to the next writer while this one is half done.
        """
        from utils.asyncio_executor import to_thread_through_cancellation

        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            return await to_thread_through_cancellation(work, *args)

    @staticmethod
    def _read_text(path: Path) -> str | None:
        """Return a file's text; None when it is not there or cannot be read."""
        try:
            return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as exc:
            logger.warning("a file of the task agent space cannot be read (%s)", type(exc).__name__)
            return None

    @staticmethod
    def _put_text(path: Path, text: str) -> None:
        """Replace a file atomically and check that what is there is what was written.

        On Windows a file held open by another program (an editor, a virus
        scanner) refuses to be replaced for a moment: tried again a few times.
        """
        from utils.file_utils import atomic_write_text

        # Written in text mode, which makes the line ends the platform's own.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        last: Exception | None = None
        for attempt in range(_WRITE_ATTEMPTS):
            try:
                atomic_write_text(path, text)
                if path.read_text(encoding="utf-8") == text:
                    return
                last = OSError("the file does not hold what was written")
            except OSError as exc:
                last = exc
            time.sleep(_WRITE_RETRY_SECONDS * (attempt + 1))
        raise last if last is not None else OSError("the file could not be written")

    @staticmethod
    def _signature(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def _edit_text(self, path: Path, change: Callable[[str], str]) -> bool:
        """Change a text file by a function of its text. Returns whether it changed.

        Read, changed and written back; when the file was written by someone
        else in between (the user saving it in an editor), it is read again,
        so their change is not written over.
        """
        for _attempt in range(_WRITE_ATTEMPTS):
            before = self._signature(path)
            text = self._read_text(path) or ""
            changed = change(text)
            if changed == text:
                return False
            if self._signature(path) != before:
                continue
            self._put_text(path, changed)
            return True
        raise OSError(f"{path.name} kept changing while it was being written")

    # -- the index --------------------------------------------------------

    def _index_add(self, index: Path, line: str, target: str) -> None:
        """Add a line to an index unless one already points at ``target``. New lines go first."""
        mark = _index_target(target)

        def change(text: str) -> str:
            lines = text.split("\n") if text else []
            if any(mark in item for item in lines):
                return text
            position = next((number for number, item in enumerate(lines) if item.startswith("- ")), None)
            if position is None:
                # No list yet: after whatever the file holds, before the
                # blank lines it ends in.
                position = len(lines)
                while position > 0 and not lines[position - 1].strip():
                    position -= 1
            lines.insert(position, line)
            result = "\n".join(lines)
            return result if result.endswith("\n") else result + "\n"

        self._edit_text(index, change)

    def _index_set(self, index: Path, line: str, target: str) -> None:
        """Put a line into an index in place of the one that points at ``target``, or first."""
        mark = _index_target(target)

        def change(text: str) -> str:
            lines = text.split("\n") if text else []
            for number, item in enumerate(lines):
                if mark in item:
                    lines[number] = line
                    return "\n".join(lines)
            return text

        if not self._edit_text(index, change):
            self._index_add(index, line, target)

    def _index_remove(self, index: Path, target: str) -> None:
        mark = _index_target(target)

        def change(text: str) -> str:
            if not text:
                return text
            return "\n".join(item for item in text.split("\n") if mark not in item)

        if index.exists():
            self._edit_text(index, change)

    # -- entries ----------------------------------------------------------

    def _entry_from(self, path: Path, project: str | None, *, with_content: bool) -> Entry | None:
        text = self._read_text(path)
        if text is None:
            return None
        document = _parse_document(text)
        try:
            modified = path.stat().st_mtime
        except OSError:
            modified = self._clock()
        sources = tuple(item.strip() for item in document.get("sources").split(",") if item.strip())
        return Entry(
            id=path.stem,
            project=project,
            title=document.get("name") or path.stem,
            description=document.get("description"),
            type=_entry_type(document.get("type")),
            created=document.get("created") or _date(modified),
            updated=document.get("updated") or _date(modified),
            sources=sources,
            content=document.body.strip() if with_content else "",
        )

    def _entries(self, directory: Path, project: str | None, *, with_content: bool = False) -> list[Entry]:
        try:
            files = sorted(item for item in directory.iterdir() if item.suffix == ".md" and item.is_file())
        except OSError:
            return []
        entries = [self._entry_from(item, project, with_content=with_content) for item in files]
        return [entry for entry in entries if entry is not None]

    def _same_topic(self, directory: Path, project: str | None, title: str) -> Entry | None:
        """Return the entry of a directory that is about the topic of ``title``, if there is one."""
        from config.nekoclaw_settings import SPACE_SAME_TOPIC_SIMILARITY
        from nekoclaw.host.selection import similarity

        wanted = normalize_name(title)
        best: tuple[float, Entry] | None = None
        for entry in self._entries(directory, project):
            if normalize_name(entry.title) == wanted:
                return entry
            score = similarity(entry.title, title)
            if score >= SPACE_SAME_TOPIC_SIMILARITY and (best is None or score > best[0]):
                best = (score, entry)
        return best[1] if best is not None else None

    @staticmethod
    def _free_name(directory: Path, wanted: str) -> str:
        name, number = wanted, 2
        while (directory / f"{name}.md").exists():
            name = f"{wanted}-{number}"
            number += 1
        return name

    def _file_entry(
        self,
        project: str | None,
        *,
        title: str,
        content: str,
        kind: str,
        description: str = "",
        source: str | None = None,
    ) -> tuple[Entry, bool]:
        """Keep one entry. Returns it and whether a new file was made for it.

        An entry about the same topic is added to: it gets a section with
        today's date at its end, and what it said before stays.
        """
        from config.nekoclaw_settings import (
            SPACE_ENTRY_CONTENT_MAX_TOKENS,
            SPACE_ENTRY_DESCRIPTION_MAX_TOKENS,
            SPACE_ENTRY_TITLE_MAX_TOKENS,
        )

        paths = self._paths()
        self._revive(project)
        title = _cut(_one_line(redact(title)), SPACE_ENTRY_TITLE_MAX_TOKENS)
        content = _cut(redact(content), SPACE_ENTRY_CONTENT_MAX_TOKENS)
        if not title or not content:
            raise SpaceError("an entry needs a title and a text")
        description = _cut(_one_line(redact(description)) or _one_line(content), SPACE_ENTRY_DESCRIPTION_MAX_TOKENS)
        directory = paths.memory_dir(project)
        today = _date(self._clock())
        existing = self._same_topic(directory, project, title)
        if existing is not None:
            path = directory / f"{existing.id}.md"

            def add(text: str) -> str:
                document = _parse_document(text)
                if content in document.body:
                    return text
                addition = f"## {today}\n\n{content}"
                document.body = f"{document.body.rstrip()}\n\n{addition}\n"
                if document.has_meta:
                    document.set("updated", today)
                    if source:
                        known = [item.strip() for item in document.get("sources").split(",") if item.strip()]
                        if source not in known:
                            document.set("sources", ", ".join([*known, source]))
                    return _render_document(document)
                return document.body

            self._edit_text(path, add)
            # Its line in the index is left as it is (the user may have
            # reworded it); only a missing one is put back.
            self._index_add(
                paths.index(project),
                _index_line(existing.title, f"{paths.MEMORY_DIR}/{existing.id}.md", existing.description),
                f"{paths.MEMORY_DIR}/{existing.id}.md",
            )
            entry = self._entry_from(path, project, with_content=True)
            return entry or existing, False
        try:
            wanted = slug(title)
        except ValueError:
            wanted = "entry"
        name = self._free_name(directory, wanted)
        document = _Document(
            meta=[
                ("name", title),
                ("description", description),
                ("type", _entry_type(kind)),
                ("created", today),
                ("updated", today),
                ("sources", source or ""),
            ],
            body=content,
            has_meta=True,
        )
        path = directory / f"{name}.md"
        self._put_text(path, _render_document(document))
        target = f"{paths.MEMORY_DIR}/{name}.md"
        self._index_add(paths.index(project), _index_line(title, target, description), target)
        entry = self._entry_from(path, project, with_content=True)
        if entry is None:
            raise SpaceError("the entry could not be read back")
        return entry, True

    async def submit(self, commission: Commission, lesson: dict[str, Any]) -> str | None:
        """Keep what a task agent filed with ``note_lesson``; the ``LessonSink`` of the manager.

        ``feedback`` never arrives here: it is a task preference.
        """
        scope = str(lesson.get("scope") or "project").strip().lower()
        project = None if scope == "global" else (commission.project or DEFAULT_PROJECT)
        try:
            entry, created = await self._write(
                lambda: self._file_entry(
                    project,
                    title=str(lesson.get("title") or ""),
                    content=str(lesson.get("content") or ""),
                    kind=str(lesson.get("category") or ""),
                    source=commission.id,
                )
            )
        except (SpaceError, ValueError) as exc:
            return f"not kept: {exc}"
        if self._ledger is not None:
            # On the commission's record too, so the board can show it.
            await self._ledger.append_event(
                commission.id,
                "lesson",
                {**dict(lesson), "entry": entry.id, "project": project, "created": created},
            )
        where = "the space of every project" if project is None else f"project {project}"
        return f"kept in {where} as {entry.id}.md" if created else f"added to {entry.id}.md in {where}"

    async def file_entry(
        self,
        project: str | None,
        *,
        title: str,
        content: str,
        kind: str = _DEFAULT_TYPE,
        description: str = "",
        source: str | None = None,
    ) -> tuple[Entry, bool]:
        """Keep one entry; see ``_file_entry``. Raises ``SpaceError`` for one that cannot be kept."""
        try:
            return await self._write(
                lambda: self._file_entry(
                    project, title=title, content=content, kind=kind, description=description, source=source
                )
            )
        except ValueError as exc:
            raise SpaceError(str(exc)) from exc

    # -- for the console: entries -----------------------------------------

    def _scope(self, project: str | None, archive_year: str | None = None) -> Path:
        paths = self._paths()
        if archive_year is not None:
            if project is None:
                raise SpaceError("an archived project needs its directory")
            # ``project`` is the directory's name here (``Project.directory``).
            return paths.archived_dir(archive_year, project)
        return paths.scope_dir(project)

    async def list_entries(self, project: str | None = None, *, archive_year: str | None = None) -> list[Entry]:
        """Return the entries of the space (``project`` None) or of a project, without their text.

        For an archived project give its year and, as ``project``, the name
        of its directory (``Project.directory``).
        """

        def work() -> list[Entry]:
            return self._entries(self._scope(project, archive_year) / SpacePaths.MEMORY_DIR, project)

        return await self._read(work)

    async def read_entry(
        self, entry_id: str, project: str | None = None, *, archive_year: str | None = None
    ) -> Entry | None:
        def work() -> Entry | None:
            path = self._scope(project, archive_year) / SpacePaths.MEMORY_DIR / f"{safe_segment(entry_id)}.md"
            return self._entry_from(path, project, with_content=True)

        return await self._read(work)

    async def edit_entry(
        self,
        entry_id: str,
        project: str | None = None,
        *,
        title: str | None = None,
        description: str | None = None,
        kind: str | None = None,
        content: str | None = None,
    ) -> Entry | None:
        """Change an entry as the user asks; what is given replaces what was there.

        Returns the entry, or None when there is none of that id. Its line
        in the index is rewritten when the title or the description changed.
        """

        def work() -> Entry | None:
            paths = self._paths()
            path = paths.memory_dir(project) / f"{safe_segment(entry_id)}.md"
            text = self._read_text(path)
            if text is None:
                return None
            document = _parse_document(text)
            if not document.has_meta:
                document = _Document(meta=[("name", path.stem)], body=document.body, has_meta=True)
            if title is not None and _one_line(title):
                document.set("name", _one_line(title))
            if description is not None:
                document.set("description", _one_line(description))
            if kind is not None:
                document.set("type", _entry_type(kind))
            if content is not None:
                document.body = content
            document.set("updated", _date(self._clock()))
            self._put_text(path, _render_document(document))
            entry = self._entry_from(path, project, with_content=True)
            if entry is not None and (title is not None or description is not None):
                target = f"{paths.MEMORY_DIR}/{entry.id}.md"
                self._index_set(paths.index(project), _index_line(entry.title, target, entry.description), target)
            return entry

        return await self._write(work)

    async def delete_entry(self, entry_id: str, project: str | None = None) -> bool:
        """Remove an entry and its line in the index, as the user asks. Returns whether there was one."""

        def work() -> bool:
            paths = self._paths()
            name = safe_segment(entry_id)
            path = paths.memory_dir(project) / f"{name}.md"
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            self._index_remove(paths.index(project), f"{paths.MEMORY_DIR}/{name}.md")
            return True

        return await self._write(work)

    # -- for the console: rules and indexes -------------------------------

    async def read_rules(self, project: str | None = None) -> str:
        return await self._read(lambda: self._read_text(self._paths().rules(project)) or "")

    async def write_rules(self, text: str, project: str | None = None) -> None:
        """Write the rules as the user gave them. The host itself never calls this."""
        await self._write(lambda: self._put_text(self._paths().rules(project), text))

    async def read_index(self, project: str | None = None) -> str:
        return await self._read(lambda: self._read_text(self._paths().index(project)) or "")

    # -- projects ---------------------------------------------------------

    def _project_from(self, directory: Path, *, archive_year: str | None = None) -> Project:
        """Read a project's file; a directory without a readable one is a project of its name."""
        base = Project(name=directory.name, archive_year=archive_year, directory=directory.name)
        if archive_year is not None:
            base = replace(base, status=PROJECT_ARCHIVED)
        try:
            data = json.loads((directory / SpacePaths.PROJECT_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return base
        if not isinstance(data, dict):
            return base

        def number(key: str, default: float = 0.0) -> float:
            value = data.get(key)
            return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default

        archived_at = data.get("archived_at")
        return Project(
            name=str(data.get("name") or directory.name),
            description=_one_line(data.get("description")),
            workdir=str(data["workdir"]) if isinstance(data.get("workdir"), str) and data["workdir"] else None,
            status=PROJECT_ARCHIVED if archive_year is not None else PROJECT_ACTIVE,
            created_at=number("created_at"),
            last_commission_at=number("last_commission_at"),
            commissions=int(number("commissions")),
            archived_at=float(archived_at) if isinstance(archived_at, (int, float)) and archive_year is not None else None,
            archive_year=archive_year,
            archive_attempts=int(number("archive_attempts")),
            directory=directory.name,
        )

    def _put_project(self, directory: Path, project: Project) -> None:
        from utils.file_utils import atomic_write_json

        data = project.to_json()
        data.pop("archive_year", None)
        data.pop("directory", None)
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write_json(directory / SpacePaths.PROJECT_FILE, {"version": _PROJECT_FILE_VERSION, **data}, ensure_ascii=False, indent=2)

    def _active_projects(self) -> list[Project]:
        try:
            directories = sorted(item for item in self._paths().projects_dir().iterdir() if item.is_dir())
        except OSError:
            return []
        return [self._project_from(item) for item in directories]

    def _archived_projects(self) -> list[Project]:
        projects: list[Project] = []
        try:
            years = sorted(item for item in self._paths().archive_dir().iterdir() if item.is_dir())
        except OSError:
            return []
        for year in years:
            try:
                directories = sorted(item for item in year.iterdir() if item.is_dir())
            except OSError:
                continue
            projects.extend(self._project_from(item, archive_year=year.name) for item in directories)
        return projects

    def _directory_of(self, project: Project) -> Path:
        paths = self._paths()
        if project.archive_year is not None:
            return paths.archived_dir(project.archive_year, project.directory or project.name)
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
        wanted = normalize_name(project)
        archived = next((item for item in self._archived_projects() if normalize_name(item.name) == wanted), None)
        if archived is not None:
            self._restore(archived)

    async def list_projects(self, *, include_archived: bool = True) -> list[Project]:
        """Return the projects, the archived ones last."""

        def work() -> list[Project]:
            projects = self._active_projects()
            if include_archived:
                projects.extend(self._archived_projects())
            return projects

        return await self._read(work)

    async def get_project(self, name: str) -> Project | None:
        """Return a project that is not archived, or None."""

        def work() -> Project | None:
            directory = self._paths().project_dir(name)
            return self._project_from(directory) if directory.is_dir() else None

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
    ) -> Project:
        """Return the project of a name, making it when it is not there.

        With ``count`` a commission is counted for it and its idle time
        starts anew.
        """
        from config.nekoclaw_settings import SPACE_PROJECT_DESCRIPTION_MAX_TOKENS

        paths = self._paths()
        now = self._clock() if at is None else at
        directory = paths.project_dir(name)
        known = directory.is_dir() and (directory / paths.PROJECT_FILE).is_file()
        project = self._project_from(directory) if known else Project(name=name, created_at=now)
        project = replace(
            project,
            name=name,
            description=project.description or _cut(_one_line(redact(description)), SPACE_PROJECT_DESCRIPTION_MAX_TOKENS),
            workdir=project.workdir or workdir,
            created_at=project.created_at or now,
            last_commission_at=max(project.last_commission_at, now) if count else project.last_commission_at,
            commissions=project.commissions + (1 if count else 0),
            status=PROJECT_ACTIVE,
            archived_at=None,
            archive_year=None,
            archive_attempts=0,
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
        from config.nekoclaw_settings import SPACE_PROJECT_MATCH_MIN_CHARS

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
        return best[1] if best is not None else None

    def _restore(self, project: Project) -> Project:
        """Move an archived project back among the projects."""
        paths = self._paths()
        source = self._directory_of(project)
        target = paths.project_dir(project.name)
        if target.exists():
            # A project of the name is there already: that one is used, and
            # the archived one stays where it is.
            return self._project_from(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        _move(source, target)
        restored = replace(
            project, status=PROJECT_ACTIVE, archived_at=None, archive_year=None, archive_attempts=0, directory=target.name
        )
        self._put_project(target, restored)
        self._index_remove(paths.index(None), f"archive/{project.archive_year}/{source.name}/{paths.SUMMARY_NAME}")
        line, link = self._project_line(restored)
        self._index_add(paths.index(None), line, link)
        return restored

    async def open_project(
        self, name: str, *, description: str = "", workdir: str | None = None, at: float | None = None
    ) -> Project:
        """Return the project of a name for a commission filed under it now.

        One that is archived is moved back, one that is not there is made;
        the commission is counted for it.
        """
        safe_segment(name, "project")

        def work() -> Project:
            if not self._paths().project_dir(name).is_dir():
                wanted = normalize_name(name)
                archived = next(
                    (item for item in self._archived_projects() if normalize_name(item.name) == wanted), None
                )
                if archived is not None:
                    return self._ensure_project(self._restore(archived).name, at=at)
            return self._ensure_project(name, description=description, workdir=workdir, at=at)

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
        self, name: str, summary: str, description: str, unless_touched_since: float | None = None
    ) -> Project | None:
        """Move a project to the archive, with its summary. Returns it, or None when it was not moved.

        With ``unless_touched_since``, a project a commission was filed under
        after that time stays where it is. Filing and this run under the same
        lock, so the look and the move are one step.
        """
        paths = self._paths()
        source = paths.project_dir(name)
        if not source.is_dir():
            return None
        project = self._project_from(source)
        if unless_touched_since is not None and project.last_commission_at > unless_touched_since:
            return None
        now = self._clock()
        year = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y")
        if summary.strip():
            self._put_text(source / paths.SUMMARY_NAME, redact(summary).strip() + "\n")
        root = paths.archive_dir() / year
        root.mkdir(parents=True, exist_ok=True)
        target = root / source.name
        number = 2
        while target.exists():
            target = root / f"{source.name}-{number}"
            number += 1
        try:
            _move(source, target)
        except OSError as exc:
            logger.warning("a project could not be moved to the archive (%s); it stays where it is", type(exc).__name__)
            return None
        archived = replace(
            project,
            description=project.description or _one_line(description),
            status=PROJECT_ARCHIVED,
            archived_at=now,
            archive_year=year,
            directory=target.name,
        )
        self._put_project(target, archived)
        _line, link = self._project_line(project)
        self._index_remove(paths.index(None), link)
        archive_link = f"archive/{year}/{target.name}/{paths.SUMMARY_NAME}"
        self._index_add(
            paths.index(None),
            _index_line(f"archived project: {name}", archive_link, _one_line(description) or archived.description),
            archive_link,
        )
        return archived

    async def archive_project(
        self,
        name: str,
        *,
        summary: str = "",
        description: str = "",
        unless_touched_since: float | None = None,
    ) -> Project | None:
        """Archive a project; see ``_archive``."""
        if name == DEFAULT_PROJECT:
            return None
        return await self._write(lambda: self._archive(name, summary, description, unless_touched_since))

    async def note_archive_attempt(self, name: str) -> int:
        """Count a summary that could not be written for a project; returns the count."""

        def work() -> int:
            directory = self._paths().project_dir(name)
            if not directory.is_dir():
                return 0
            project = self._project_from(directory)
            project = replace(project, archive_attempts=project.archive_attempts + 1)
            self._put_project(directory, project)
            return project.archive_attempts

        return await self._write(work)

    # -- reviews ----------------------------------------------------------

    def _review_from(self, path: Path, project: str, archive_year: str | None, *, with_content: bool) -> Review | None:
        text = self._read_text(path)
        if text is None:
            return None
        document = _parse_document(text)
        entries = tuple(item.strip() for item in document.get("entries").split(",") if item.strip())
        return Review(
            commission_id=path.stem,
            project=project,
            archive_year=archive_year,
            title=document.get("title"),
            status=document.get("status"),
            ended=document.get("ended"),
            entries=entries,
            content=document.body.strip() if with_content else "",
        )

    async def write_review(
        self,
        project: str,
        commission: Commission,
        text: str,
        *,
        entries: tuple[str, ...] = (),
    ) -> Review:
        """Keep the review of a commission under a project."""

        def work() -> Review:
            paths = self._paths()
            self._revive(project)
            path = paths.review(project, commission.id)
            document = _Document(
                meta=[
                    ("commission", commission.id),
                    ("title", _one_line(redact(commission.title))),
                    ("status", commission.status),
                    ("started", _datetime(commission.started_at)),
                    ("ended", _datetime(commission.ended_at)),
                    ("entries", ", ".join(entries)),
                ],
                body=f"**Commission**: {redact(commission.dispatch_text).strip()}\n\n{redact(text).strip()}",
                has_meta=True,
            )
            self._put_text(path, _render_document(document))
            review = self._review_from(path, project, None, with_content=True)
            if review is None:
                raise SpaceError("the review could not be read back")
            return review

        return await self._write(work)

    def _review_directories(self) -> list[tuple[Path, str, str | None]]:
        """Return the ``reviews`` directory of every project: (directory, project, archive year)."""
        directories = [
            (self._directory_of(project) / SpacePaths.REVIEWS_DIR, project.name, project.archive_year)
            for project in (*self._active_projects(), *self._archived_projects())
        ]
        return directories

    async def list_reviews(self, project: str | None = None) -> list[Review]:
        """Return the reviews of a project, or of every project, newest first, without their text."""

        def work() -> list[Review]:
            reviews: list[Review] = []
            for directory, name, year in self._review_directories():
                if project is not None and name != project:
                    continue
                try:
                    files = [item for item in directory.iterdir() if item.suffix == ".md" and item.is_file()]
                except OSError:
                    continue
                for item in files:
                    review = self._review_from(item, name, year, with_content=False)
                    if review is not None:
                        reviews.append(review)
            return sorted(reviews, key=lambda review: review.ended, reverse=True)

        return await self._read(work)

    async def read_review(self, commission_id: str) -> Review | None:
        """Return the review of a commission, wherever its project is, or None."""

        def work() -> Review | None:
            name = f"{safe_segment(commission_id, 'commission id')}.md"
            for directory, project, year in self._review_directories():
                path = directory / name
                if path.is_file():
                    return self._review_from(path, project, year, with_content=True)
            return None

        return await self._read(work)

    async def delete_review(self, commission_id: str) -> bool:
        """Remove the review of a commission, as the user asks. Returns whether there was one."""

        def work() -> bool:
            name = f"{safe_segment(commission_id, 'commission id')}.md"
            for directory, _project, _year in self._review_directories():
                try:
                    (directory / name).unlink()
                except FileNotFoundError:
                    continue
                return True
            return False

        return await self._write(work)

    # -- what a task agent is handed --------------------------------------

    async def brief(self, project: str | None) -> str:
        """Return what the space adds to a task agent's instructions; empty when it holds nothing.

        The rules and the index of the space, then those of the commission's
        project, each within its own cap. The entries are not included: the
        task agent reads the ones it needs.
        """
        from config.nekoclaw_settings import (
            SPACE_BRIEF_GLOBAL_INDEX_MAX_TOKENS,
            SPACE_BRIEF_GLOBAL_RULES_MAX_TOKENS,
            SPACE_BRIEF_MAX_TOKENS,
            SPACE_BRIEF_PROJECT_INDEX_MAX_TOKENS,
            SPACE_BRIEF_PROJECT_RULES_MAX_TOKENS,
        )
        from config.prompts import prompts_nekoclaw_space as texts
        from utils.tokenize import count_tokens, take_lines_within_token_budget, truncate_to_tokens

        name = project or DEFAULT_PROJECT

        def work() -> tuple[str, list[tuple[str, str, int, bool]]]:
            # Heading, text, cap, and whether it is an index.
            paths = self._paths()

            def text_of(path: Path) -> str:
                return self._read_text(path) or ""

            return str(paths.root), [
                (texts.SPACE_BRIEF_GLOBAL_RULES_HEADER, text_of(paths.rules(None)), SPACE_BRIEF_GLOBAL_RULES_MAX_TOKENS, False),
                (texts.SPACE_BRIEF_GLOBAL_INDEX_HEADER, text_of(paths.index(None)), SPACE_BRIEF_GLOBAL_INDEX_MAX_TOKENS, True),
                (
                    texts.SPACE_BRIEF_PROJECT_RULES_HEADER.format(project=name),
                    text_of(paths.rules(name)),
                    SPACE_BRIEF_PROJECT_RULES_MAX_TOKENS,
                    False,
                ),
                (
                    texts.SPACE_BRIEF_PROJECT_INDEX_HEADER.format(project=name),
                    text_of(paths.index(name)),
                    SPACE_BRIEF_PROJECT_INDEX_MAX_TOKENS,
                    True,
                ),
            ]

        try:
            root, sections = await self._read(work)
        except (OSError, ValueError):
            logger.warning("the task agent space could not be read for a task agent's instructions", exc_info=True)
            return ""
        parts: list[str] = []
        for header, text, cap, is_index in sections:
            text = text.strip()
            if not text:
                continue
            if count_tokens(text) > cap:
                if is_index:
                    # Whole lines, so no line ends in half a link.
                    lines, _dropped = take_lines_within_token_budget(text.split("\n"), cap)
                    text = "\n".join(lines)
                text = f"{truncate_to_tokens(text, cap).rstrip()}\n{texts.SPACE_BRIEF_CUT_NOTE}"
            parts.append(f"{header}\n{text}")
        if not parts:
            return ""
        note = texts.SPACE_BRIEF_NOTE.format(root=root, project=name)
        return truncate_to_tokens("\n\n".join([note, *parts]), SPACE_BRIEF_MAX_TOKENS)

    async def contains(self, path: str) -> bool:
        """Whether a path lies inside the space, links resolved."""
        from nekoclaw.runtime.permissions import is_inside

        def work() -> bool:
            return is_inside(Path(path), self._paths().root)

        try:
            return await self._read(work)
        except (OSError, ValueError):
            return False

    # -- upkeep state ------------------------------------------------------

    async def read_state(self) -> dict[str, Any]:
        """Return what the space remembers about its own upkeep; empty when nothing is kept."""

        def work() -> dict[str, Any]:
            try:
                data = json.loads(self._paths().state_file().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
            return data if isinstance(data, dict) else {}

        return await self._read(work)

    async def write_state(self, **values: Any) -> None:
        from utils.file_utils import atomic_write_json

        def work() -> None:
            path = self._paths().state_file()
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            data.update(values)
            data["version"] = _STATE_FILE_VERSION
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, data, ensure_ascii=False, indent=2)

        await self._write(work)

    async def exists(self) -> bool:
        """Whether anything was ever kept in the space."""
        return await self._read(lambda: self._paths().root.is_dir())


def _move(source: Path, target: Path) -> None:
    """Move a directory, trying again for a moment while Windows says it is in use."""
    last: OSError | None = None
    for attempt in range(_WRITE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            last = exc
            if not source.exists():
                raise
            time.sleep(_WRITE_RETRY_SECONDS * (attempt + 1))
    try:
        # Another volume (a link in between): copied and then removed.
        shutil.move(str(source), str(target))
    except OSError:
        raise last if last is not None else OSError("the directory could not be moved") from None
