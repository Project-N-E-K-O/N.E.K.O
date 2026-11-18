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
goes is the caller's business: today it follows a task agent's brief
(``TaskAgentManager._start``).
"""

from __future__ import annotations

import logging
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


def repository_root(workdir: Path) -> Path:
    """Return the root of the repository a directory is in, or the directory itself when it is in none.

    The nearest directory, going up, that has a ``.git`` (a directory in
    a clone, a file in a worktree or a submodule). Nothing is read but
    whether that name exists, and no further up than
    ``REPO_ROOT_SEARCH_MAX_LEVELS`` directories.
    """
    from config.nekoclaw_settings import REPO_ROOT_SEARCH_MAX_LEVELS

    current = workdir
    for _level in range(REPO_ROOT_SEARCH_MAX_LEVELS + 1):
        try:
            if (current / ".git").exists():
                return current
        except OSError:
            break
        if current.parent == current:
            break
        current = current.parent
    return workdir


def _files_of(directory: Path) -> list[Path]:
    """Return the files directly in a directory, by name; none when it cannot be read."""
    from config.nekoclaw_settings import REPO_DOCUMENT_DIRECTORY_MAX_FILES

    files: list[Path] = []
    try:
        for item in directory.iterdir():
            if item.is_file():
                files.append(item)
                if len(files) > REPO_DOCUMENT_DIRECTORY_MAX_FILES:
                    # A directory this full is no set of rules; what is listed of it is the beginning.
                    break
    except OSError:
        return []
    return sorted(files, key=lambda item: item.name)


def _size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def documents(root: Path) -> list[Document]:
    """Return what a repository keeps for agents, in the order of ``REPO_DOCUMENTS``. Reads names and sizes only."""
    from config.nekoclaw_settings import REPO_DOCUMENTS

    found: list[Document] = []
    for kind, name in REPO_DOCUMENTS:
        path = root.joinpath(*name.split("/"))
        try:
            if kind == FILE:
                if path.is_file():
                    found.append(Document(name, size=_size(path)))
            elif kind == FILES:
                found.extend(Document(f"{name}/{item.name}", size=_size(item)) for item in _files_of(path))
            elif kind == COUNT:
                count = len(_files_of(path))
                if count:
                    found.append(Document(f"{name}/", files=count))
        except OSError:
            continue
    return found


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
    """
    from config.nekoclaw_settings import REPO_DOCUMENTS_MAX_LINES, REPO_DOCUMENTS_MAX_TOKENS
    from config.prompts import prompts_nekoclaw_brain as texts
    from utils.tokenize import count_tokens

    if not found:
        return ""
    lines = [
        f"- {item.path} ({texts.TASK_AGENT_BRIEF_REPO_DOCUMENTS_COUNT.format(count=item.files)})"
        if item.files is not None
        else f"- {item.path} ({_size_text(item.size)})"
        for item in found
    ]
    note = texts.TASK_AGENT_BRIEF_REPO_DOCUMENTS_NOTE.format(root=root.as_posix())
    shown = lines[:REPO_DOCUMENTS_MAX_LINES]

    def whole() -> str:
        left_out = len(lines) - len(shown)
        more = [texts.TASK_AGENT_BRIEF_REPO_DOCUMENTS_MORE.format(count=left_out)] if left_out else []
        return "\n".join([note, *shown, *more])

    # Paths can be long. What is over comes off the end of the list, a whole line at a time.
    while shown and count_tokens(whole()) > REPO_DOCUMENTS_MAX_TOKENS:
        shown.pop()
    return whole() if shown else ""


def listing(workdir: str | Path | None) -> str:
    """Return what to tell a task agent that works in ``workdir`` about the files its repository keeps for agents.

    Empty when the directory is in no repository that has any, or cannot
    be looked at: a task agent starts without the list then. Blocking.
    """
    if not workdir:
        return ""
    try:
        root = repository_root(Path(workdir))
        return render(root, documents(root))
    except (OSError, ValueError):
        logger.warning("the files a repository keeps for agents could not be listed", exc_info=True)
        return ""
