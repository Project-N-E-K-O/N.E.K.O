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

"""Low-level file, index, rule, and state I/O mixin for ``TaskAgentSpace``.

Method-only mixin: every instance attribute is initialized in
``TaskAgentSpace.__init__`` (``nekopower.host.space.manager``).
"""

from __future__ import annotations

import asyncio
import json
import time

from collections.abc import Callable
from pathlib import Path
from typing import Any
from nekopower.host.paths import SpacePaths

from .models import logger, T, _STATE_FILE_VERSION, _WRITE_ATTEMPTS, SpaceError, _NotAsWritten
from .markdown import (
    _one_line,
    _plain_lines,
    _points_at,
    _with_line_first,
    _lines_as_text,
    _index_marks,
    _index_line,
)

# Late-binding read point for symbols that tests rebind on
# ``nekopower.host.space`` via ``monkeypatch.setattr`` / ``patch``. Do NOT
# from-import those names here: a from-import snapshots the value at
# import time and the facade patch would silently miss this module.
from nekopower.host import space as _space_facade


class StorageMixin:
    """Low-level locking, file I/O, index manipulation, rules, and upkeep state methods."""

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

    async def _write_judged(self, work: Callable[[bool], T]) -> T:
        """Run blocking work that judges notes, told whether files outside the working directories may be looked at.

        Like ``_write``. Whether they may is asked once its turn has
        come, not before: the user can change that setting while a write
        waits for the ones before it, and what they took back is not
        used.
        """
        from utils.asyncio_executor import to_thread_through_cancellation

        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            allowed = await self._outside_allowed()
            return await to_thread_through_cancellation(work, allowed)

    async def _outside_allowed(self) -> bool:
        """Whether the user lets task agents read outside the working directories without being asked.

        Asked every time it matters, so that it goes by the setting as it
        is at that moment: a file out there counts as what an entry rests
        on, and is looked at, only while a task agent may read it unasked.
        False when nobody can say.
        """
        if self._outside_readable is None:
            return False
        try:
            return bool(await self._outside_readable())
        except Exception:  # noqa: BLE001 - not knowing is not being allowed
            logger.warning("whether task agents may read outside the working directory could not be told", exc_info=True)
            return False

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
    def _put_text(path: Path, text: str) -> bool:
        """Replace a file atomically and check that what is there is what was written.

        On Windows a file held open by another program (an editor, a virus
        scanner) refuses to be replaced for a moment: tried again a few times.

        Returns whether it was looked at afterwards. A file that was
        replaced and cannot be read for a while (a virus scanner looks at
        every new file) is written all the same: that says nothing against
        what it holds, and it is not said to have failed. Raises
        ``_NotAsWritten`` when it holds something else.
        """
        from utils.file_utils import atomic_write_text

        # Written in text mode, which makes the line ends the platform's own.
        text = _plain_lines(text)
        last: Exception | None = None
        for attempt in range(_WRITE_ATTEMPTS):
            try:
                atomic_write_text(path, text)
            except OSError as exc:
                last = exc
                time.sleep(_space_facade._WRITE_RETRY_SECONDS * (attempt + 1))
                continue
            # Replaced. From here on it is only looked at, never written
            # again: what it holds now may be what the user saved a moment
            # after, and writing the same text once more would put that out.
            for look in range(_WRITE_ATTEMPTS):
                try:
                    there = path.read_text(encoding="utf-8")
                except UnicodeError as exc:
                    # Not text: not what was written.
                    raise _NotAsWritten(f"{path.name} does not hold what was written") from exc
                except OSError as exc:
                    last = exc
                    time.sleep(_space_facade._WRITE_RETRY_SECONDS * (look + 1))
                    continue
                if there == text:
                    return True
                raise _NotAsWritten(f"{path.name} does not hold what was written")
            logger.warning(
                "a file of the task agent space could not be read back after it was written (%s)",
                type(last).__name__,
            )
            return False
        raise last if last is not None else OSError("the file could not be written")

    @staticmethod
    def _signature(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def _edit_text(
        self, path: Path, change: Callable[[str], str], wrote: Callable[[str], None] | None = None
    ) -> bool:
        """Change a text file by a function of its text. Returns whether it changed.

        Read, changed and written back; when the file was written by someone
        else in between (the user saving it in an editor), it is read again,
        so their change is not written over. So it is when the file holds
        something else right after it was replaced: the change is made
        again to what is there then. A file that could not be looked at
        after it was replaced is read once more, see ``_holds``. ``wrote``
        is told each text that was written, of the tries that were.
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
                seen = self._put_text(path, changed)
            except _NotAsWritten:
                # Something else was saved right after it.
                written = True
                if wrote is not None:
                    wrote(changed)
                continue
            if wrote is not None:
                wrote(changed)
            # Told right here, whichever try this was: the last one too is
            # written when it was replaced and nothing says otherwise.
            if seen or self._holds(path, changed):
                return True
            # Something else was saved while it could not be looked at.
            written = True
        raise OSError(f"{path.name} kept changing while it was being written")

    def _holds(self, path: Path, text: str) -> bool:
        """Whether a file that was replaced and could not be looked at holds ``text``, as far as can be told.

        Read once more. When it holds what was written, the change is in
        it and is not made a second time: not every change is one that
        leaves alone a text that has it already. One that still cannot be
        read (held by another program) is taken to hold it. So is one that
        is gone: it was taken away after it was written, and making the
        change again to an empty file would bring back a piece of what was
        removed. One that is not text does not hold it: something else was
        saved over it.
        """
        try:
            there = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        except UnicodeError:
            return False
        except OSError:
            return True
        return there == _plain_lines(text)

    # -- the index --------------------------------------------------------

    def _index_add(self, index: Path, line: str, target: str) -> None:
        """Add a line to an index unless one already points at ``target``. New lines go first."""
        marks = _index_marks(target)

        def change(text: str) -> str:
            lines = text.split("\n") if text else []
            if any(_points_at(item, marks) for item in lines):
                return text
            return _lines_as_text(_with_line_first(lines, line))

        self._edit_text(index, change)

    def _index_swap(self, index: Path, old: str, line: str, new: str, *, below: bool = False) -> None:
        """Put ``line``, which points at ``new``, in the place of the line that points at ``old``: in one write.

        Taking the one out and adding the other in two writes could stop
        between them, with neither line there. Without a line that points
        at ``old`` the new one is added like any other; when one points at
        ``new`` already, the old one is only taken out.

        With ``below``, ``old`` is a directory, written with the ``/`` it
        ends in, and the line to go is the one that points at anything in it.
        """
        gone = (f"]({old}", f"](<{old}") if below else _index_marks(old)
        there = _index_marks(new)

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
            return _lines_as_text(kept if placed else _with_line_first(kept, line))

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
            return _lines_as_text(lines)

        self._edit_text(index, change)

    def _index_set(self, index: Path, line: str, target: str) -> None:
        """Put a line into an index in place of the one that points at ``target``, or first."""
        marks = _index_marks(target)

        def change(text: str) -> str:
            lines = text.split("\n") if text else []
            for number, item in enumerate(lines):
                if _points_at(item, marks):
                    lines[number] = line
                    return "\n".join(lines)
            return text

        if not self._edit_text(index, change):
            self._index_add(index, line, target)

    def _index_retitle(self, index: Path, target: str, title: str, description: str) -> tuple[str, str] | None:
        """Have the line of an index that points at ``target`` call it ``title``; the rest stays. Returns it as it was and is.

        What follows the link may have been reworded by the user, and is
        theirs. Without such a line none is added (the user may have taken
        it out), and None is returned.
        """
        marks = _index_marks(target)
        plain = _one_line(title).replace("[", "(").replace("]", ")")
        # Of each text a try made, the line it replaced and the line it put.
        tried: dict[str, tuple[str, str]] = {}
        swapped: list[tuple[str, str]] = []

        def change(text: str) -> str:
            lines = text.split("\n") if text else []
            for number, item in enumerate(lines):
                if _points_at(item, marks):
                    link = item.find("](")
                    opening = item.rfind("[", 0, link)
                    if opening < 0:
                        lines[number] = _index_line(title, target, description)
                    else:
                        lines[number] = item[: opening + 1] + plain + item[link:]
                    result = "\n".join(lines)
                    tried[result] = (item, lines[number])
                    return result
            return text

        # Only tries that were written count, and of them the last: what it
        # replaced is the line as it was last saved by anyone else.
        self._edit_text(index, change, wrote=lambda text: swapped.append(tried[text]))
        return swapped[-1] if swapped else None

    def _index_reline(self, index: Path, was: str, now: str) -> None:
        """Put ``now`` in the place of a line of an index that is ``was`` to the letter; one that says anything else stays."""

        def change(text: str) -> str:
            lines = text.split("\n") if text else []
            return "\n".join(now if item == was else item for item in lines)

        self._edit_text(index, change)

    @staticmethod
    def _index_line_in(text: str, target: str) -> str | None:
        """Return the line of an index's text that points at ``target``, as it is written there, or None."""
        marks = _index_marks(target)
        return next((item for item in text.split("\n") if _points_at(item, marks)), None)

    def _index_remove(self, index: Path, target: str, *, told: bool = False) -> str | None:
        """Take the lines that point at ``target`` out of an index. Returns the first, as it was when it was taken out, or None.

        Told only when ``told`` is given. As it was in the text the last
        write that took it out was made from: a line the user reworded
        meanwhile is returned as they worded it.
        """
        marks = _index_marks(target)
        # Of each text a try made, the line it took out.
        tried: dict[str, str] = {}
        removed: list[str] = []

        def change(text: str) -> str:
            if not text:
                return text
            lines = text.split("\n")
            gone = [item for item in lines if _points_at(item, marks)]
            result = "\n".join(item for item in lines if not _points_at(item, marks))
            if gone:
                tried[result] = gone[0]
            return result

        if not index.exists():
            return None
        if told:
            self._edit_text(index, change, wrote=lambda text: removed.append(tried[text]))
        else:
            self._edit_text(index, change)
        return removed[-1] if removed else None

    # -- scopes and rules --------------------------------------------------

    def _scope(self, project: str | None, archive_year: str | None = None) -> Path:
        paths = self._paths()
        if archive_year is not None:
            if project is None:
                raise SpaceError("an archived project needs its directory")
            # ``project`` is the directory's name here (``Project.directory``).
            return paths.archived_dir(archive_year, project)
        return paths.scope_dir(project)

    # -- for the console: rules and indexes -------------------------------

    async def read_rules(self, project: str | None = None, *, archive_year: str | None = None) -> str:
        """Return the rules of a project or of every task agent; with ``archive_year``, of an archived project."""
        return await self._read(
            lambda: self._read_text(self._scope(project, archive_year) / SpacePaths.RULES_NAME) or ""
        )

    async def write_rules(self, text: str, project: str | None = None) -> None:
        """Write the rules as the user gave them. The host itself never calls this."""

        def work() -> None:
            # Archived a moment ago: brought back, not begun anew beside the archived one.
            self._revive(project)
            self._put_text(self._paths().rules(project), text)

        await self._write(work)

    async def read_index(self, project: str | None = None, *, archive_year: str | None = None) -> str:
        """Return the index of a project or of every task agent; with ``archive_year``, of an archived project."""
        return await self._read(
            lambda: self._read_text(self._scope(project, archive_year) / SpacePaths.INDEX_NAME) or ""
        )

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
            data = json.loads(path.read_text(encoding="utf-8-sig"))
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
