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

"""The ``glob`` and ``grep`` tools.

dsh runs a bundled ripgrep for both. This runtime bundles no binary, so the
search is done in Python while keeping what the model sees: the result layout,
the caps, the file selection rules and the error wording of dsh
``packages/fs/tool-fs-search``.

* ``glob`` lists files (never directories), including hidden and ignored ones,
  skips version-control metadata, and orders by modification time.
* ``grep`` skips hidden files, ignored files and binary files, like ripgrep's
  defaults. Ignore rules come from ``git ls-files`` when the search root is in
  a git work tree and git is installed; otherwise only the hidden-file rule
  applies. The pattern uses Python ``re`` syntax and is matched line by line.

Patterns come from the model, so matching must not be able to stall the
process. The standard ``re`` module keeps the interpreter lock for the whole
match, and a pattern that backtracks without end would freeze every session
sharing the event loop. Both tools therefore match with the ``regex`` package:
it releases the lock while matching text and stops a match at a time limit.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import regex

from nekoclaw.runtime.loop.tools import AgentTool
from nekoclaw.runtime.tools.base import (
    SpillStore,
    ToolCallInfo,
    ToolContext,
    ToolError,
    define_tool,
    run_cancellable,
)
from nekoclaw.runtime.tools.textio import BINARY_SAMPLE_BYTES

GLOB_MAX_RESULTS = 100
GREP_MAX_MATCHES = 250
GREP_MAX_LINE_BYTES = 2000
SEARCH_TIMEOUT_SECONDS = 30.0
# Longest a single match (one line, or one path against a glob) may take.
MATCH_TIMEOUT_SECONDS = 2.0
# Only this much of one line is held in memory and matched.
GREP_LINE_MAX_BYTES = 1024 * 1024
_BUDGET_CHECK_LINES = 1024
# Stands in for dsh's cap on raw ripgrep output: a search that produces more
# than this is refused instead of being held in memory.
SEARCH_RAW_OUTPUT_MAX_BYTES = 20_000_000
VCS_DIRECTORIES = frozenset({".git", ".svn", ".hg", ".bzr", ".jj", ".sl"})
_GIT_TIMEOUT_SECONDS = 20.0
_NUL = bytes(1)
SPILL_HINT = "Use read with offset/limit, or grep this path to search within it."


class _Stopped(Exception):
    """The search was told to stop (timeout or abort)."""


# ---------------------------------------------------------------------------
# Glob patterns (gitignore-style, as ripgrep's --glob reads them)
# ---------------------------------------------------------------------------


def _translate_class(pattern: str, index: int) -> tuple[str, int] | None:
    """Translate a ``[...]`` class starting at ``index``; ``None`` if unterminated."""
    cursor = index + 1
    if cursor < len(pattern) and pattern[cursor] in "!^":
        cursor += 1
    if cursor < len(pattern) and pattern[cursor] == "]":
        cursor += 1
    while cursor < len(pattern) and pattern[cursor] != "]":
        cursor += 1
    if cursor >= len(pattern):
        return None
    body = pattern[index + 1: cursor]
    negate = body[:1] in ("!", "^")
    if negate:
        body = body[1:]
    body = body.replace("\\", "\\\\").replace("/", "")
    if not body:
        return None
    return f"[{'^/' if negate else ''}{body}]", cursor + 1


def _translate(pattern: str) -> str:
    out: list[str] = []
    index = 0
    depth = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            run = 1
            while index + run < length and pattern[index + run] == "*":
                run += 1
            if run >= 2:
                at_segment_start = index == 0 or pattern[index - 1] == "/"
                if at_segment_start and pattern[index + run: index + run + 1] == "/":
                    out.append("(?:.*/)?")
                    index += run + 1
                    continue
                out.append(".*")
            else:
                out.append("[^/]*")
            index += run
        elif char == "?":
            out.append("[^/]")
            index += 1
        elif char == "[":
            translated = _translate_class(pattern, index)
            if translated is None:
                out.append(regex.escape(char))
                index += 1
            else:
                out.append(translated[0])
                index = translated[1]
        elif char == "{":
            depth += 1
            out.append("(?:")
            index += 1
        elif char == "}" and depth > 0:
            depth -= 1
            out.append(")")
            index += 1
        elif char == "," and depth > 0:
            out.append("|")
            index += 1
        elif char == "\\" and index + 1 < length:
            out.append(regex.escape(pattern[index + 1]))
            index += 2
        else:
            out.append(regex.escape(char))
            index += 1
    out.append(")" * depth)
    return "".join(out)


def compile_glob(pattern: str, *, case_insensitive: bool) -> regex.Pattern[str]:
    """Compile a glob for matching ``/``-separated paths relative to a root.

    A pattern without ``/`` matches the file name at any depth. One with ``/``
    is anchored at the root; ``**`` crosses directories.
    """
    normalized = pattern.replace("\\", "/") if os.sep == "\\" else pattern
    anchored = "/" in normalized.strip("/") or normalized.startswith("/")
    body = _translate(normalized.lstrip("/"))
    prefix = "" if anchored else "(?:.*/)?"
    flags = regex.IGNORECASE if case_insensitive else 0
    try:
        return regex.compile(f"{prefix}{body}", flags)
    except regex.error as exc:
        raise ToolError(f"invalid glob pattern {pattern!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# File enumeration
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Budget:
    """Stop flag plus deadline, checked from the worker thread."""

    stop: threading.Event
    deadline: float

    def check(self) -> None:
        if self.stop.is_set() or time.monotonic() > self.deadline:
            raise _Stopped

    def match_timeout(self) -> float:
        """Return how long the next single match may run."""
        return max(0.001, min(MATCH_TIMEOUT_SECONDS, self.deadline - time.monotonic()))


def _slow_pattern(kind: str, budget: _Budget) -> ToolError:
    """Build the error for a match that hit its time limit."""
    # When the whole search ran out of time, say that instead.
    budget.check()
    return ToolError(
        f"{kind} pattern is too slow: a single match took longer than "
        f"{MATCH_TIMEOUT_SECONDS:g}s; simplify the pattern"
    )


def _glob_matches(matcher: regex.Pattern[str], relative: str, budget: _Budget) -> bool:
    try:
        return matcher.fullmatch(relative, timeout=budget.match_timeout()) is not None
    except TimeoutError as exc:
        raise _slow_pattern("glob", budget) from exc


def _is_link_stat(info: os.stat_result) -> bool:
    """Return whether an ``lstat`` result describes a symbolic link or a junction."""
    if stat.S_ISLNK(info.st_mode):
        return True
    # Windows: a junction is a directory with a mount-point reparse tag. Other
    # reparse points (cloud placeholders, deduplicated files) are ordinary files.
    tag = getattr(info, "st_reparse_tag", 0)
    return tag != 0 and tag in (
        getattr(stat, "IO_REPARSE_TAG_SYMLINK", -1),
        getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1),
    )


def _is_link(path: str) -> bool:
    try:
        return _is_link_stat(os.lstat(path))
    except OSError:
        return False


def _entry_is_link(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True
    # Only Windows has junctions, and there the entry already holds the
    # attributes, so asking costs no system call.
    return os.name == "nt" and _is_link_stat(entry.stat(follow_symlinks=False))


class _LinkFilter:
    """Tells whether a path below a root is reached through a link.

    A directory search never reads through symbolic links or junctions, like
    ripgrep by default. The call was classified by the directory it names, and
    a link inside that directory can point anywhere.
    """

    def __init__(self, root: Path) -> None:
        self._root = str(root)
        self._directories: dict[str, bool] = {"": False}

    def _directory_is_linked(self, relative: str) -> bool:
        known = self._directories.get(relative)
        if known is None:
            parent = relative.rpartition("/")[0]
            known = self._directory_is_linked(parent) or _is_link(os.path.join(self._root, relative))
            self._directories[relative] = known
        return known

    def reached_through_link(self, relative: str) -> bool:
        directory = relative.rpartition("/")[0]
        return self._directory_is_linked(directory) or _is_link(os.path.join(self._root, relative))


def _walk_files(root: Path, budget: _Budget, *, skip_hidden: bool) -> list[tuple[str, os.DirEntry[str]]]:
    """Return ``(relative posix path, entry)`` for every regular file below ``root``.

    Symbolic links and junctions are not followed. Version-control metadata is
    always skipped; names starting with a dot are skipped when ``skip_hidden``
    is set.
    """
    found: list[tuple[str, os.DirEntry[str]]] = []
    pending: list[tuple[Path, str]] = [(root, "")]
    while pending:
        budget.check()
        directory, prefix = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if skip_hidden and name.startswith("."):
                continue
            try:
                if _entry_is_link(entry):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if name in VCS_DIRECTORIES:
                        continue
                    pending.append((Path(entry.path), f"{prefix}{name}/"))
                elif entry.is_file(follow_symlinks=False):
                    found.append((f"{prefix}{name}", entry))
            except OSError:
                continue
    return found


def _git_listed_files(root: Path) -> list[str] | None:
    """Return files git does not ignore below ``root``, or ``None`` if git cannot say."""
    git = shutil.which("git")
    if git is None:
        return None
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [git, "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            creationflags=creation_flags,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    names = completed.stdout.split(_NUL)
    # A name that is not UTF-8 decodes with replacement characters and is then
    # simply not found on disk, which skips it.
    return [name.decode("utf-8", errors="replace") for name in names if name]


def _is_hidden(relative: str) -> bool:
    return any(part.startswith(".") for part in relative.split("/"))


def display_path(path: Path, workdir: Path) -> str:
    """Show ``path`` relative to the working directory when it lies inside it."""
    try:
        relative = os.path.relpath(path, workdir)
    except ValueError:
        return str(path)
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return str(path)
    return relative


# ---------------------------------------------------------------------------
# glob
# ---------------------------------------------------------------------------


def glob_files(root: Path, pattern: str, budget: _Budget, *, case_insensitive: bool) -> list[Path]:
    """Return matching files below ``root``, oldest modification first."""
    matcher = compile_glob(pattern, case_insensitive=case_insensitive)
    matched: list[tuple[int, str, Path]] = []
    for relative, entry in _walk_files(root, budget, skip_hidden=False):
        if not _glob_matches(matcher, relative, budget):
            continue
        try:
            modified = entry.stat(follow_symlinks=False).st_mtime_ns
        except OSError:
            continue
        matched.append((modified, relative, Path(entry.path)))
    matched.sort(key=lambda item: (item[0], item[1]))
    return [path for _modified, _relative, path in matched]


def format_glob_output(paths: list[str], spill_path: Path | None) -> str:
    if not paths:
        return "No files found"
    if len(paths) <= GLOB_MAX_RESULTS:
        return "\n".join(paths)
    shown = paths[:GLOB_MAX_RESULTS]
    recovery = (
        f"Full sorted result stored at: {spill_path}. {SPILL_HINT}"
        if spill_path is not None
        else "The complete result could not be saved; narrow pattern or path to see more."
    )
    body = "\n".join(shown)
    return f"{body}\n\n(Showing {len(shown)} of {len(paths)} paths. {recovery})"


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GrepMatch:
    path: str
    line_number: int
    line: str


def validate_include(include: str) -> None:
    if not include.strip():
        raise ToolError("include must be a non-empty glob when given")
    if include.startswith("!"):
        raise ToolError(
            'include must be a positive glob filter; negated patterns ("!…") are not supported'
        )
    depth = 0
    for char in include:
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            raise ToolError(
                "include must be one glob, not a comma-separated list (use {a,b} alternation instead)"
            )


def preview_line(line: str, max_bytes: int = GREP_MAX_LINE_BYTES) -> str:
    encoded = line.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return line
    kept = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return f"{kept} (line truncated)"


def _search_file(
    path: Path,
    shown: str,
    expression: regex.Pattern[str],
    out: list[GrepMatch],
    budget: _Budget,
) -> int:
    """Append the matches of one file to ``out``; return the bytes of text added.

    The file is read one line at a time, so its size does not decide how much
    memory the search takes, and the search budget is checked as it goes. Of a
    line longer than ``GREP_LINE_MAX_BYTES`` only that much is matched.
    """
    added = 0
    try:
        with open(path, "rb") as handle:
            if _NUL in handle.read(BINARY_SAMPLE_BYTES):
                return 0
            handle.seek(0)
            number = 0
            while True:
                raw = handle.readline(GREP_LINE_MAX_BYTES + 1)
                if not raw:
                    break
                number += 1
                if number % _BUDGET_CHECK_LINES == 0:
                    budget.check()
                cut = len(raw) > GREP_LINE_MAX_BYTES and not raw.endswith(b"\n")
                if cut:
                    raw = raw[:GREP_LINE_MAX_BYTES]
                    # Skip the rest of the line without holding it in memory.
                    while True:
                        budget.check()
                        rest = handle.readline(GREP_LINE_MAX_BYTES)
                        if not rest or rest.endswith(b"\n"):
                            break
                if raw.endswith(b"\n"):
                    raw = raw[:-1]
                if raw.endswith(b"\r"):
                    raw = raw[:-1]
                valid = True
                try:
                    # A cut may fall inside a character; that is not bad encoding.
                    line = raw.decode("utf-8", errors="ignore" if cut else "strict")
                except UnicodeDecodeError:
                    line = raw.decode("utf-8", errors="replace")
                    valid = False
                try:
                    found = expression.search(line, timeout=budget.match_timeout())
                except TimeoutError as exc:
                    raise _slow_pattern("grep", budget) from exc
                if found is None:
                    continue
                if not valid:
                    line = "(line is not valid UTF-8)"
                out.append(GrepMatch(shown, number, line))
                added += len(line)
                if added > SEARCH_RAW_OUTPUT_MAX_BYTES:
                    raise _too_much_output()
    except OSError:
        # Unreadable, or it went away while being read: what was found stays.
        pass
    return added


def _too_much_output() -> ToolError:
    return ToolError(
        "grep produced more output than can be processed; narrow pattern, path, or include"
    )


def grep_files(
    target: Path,
    expression: regex.Pattern[str],
    include: str | None,
    workdir: Path,
    budget: _Budget,
    *,
    case_insensitive_globs: bool,
) -> list[GrepMatch]:
    """Search ``target`` (a file or a directory) and return every match."""
    matches: list[GrepMatch] = []
    if target.is_file():
        _search_file(target, display_path(target, workdir), expression, matches, budget)
        return matches

    include_matcher = (
        compile_glob(include, case_insensitive=case_insensitive_globs) if include else None
    )
    listed = _git_listed_files(target)
    links: _LinkFilter | None = None
    if listed is None:
        relatives = [relative for relative, _entry in _walk_files(target, budget, skip_hidden=True)]
    else:
        relatives = [relative for relative in listed if not _is_hidden(relative)]
        # git lists a link as a file and looks through junctions; the walk
        # above has already left both out.
        links = _LinkFilter(target)
    relatives.sort()
    total = 0
    for relative in relatives:
        budget.check()
        if include_matcher is not None and not _glob_matches(include_matcher, relative, budget):
            continue
        if links is not None and links.reached_through_link(relative):
            continue
        path = target / relative
        total += _search_file(path, display_path(path, workdir), expression, matches, budget)
        if total > SEARCH_RAW_OUTPUT_MAX_BYTES:
            raise _too_much_output()
    return matches


def format_grep_matches(matches: list[GrepMatch]) -> str:
    groups: dict[str, list[GrepMatch]] = {}
    for match in matches:
        groups.setdefault(match.path, []).append(match)
    sections = []
    for path, group in groups.items():
        lines = "\n".join(f"Line {match.line_number}: {match.line}" for match in group)
        sections.append(f"{path}\n{lines}")
    return "\n\n".join(sections)


def format_grep_output(matches: list[GrepMatch], spill_path: Path | None) -> str:
    if not matches:
        return "No matches found"
    previewed = [GrepMatch(m.path, m.line_number, preview_line(m.line)) for m in matches]
    seen = len(previewed)
    if seen <= GREP_MAX_MATCHES:
        noun = "match" if seen == 1 else "matches"
        return f"Found {seen} {noun}\n\n{format_grep_matches(previewed)}"
    kept = previewed[:GREP_MAX_MATCHES]
    recovery = (
        f"Full grep result stored at: {spill_path}. {SPILL_HINT}"
        if spill_path is not None
        else "The complete result could not be saved; narrow pattern, path, or include to see more."
    )
    return f"Found {len(kept)} of {seen} matches\n\n{format_grep_matches(kept)}\n\n({recovery})"


# ---------------------------------------------------------------------------
# Tool wiring
# ---------------------------------------------------------------------------


async def _run_search(call: ToolCallInfo, name: str, work: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking search in a worker thread under the search budget."""
    budget = _Budget(threading.Event(), time.monotonic() + SEARCH_TIMEOUT_SECONDS)
    try:
        return await run_cancellable(asyncio.to_thread(work, *args, budget, **kwargs), call)
    except _Stopped as exc:
        raise ToolError(
            f"{name} timed out after {int(SEARCH_TIMEOUT_SECONDS * 1000)}ms; narrow the search"
        ) from exc
    finally:
        budget.stop.set()


def _save_spill(spill: SpillStore, hint: str, text: str) -> Path | None:
    return spill.save(hint, text)


def build_search_tools(ctx: ToolContext, schemas: dict[str, dict[str, Any]]) -> list[AgentTool]:
    case_insensitive = ctx.platform == "win32"

    def search_root(args: dict[str, Any]) -> Path:
        raw = args.get("path")
        if raw is None:
            return ctx.workdir
        if not str(raw).strip():
            raise ToolError("path must be a non-empty string when given")
        return ctx.resolve(str(raw))

    async def glob(args: dict[str, Any], call: ToolCallInfo) -> str:
        pattern = str(args["pattern"])
        if not pattern.strip():
            raise ToolError("pattern must be a non-empty string")
        root = search_root(args)
        if not root.is_dir():
            raise ToolError(f'cannot search "{root}": not a directory')
        found = await _run_search(
            call, "glob", glob_files, root, pattern, case_insensitive=case_insensitive
        )
        shown = [display_path(path, ctx.workdir) for path in found]
        spill_path = None
        if len(shown) > GLOB_MAX_RESULTS:
            spill_path = await asyncio.to_thread(
                _save_spill, ctx.spill, "glob-results", "\n".join(shown)
            )
        return format_glob_output(shown, spill_path)

    async def grep(args: dict[str, Any], call: ToolCallInfo) -> str:
        pattern = str(args["pattern"])
        if not pattern:
            raise ToolError("pattern must be a non-empty string")
        target = search_root(args)
        include = args.get("include") or None
        if include is not None:
            validate_include(str(include))
        if not target.exists():
            raise ToolError(f'cannot search "{target}": not found')
        try:
            expression = regex.compile(pattern)
        except regex.error as exc:
            raise ToolError(f"invalid regular expression: {exc}") from exc
        matches = await _run_search(
            call,
            "grep",
            grep_files,
            target,
            expression,
            str(include) if include is not None else None,
            ctx.workdir,
            case_insensitive_globs=case_insensitive,
        )
        spill_path = None
        if len(matches) > GREP_MAX_MATCHES:
            previewed = [GrepMatch(m.path, m.line_number, preview_line(m.line)) for m in matches]
            full = f"Found {len(matches)} matches\n\n{format_grep_matches(previewed)}"
            spill_path = await asyncio.to_thread(_save_spill, ctx.spill, "grep-results", full)
        return format_grep_output(matches, spill_path)

    return [
        define_tool(schemas["glob"], glob, parallel=True),
        define_tool(schemas["grep"], grep, parallel=True),
    ]
