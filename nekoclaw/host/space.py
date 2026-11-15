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
import errno
import hashlib
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
# The names a directory has while it is copied to another volume, while what
# it was copied from is out of sight, and once that is known to be of no use
# any more. None is a project's name (a name cannot begin with a dot), and a
# directory whose name begins with a dot is not taken for a project. Only
# what has the last of the three names is ever removed by a later move.
_UNFINISHED_PREFIX = ".unfinished-"
_REMOVING_PREFIX = ".removing-"
_GONE_PREFIX = ".gone-"
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
    # A summary written to archive it at a time it could not be moved:
    # ``{"for": the last_commission_at it was written at, "summary", "line"}``.
    pending_archive: dict[str, Any] | None = None

    def kept_summary(self) -> tuple[str, str] | None:
        """Return the summary and the one line kept from a try to archive it that failed, while they hold.

        They hold as long as no commission was filed under the project
        since: after that it is another project that would be summed up.
        """
        kept = self.pending_archive
        if not isinstance(kept, dict) or kept.get("for") != self.last_commission_at:
            return None
        summary, line = kept.get("summary"), kept.get("line")
        if not isinstance(summary, str) or not summary.strip():
            return None
        return summary, line if isinstance(line, str) else ""

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


# What became of an entry that was filed: a new file, added to one, or there already.
_CREATED, _ADDED, _THERE = "created", "added", "there"

# What the target of a link is made of when it can be written as it is:
# letters and digits of any script, and the few marks the paths here have.
_PLAIN_TARGET = re.compile(r"[\w./~-]+")


def _link(target: str) -> str:
    """Write the target of a link so that it is one: in angle brackets unless it is plain.

    A project's name is part of the path its line points at, and may have
    spaces, brackets, ``#``, ``%`` and the like in it (``My App (v2)``).
    The path is written as it is and not percent-encoded: a line of an
    index is read by a task agent before anyone else, and it opens the
    path it finds there. A viewer still does not follow a target with
    ``#`` or ``%`` in it rightly.
    """
    return target if _PLAIN_TARGET.fullmatch(target) else f"<{target}>"


def _digest(content: str) -> str:
    """Return a short mark of a text that was filed, to tell later that the same text is filed again."""
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:12]


# The heading the host puts above what is added to an entry on a later day.
_DATED_HEADING = re.compile(r"(?m)^## \d{4}-\d{2}-\d{2}[ \t]*$")


def _listed(document: _Document, key: str) -> list[str]:
    """Return what a line of the front matter lists, separated by commas."""
    return [item.strip() for item in document.get(key).split(",") if item.strip()]


def _marks_of(document: _Document) -> list[str]:
    """Return the marks of the texts an entry was given.

    The ones it keeps in its front matter. An entry that keeps none (one
    the user wrote by hand, with or without front matter) is taken to have
    been given what stands between its dated headings: without that, the
    same text would be added to it every time it is filed.
    """
    marks = _listed(document, "texts")
    if marks:
        return marks
    return [_digest(part) for part in _DATED_HEADING.split(document.body) if part.strip()]


def _points_at(line: str, marks: tuple[str, str]) -> bool:
    """Whether a line of an index has one of the marks of a target in it."""
    return any(mark in line for mark in marks)


def _said_already(document: _Document, content: str) -> bool:
    """Whether an entry has ``content`` word for word, as what it said first or as one of its additions.

    Told by the marks the entry keeps of every text that was filed
    (``texts`` in its front matter), not by where its dated headings are:
    a note can have such a heading inside it, and a shorter note can be
    the end of a longer one. The text must still be there as well, so one
    the user took out by hand is added again. A text that is all the
    entry says is there already whatever the marks are: the entry may
    have been written by hand, or its text replaced in an editor. For an
    entry that keeps no marks see ``_marks_of``.
    """
    if document.body.strip() == content.strip():
        return True
    if _digest(content) not in _marks_of(document):
        return False
    # Still there as a text of its own, between blank lines or at an end of
    # the entry: found inside another sentence, it is not what was filed.
    alone = r"(?:\A|\n[ \t]*\n)" + re.escape(content.strip()) + r"(?:[ \t]*\n[ \t]*\n|\s*\Z)"
    return re.search(alone, document.body.strip()) is not None


def _index_marks(target: str) -> tuple[str, str]:
    """What a line of an index that points at ``target`` has in it, whichever way the target is written."""
    return f"]({target})", f"](<{target}>)"


def _index_line(title: str, target: str, description: str) -> str:
    title = _one_line(title).replace("[", "(").replace("]", ")")
    line = f"- [{title}]({_link(target)})"
    description = _one_line(description)
    return f"{line} — {description}" if description else line


class SpaceError(RuntimeError):
    """Something asked of the space cannot be done: an unknown entry, an unusable name."""


class _NotAsWritten(OSError):
    """A file was replaced, and what it holds afterwards is not what was written, or cannot be read."""


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
    def _text_to_change(path: Path) -> str:
        """Return the text of a file that is about to be changed; empty when there is no file.

        Raises OSError when the file is there and cannot be read (held open
        by another program, not text): taken for empty, it would be written
        over with the little that is being added.
        """
        try:
            return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        except FileNotFoundError:
            return ""
        except UnicodeError as exc:
            raise OSError(f"{path.name} is not text this can read; it is left as it is") from exc

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
            except OSError as exc:
                last = exc
                time.sleep(_WRITE_RETRY_SECONDS * (attempt + 1))
                continue
            # Replaced. From here on it is only looked at, never written
            # again: what it holds now may be what the user saved a moment
            # after, and writing the same text once more would put that out.
            for look in range(_WRITE_ATTEMPTS):
                try:
                    there = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    last = exc
                    time.sleep(_WRITE_RETRY_SECONDS * (look + 1))
                    continue
                if there == text:
                    return
                raise _NotAsWritten(f"{path.name} does not hold what was written")
            raise _NotAsWritten(f"{path.name} could not be read after it was written") from last
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
        so their change is not written over. So it is when the file does not
        hold what was written right after it was replaced: the change is
        made again to what is there then.
        """
        written = False
        for _attempt in range(_WRITE_ATTEMPTS):
            before = self._signature(path)
            text = self._text_to_change(path)
            changed = change(text)
            if changed == text:
                # Nothing to change, or it holds the change already.
                return written
            if self._signature(path) != before:
                continue
            try:
                self._put_text(path, changed)
            except _NotAsWritten:
                written = True
                continue
            return True
        raise OSError(f"{path.name} kept changing while it was being written")

    # -- the index --------------------------------------------------------

    def _index_add(self, index: Path, line: str, target: str) -> None:
        """Add a line to an index unless one already points at ``target``. New lines go first."""
        marks = _index_marks(target)

        def change(text: str) -> str:
            lines = text.split("\n") if text else []
            if any(mark in item for item in lines for mark in marks):
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

    def _index_swap(self, index: Path, old: str, line: str, new: str) -> None:
        """Put ``line``, which points at ``new``, in the place of the line that points at ``old``: in one write.

        Taking the one out and adding the other in two writes could stop
        between them, with neither line there. Without a line that points
        at ``old`` the new one is added like any other; when one points at
        ``new`` already, the old one is only taken out.
        """
        gone, there = _index_marks(old), _index_marks(new)

        def change(text: str) -> str:
            lines = text.split("\n") if text else []
            placed = any(_points_at(item, there) for item in lines)
            kept: list[str] = []
            for item in lines:
                if not _points_at(item, gone):
                    kept.append(item)
                elif not placed:
                    kept.append(line)
                    placed = True
            if not placed:
                position = next((number for number, item in enumerate(kept) if item.startswith("- ")), None)
                if position is None:
                    position = len(kept)
                    while position > 0 and not kept[position - 1].strip():
                        position -= 1
                kept.insert(position, line)
            result = "\n".join(kept)
            return result if result.endswith("\n") else result + "\n"

        self._edit_text(index, change)

    def _index_put_back(self, index: Path, line: str, target: str, position: int) -> None:
        """Put a line that was taken out of an index back where it was, unless one points at ``target`` again.

        Where it was, not first: the first lines of an index are the newest
        entries, and an index too long for a task agent's instructions is
        cut at its other end.
        """
        marks = _index_marks(target)

        def change(text: str) -> str:
            lines = text.split("\n") if text else []
            if any(_points_at(item, marks) for item in lines):
                return text
            lines.insert(min(max(position, 0), len(lines)), line)
            result = "\n".join(lines)
            return result if result.endswith("\n") else result + "\n"

        self._edit_text(index, change)

    def _index_set(self, index: Path, line: str, target: str) -> None:
        """Put a line into an index in place of the one that points at ``target``, or first."""
        marks = _index_marks(target)

        def change(text: str) -> str:
            lines = text.split("\n") if text else []
            for number, item in enumerate(lines):
                if any(mark in item for mark in marks):
                    lines[number] = line
                    return "\n".join(lines)
            return text

        if not self._edit_text(index, change):
            self._index_add(index, line, target)

    @staticmethod
    def _index_line_in(text: str, target: str) -> str | None:
        """Return the line of an index's text that points at ``target``, as it is written there, or None."""
        marks = _index_marks(target)
        return next((item for item in text.split(chr(10)) if any(mark in item for mark in marks)), None)

    def _index_remove(self, index: Path, target: str) -> None:
        marks = _index_marks(target)

        def change(text: str) -> str:
            if not text:
                return text
            return "\n".join(item for item in text.split("\n") if not any(mark in item for mark in marks))

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
        sources = tuple(_listed(document, "sources"))
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
    ) -> tuple[Entry, str]:
        """Keep one entry. Returns it and what became of it: a new file, added to one, or there already.

        An entry about the same topic is added to: it gets a section with
        today's date at its end, and what it said before stays. Only a
        text that is there word for word, as what the entry said first or
        as one of its additions, is not added again; one that merely
        occurs inside a longer text is.
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

            fresh_text: list[bool] = []

            def add(text: str) -> str:
                document = _parse_document(text)
                fresh = not _said_already(document, content)
                # Made a second time when the file had to be read again: what
                # was added the first time is still what this filing added.
                fresh_text[:] = [fresh or bool(fresh_text and fresh_text[0])]
                if fresh:
                    addition = f"## {today}\n\n{content}"
                    document.body = f"{document.body.rstrip()}\n\n{addition}\n"
                if not document.has_meta:
                    return document.body if fresh else text
                # Who filed it is noted even when the text is there already:
                # an entry two commissions filed is not one commission's own.
                known = _listed(document, "sources")
                another = bool(source) and source not in known
                # The text is in the entry now, added or there already, so
                # its mark is kept: one that was put there in an editor has
                # none yet, and would not be known again once the entry
                # says more than it.
                marks = _marks_of(document)
                unmarked = _digest(content) not in marks
                if not fresh and not another and not unmarked:
                    return text
                if fresh:
                    document.set("updated", today)
                if unmarked:
                    document.set("texts", ", ".join([*marks, _digest(content)]))
                if another:
                    document.set("sources", ", ".join([*known, source]))
                return _render_document(document)

            self._edit_text(path, add)
            # Its line in the index is left as it is (the user may have
            # reworded it); only a missing one is put back.
            self._index_add(
                paths.index(project),
                _index_line(existing.title, f"{paths.MEMORY_DIR}/{existing.id}.md", existing.description),
                f"{paths.MEMORY_DIR}/{existing.id}.md",
            )
            entry = self._entry_from(path, project, with_content=True)
            return entry or existing, _ADDED if fresh_text and fresh_text[0] else _THERE
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
                # A mark of every text filed; see ``_said_already``.
                ("texts", _digest(content)),
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
        return entry, _CREATED

    async def submit(self, commission: Commission, lesson: dict[str, Any]) -> str | None:
        """Keep what a task agent filed with ``note_lesson``; the ``LessonSink`` of the manager.

        ``feedback`` never arrives here: it is a task preference.
        """
        scope = str(lesson.get("scope") or "project").strip().lower()
        kind = str(lesson.get("category") or "")
        # How a project stands is the project's own, whatever scope came with it.
        everywhere = scope == "global" and _entry_type(kind) != "progress"
        project = None if everywhere else (commission.project or DEFAULT_PROJECT)
        try:
            entry, how = await self._write(
                lambda: self._file_entry(
                    project,
                    title=str(lesson.get("title") or ""),
                    content=str(lesson.get("content") or ""),
                    kind=kind,
                    source=commission.id,
                )
            )
        except (SpaceError, ValueError) as exc:
            return f"not kept: {exc}"
        except OSError as exc:
            logger.warning("a lesson could not be written into the task agent space (%s)", type(exc).__name__)
            return "not kept: the files of the task agent space could not be written"
        if self._ledger is not None:
            # On the commission's record too, so the board can show it.
            await self._ledger.append_event(
                commission.id,
                "lesson",
                {
                    **dict(lesson),
                    "entry": entry.id,
                    "project": project,
                    "created": how == _CREATED,
                    "added": how != _THERE,
                },
            )
        where = "the space of every project" if project is None else f"project {project}"
        if how == _CREATED:
            return f"kept in {where} as {entry.id}.md"
        if how == _ADDED:
            return f"added to {entry.id}.md in {where}"
        # Said as it is: the task agent is not told something was kept that was not.
        return f"already in {entry.id}.md in {where}, word for word; nothing was added"

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
            entry, how = await self._write(
                lambda: self._file_entry(
                    project, title=title, content=content, kind=kind, description=description, source=source
                )
            )
        except ValueError as exc:
            raise SpaceError(str(exc)) from exc
        return entry, how == _CREATED

    async def refile_entries(self, commission_id: str, source: str, target: str) -> list[str]:
        """Move what one commission alone filed under a project to another project. Returns the ids there.

        For a commission found to belong to a project after it ended: what
        it filed while it was one of ``_daily`` goes with it, or the
        project's index would not have it. An entry other commissions
        added to stays where it is, and so does every entry of theirs.
        The files are moved as they are, each under a name that is free,
        and so are their lines in the index: a line the user reworded goes
        along as it is, but for the file it points at. Raises OSError, with
        nothing moved, when the index they are listed in cannot be read.
        """

        def work() -> list[str]:
            paths = self._paths()
            self._revive(target)
            origin, destination = paths.memory_dir(source), paths.memory_dir(target)
            # Read before anything moves, and not taken for empty when it
            # cannot be read: a line the user reworded would be replaced by
            # one made anew, and theirs removed once the index reads again.
            self._text_to_change(paths.index(source))
            moved: list[str] = []
            for entry in self._entries(origin, source):
                if entry.sources != (commission_id,):
                    continue
                old = origin / f"{entry.id}.md"
                name = self._free_name(destination, entry.id)
                # Written where it goes before it is removed where it was,
                # and removed there only as the file that was copied: one
                # the user saved from an editor in between is copied again.
                same = copied = False
                for _attempt in range(_WRITE_ATTEMPTS):
                    before = self._signature(old)
                    text = self._read_text(old)
                    if text is None:
                        break
                    self._put_text(destination / f"{name}.md", text)
                    copied = True
                    same = self._signature(old) == before
                    if same:
                        break
                if text is None:
                    if copied:
                        # There a moment ago, gone or unreadable now: the
                        # copy made of it is not left behind with no line.
                        try:
                            (destination / f"{name}.md").unlink()
                        except OSError:
                            logger.warning("the copy of an entry that is gone could not be removed", exc_info=True)
                    continue
                link = f"{paths.MEMORY_DIR}/{name}.md"
                was = f"{paths.MEMORY_DIR}/{entry.id}.md"
                marks = _index_marks(was)
                taken: list[str] = []
                place: list[int] = []

                def take(
                    text: str, marks: tuple[str, str] = marks, taken: list[str] = taken, place: list[int] = place
                ) -> str:
                    lines = text.split(chr(10))
                    found = [number for number, item in enumerate(lines) if _points_at(item, marks)]
                    if found:
                        # Kept when this is made a second time and the line is gone by then.
                        taken[:] = [lines[number] for number in found]
                        place[:] = found[:1]
                    return chr(10).join(item for item in lines if not _points_at(item, marks))

                def put_back(taken: list[str] = taken, place: list[int] = place, was: str = was) -> None:
                    # Where it was, and never in the way of what else is to be done.
                    if not taken:
                        return
                    try:
                        self._index_put_back(paths.index(source), taken[0], was, place[0] if place else 0)
                    except OSError:
                        logger.warning("the line of an entry could not be put back into its index", exc_info=True)

                line = None
                try:
                    if same:
                        # Its line goes with it: taken out of the index as
                        # the index is at that moment, so one the user
                        # reworded while the file was copied goes along as
                        # they left it.
                        self._edit_text(paths.index(source), take)
                        line = taken[0] if taken else None
                    else:
                        line = self._index_line_in(self._text_to_change(paths.index(source)), was)
                except OSError:
                    # The index cannot be read any more, keeps being saved,
                    # or was written and could not be read back: the entry
                    # stays there as well. A line that was taken out is put
                    # back, and it is the line that goes along.
                    same = False
                    if taken:
                        line = taken[0]
                        put_back()
                if line is None:
                    line = _index_line(entry.title, link, entry.description)
                else:
                    for mark in marks:
                        line = line.replace(mark, f"]({_link(link)})")
                try:
                    self._index_add(paths.index(target), line, link)
                except OSError:
                    # The index of where it goes cannot be written: the
                    # entry stays where it was, listed there as it was, and
                    # the copy made of it is taken back.
                    put_back()
                    try:
                        (destination / f"{name}.md").unlink()
                    except OSError:
                        logger.warning("the copy of an entry that stays where it was could not be removed", exc_info=True)
                    raise
                moved.append(name)
                if same and self._signature(old) != before:
                    # Saved once more while its line was moved: what is at
                    # the target is no longer what the file says, so the
                    # file stays, and its line is put back.
                    same = False
                    put_back()
                if not same:
                    # It kept being saved: it stays where it was as well.
                    logger.warning("an entry copied to another project kept changing; it is left where it was too")
                    continue
                try:
                    old.unlink()
                except OSError:
                    logger.warning("an entry moved to another project could not be removed where it was", exc_info=True)
                    # Still there, so its line is put back.
                    put_back()
            return moved

        return await self._write(work)

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
        archive_year: str | None = None,
        title: str | None = None,
        description: str | None = None,
        kind: str | None = None,
        content: str | None = None,
    ) -> Entry | None:
        """Change an entry as the user asks; what is given replaces what was there.

        Returns the entry, or None when there is none of that id. Its line
        in the index is rewritten when the title or the description changed.
        An entry of an archived project is named as for ``list_entries``
        and changed where it is: the project stays archived.
        """

        def work() -> Entry | None:
            scope = self._scope(project, archive_year)
            path = scope / SpacePaths.MEMORY_DIR / f"{safe_segment(entry_id)}.md"
            if not path.is_file():
                return None

            def change(text: str) -> str:
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
                    # The marks it has stay: what was filed before may still
                    # be in the new text, word for word. The new text as a
                    # whole gets a mark too.
                    marks = _marks_of(document)
                    document.body = content
                    if _digest(content) not in marks:
                        marks = [*marks, _digest(content)]
                    document.set("texts", ", ".join(marks))
                document.set("updated", _date(self._clock()))
                return _render_document(document)

            # Made to the file as it is at the moment it is replaced: what
            # was saved from an editor meanwhile stays, but for what is
            # changed here.
            self._edit_text(path, change)
            entry = self._entry_from(path, project, with_content=True)
            if entry is not None and (title is not None or description is not None):
                target = f"{SpacePaths.MEMORY_DIR}/{entry.id}.md"
                self._index_set(
                    scope / SpacePaths.INDEX_NAME, _index_line(entry.title, target, entry.description), target
                )
            return entry

        return await self._write(work)

    async def delete_entry(
        self, entry_id: str, project: str | None = None, *, archive_year: str | None = None
    ) -> bool:
        """Remove an entry and its line in the index, as the user asks. Returns whether there was one.

        An entry of an archived project is named as for ``list_entries``.
        """

        def work() -> bool:
            scope = self._scope(project, archive_year)
            name = safe_segment(entry_id)
            path = scope / SpacePaths.MEMORY_DIR / f"{name}.md"
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            self._index_remove(scope / SpacePaths.INDEX_NAME, f"{SpacePaths.MEMORY_DIR}/{name}.md")
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
            data = json.loads((directory / SpacePaths.PROJECT_FILE).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None, True
        except (OSError, ValueError):
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

    def _archived_projects(self) -> list[Project]:
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
        # Read once, and not at all filed under when its record cannot be
        # read: a commission must not start without where its files are.
        project, known = self._project_to_change(directory)
        if not known:
            project = Project(name=name, created_at=now)
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
        target.parent.mkdir(parents=True, exist_ok=True)
        _move(source, target)
        try:
            # As its file has it now, not as it was listed a while ago.
            current, _known = self._project_to_change(target)
        except OSError:
            # Its record cannot be read: it is back, and its file as it was.
            logger.warning("the file of a project brought back cannot be read; it is left as it is")
            restored = replace(project, status=PROJECT_ACTIVE, archived_at=None, archive_year=None, directory=target.name)
        else:
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
            self._index_swap(
                paths.index(None), f"archive/{project.archive_year}/{source.name}/{paths.SUMMARY_NAME}", line, link
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
    ) -> Project | None:
        """Return the project of a name for a commission filed under it now.

        A project of the name, however the name is spelled, is used; one
        that is not there is made; the commission is counted for it. One
        that is archived is moved back, or, with ``unless_archived``, left
        where it is and None is returned. Looking and what follows are one
        step under the lock archiving runs under, so a project archived a
        moment ago is seen as archived.
        """
        safe_segment(name, "project")

        def work() -> Project | None:
            wanted = normalize_name(name)
            active = next((item for item in self._active_projects() if normalize_name(item.name) == wanted), None)
            if active is not None:
                return self._ensure_project(active.name, description=description, workdir=workdir, at=at)
            archived = self._archived_of(name)
            if archived is not None:
                if unless_archived:
                    return None
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
            if summary.strip():
                self._put_text(source / paths.SUMMARY_NAME, redact(summary).strip() + "\n")
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
            _move(source, target)
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
            archive_link = f"archive/{year}/{target.name}/{paths.SUMMARY_NAME}"
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

    # -- reviews ----------------------------------------------------------

    def _review_from(self, path: Path, project: str, archive_year: str | None, *, with_content: bool) -> Review | None:
        text = self._read_text(path)
        if text is None:
            return None
        document = _parse_document(text)
        entries = tuple(_listed(document, "entries"))
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

    async def list_reviews(
        self, project: str | None = None, *, with_content: bool = False, limit: int | None = None
    ) -> list[Review]:
        """Return the reviews of a project, or of every project, newest first; the first ``limit`` when given.

        Without their text unless ``with_content``: the text is read of the
        ones returned only.
        """

        def work() -> list[Review]:
            found: list[tuple[Review, Path]] = []
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
                        found.append((review, item))
            found.sort(key=lambda pair: pair[0].ended, reverse=True)
            if limit is not None:
                found = found[: max(limit, 0)]
            if not with_content:
                return [review for review, _path in found]
            whole = (
                self._review_from(path, review.project, review.archive_year, with_content=True)
                for review, path in found
            )
            return [review for review in whole if review is not None]

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

        def archived_meanwhile() -> bool:
            return name != DEFAULT_PROJECT and not self._paths().project_dir(name).is_dir()

        try:
            if await self._read(archived_meanwhile):
                # A commission of a project that was idle long is taken up
                # again at the moment the project is archived: the project
                # is brought back, or the task agent would start without
                # its rules and its index.
                await self._write(lambda: self._revive(name))
        except (OSError, ValueError):
            logger.warning("a project of the task agent space could not be brought back", exc_info=True)

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

    async def named_in(self, command: str) -> bool:
        """Whether a shell command names the space: the name of its directory occurs in it.

        Only the directory's own name (``task_agent_space``), in any case,
        so that a path from any starting point is seen: with the drive
        written another way, from the home directory, relative to a
        working directory above the space, or after the command changed
        into the directory above. The odd command that only mentions the
        name is refused with them. A command that puts the name together
        itself (a variable, a wildcard) is not seen.
        """

        def work() -> bool:
            return self._paths().root.name.casefold() in command.casefold()

        try:
            return await self._read(work)
        except (OSError, ValueError):
            return False

    # -- upkeep state ------------------------------------------------------

    @staticmethod
    def _state_in(path: Path) -> dict[str, Any]:
        """Return what the state file holds; empty when there is none, or it is not what this writes.

        Raises OSError when the file is there and cannot be read (held open
        by another program for a moment). Taken for empty, the next write
        would drop what it holds: when the space was last tidied, how far
        the cleaning got and what it is to try again, from when on
        commissions are reviewed.
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except ValueError:
            # Not JSON, or not in the encoding this writes: begun anew.
            return {}
        return data if isinstance(data, dict) else {}

    async def read_state(self) -> dict[str, Any]:
        """Return what the space remembers about its own upkeep; empty when nothing is kept.

        Raises OSError when it is kept and cannot be read; see ``_state_in``.
        """
        return await self._read(lambda: self._state_in(self._paths().state_file()))

    async def write_state(self, **values: Any) -> None:
        """Keep values among what the space remembers about its own upkeep.

        Raises OSError, with nothing written, when what is kept cannot be read.
        """
        from utils.file_utils import atomic_write_json

        def work() -> None:
            path = self._paths().state_file()
            data = self._state_in(path)
            data.update(values)
            data["version"] = _STATE_FILE_VERSION
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, data, ensure_ascii=False, indent=2)

        await self._write(work)

    async def exists(self) -> bool:
        """Whether anything was ever kept in the space."""
        return await self._read(lambda: self._paths().root.is_dir())


def _is_project(directory: Path) -> bool:
    """Whether a directory among the projects is one.

    Not one whose name begins with a dot: a copy still being made, or what
    is left of a directory that was copied elsewhere (``_copy_across``).
    """
    return directory.is_dir() and not directory.name.startswith(".")


def _free_beside(source: Path, prefix: str) -> Path:
    """Return a name next to ``source`` that begins with ``prefix`` and that nothing has."""
    free = source.with_name(f"{prefix}{source.name}")
    number = 2
    while free.exists():
        free = source.with_name(f"{prefix}{source.name}-{number}")
        number += 1
    return free


def _set_aside_name(source: Path) -> Path:
    """Return a name next to ``source`` that nothing has, for it to be out of sight under.

    What an earlier move left under such a name is not touched: a move
    that was cut off before it had looked at what it set aside may have
    left the only copy of what the user saved last there.
    """
    return _free_beside(source, _REMOVING_PREFIX)


def _remove_set_aside(aside: Path, source: Path) -> None:
    """Remove a directory that was set aside and is known to be of no use any more.

    Given the name of what can go first, in one step: removing can stop
    half way, and what is left is then told from what must stay. What
    earlier moves left under such a name is cleared away with it.
    """
    for left in source.parent.glob(f"{_GONE_PREFIX}{source.name}*"):
        shutil.rmtree(left, ignore_errors=True)
    gone = _free_beside(source, _GONE_PREFIX)
    try:
        os.replace(aside, gone)
    except OSError:
        logger.warning("a directory that can go could not be given the name of one", exc_info=True)
        return
    shutil.rmtree(gone, ignore_errors=True)


def _bring_up_to(copy: Path, original: Path) -> None:
    """Make a copy of a directory hold what the original holds now: files written anew, and none that are gone.

    Raises OSError when the copy does not hold the same files in the end;
    the original is then the only place that has them all.
    """
    # What is a file in the one and a directory in the other is taken out
    # of the copy first: copied over a directory of its name, a file would
    # land inside it.
    for directory, directories, names in os.walk(original):
        inside = copy / Path(directory).relative_to(original)
        for name in names:
            if (inside / name).is_dir() and not (inside / name).is_symlink():
                shutil.rmtree(inside / name)
        for name in directories:
            if (inside / name).is_file() or (inside / name).is_symlink():
                (inside / name).unlink()
    shutil.copytree(original, copy, dirs_exist_ok=True)
    held = {name: size for name, _written, size in _files_of(original)}
    for name, _written, _size in _files_of(copy):
        if name not in held:
            (copy / name).unlink()
    if {name: size for name, _written, size in _files_of(copy)} != held:
        raise OSError(f"the copy of {original.name} does not hold what was saved last")


def _files_of(root: Path) -> list[tuple[str, int, int]]:
    """Return what a directory holds: every file's path in it, when it was last written and its size.

    Raises OSError when a directory in it cannot be listed or a file
    cannot be looked at: left out, two such lists could be the same though
    the directories are not.
    """

    def fail(error: OSError) -> None:
        raise error

    found: list[tuple[str, int, int]] = []
    for directory, _directories, names in os.walk(root, onerror=fail):
        for name in names:
            path = Path(directory) / name
            stat = path.stat()
            found.append((path.relative_to(root).as_posix(), stat.st_mtime_ns, stat.st_size))
    return sorted(found)


def _copy_across(source: Path, target: Path) -> None:
    """Move a directory to another volume, so that it is whole under its name somewhere at every moment.

    1. Copied whole under another name on the target's volume. A copy that
       stops half way (the volume is full) is removed again.
    2. The copy is given the target's name. From here on the directory is
       whole in both places.
    3. The source is renamed out of sight where it is. That is one step on
       its own volume: it happens whole or not at all. If it cannot be done
       (a file in it is held open), the copy is taken out of sight again
       and removed, and the source stays.
    4. What was set aside is removed. Before that it is looked at once
       more: when a file in it was saved while it was copied, it is put
       back, the copy is taken back, and OSError is raised. Only once it
       is known to hold nothing the target lacks is it given a name that
       says it can go, and removed; removing can stop half way, and what
       is left then has a name that is no project's. Stopped before that,
       it keeps the name it was set aside under, which nothing removes.

    So neither place ever has part of the directory under its name:
    removing the source under its own name could leave half of an active
    project behind, which would be taken for the project itself. And there
    is no moment at which the directory is under its name in neither
    place. A process stopped anywhere on the way leaves it whole at the
    source, at the target, or at both, and whatever has a name that begins
    with a dot is a leftover nothing depends on. (Setting the source aside
    before the copy has its name would, stopped there, leave both out of
    sight, and the next move would remove them as leftovers.)
    """
    unfinished = target.with_name(f"{_UNFINISHED_PREFIX}{target.name}")
    shutil.rmtree(unfinished, ignore_errors=True)
    before = _files_of(source)
    try:
        shutil.copytree(source, unfinished)
        os.replace(unfinished, target)
    except BaseException:
        shutil.rmtree(unfinished, ignore_errors=True)
        raise

    def take_back() -> None:
        try:
            os.replace(target, unfinished)
        except OSError:
            logger.warning("the copy of a project that stays where it was could not be taken back", exc_info=True)
        else:
            shutil.rmtree(unfinished, ignore_errors=True)

    aside = _set_aside_name(source)
    try:
        os.replace(source, aside)
    except BaseException:
        take_back()
        raise
    # Out of sight, nothing is saved into it any more. A file the user saved
    # from an editor while it was copied is not in the copy: the directory
    # goes back to where it was, and the copy is taken back.
    try:
        after: list[tuple[str, int, int]] | None = _files_of(aside)
    except OSError:
        # Not known to be what was copied: taken for changed.
        after = None
    if after != before:
        try:
            os.replace(aside, source)
        except OSError:
            # It cannot be put back (something has its name by now). What
            # the user saved last is only in what was set aside, and nothing
            # is saved into that any more: the copy is brought up to it, and
            # the move is done after all. If that cannot be done either,
            # what was set aside stays as it is: nothing removes it.
            logger.warning("a project that changed while it was copied could not be put back", exc_info=True)
            _bring_up_to(target, aside)
        else:
            take_back()
            raise OSError(f"{source.name} was changed while it was copied to another volume; it stays where it was")
    # Only now is it known that the target has all of it.
    _remove_set_aside(aside, source)


def _move(source: Path, target: Path) -> None:
    """Move a directory by renaming it, trying again for a moment while Windows says it is in use.

    A directory that cannot be renamed stays where it is, whole, and the
    error is raised. It is not copied and then removed: with a file in it
    held open, the removing would stop half way, and the project would be
    in two places with neither of them complete. Only when the target is on
    another volume (a link somewhere in between), where renaming cannot
    work at all, is it copied.
    """
    last: OSError | None = None
    for attempt in range(_WRITE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            last = exc
            if exc.errno == errno.EXDEV:
                _copy_across(source, target)
                return
            if not source.exists():
                raise
            time.sleep(_WRITE_RETRY_SECONDS * (attempt + 1))
    raise last if last is not None else OSError("the directory could not be moved")
