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

"""The files a repository keeps for agents, listed for a task agent that works in it.

Many repositories carry files written for coding agents: ``AGENTS.md``,
``CLAUDE.md``, rules for one editor or another, records of decisions. A task
agent that works in such a repository is told that they are there: one line
for each, its path and its size, after a sentence that says what they are.

That is all the host does with them. It reads none of them, and none of them
becomes part of a task agent's instructions: a repository may be one somebody
else wrote (a clone, a branch under review), and what its files say is what
its author wants an agent to do. The task agent reads the ones that bear on
its work, as reference material; the commission and the user come first.

Which files count, and how many lines the list may have, is in
``config/nekoclaw_settings.py`` (``REPO_DOCUMENTS`` and what follows it); the
sentence is in ``config/prompts/prompts_nekoclaw_brain.py``.

``listing`` is the one function the host calls: it takes a working
directory and returns the text, empty when there is nothing to list. It
reads the file system, so it is called in a worker thread. Where the text
goes is the caller's business: today it is part of the message that opens
a commission in its session (``TaskAgentManager._opening``).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# How an entry of ``REPO_DOCUMENTS`` is listed: the file itself, every file
# of a directory, or only how many files a directory has.
FILE, FILES, COUNT = "file", "files", "count"


@dataclass(frozen=True, slots=True)
class Document:
    """One line of the list: a file, or a directory that is only counted."""

    # Relative to the repository's root, with forward slashes; a directory ends in one.
    path: str
    # In bytes; None for a directory that is counted.
    size: int | None = None
    # How many files a directory that is counted has.
    files: int | None = None
    # Whether there is more than this says. Of a file: its directory is
    # known to have more files than are taken into the list, so at least
    # one more is left out. Of a directory that is counted: the number is
    # the least it can be (it has more files than are counted, or was
    # not looked at to its end).
    more: bool = False
    # Of a file: its directory was not looked at to its end. Nothing is
    # known to be left out by that; but a number of files that are left
    # out for another reason is then the least it can be.
    cut: bool = False


def repository_root(workdir: Path, *, upward: bool = True) -> Path:
    """Return the root of the repository a directory is in, or the directory itself when it is in none.

    The nearest directory, going up, that has a ``.git`` (a directory in
    a clone, a file in a worktree or a submodule). Nothing is read but
    whether that name exists, and no further up than
    ``REPO_ROOT_SEARCH_MAX_LEVELS`` directories.

    Going up starts from where the directory really is: one reached
    through a link is in the repository the link leads into, not in
    whatever the link itself lies in.

    With ``upward`` False nothing above the directory is looked at: it
    is its own root. For a directory nobody chose to work in, see
    ``listing``.
    """
    from config.nekoclaw_settings import REPO_ROOT_SEARCH_MAX_LEVELS

    try:
        start = workdir.resolve()
    except (OSError, RuntimeError):
        start = workdir
    if not upward:
        return start
    current = start
    for _level in range(REPO_ROOT_SEARCH_MAX_LEVELS + 1):
        try:
            if (current / ".git").exists():
                return current
        except OSError:
            break
        if current.parent == current:
            break
        current = current.parent
    return start


def _inside(path: Path | str, root: Path) -> bool:
    """Whether a path is in the repository once links are resolved. False when that cannot be told."""
    from nekoclaw.runtime.permissions import is_inside

    try:
        return is_inside(Path(path), root)
    except (OSError, ValueError):
        return False


def _files_of(directory: Path, root: Path | None = None) -> tuple[list[Path], bool, bool]:
    """Return the first files directly in a directory, by name; whether it has more; whether it was not looked at to its end.

    The second says that more files were seen than are returned. The
    third says only that not every name was looked at, or that one could
    not be told to be a file or not: what was left may be files or may
    be none, so it is no proof of a file that is not listed.

    With ``root``, a link that leads out of that directory is no file of
    the repository and is left out: its name and size would be those of
    something the repository's author pointed at, not of something they
    wrote.

    Empty when it cannot be read. The first
    ``REPO_DOCUMENT_DIRECTORY_MAX_FILES`` by name: a directory fuller than
    that is no set of rules, and what is listed of it is its beginning,
    the same every time and not whatever the file system happens to give
    first. Which those are is known once every name was seen; no more
    than ``REPO_DOCUMENT_DIRECTORY_MAX_SCANNED`` names are, and of a
    directory fuller than that the first of the ones that were seen.
    """
    from config.nekoclaw_settings import REPO_DOCUMENT_DIRECTORY_MAX_FILES, REPO_DOCUMENT_DIRECTORY_MAX_SCANNED

    names: list[str] = []
    cut = False
    try:
        with os.scandir(directory) as items:
            for seen, item in enumerate(items):
                if seen >= REPO_DOCUMENT_DIRECTORY_MAX_SCANNED:
                    cut = True
                    break
                try:
                    if not item.is_file():
                        continue
                    if root is not None and item.is_symlink() and not _inside(item.path, root):
                        continue
                    names.append(item.name)
                except OSError:
                    # Not known to be a file or not: the directory was
                    # not looked at whole.
                    cut = True
    except OSError:
        return [], False, False
    names.sort()
    more = len(names) > REPO_DOCUMENT_DIRECTORY_MAX_FILES
    return [directory / name for name in names[:REPO_DOCUMENT_DIRECTORY_MAX_FILES]], more, cut


def _size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def documents(root: Path) -> list[Document]:
    """Return what a repository keeps for agents, in the order of ``REPO_DOCUMENTS``. Reads names and sizes only.

    Only what is in the repository: a file or a directory of these names
    that is a link out of it (a repository that was cloned may have
    ``.agent/rules`` point at any directory of the machine) is not
    listed, and neither is a link in such a directory that leads out.
    """
    from config.nekoclaw_settings import REPO_DOCUMENTS

    found: list[Document] = []
    for kind, name in REPO_DOCUMENTS:
        path = root.joinpath(*name.split("/"))
        try:
            if not _inside(path, root):
                continue
            if kind == FILE:
                if path.is_file():
                    found.append(Document(name, size=_size(path)))
            elif kind == FILES:
                # Only files that were seen and are not taken: where the
                # directory was merely not looked at to its end, nothing is
                # known to be left out.
                files, more, cut = _files_of(path, root)
                found.extend(Document(f"{name}/{item.name}", size=_size(item), more=more, cut=cut) for item in files)
            elif kind == COUNT:
                files, more, cut = _files_of(path, root)
                if files:
                    found.append(Document(f"{name}/", files=len(files), more=more or cut))
        except OSError:
            continue
    return found


# The character that begins an escape in what ``_shown`` writes.
_ESCAPE = chr(92)


def _shown(text: str, most: int | None = None) -> str | None:
    """Return a path as one line of text that still says what the path is; None for one longer than a path may be.

    What a file is called is the repository's author's to choose, and on
    most file systems a name may hold line ends. Written out as it is,
    such a name would end its line of the list and go on as text of its
    own in a task agent's instructions. A line end, and whatever else is
    no printable character, is written the way Python writes it inside a
    string, and so is the character that begins such an escape. The
    line stays one line, and the path can still be read off it.

    A path that takes more than ``most`` tokens written so
    (``REPO_DOCUMENT_PATH_MAX_TOKENS`` unless said otherwise) is not
    written at all. Cut short it would name another file or none, and
    the cut could fall inside an escape.
    """
    from config.nekoclaw_settings import REPO_DOCUMENT_PATH_MAX_TOKENS
    from utils.tokenize import count_tokens

    written: list[str] = []
    for char in str(text):
        if char == _ESCAPE:
            written.append(_ESCAPE * 2)
        elif char.isprintable():
            written.append(char)
        else:
            written.append(char.encode("unicode_escape").decode("ascii"))
    shown = "".join(written)
    return shown if count_tokens(shown) <= (most or REPO_DOCUMENT_PATH_MAX_TOKENS) else None


def _size_text(size: int | None) -> str:
    if size is None:
        return "size unknown"
    if size < 1024:
        return f"{size} B"
    if size < 10 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024:
        return f"{round(size / 1024)} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def render(root: Path, found: list[Document]) -> str:
    """Return the list as a task agent is given it; empty when there is nothing to list.

    The sentence that says what the files are, then one line for each, no
    more than ``REPO_DOCUMENTS_MAX_LINES`` and no more text than
    ``REPO_DOCUMENTS_MAX_TOKENS``; a last line says how many were left out.
    Every path is written as one line (``_shown``), whatever the file is
    called; one that is too long to write is among those left out, and
    when the root itself is, there is no list. Of a directory that was
    not looked at to its end, what is said is the least it can be: "at
    least" so many.
    """
    from config.nekoclaw_settings import (
        REPO_DOCUMENTS_MAX_LINES,
        REPO_DOCUMENTS_MAX_TOKENS,
        REPO_DOCUMENTS_ROOT_MAX_TOKENS,
    )
    from config.prompts import prompts_nekoclaw_brain as texts
    from utils.tokenize import count_tokens

    where = _shown(root.as_posix(), REPO_DOCUMENTS_ROOT_MAX_TOKENS)
    if not found or where is None:
        return ""
    def counted(item: Document) -> str:
        count = texts.TASK_AGENT_BRIEF_REPO_DOCUMENTS_COUNT
        if item.more:
            # Not all of them were looked at.
            count = texts.TASK_AGENT_BRIEF_REPO_DOCUMENTS_COUNT_AT_LEAST
        return count.format(count=item.files)

    lines: list[str] = []
    for item in found:
        path = _shown(item.path)
        if path is not None:
            lines.append(f"- {path} ({counted(item) if item.files is not None else _size_text(item.size)})")
    # The ones whose path is too long to write: there, and not listed.
    unwritten = len(found) - len(lines)
    note = texts.TASK_AGENT_BRIEF_REPO_DOCUMENTS_NOTE.format(root=where)
    shown = lines[:REPO_DOCUMENTS_MAX_LINES]
    # Directories whose files are listed one by one and were not looked at
    # to the end: each has one file more at the least.
    unseen = len({item.path.rsplit("/", 1)[0] for item in found if item.more and item.files is None})
    # A directory that was not looked at to its end adds none to those
    # left out, since what was not seen may be no file at all. But when
    # some are left out anyway, how many is then the least it can be.
    open_ended = any(item.cut for item in found if item.files is None)

    def whole() -> str:
        left_out = len(lines) - len(shown) + unseen + unwritten
        at_least = bool(unseen) or open_ended
        said = texts.TASK_AGENT_BRIEF_REPO_DOCUMENTS_MORE_AT_LEAST if at_least else texts.TASK_AGENT_BRIEF_REPO_DOCUMENTS_MORE
        more = [said.format(count=left_out)] if left_out else []
        return "\n".join([note, *shown, *more])

    # Paths can be long. What is over comes off the end of the list, a whole line at a time.
    while shown and count_tokens(whole()) > REPO_DOCUMENTS_MAX_TOKENS:
        shown.pop()
    return whole() if shown else ""


def listing(workdir: str | Path | None, *, upward: bool = True) -> str:
    """Return what to tell a task agent that works in ``workdir`` about the files its repository keeps for agents.

    Empty when the directory is in no repository that has any, or cannot
    be looked at: a task agent starts without the list then. Blocking.

    ``upward`` is False for a directory the host made for a commission
    that named none. Nobody chose to work there, and whatever repository
    lies above it (the folder the application keeps its data in may be
    inside one, a home directory that is a repository of settings) has
    nothing to do with the commission: only the directory itself is
    looked at.
    """
    if not workdir:
        return ""
    try:
        root = repository_root(Path(workdir), upward=upward)
        return render(root, documents(root))
    except (OSError, ValueError):
        logger.warning("the files a repository keeps for agents could not be listed", exc_info=True)
        return ""
