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

An entry a task agent or a review filed says how it is known (``verified``,
``observed`` or ``inferred``), which files it rests on, and how many
commissions have filed it. From these the host tells, without asking a
model, whether a note takes effect: one that was verified does; one that was
observed does when a file it names exists; one that was only inferred does
once a second commission files the same. An entry has one of four states.
It is in effect (a line of its index points at it); or pending, a note that
has not taken effect yet (a file like any other that says so in its front
matter, with no line in any index: a task agent comes upon it only by
searching or reading the directory, and may confirm it by checking it and
filing it again as verified); or expired, once none of the files it rests
on exists any more (the file stays, the line goes); or the user removed it,
and then ``notes.json`` remembers it so that the same note is not kept
again.

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
import threading
import time
import unicodedata
from collections.abc import Awaitable, Callable, Iterable
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
# How a note is known, the strongest first: checked by hand, seen to happen,
# concluded. What ``note_lesson`` calls them, as the tool's schema has them.
CONFIDENCES: tuple[str, ...] = ("verified", "observed", "inferred")
VERIFIED, OBSERVED, INFERRED = CONFIDENCES
# The states an entry that is a file can be in. Pending is a note that has
# not taken effect yet: a file with no line in any index. The fourth state
# there is, removed by the user, is one of no file: a record in
# ``notes.json``.
ENTRY_ACTIVE = "active"
ENTRY_PENDING = "pending"
ENTRY_EXPIRED = "expired"
_NOTES_FILE_VERSION = 1
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
    # How it is known, one of ``CONFIDENCES``; empty for an entry nobody
    # said that of (one the user wrote, one filed from the console).
    confidence: str = ""
    # The files it rests on, as they were found when it was filed.
    evidence: tuple[str, ...] = ()
    # How many commissions have filed it.
    seen: int = 1
    # ``ENTRY_ACTIVE``, ``ENTRY_PENDING`` or ``ENTRY_EXPIRED``.
    status: str = ENTRY_ACTIVE
    # The commissions counted in ``seen``, the last of them; each is the
    # commission she gave, which stands for the task agents it started too.
    seen_by: tuple[str, ...] = ()
    # The ones of ``evidence`` that lie outside the working directories:
    # looked at only while the user lets task agents read there unasked.
    evidence_outside: tuple[str, ...] = ()
    # Files kept with it for whoever reads it, and for nothing else: they
    # neither have it take effect nor have it expire. The ones a review or
    # the summary of a project names for a global entry: the files of one
    # project do not show that it holds for every project.
    noted: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["sources"] = list(self.sources)
        data["evidence"] = list(self.evidence)
        data["seen_by"] = list(self.seen_by)
        data["evidence_outside"] = list(self.evidence_outside)
        data["noted"] = list(self.noted)
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

    def drop(self, key: str) -> None:
        self.meta = [(name, value) for name, value in self.meta if name != key]


@dataclass(frozen=True, slots=True)
class _Rested:
    """An entry that rests on files, as it was read when those files are looked at (``check_evidence``).

    ``place`` is where it stands among the entries of that call;
    ``outside`` are the files that lie outside the working directories;
    ``listed`` is whether one that says it expired still has its line;
    ``said`` is a mark of all its file said when it was read
    (``_text_mark``).
    """

    place: int
    scope: str | None
    id: str
    evidence: tuple[str, ...]
    outside: tuple[str, ...]
    expired: bool
    listed: bool
    said: str


@dataclass(frozen=True, slots=True)
class _Known:
    """What is known of a note beyond its words, as an entry keeps it."""

    # One of ``CONFIDENCES``.
    confidence: str
    # Files that were found to exist when it was filed.
    evidence: tuple[str, ...] = ()
    # The commission that is counted as having seen it: the one she gave.
    observer: str | None = None
    # The commissions that saw it before, where it waited until now, and
    # the ones it came from then: put on record with this filing, in one
    # write.
    seen_before: tuple[str, ...] = ()
    sources_before: tuple[str, ...] = ()
    # The ones of ``evidence`` that lie outside the working directories.
    # They count because the user lets task agents read there unasked.
    outside: tuple[str, ...] = ()
    # The ones of ``evidence`` of which nothing is told at this moment
    # (``_recorded``): on record, and they speak for nothing now.
    untold: tuple[str, ...] = ()
    # Files the entry it is kept in rested on that were found gone just
    # now: they go from its record.
    gone: tuple[str, ...] = ()
    # Files kept with it for reference only (``Entry.noted``).
    noted: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Note:
    """A note that is to be judged: by a task agent, or made by a review or a summary."""

    title: str
    content: str
    kind: str
    known: _Known
    description: str = ""
    # The commission it comes from.
    source: str | None = None
    # For one that waited somewhere else and goes with its commission:
    # the day it was first filed and the day it was last filed. It has
    # waited that long, not since it was brought along.
    waited: tuple[str, str] | None = None
    # Whether files may have it take effect. Not for a global entry a
    # review or a summary made: the files of one project do not show
    # that it holds for every project. Told apart from how it is known,
    # which a pending entry of its text adds to (``_stronger``): seen by
    # the task agent, with a file from the review, it is still the files
    # of one project.
    files_vouch: bool = True


@dataclass(frozen=True, slots=True)
class _Taken:
    """What became of a note that was judged."""

    # The entry it is in. For one that is not in effect yet, a pending one.
    entry: Entry
    # ``_CREATED``, ``_ADDED``, ``_THERE``, or ``_HELD`` for one that waits.
    how: str
    # Whether this filing was counted as one more commission that has seen it.
    counted: bool = False
    # Whether the entry had expired and is in effect again.
    revived: bool = False
    # For one that is not in effect: how it was filed.
    confidence: str = ""
    # Whether it used one of the places its commission has: a note that was
    # there already uses none, nor does one of the commission's own that
    # waited and is filed again (it used its place when it began to wait).
    charged: bool = True
    # Files that were found gone as it was judged, or after that as it
    # was written: one of them that the note named did not count, though
    # it was there a moment before.
    gone: tuple[str, ...] = ()
    # Of the files the note named, the ones that spoke for it in the
    # end: found, and not found gone or untold of by any later look.
    spoke: tuple[str, ...] = ()


def _one_line(text: Any) -> str:
    return _WHITESPACE.sub(" ", str(text or "")).strip()


def _plain_lines(text: str) -> str:
    """Return a text with the line ends it is kept with.

    What was filed is told again by its words: the same text with other
    line ends is the same text.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _cut(text: Any, max_tokens: int) -> str:
    from utils.tokenize import truncate_to_tokens

    return truncate_to_tokens(_plain_lines(str(text or "")).strip(), max_tokens).strip()


def _shown(path: Any, max_tokens: int) -> str:
    """Return a path as a task agent is told of it: whole when it is short, else its end.

    What tells one file from another stands last, its name and the
    directories next to it. A path that is too long is cut at the
    front, a directory at a time, and says that it was.
    """
    from utils.tokenize import count_tokens

    # As it is: a name with two spaces in a row is another file than
    # the one with one.
    text = _plain_lines(str(path or "")).strip()
    if count_tokens(text) <= max_tokens:
        return text
    parts = text.replace("\\", "/").split("/")
    while len(parts) > 1:
        parts = parts[1:]
        shorter = ".../" + "/".join(parts)
        if count_tokens(shorter) <= max_tokens:
            return shorter
    # A name that is too long by itself.
    return _cut(".../" + parts[-1], max_tokens)


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
# What became of a note that does not take effect yet: it is in a pending entry.
_HELD = "held"

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


# What a sentence puts around a word and what it ends one with: taken off the
# ends of a word when two texts are compared. Backticks and asterisks only
# mark up what stands between them.
_WORD_OPENERS = "([{\"'«“‘「『《【`*"
_WORD_CLOSERS = ".,;:!?…)]}\"'»”’」』》】`*。，、；：！？"
# Where words are not set apart by spaces (Chinese, Japanese), the marks of a
# sentence stand inside what a space-separated reading takes for one word.
# These are marks of such text only, and no part of a word anywhere.
_MARKS_OF_UNSPACED = "。、「」『』《》【】"
# These are written in code as well (``foo(bar)``, ``a:b``): inside such a
# word they are marks of the sentence only next to a character of that text.
# All that is taken off the ends of a word is here, so that such text reads
# the same with spaces around what a sentence puts there and without.
_MARKS_OF_BOTH = ".,;:!?()[]{}\"'«»“”‘’`*"
_MARKS_INSIDE_UNSPACED = re.compile(f"[{re.escape(_MARKS_OF_UNSPACED + _MARKS_OF_BOTH)}]+")


def _wide(char: str) -> bool:
    """Whether a character is one of a text that puts no spaces between words (Chinese, Japanese, Korean)."""
    return bool(char) and unicodedata.east_asian_width(char) in "WF"


def _kept_of(word: str, marks: re.Match[str]) -> str:
    """Return what stays of a run of marks inside a word that has wide characters: what belongs to the word.

    The run belongs to the sentence, and goes, when one of its marks is
    a mark of such text only, or when a wide character stands right
    before or after it: two clauses with a comma between them are
    written without a space. Between narrow characters the run is part
    of what is written there, a call or a key and its value, and stays.

    The full stops a run ends with stay when a narrow character follows
    them, whatever else the run holds and whatever stands before it:
    there they begin a name (``.env``, ``../lib``) or stand inside one
    (``app.py``). A full stop ends what stands before it, so it is the
    sentence's only where the sentence goes on after it.
    """
    run = marks.group()
    before = word[marks.start() - 1] if marks.start() else ""
    after = word[marks.end()] if marks.end() < len(word) else ""
    stops = ""
    if after and not _wide(after):
        rest = run.rstrip(".")
        run, stops = rest, run[len(rest) :]
    if any(char in _MARKS_OF_UNSPACED for char in run) or _wide(before) or _wide(after):
        return stops
    return run + stops


def _normal(text: Any) -> str:
    """Return a text as two filings of it are compared: without white space, the punctuation of sentences, and case.

    Wide and narrow forms of a character are one. What a sentence puts
    around a word goes: the mark it ends with, quotation marks, brackets,
    backticks. What stands inside a word stays, because there it carries
    meaning: ``a/b.py`` is not ``ab.py``, ``-x`` is not ``x``, ``3.10`` is
    not ``310``, and ``>=`` is not ``<=``. In text that puts no spaces
    between words, the marks of a sentence are inside what reads as one
    word, and go as well; a mark that code is written with stays where
    it stands between narrow characters (``_kept_of``).
    """
    folded = unicodedata.normalize("NFKC", str(text or "")).casefold()
    words: list[str] = []
    for word in folded.split():
        word = "".join(char for char in word if unicodedata.category(char)[0] != "C")
        word = word.lstrip(_WORD_OPENERS).rstrip(_WORD_CLOSERS)
        if any(_wide(char) for char in word):
            word = _MARKS_INSIDE_UNSPACED.sub(lambda marks, word=word: _kept_of(word, marks), word)
        if word:
            words.append(word)
    return "".join(words)


def _text_mark(text: str) -> str:
    """Return a mark of all a file says, to tell later whether it says the very same."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _digest(content: str) -> str:
    """Return a short mark of a text that was filed, to tell later that the same text is filed again.

    Of the text as ``_normal`` leaves it: the same words spaced or
    capitalised another way, or set between other marks of a sentence, are
    the same text.
    """
    return hashlib.sha256(_normal(content).encode("utf-8")).hexdigest()[:12]


# The mark of a text that says nothing once it is compared (empty, or marks of
# a sentence only). An entry can come to keep it: emptied in the console, or
# given a text that is nothing but such marks.
_NO_TEXT = _digest("")


def _confidence(value: Any) -> str:
    """Return how a note is known as one of ``CONFIDENCES``, or empty for anything else."""
    known = str(value or "").strip().lower()
    return known if known in CONFIDENCES else ""


def _stronger(one: str, other: str) -> str:
    """Return the stronger of two ways a note is known; one that is not said is the weakest."""
    ranked = [item for item in CONFIDENCES if item in (one, other)]
    return ranked[0] if ranked else ""


def _evidence_in(document: _Document) -> list[str]:
    """Return the files an entry rests on, as its front matter lists them."""
    raw = document.get("evidence")
    if not raw:
        return []
    try:
        listed = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(listed, list):
        return []
    return [item for item in listed if isinstance(item, str) and item]


def _noted_in(document: _Document) -> list[str]:
    """Return the files an entry keeps for reference only (``Entry.noted``), as its front matter lists them."""
    raw = document.get("noted")
    if not raw:
        return []
    try:
        listed = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(listed, list):
        return []
    return [item for item in listed if isinstance(item, str) and item]


def _set_noted(document: _Document, paths: list[str]) -> None:
    """Write the files an entry keeps for reference only into its front matter, as JSON; none, no line."""
    if paths:
        document.set("noted", json.dumps(paths, ensure_ascii=False))
    else:
        document.drop("noted")


def _seen_in(document: _Document) -> int:
    """Return how many commissions have filed an entry; one when it does not say."""
    try:
        return max(int(document.get("seen") or 1), 1)
    except ValueError:
        return 1


def _not_among(paths: Iterable[str], among: Iterable[str]) -> list[str]:
    """Return the files of ``paths`` that are not among ``among``, told apart as ``_joined`` tells them."""
    there = {os.path.normcase(item) for item in among}
    return [path for path in paths if os.path.normcase(path) not in there]


def _joined(first: Iterable[Any], second: Iterable[Any], most: int) -> list[str]:
    """Return two lists of files as one, each file once, the newest ``most`` of them.

    The same file written with another case is one file where the file
    system reads names so. A file that is named again stands where it
    was named last: it was looked at just now, and is not the one to go
    for being the oldest.
    """
    paths: dict[str, str] = {}
    for item in [*first, *second]:
        if not isinstance(item, str) or not item:
            continue
        key = os.path.normcase(item)
        paths.pop(key, None)
        paths[key] = item
    return list(paths.values())[-most:] if most > 0 else []


def _lexically_inside(path: str, base: str) -> bool:
    """Whether a path lies in a directory, going by how the two are written. Reads nothing."""
    target, root = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(base))
    try:
        return os.path.commonpath([target, root]) == root
    except ValueError:
        # Different drives on Windows.
        return False


def _real_place(path: str) -> str:
    """Return where a directory really is, links resolved; the path itself when that cannot be told."""
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        return path


def evidence_of(
    raw: Any, bases: Iterable[str], *, looked_at: int | None = None, outside: bool = False
) -> tuple[list[str], list[tuple[str, str]]]:
    """Return the files among ``raw`` that count as evidence, and the others with why they do not.

    A path counts when it names a file that exists inside one of ``bases``
    (the working directory of the commission, that of its project), links
    resolved. A relative one is read against each base in turn. A path
    that leads out of them all is not looked at, so that nothing is told
    about a file a task agent would have to ask before it may read; it is
    said to be outside. Blocking: it reads the file system.

    With ``outside`` a path that leads out of them all is looked at as
    well, and counts when it names a file: for a user who lets task
    agents read outside the working directory without being asked, so
    nothing is told that a task agent could not see for itself.

    What is returned is where each file really is, links resolved: that
    is what is kept, so a path that leads somewhere else later on is told
    from the file it was (``_stands``). A path may be given that way as
    well: one that lies in where a base really is counts like one that
    lies in the base as it is written (a working directory reached
    through a link, as the temporary directories of some systems are).

    Of what a task agent names, the first ``SPACE_EVIDENCE_MAX_PATHS`` are
    looked at. ``looked_at`` is for words taken from a text, most of
    which are no paths at all: so many of them are looked at, and the
    first ``SPACE_EVIDENCE_MAX_PATHS`` that are files are kept.
    """
    from config.nekoclaw_settings import SPACE_EVIDENCE_MAX_PATHS, SPACE_EVIDENCE_PATH_MAX_TOKENS
    from nekoclaw.runtime.permissions import is_inside
    from utils.tokenize import count_tokens

    # Each base as it is written, and where it really is. Only the base
    # itself is looked at for that, never a path that was named.
    roots = [(str(base), _real_place(str(base))) for base in bases if base]
    counted: list[str] = []
    others: list[tuple[str, str]] = []
    items = raw if isinstance(raw, (list, tuple)) else []
    for item in items[: looked_at or SPACE_EVIDENCE_MAX_PATHS]:
        if len(counted) >= SPACE_EVIDENCE_MAX_PATHS:
            break
        # Looked for as it was written: a name may have two spaces in a
        # row, and with one it is another file. On one line only for
        # what is said of it.
        named = item.strip() if isinstance(item, str) else ""
        text = _one_line(named)
        if not text:
            continue
        if (
            "\x00" in named
            or "\n" in named
            or "\r" in named
            or count_tokens(text) > SPACE_EVIDENCE_PATH_MAX_TOKENS
        ):
            # Told as it was named where that is one line: a name
            # keeps the spaces it has.
            others.append((_cut(text if "\n" in named or "\r" in named else named, 24), "not a usable path"))
            continue
        found: str | None = None
        within = False
        # Where it leads, when that is out of a base.
        beyond: list[str] = []
        for root, real_root in roots:
            # As it is written: where a directory in it is a link, a step
            # back from there leads somewhere else than taking the two
            # names out of the path would.
            candidate = os.path.join(root, named)
            if not _lexically_inside(candidate, root) and not _lexically_inside(candidate, real_root):
                beyond.append(candidate)
                continue
            try:
                if not is_inside(Path(candidate), Path(root)):
                    # A link in it leads out of the directory.
                    beyond.append(candidate)
                    continue
                within = True
                if os.path.isfile(candidate):
                    found = Path(os.path.realpath(candidate)).as_posix()
                    break
            except (OSError, ValueError):
                continue
        if found is None and outside:
            # The user lets task agents read out there unasked.
            if not roots and os.path.isabs(named):
                beyond.append(named)
            for candidate in beyond:
                try:
                    if os.path.isfile(candidate):
                        found = Path(os.path.realpath(candidate)).as_posix()
                        break
                except (OSError, ValueError):
                    continue
        if found is not None:
            counted.append(found)
        else:
            if not roots and not os.path.isabs(named):
                # Whatever may be read: there is nothing it could be
                # read against, and it was not looked for.
                reason = "no working directory to read it against"
            else:
                reason = "not found" if within or outside else "outside the working directory"
            # Told as it was named: a name keeps the spaces it has.
            others.append((named, reason))
    return _joined(counted, (), SPACE_EVIDENCE_MAX_PATHS), others


# What ends a word in a sentence, for finding the paths it names: white
# space, quotation marks, brackets, and the punctuation of Chinese.
_WORD_BREAKS = re.compile(r"[\s`'\"“”‘’<>()\[\]{}，。；：、！？（）「」『』《》]+")
# A run of what a path is written with in ASCII, wherever it stands: text
# in Chinese or Japanese puts no space around a path. With the letter of
# its drive, where it has one (``C:/repo/app.py``): without it the rest
# would be read against the working directory, on another drive.
_PLAIN_PATH = re.compile(r"(?:(?<![A-Za-z0-9])[A-Za-z]:(?=[/\\]))?[A-Za-z0-9_.~\-/\\]+")
_WEB_ADDRESS = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://\S+")
# What is set in quotes, on one line: a path there may hold spaces. Not
# single quotes, which are as often an apostrophe.
_QUOTED = re.compile(r"`([^`\n]+)`|\"([^\"\n]+)\"|“([^”\n]+)”|「([^」\n]+)」|『([^』\n]+)』")
_HAS_EXTENSION = re.compile(r"[^./\\]\.[A-Za-z0-9_]{1,8}$")
# What ends a clause, which a path does not hold.
_CLAUSE_MARKS = re.compile(r"[,;，；。]")
# The number of a version, which is written like a file with an extension.
_A_NUMBER = re.compile(r"[0-9.]+")


def paths_named_in(text: Any) -> list[str]:
    """Return what in a text may be the path of a file: words with a path separator in them, or a file extension.

    For an entry a model made from an account of a commission, which has
    no list of files of its own: the files it rests on are the ones its
    words name. Whether such a word is a file is for ``evidence_of`` to
    tell; this only looks at how it is written.

    The runs of what a path is written with in ASCII come first, then the
    words as white space and punctuation set them apart: in text that
    puts no spaces between words such a word is a whole clause with a
    path somewhere in it, and those would use up what is looked at.
    """
    from config.nekoclaw_settings import SPACE_EVIDENCE_NAMED_MAX_PATHS

    # An address of the web is no file, whatever it ends in.
    said = _WEB_ADDRESS.sub(" ", str(text or ""))
    quotes = list(_QUOTED.finditer(said))
    plain = list(_PLAIN_PATH.finditer(said))

    def after_a_part(at: int) -> bool:
        # In quotes, right after a part with a separator and no extension
        # (``data/old``, ``D:/My``), with nothing but white space between:
        # what stands at ``at`` may be the end of a path with a space in it
        # (``data/old report.csv``), and is no file named by itself. After
        # a file (``ruff check a/b.py c/d.py``) or a word (``cat
        # README.md``) it is one.
        quote = next((match for match in quotes if match.start() < at < match.end()), None)
        if quote is None:
            return False
        earlier = [run for run in plain if quote.start() < run.start() and run.end() <= at]
        if not earlier:
            return False
        last = earlier[-1]
        gap = said[last.end() : at]
        part = last.group(0)
        return bool(gap) and not gap.strip() and ("/" in part or "\\" in part) and _HAS_EXTENSION.search(part) is None

    def whole(run: re.Match[str]) -> bool:
        if after_a_part(run.start()):
            return False
        # Not the end of a path with words of another script in it
        # (``notes/旧report.csv``): what follows the last separator before
        # it is not a file of its own name. That path is a word as white
        # space and punctuation set it apart, and is looked at as such.
        # It is when the words right before it follow a separator. (A path
        # with the letter of its drive is one run: see ``_PLAIN_PATH``.)
        before = run.start()
        while before > 0 and not said[before - 1].isascii() and not _WORD_BREAKS.match(said[before - 1]):
            before -= 1
        return before == run.start() or before == 0 or said[before - 1] not in "/\\"

    # What is in quotes and holds a space is also taken whole: a path may
    # (``data/old report.csv``). Not what is in plain double quotes and
    # holds the marks that end a clause: such quotes were paired by
    # mistake (``a 5" screen; ...``). In backticks and Chinese quotes a
    # comma may well be part of a name (``data/Q1 report, final.csv``).
    quoted = []
    for match in quotes:
        inside = next(group for group in match.groups() if group is not None).strip()
        paired_by_mistake = match.group(0).startswith('"') and _CLAUSE_MARKS.search(inside) is not None
        if " " in inside and not paired_by_mistake:
            quoted.append((match.start(), inside))

    # The words as white space and punctuation set them apart, where each begins.
    words: list[tuple[int, str]] = []
    at = 0
    for gap in [*_WORD_BREAKS.finditer(said), None]:
        stop = gap.start() if gap is not None else len(said)
        if stop > at:
            words.append((at, said[at:stop]))
        if gap is not None:
            at = gap.end()

    found: list[str] = []
    # The runs (and what is quoted) in the order they are written, then the words.
    runs = sorted([*quoted, *((run.start(), run.group(0)) for run in plain if whole(run))])
    for word in [*(item for _at, item in runs), *(item for at, item in words if not after_a_part(at))]:
        word = word.rstrip(".,;:!?…")
        if len(word) < 3 or word in found or _A_NUMBER.fullmatch(word):
            continue
        if "/" not in word and "\\" not in word and _HAS_EXTENSION.search(word) is None:
            continue
        found.append(word)
        if len(found) >= SPACE_EVIDENCE_NAMED_MAX_PATHS:
            break
    return found


def _stands(path: str) -> bool | None:
    """Whether a file an entry rests on is there. None when that cannot be told.

    It cannot be told while the drive it is on is not there, or while the
    file system refuses to say: an entry does not expire over that. A
    drive that is not there is known by its letter, so this holds where
    drives have letters (Windows: a disk that is unplugged, a share that
    is down). Where volumes are mounted below a directory (macOS, Linux)
    one that is away looks like a file that is gone: False. The entry
    expires then, and is in effect again at the first look after the
    volume is back (``check_evidence``).

    A path that leads somewhere else by now, through a link put in its
    place or in the place of a directory above it, does not stand. What
    is kept is where the file really was (``evidence_of``), and where
    such a link leads is not looked at: whether a note takes effect or
    an entry expires says nothing about a file outside the directories
    its evidence was found in.
    """
    import stat

    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        return None
    if os.path.normcase(os.path.normpath(real)) != os.path.normcase(os.path.normpath(path)):
        return False
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return None if _drive_away(path) else False
    except (OSError, ValueError):
        return None


def _drive_away(path: Any) -> bool:
    """Whether the drive or the share a path begins with is not there at this moment.

    Where drives have letters, a file on one that is unplugged, or on a
    share that is down, is not found, like one that was deleted. The
    two are told apart by whether the drive itself is there.
    """
    anchor = Path(path).anchor
    return bool(anchor) and not os.path.exists(anchor)


def _place_of(path: str) -> str:
    """Return where a file is, as far as its path tells: the drive or share on Windows, the first two directories elsewhere.

    Reads nothing. It stands for the disk or the share the file is on:
    one that does not answer is not asked again while a question about
    it is out (``_asked``). Elsewhere than on Windows that is a guess by
    where disks and shares are mounted as a rule (``/Volumes/name``,
    ``/mnt/name``, a home directory); a share mounted further down has
    the directories beside it pass for unanswered with it, until it
    answers.
    """
    drive, rest = os.path.splitdrive(os.path.normcase(os.path.abspath(path)))
    if drive:
        return drive
    return os.sep + os.sep.join([part for part in rest.split(os.sep) if part][:2])


class _Question:
    """A question to the file system that may never be answered; see ``_asked``.

    ``places`` is where the files it is about are; ``at`` the ones of
    those places it is asked about at this moment, which is all of them
    unless whoever asks says otherwise as it goes.
    """

    __slots__ = ("places", "at", "answered", "given_up")

    def __init__(self, places: Iterable[str]) -> None:
        self.places = frozenset(places)
        self.at = self.places
        self.answered = False
        self.given_up = False


# The questions to the file system that are out: asked, and not answered yet
# (``_asked``). ``given_up`` marks the ones nobody waits for any more. And the
# lock they are counted under.
_ASKING = threading.Lock()
_OUT: set[_Question] = set()


def _places_out() -> set[str]:
    """Return the places a question that was given up on is at: nothing is asked about them until it is answered."""
    with _ASKING:
        return {place for other in _OUT if other.given_up for place in other.at}


def _question(places: Iterable[str]) -> _Question:
    """Return a question about ``places`` that may be asked, counted as out. Raises TimeoutError when it may not; see ``_asked``."""
    from config.nekoclaw_settings import SPACE_EVIDENCE_ASKED_AT_ONCE

    question = _Question(places)
    with _ASKING:
        if any(other.given_up and question.places & other.at for other in _OUT):
            raise TimeoutError("a question about that place is still unanswered")
        full = len(_OUT) >= SPACE_EVIDENCE_ASKED_AT_ONCE
        if not full:
            # Counted before its thread is started, under the lock it was
            # looked at with: two that begin at one moment are two.
            _OUT.add(question)
    if full:
        logger.warning(
            "%d questions to the file system are out, which is as many as may be: nothing is known of the files "
            "notes rest on until one of them is answered",
            SPACE_EVIDENCE_ASKED_AT_ONCE,
        )
        raise TimeoutError("too many questions to the file system are out")
    return question


def _asking(question: _Question, look: Callable[[], T]) -> Callable[[], T]:
    """Return ``look`` as it is run in the thread that asks: the question is no longer out once it comes back."""

    def answered() -> T:
        try:
            return look()
        finally:
            _answered(question)

    return answered


def _answered(question: _Question) -> None:
    """Take a question off the count: it was answered, or no thread could be started to ask it in."""
    with _ASKING:
        question.answered = True
        _OUT.discard(question)


def _give_up(question: _Question) -> None:
    """Mark a question as one nobody waits for any more, unless it was answered at that very moment.

    It stays out, and counted, until its thread comes back: giving up
    ends the wait, not the question.
    """
    with _ASKING:
        if not question.answered:
            question.given_up = True


def _asked(places: Iterable[str], look: Callable[[], T]) -> T:
    """Ask the file system about files a note rests on, and wait for the answer only so long.

    For what is asked under the lock on the space's writes. A file on a
    share that does not answer would hold up every write for as long as
    it does not, and a write that is cancelled waits for its work all
    the same. So the question is asked in a thread of its own and
    waited for ``SPACE_EVIDENCE_ASK_SECONDS``. Raises TimeoutError when
    there is no answer by then; the thread ends when there is one.

    ``places`` is where the files are (``_place_of``). While a question
    that was given up on is out, none is asked about the place it is
    at: TimeoutError at once, so that a share that does not answer
    costs a thread or two and not one for every question, and the files
    elsewhere are still told about. With
    ``SPACE_EVIDENCE_ASKED_AT_ONCE`` questions out, given up on or not,
    none is asked about any place until one of them is answered (which,
    for a share that never answers, is when the host is started again):
    that is logged. TimeoutError as well when no thread can be started
    to ask in.
    """
    from config.nekoclaw_settings import SPACE_EVIDENCE_ASK_SECONDS
    from nekoclaw.host.detached import NoThread, wait_detached

    question = _question(places)
    try:
        return wait_detached(_asking(question, look), name="nekoclaw-space-file", seconds=SPACE_EVIDENCE_ASK_SECONDS)
    except TimeoutError:
        _give_up(question)
        raise
    except NoThread:
        _answered(question)
        raise TimeoutError("the file system could not be asked") from None


async def _asked_off_the_loop(places: Iterable[str], look: Callable[[Callable[[Iterable[str]], None]], T]) -> T:
    """``_asked`` for the event loop, where nothing is held while it waits: for ``SPACE_EVIDENCE_BEFORE_SECONDS``.

    One question may be about files in several places. ``look`` is
    handed what it says where it is with, as it goes from one to the
    next: given up on, the question is out for the place it is at, and
    the others are still asked about (a share that does not answer
    does not have the files of the working directory pass for
    unanswered).
    """
    from config.nekoclaw_settings import SPACE_EVIDENCE_BEFORE_SECONDS
    from nekoclaw.host.detached import NoThread, run_detached

    question = _question(places)

    def at(now: Iterable[str]) -> None:
        with _ASKING:
            if question.given_up:
                # Nobody waits for it any more: the file it was at has
                # answered by now, and nothing more is asked. What may be
                # looked at could have changed since (the user may have
                # taken back reading outside the working directories).
                raise _NobodyWaits
            question.at = frozenset(now)

    try:
        asking = run_detached(_asking(question, lambda: look(at)), name="nekoclaw-space-file")
    except NoThread:
        _answered(question)
        raise TimeoutError("the file system could not be asked") from None
    # Looked at here when nobody waits for it any more.
    asking.add_done_callback(lambda done: done.cancelled() or done.exception())
    try:
        return await asyncio.wait_for(asyncio.shield(asking), SPACE_EVIDENCE_BEFORE_SECONDS)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        # Also when whoever asked was stopped: the thread is still at it.
        _give_up(question)
        raise


class _NobodyWaits(Exception):
    """Ends a look for files that nobody waits for any more; see ``_asked_off_the_loop``."""


def _not_looked_at(named: list[str], looked_at: int | None = None) -> list[tuple[str, str]]:
    """Return what is said of the files a note names when the file system did not answer: none was looked at."""
    from config.nekoclaw_settings import SPACE_EVIDENCE_MAX_PATHS

    # As they were named: a name keeps the spaces it has. On one line
    # where it has more than one.
    unseen = [
        _one_line(item) if "\n" in item.strip() or "\r" in item.strip() else item.strip()
        for item in named[: looked_at or SPACE_EVIDENCE_MAX_PATHS]
    ]
    # Told by its end, where its name is (``_shown``).
    return [(_shown(text, 24), "not looked at, the disk does not answer") for text in unseen if text]


def _stood(paths: list[str]) -> dict[str, bool | None]:
    """Return whether each of ``paths`` is there (``_stands``), waited for only so long.

    For a caller under the lock on the space's writes (``_asked``). The
    files of one place are asked about together. Those of a place that
    does not answer are not in what is returned: nothing is known of
    them.
    """
    by_place: dict[str, list[str]] = {}
    for path in paths:
        by_place.setdefault(_place_of(path), []).append(path)
    there: dict[str, bool | None] = {}
    for place, files in by_place.items():
        try:
            there.update(_asked([place], lambda files=files: {path: _stands(path) for path in files}))
        except TimeoutError:
            continue
    return there


def _recorded(paths: Iterable[Any], outside: Iterable[str] = (), allowed: bool = True) -> tuple[list[str], list[str]]:
    """Return the files among ``paths`` to keep on record, and the ones of those of which nothing is told just now.

    For files that were found when a note was filed and are counted on
    again later. One that is known to be gone is not kept: it no longer
    speaks for the note. One of which nothing is told stays on record,
    and speaks for nothing at this moment: one outside the working
    directories (``outside``) while the user does not let task agents
    read there unasked, which is not looked at; and any when the file
    system does not answer in time (``_stood``, this being asked under
    the lock on the space's writes). What is not known to be gone is
    not taken off the record: a slow disk would lose a note the files
    it rests on for good.
    """
    apart = set(outside)
    named = [path for path in paths if isinstance(path, str) and path]
    there = _stood([path for path in named if allowed or path not in apart])
    kept = [path for path in named if there.get(path) is not False]
    return kept, [path for path in kept if not there.get(path)]


def _gone(evidence: Iterable[str], outside: Iterable[str] = (), allowed: bool = True) -> bool | None:
    """Whether every file an entry rests on is gone. None when that cannot be told.

    False as soon as one of them is there. None when none is seen to be
    there and one of them cannot be looked at: its drive is away, or it
    lies outside the working directories (``outside``) and the user no
    longer lets task agents read there unasked.
    """
    apart = set(outside)
    unknown = False
    for path in evidence:
        there = None if (path in apart and not allowed) else _stands(path)
        if there:
            return False
        if there is None:
            unknown = True
    return None if unknown else True


def _outside_of(paths: Iterable[str], bases: Iterable[str]) -> list[str]:
    """Return the files among ``paths`` that lie in none of ``bases``, going by where each really is."""
    places = [_real_place(str(base)) for base in bases if base]
    return [path for path in paths if not any(_lexically_inside(path, place) for place in places)]


# The heading the host gives a text that is added to an entry: the day.
_ADDITION = re.compile(r"## \d{4}-\d{2}-\d{2}")


def _texts_of(body: str, marks: Iterable[str]) -> list[str] | None:
    """Return the texts an entry was given, one after another. None when it does not say them as they were filed.

    For an entry the host made whose texts are filed somewhere else one
    by one. ``marks`` are the marks it keeps of the texts it was given,
    in the order they were filed (``texts`` in its front matter). The
    host puts a dated heading between two texts. Which heading is the
    host's is told by what stands before it: since the last one, that
    must be the very text that was filed next. A text may begin with a
    heading like that, have one inside it, or begin like another text
    of the entry; the line is part of what it says then, and stays.

    None when the texts do not come out so, each with its mark and in
    that order: the user rewrote something in an editor, and where one
    text ends and the next begins cannot be told any more.
    """
    expected = list(marks)
    texts: list[str] = []
    lines: list[str] = []

    def next_text() -> bool:
        return len(texts) < len(expected) and _digest("\n".join(lines)) == expected[len(texts)]

    for line in body.split("\n"):
        if _ADDITION.fullmatch(line.strip()) and next_text():
            texts.append("\n".join(lines).strip())
            lines = []
        else:
            lines.append(line)
    if not next_text():
        return None
    texts.append("\n".join(lines).strip())
    return texts if len(texts) == len(expected) else None


def _notes_of(text: str) -> dict[str, Any]:
    """Return what a ``notes.json`` holds. Raises OSError for one that is not what this module writes.

    Taken for empty, such a file would be written over with the little
    that is being added to it. What else is in it stays as it is.
    """
    if not text.strip():
        return {"version": _NOTES_FILE_VERSION, "removed": []}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise OSError("notes.json cannot be read; it is left as it is") from exc
    if not isinstance(data, dict) or not isinstance(data.get("removed", []), list):
        raise OSError("notes.json is not what the host writes; it is left as it is")
    return {
        **data,
        "version": _NOTES_FILE_VERSION,
        "removed": [item for item in data.get("removed", []) if isinstance(item, dict)],
    }


def _notes_text(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _strings(value: Any) -> list[str]:
    """Return the strings of a list read from a file that a person may have edited."""
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


def _uncounted(others: list[tuple[str, str]]) -> str:
    """Return what a task agent is told about the files it named that do not count as evidence; empty when all do."""
    if not others:
        return ""
    from config.prompts.prompts_nekoclaw_space import SPACE_EVIDENCE_NOT_COUNTED

    return SPACE_EVIDENCE_NOT_COUNTED.format(paths=", ".join(f"{_shown(path, 40)} ({why})" for path, why in others))


def _outside_in(document: _Document) -> list[str]:
    """Return the files an entry rests on that lie outside the working directories, as its front matter lists them."""
    raw = document.get("evidence_outside")
    if not raw:
        return []
    try:
        listed = _strings(json.loads(raw))
    except ValueError:
        return []
    evidence = _evidence_in(document)
    return [path for path in listed if path in evidence]


def _status_of(document: _Document) -> str:
    """Return the state an entry says it is in.

    Pending only for one the host made, which keeps marks of its texts:
    a file the user wrote is theirs whatever it says, and is not removed
    for having waited too long.
    """
    said = document.get("status").strip().lower()
    if said == ENTRY_EXPIRED:
        return ENTRY_EXPIRED
    if said == ENTRY_PENDING and _listed(document, "texts"):
        return ENTRY_PENDING
    return ENTRY_ACTIVE


def _set_evidence(document: _Document, paths: list[str], outside: Iterable[str] = ()) -> None:
    """Write the files an entry rests on into its front matter: one line, as JSON (a path may hold a comma).

    ``outside`` are the ones that lie outside the working directories;
    those of them that are among ``paths`` are listed as such.
    """
    if paths:
        document.set("evidence", json.dumps(paths, ensure_ascii=False))
    else:
        document.drop("evidence")
    apart = set(outside)
    still = [path for path in paths if path in apart]
    if still:
        document.set("evidence_outside", json.dumps(still, ensure_ascii=False))
    else:
        document.drop("evidence_outside")


def _removed_from(records: Iterable[dict[str, Any]]) -> tuple[set[str], set[str]]:
    """Return the titles and the marks of what was removed from a layer, as entries are compared to them."""
    removed = list(records)
    titles = {_normal(item.get("title")) for item in removed} - {""}
    # The mark of no text tells nothing: it would be that of every entry
    # that is empty and of every part of one that is. Left out here as
    # well as where records are written, for the records that have it.
    marks = {mark for item in removed for mark in _strings(item.get("marks"))} - {_NO_TEXT}
    return titles, marks


def _removed_note(removed: tuple[set[str], set[str]], mark: Any, title: Any) -> bool:
    """Whether a note is one the user removed: by the mark of its text, or by its title (``_removed_from``)."""
    titles, marks = removed
    return mark in marks or _normal(title) in titles


def _gone_for_good(path: Path) -> bool:
    """Whether a file is not there any more, as far as that is known for certain.

    Only when the file system says there is no such file, on a drive
    that is there. One that cannot be looked at just now (a drive that
    is unplugged, a share that is down, a directory that may not be
    read) may be there all the same.
    """
    try:
        os.stat(path)
    except FileNotFoundError:
        return not _drive_away(path)
    except (OSError, ValueError):
        return False
    return False


def _may_be_a_file(path: Path) -> bool:
    """Whether a path may name a file at this moment: it does, or that cannot be told.

    Not when the file system says there is no such file, on a drive
    that is there; nor when something else stands there (a directory by
    that name). One that cannot be looked at just now may be a file all
    the same.
    """
    import stat

    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except FileNotFoundError:
        return _drive_away(path)
    except (OSError, ValueError):
        return True


def _removed_there(removed: tuple[set[str], set[str]], text: str, title: str) -> bool:
    """Whether an entry is one the user removed, going by what was removed from a layer (``_removed_from``).

    By any of the texts it holds, or by its title, as a note that is
    filed is told to be one (``_take_note``). ``text`` is the entry as
    it was read to be copied; ``title`` is what it is called when its
    text names no title.

    The texts it holds are the ones it was given, of which it keeps the
    marks, and what it says now: its text may have been replaced in an
    editor since, and a text that was removed elsewhere stands in it
    without a mark then, as all it says or as a part of it.
    """
    titles, wanted = removed
    document = _parse_document(text)
    if _normal(document.get("name") or title) in titles:
        return True
    if wanted & {*_listed(document, "texts"), _digest(document.body)}:
        return True
    # None when the entry is too long to look through: not told then.
    return bool(_standing_marks(document.body, wanted))


def _speaks_no_more(known: _Known, told: dict[str, bool | None]) -> bool:
    """Whether a file that spoke for a note is gone, or not told of, now that it was asked about once more.

    ``told`` is what was told this time of each file that was asked
    about: True, False, or None for nothing. A file spoke for the note
    when it was found as the note was judged: it is among
    ``known.evidence`` and not among ``known.untold``. Of two answers
    the later one counts, so a note that is in effect by its files and
    nothing else is judged anew then (``_judged``); for both ways a
    pending entry takes effect, where it is and in another entry.
    """
    return any(path in told and not told[path] and path not in known.untold for path in known.evidence)


def _keep_known(
    document: _Document,
    known: _Known | None,
    allowed: bool = True,
    *,
    pending: bool = False,
    judged: bool = False,
    told: dict[str, bool | None] | None = None,
    judged_with: _Known | None = None,
) -> bool:
    """Write into an entry what a filing adds to what is known of it. Returns whether anything changed.

    Whatever is filed into an entry that had expired has it in effect
    again, and so has what is filed into a pending one unless it is
    ``pending`` itself; of the files it rested on, the ones that are gone
    are no longer what it rests on, or it would expire at the next look.
    The stronger way of knowing it is kept, the files are added to the
    ones it has, and a commission that has not filed it before is counted.

    ``allowed`` is whether the user lets task agents read outside the
    working directories unasked: only then are the files an entry rests
    on there looked at.

    ``judged`` says that a note takes effect with this entry (one that
    waited, with its text or under its title, or one that had expired),
    and by nothing but the files ``known`` tells of, asked a moment
    before.
    Asked about once more here, a file that spoke for the note may be
    gone, or nothing may be told of it: of two answers the later one
    counts. Raises ``_Rewritten`` then, with nothing of this entry
    changed, and the note is judged anew (``_take_note``); the pending
    entries it confirmed by its title before that have taken effect,
    each by itself (``_judged``). A note that takes effect by
    something else (checked by hand, seen by enough commissions) is
    not held up: it is written, without the files found gone. What
    was told of each file that was asked about is put into ``told``,
    for whoever tells what became of the note. ``judged_with`` is what
    the note was judged with where that is not ``known``: a pending
    entry that goes into one that had expired brings what it says of
    itself, and the entry comes back by the note.
    """
    from config.nekoclaw_settings import SPACE_EVIDENCE_MAX_PATHS, SPACE_SEEN_BY_KEPT

    changed = False
    # Files found gone here, asked about last: not put back on record by
    # what ``known`` tells of them, which was asked a moment before.
    gone_now: set[str] = set()
    # And the ones not found gone here (there, or not told of): on
    # record, though the look a moment before found them gone.
    not_gone_now: set[str] = set()
    status = _status_of(document)
    waited = status == ENTRY_PENDING
    if status == ENTRY_EXPIRED or (status == ENTRY_PENDING and not pending):
        apart = _outside_in(document)
        document.drop("status")
        document.drop("expired")
        document.drop("notice")
        rested = _evidence_in(document)
        # Asked under the lock on the space's writes, so only for so long;
        # one of which nothing is told stays, like one that cannot be told.
        asked = [path for path in rested if allowed or path not in apart]
        there = _stood(asked)
        answers = {path: there.get(path) for path in asked}
        judge = judged_with if judged_with is not None else known
        if judged and judge is not None and _speaks_no_more(judge, answers):
            raise _Rewritten("a file the note was judged with does not speak for it by now", told=answers)
        gone_now = {path for path, stands in answers.items() if stands is False}
        not_gone_now = {path for path, stands in answers.items() if stands is not False}
        if told is not None:
            told.update(answers)
        _set_evidence(document, [path for path in rested if path not in gone_now], apart)
        changed = True
    if known is None:
        return changed
    confidence = _stronger(_confidence(document.get("confidence")), known.confidence)
    if confidence and confidence != document.get("confidence"):
        document.set("confidence", confidence)
        changed = True
    before, apart = _evidence_in(document), _outside_in(document)
    evidence = _joined(
        [path for path in before if path not in known.gone or path in not_gone_now],
        [path for path in known.evidence if path not in gone_now],
        SPACE_EVIDENCE_MAX_PATHS,
    )
    outside = [path for path in evidence if path in {*apart, *known.outside}]
    if evidence != before or outside != apart:
        _set_evidence(document, evidence, outside)
        changed = True
    # For reference only: none that it rests on by now (that one says more).
    # The ones it rests on are left out before the cap, so that a file it
    # rests on does not push out one it notes and is then dropped itself.
    noted_before = _noted_in(document)
    noted = _joined(
        _not_among(noted_before, evidence),
        _not_among(known.noted, evidence),
        SPACE_EVIDENCE_MAX_PATHS,
    )
    if noted != noted_before:
        _set_noted(document, noted)
        changed = True
    # An entry that names nobody who has seen it was filed from the
    # console, or before this was kept: the commissions it came from are
    # the ones that have seen it.
    # A pending entry nobody is named for (made by the summary of a
    # project, which is no sighting) was seen by none that counts: the
    # commissions that file it are counted from the first.
    seen_by = _listed(document, "seen_by") or _listed(document, "sources")
    if document.get("seen"):
        seen = _seen_in(document)
    else:
        seen = len(seen_by) if waited else max(len(seen_by), 1)
    counted = False
    for observer in (*known.seen_before, known.observer):
        if observer and observer not in seen_by:
            seen_by.append(observer)
            seen += 1
            counted = True
    if counted:
        document.set("seen", str(seen))
        document.set("seen_by", ", ".join(seen_by[-SPACE_SEEN_BY_KEPT:]))
        changed = True
    return changed


def _listed(document: _Document, key: str) -> list[str]:
    """Return what a line of the front matter lists, separated by commas."""
    return [item.strip() for item in document.get(key).split(",") if item.strip()]


def _points_at(line: str, marks: tuple[str, str]) -> bool:
    """Whether the link a line of an index begins with is one of the marks of a target.

    The first link of the line, not any link in it: what follows the link
    is the entry's description, which can quote the link of another entry
    (a task agent sees the index written just so). Told by a mark anywhere
    in it, such a line would be taken for the other entry's and be changed
    or removed with it.
    """
    first = line.find("](")
    return first >= 0 and any(line.startswith(mark, first) for mark in marks)


def _with_line_first(lines: list[str], line: str) -> list[str]:
    """Return the lines of an index with ``line`` before the first entry it lists.

    When it lists nothing yet: after whatever it holds, before the blank
    lines it ends in.
    """
    position = next((number for number, item in enumerate(lines) if item.startswith("- ")), None)
    if position is None:
        position = len(lines)
        while position > 0 and not lines[position - 1].strip():
            position -= 1
    return [*lines[:position], line, *lines[position:]]


def _lines_as_text(lines: list[str]) -> str:
    """Return lines as the text of a file, which ends in a line end."""
    result = "\n".join(lines)
    return result if result.endswith("\n") else result + "\n"


# How much of a text given in the console is looked through for what still
# stands in it. Every part is marked with all that follows it, line by
# line, so the work is the parts times the lines, and the parts times the
# characters: about a third of a second at either limit. The text itself
# has no limit; it is the user's.
_STANDING_STEPS_MAX = 1_000_000
_STANDING_CHARACTERS_MAX = 200_000_000


def _standing_marks(body: str, marks: Iterable[str]) -> set[str] | None:
    """Return which of ``marks`` are those of a text that stands in an entry by itself: one part of it, or several in a row.

    A part is what stands between blank lines, which is how
    ``_said_already`` tells that a text is there. For telling which of an
    entry's marks still mean something once its text was replaced: a mark
    of a text that is no longer there is dropped, or the marks would grow
    with every change; one of a text that still stands, with more written
    around it, is kept, or the same text would be added once more.

    None when the text is too long to look through while every other
    change to the space waits.
    """
    wanted = set(marks)
    if not wanted:
        return set()
    lines = body.split("\n")
    firsts = [
        number for number, line in enumerate(lines) if line.strip() and (number == 0 or not lines[number - 1].strip())
    ]
    if len(firsts) * len(lines) > _STANDING_STEPS_MAX or len(firsts) * len(body) > _STANDING_CHARACTERS_MAX:
        return None
    lasts = {
        number
        for number, line in enumerate(lines)
        if line.strip() and (number + 1 == len(lines) or not lines[number + 1].strip())
    }
    # Each line as ``_digest`` reads it: white space is no part of a mark, so
    # the lines of a text, one after another, are the text.
    plain = [_normal(line).encode("utf-8") for line in lines]
    found: set[str] = set()
    for first in firsts:
        if len(found) == len(wanted):
            break
        # One pass for all that begins here, each as ``_digest`` would mark it.
        running = hashlib.sha256()
        for number in range(first, len(lines)):
            running.update(plain[number])
            if number in lasts:
                mark = running.copy().hexdigest()[:12]
                if mark in wanted:
                    found.add(mark)
    return found


def _said_already(document: _Document, content: str) -> bool:
    """Whether an entry has ``content``, as what it said first or as one of its additions.

    The same words count as the same text however they are spaced,
    punctuated and capitalised (``_normal``). Told by the marks the entry
    keeps of every text that was filed
    (``texts`` in its front matter), not by where its dated headings are:
    a note can have such a heading inside it, and a shorter note can be
    the end of a longer one. The text must still be there as well, so one
    the user took out by hand is added again. A text that is all the
    entry says is there already whatever the marks are: the entry may
    have been written by hand, or its text replaced in an editor.

    An entry that keeps no marks (one the user wrote by hand, with or
    without front matter) cannot say what it was given: whatever stands
    in it by itself is taken to have been. It is never given marks
    either. Which of its parts were texts of their own cannot be told,
    and with marks for some of them the others would count as never said
    from then on, and be added a second time.
    """
    if _normal(document.body) == _normal(content):
        return True
    mark = _digest(content)
    kept = _listed(document, "texts")
    if kept and mark not in kept:
        return False
    # An entry that keeps no marks (one written by hand) cannot say what it
    # was given: what stands in it by itself is taken to have been.
    # Still there as a text of its own, between blank lines or at an end of
    # the entry: found inside another sentence, it is not what was filed.
    standing = _standing_marks(document.body, [mark])
    if standing is not None:
        return mark in standing
    # Too long to look through part by part: looked for as it was written.
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


# How many commissions the count of notes added is kept for at one time.
_COUNTED_COMMISSIONS = 512
# Room for the line that says a part was cut, when a part is cut further.
_BRIEF_CUT_ROOM = 12
# In a path with forward slashes: a step that stays where it is, and a name
# with the step back that undoes it. How many steps back are followed. A
# name may hold spaces: between quotation marks it is one name, and where it
# is not, reading it as one asks a question too many at the worst.
_STEP_IN_PLACE = re.compile(r"/\.(?=/)")
_STEP_BACK = re.compile(r"/(?!\.\.?/)[^/\"']+/\.\.(?=/|[\s\"']|$)")
_STEPS_BACK_MAX = 64
# How a command writes the home directory: a shell of the Unix world (on
# Windows as well, where it has the variable of Windows too), cmd, PowerShell.
_HOME_NAMES = (
    "~",
    "$HOME",
    "${HOME}",
    "$USERPROFILE",
    "${USERPROFILE}",
    "%USERPROFILE%",
    "$env:USERPROFILE",
    "${env:USERPROFILE}",
    "$env:HOME",
    "${env:HOME}",
)


class SpaceError(RuntimeError):
    """Something asked of the space cannot be done: an unknown entry, an unusable name."""


class NoteRemoved(SpaceError):
    """A note is one the user removed from the space, and is not kept again."""


class _NoPlace(SpaceError):
    """A note would use one of the places of a commission that has none left."""


class _Rewritten(SpaceError):
    """What a note was judged with does not hold as the note is written: it is to be judged anew.

    A pending entry it was found in by its text no longer says that
    text; or a file that spoke for it is gone, or not told of, now that
    it was asked about once more. ``told`` is what that last look told
    of each file it asked about (True, False, or None for nothing), and
    ``of`` the entry whose record those files are on.
    """

    def __init__(self, reason: str, *, told: dict[str, bool | None] | None = None, of: str = "") -> None:
        super().__init__(reason)
        self.told = dict(told or {})
        self.of = of


class _NotAsWritten(OSError):
    """A file was replaced, and what it holds afterwards is not what was written."""


class _LineNotAdded(OSError):
    """An entry is in its file, and its index could not be read or written, so no line was added for it."""

    def __init__(
        self,
        entry: Any,
        how: str,
        *,
        charged: bool | None = None,
        gone: tuple[str, ...] = (),
        spoke: tuple[str, ...] = (),
    ) -> None:
        super().__init__(f"the index has no line for {entry.id}")
        self.entry = entry
        self.entry_id = str(entry.id)
        self.how = how
        # As ``_Taken.gone`` and ``_Taken.spoke``.
        self.gone = gone
        self.spoke = spoke
        # Whether it used one of its commission's places; by default every
        # filing does that put something into the entry.
        self.charged = how != _THERE if charged is None else charged


class _LeftAside(OSError):
    """A directory could not be moved to another volume and is not where it was: it has the name it was set aside under."""


# ---------------------------------------------------------------------------
# The space
# ---------------------------------------------------------------------------


class TaskAgentSpace:
    """Reads and writes the task agent space. See the module docstring.

    ``paths`` is called in a worker thread (resolving the data root reads
    configuration). The methods without a leading underscore are coroutines
    and may be called from the event loop; the ones with it block.
    ``outside_readable`` says whether the user lets task agents read
    outside the working directories without being asked; with none given
    they are taken not to.
    """

    def __init__(
        self,
        paths: Callable[[], SpacePaths],
        *,
        ledger: Any = None,
        clock: Callable[[], float] = time.time,
        outside_readable: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self._paths_source = paths
        self._ledger = ledger
        self._clock = clock
        self._outside_readable = outside_readable
        self._lock: asyncio.Lock | None = None
        # How many notes each commission has added with ``note_lesson``,
        # and how many of its notes are on their way to be written; by the
        # commission she gave (its task agents' count with its own). Kept
        # while the host runs. Kept apart: only what was added is what
        # commissions are forgotten by when there are many.
        self._added_by: dict[str, int] = {}
        self._adding: dict[str, int] = {}
        # How far the last look at what the entries rest on got, among
        # all their files; the next one goes on from there.
        self._evidence_looked_until = 0
        # For each place, the lock a note holds while the files it names
        # there are looked for before its write (``_evidence_before``).
        self._asking_about: dict[str, asyncio.Lock] = {}
        # The entries a look at those files that was given up on is still
        # at, each with the files of it that are looked at
        # (``check_evidence``).
        self._evidence_unanswered: set[tuple[str | None, str, tuple[str, ...]]] = set()

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

    async def _evidence_before(
        self, raw: Any, bases: Iterable[str | os.PathLike[str]], *, looked_at: int | None = None
    ) -> tuple[bool, tuple[list[str], list[tuple[str, str]], list[str]]]:
        """Look for the files a note names (``evidence_of``) before its write takes its turn.

        Returns whether the ones outside the working directories were
        looked for, and what was found: the files that count, the others
        with why they do not, and the ones among the first that lie
        outside ``bases``.

        No file a note names is looked for under the lock on the space's
        writes: on a share that does not answer it would hold up every
        write. Here nothing is held, and the look is waited for longer
        (``_asked_off_the_loop``); when the file system does not answer
        by then, none counts, and each is said not to have been looked
        at. Whether task agents may read outside the working directories
        unasked is asked for the look, and again once the write's turn
        has come (``_at_its_turn``).
        """
        roots = [str(base) for base in bases if base]
        named = [item for item in (raw if isinstance(raw, (list, tuple)) else []) if isinstance(item, str) and item.strip()]
        if not named:
            # No file is named: there is nothing to ask.
            return await self._outside_allowed(), ([], [], [])
        from config.nekoclaw_settings import SPACE_EVIDENCE_MAX_PATHS

        first = named[: looked_at or SPACE_EVIDENCE_MAX_PATHS]
        # One question at a time about a place, among the notes whose
        # files are looked for before their writes: several that name
        # files on one share that does not answer cost one thread, not
        # one each. The ones that waited find the place out, and are not
        # asked. (In one order, so that no two wait for each other.)
        places = {*(_place_of(root) for root in roots), *(_place_of(item.strip()) for item in first if os.path.isabs(item.strip()))}
        held: list[asyncio.Lock] = []
        try:
            for place in sorted(places):
                lock = self._asking_about.setdefault(place, asyncio.Lock())
                await lock.acquire()
                held.append(lock)
            # Asked once its turn at these places has come, not before it
            # waited for it: the user may have taken it back meanwhile,
            # and a file out there is not looked for then.
            asked = await self._outside_allowed()
            return asked, await self._looked_for(first, roots, asked)
        finally:
            for lock in reversed(held):
                lock.release()

    @staticmethod
    async def _looked_for(
        first: list[str], bases: list[str], asked: bool
    ) -> tuple[list[str], list[tuple[str, str]], list[str]]:
        """Look for the files ``first`` names in ``bases``, leaving out what is in a place that does not answer.

        A working directory in such a place is not looked in, and a file
        there is said not to have been looked at; what is in the others
        is looked for all the same, and counts as ever.
        """
        from config.nekoclaw_settings import SPACE_EVIDENCE_MAX_PATHS

        out = _places_out()
        roots = [root for root in bases if _place_of(root) not in out]
        inside = {_place_of(root) for root in roots}

        # With no working directory at all, a path that is not whole has
        # nothing to be read against. That is told at once, and needs no
        # look at the file system: no disk is at fault, and none is
        # waited for, however many questions are out.
        baseless = [item for item in first if not bases and not os.path.isabs(item.strip())]
        told = evidence_of(baseless, [], looked_at=len(baseless))[1] if baseless else []
        asking = [item for item in first if item not in baseless]

        def reached(item: str) -> bool:
            where = item.strip()
            if os.path.isabs(where):
                return _place_of(where) not in out
            # Read against the working directories: looked for unless
            # every one of them is in a place that does not answer.
            return bool(roots)

        wanted = [item for item in asking if reached(item)]
        unasked = [item for item in asking if not reached(item)]
        unseen = [*told, *(_not_looked_at(unasked, len(unasked)) if unasked else [])]
        if not wanted:
            return [], unseen, []
        places = {*inside, *(_place_of(item.strip()) for item in wanted if os.path.isabs(item.strip()))}

        def look(at: Callable[[Iterable[str]], None]) -> tuple[list[str], list[tuple[str, str]], list[str]]:
            # Where the work was done first, each directory where it is:
            # one on a share that does not answer is what the question is
            # out for, not the files that are looked for after it.
            for root in roots:
                at([_place_of(root)])
                _real_place(root)
            # Then the files one by one, saying where each is: as
            # ``evidence_of`` does them, until enough of them count.
            counted: list[str] = []
            others: list[tuple[str, str]] = []
            for item in wanted:
                if len(counted) >= SPACE_EVIDENCE_MAX_PATHS:
                    break
                at([_place_of(item.strip())] if os.path.isabs(item.strip()) else inside)
                found, rest = evidence_of([item], roots, looked_at=1, outside=asked)
                counted, others = _joined(counted, found, SPACE_EVIDENCE_MAX_PATHS), [*others, *rest]
            at(inside)
            return counted, [*others, *unseen], _outside_of(counted, roots)

        try:
            return await _asked_off_the_loop(places, look)
        except (TimeoutError, asyncio.TimeoutError):
            return [], [*told, *_not_looked_at(asking, len(asking))], []

    @staticmethod
    def _at_its_turn(
        asked: bool, found: tuple[list[str], list[tuple[str, str]], list[str]], allowed: bool
    ) -> tuple[list[str], list[tuple[str, str]], list[str], list[str]]:
        """Return what ``_evidence_before`` found, as it counts now that the write's turn has come.

        The files that are on record with the note, the others with why
        they are not, the ones of the first that lie outside the working
        directories, and the ones of the first of which nothing is told
        at this moment, which speak for nothing.

        The user may have taken back meanwhile that task agents read
        outside the working directories unasked: a file found out there
        does not count then. Where they gave it meanwhile, the files
        out there were not looked for, and do not count this time. And
        the write may have waited its turn for a while: a note takes
        effect by a file that exists, not by one that did. So each is
        asked about once more, only so long (``_stood``), this being
        under the lock. One that is gone is no longer on record; one of
        which nothing is told was there a moment ago, and stays.
        """
        counted, others, apart = found
        if asked and not allowed and apart:
            out = set(apart)
            others = [*others, *((path, "outside the working directory") for path in counted if path in out)]
            counted, apart = [path for path in counted if path not in out], []
        there = _stood(counted)
        gone = [path for path in counted if there.get(path) is False]
        if gone:
            others = [*others, *((path, "not found") for path in gone)]
            counted = [path for path in counted if path not in gone]
            apart = [path for path in apart if path not in gone]
        return counted, others, apart, [path for path in counted if not there.get(path)]

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
                time.sleep(_WRITE_RETRY_SECONDS * (attempt + 1))
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
                    time.sleep(_WRITE_RETRY_SECONDS * (look + 1))
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

    def _edit_text(self, path: Path, change: Callable[[str], str]) -> bool:
        """Change a text file by a function of its text. Returns whether it changed.

        Read, changed and written back; when the file was written by someone
        else in between (the user saving it in an editor), it is read again,
        so their change is not written over. So it is when the file holds
        something else right after it was replaced: the change is made
        again to what is there then. A file that could not be looked at
        after it was replaced is read once more, see ``_holds``.
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
                continue
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

    @staticmethod
    def _index_line_in(text: str, target: str) -> str | None:
        """Return the line of an index's text that points at ``target``, as it is written there, or None."""
        marks = _index_marks(target)
        return next((item for item in text.split("\n") if _points_at(item, marks)), None)

    def _index_remove(self, index: Path, target: str) -> None:
        marks = _index_marks(target)

        def change(text: str) -> str:
            if not text:
                return text
            return "\n".join(item for item in text.split("\n") if not _points_at(item, marks))

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
        return self._entry_of(document, path.stem, project, modified, with_content=with_content)

    @staticmethod
    def _entry_of(document: _Document, name: str, project: str | None, modified: float, *, with_content: bool) -> Entry:
        return Entry(
            id=name,
            project=project,
            title=document.get("name") or name,
            description=document.get("description"),
            type=_entry_type(document.get("type")),
            created=document.get("created") or _date(modified),
            updated=document.get("updated") or _date(modified),
            sources=tuple(_listed(document, "sources")),
            content=document.body.strip() if with_content else "",
            confidence=_confidence(document.get("confidence")),
            evidence=tuple(_evidence_in(document)),
            seen=_seen_in(document),
            status=_status_of(document),
            seen_by=tuple(_listed(document, "seen_by")),
            evidence_outside=tuple(_outside_in(document)),
            noted=tuple(_noted_in(document)),
        )

    def _entries(self, directory: Path, project: str | None, *, with_content: bool = False) -> list[Entry]:
        try:
            files = sorted(item for item in directory.iterdir() if item.suffix == ".md" and item.is_file())
        except OSError:
            return []
        entries = [self._entry_from(item, project, with_content=with_content) for item in files]
        return [entry for entry in entries if entry is not None]

    def _same_topic(self, directory: Path, project: str | None, title: str) -> Entry | None:
        """Return the entry of a directory that is about the topic of ``title``, if there is one.

        An entry that has expired is one only by its very title: what it
        says rested on files that are gone, and a note that is merely
        about something like it does not bring that back. A pending entry
        is none at all: nothing is added to a guess for being about the
        same thing.
        """
        from config.nekoclaw_settings import SPACE_SAME_TOPIC_SIMILARITY
        from nekoclaw.host.selection import similarity

        wanted = normalize_name(title)
        best: tuple[float, Entry] | None = None
        for entry in self._entries(directory, project):
            if entry.status == ENTRY_PENDING:
                continue
            if normalize_name(entry.title) == wanted:
                return entry
            if entry.status == ENTRY_EXPIRED:
                continue
            score = similarity(entry.title, title)
            if score >= SPACE_SAME_TOPIC_SIMILARITY and (best is None or score > best[0]):
                best = (score, entry)
        return best[1] if best is not None else None

    def _same_note(
        self, directory: Path, project: str | None, title: str, content: str
    ) -> tuple[Entry | None, bool, list[tuple[Entry, bool]]]:
        """Return the entries of a directory that are this very note.

        It is the same note when an entry has the text (as ``_normal``
        compares texts), or is called the same (as it compares titles):
        two commissions that find the same thing do not write it in the
        same words, but a task agent that adds to what is on file uses its
        title. An entry that is merely about a similar topic is not the
        same note; see ``_same_topic`` for that.

        First the one that is in effect or has expired, and whether it
        has the text: the one that has it, else the one of that title.
        Then the pending ones, each with whether it has the text.
        """
        name = _normal(title)
        there: Entry | None = None
        called: Entry | None = None
        waits: list[tuple[Entry, bool]] = []
        try:
            files = sorted(item for item in directory.iterdir() if item.suffix == ".md" and item.is_file())
        except OSError:
            return None, False, []
        for path in files:
            text = self._read_text(path)
            if text is None:
                continue
            document = _parse_document(text)
            try:
                modified = path.stat().st_mtime
            except OSError:
                modified = self._clock()
            has = _said_already(document, content)
            titled = bool(name) and _normal(document.get("name") or path.stem) == name
            if not has and not titled:
                continue
            entry = self._entry_of(document, path.stem, project, modified, with_content=False)
            if entry.status == ENTRY_PENDING:
                waits.append((entry, has))
            elif has:
                there = there or entry
            else:
                called = called or entry
        if there is not None:
            return there, True, waits
        return called, False, waits

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
        into: Entry | None = None,
        known: _Known | None = None,
        may_add: bool = True,
        pending: bool = False,
        dated: tuple[str, str] | None = None,
        allowed: bool = True,
        must_say: bool = False,
        by_files: bool = False,
        told: dict[str, bool | None] | None = None,
        judged_with: _Known | None = None,
    ) -> tuple[Entry, str]:
        """Keep one entry. Returns it and what became of it: a new file, added to one, or there already.

        ``must_say`` is for a pending entry (``into``) that takes effect
        because it has the text of the note: raises ``_Rewritten``,
        before anything is written, when it no longer says that text, or
        rests on other files than ``into`` did when it was found (the
        note was judged with those). It is not what the note confirmed,
        and takes no effect by it.

        An entry about the same topic is added to: it gets a section with
        today's date at its end, and what it said before stays. Only a
        text that is there already (as ``_normal`` compares texts), as
        what the entry said first or as one of its additions, is not added
        again; one that merely occurs inside a longer text is.

        Whether a note takes effect is told before (``_take_note``).
        ``into`` is the entry that was found to be the same note, where
        one was; ``known`` is what is known of the note beyond its words,
        None for one nobody judged (filed from the console). An entry that
        had expired is in effect again once something is filed into it.

        ``pending`` is for a note that does not take effect: it is kept in
        an entry that says so, and how it is confirmed, and has no line in
        the index. ``into`` is then the pending entry it is the same note
        as, where there is one; no entry about its topic is looked for,
        as a guess adds nothing to what is in effect. Filed into a pending
        entry without ``pending``, a note has the entry take effect: it no
        longer says it is pending, and gets its line. ``dated`` is for a
        pending one that was filed elsewhere some time ago and is brought
        along: the day it was first filed and the day it was last filed,
        which it keeps.

        ``by_files``, ``told`` and ``judged_with`` are as for
        ``_keep_known``.

        ``may_add`` is False for a note whose text was seen to be in
        ``into`` and whose commission has no place left: should the text
        be gone from the entry by now (taken out in an editor at that
        very moment), it is not added again. Raises ``_NoPlace`` then,
        before anything is written. ``allowed`` is whether the user lets
        task agents read outside the working directories unasked; see
        ``_keep_known``.
        """
        from config.nekoclaw_settings import (
            SPACE_ENTRY_CONTENT_MAX_TOKENS,
            SPACE_ENTRY_DESCRIPTION_MAX_TOKENS,
            SPACE_ENTRY_TITLE_MAX_TOKENS,
            SPACE_EVIDENCE_MAX_PATHS,
            SPACE_SEEN_BY_KEPT,
        )
        from config.prompts.prompts_nekoclaw_space import SPACE_ENTRY_PENDING_NOTICE

        paths = self._paths()
        self._revive(project)
        title = _cut(_one_line(redact(title)), SPACE_ENTRY_TITLE_MAX_TOKENS)
        content = _cut(redact(content), SPACE_ENTRY_CONTENT_MAX_TOKENS)
        # A text that is nothing but the marks of a sentence says nothing,
        # and is told from no other such text: all of them have one mark.
        if not title or not _normal(content):
            raise SpaceError("an entry needs a title and a text")
        description = _cut(_one_line(redact(description)) or _one_line(content), SPACE_ENTRY_DESCRIPTION_MAX_TOKENS)
        directory = paths.memory_dir(project)
        today = _date(self._clock())
        since, last = dated if dated is not None else ("", "")
        if into is not None:
            existing: Entry | None = into
        else:
            existing = None if pending else self._same_topic(directory, project, title)
        if existing is not None:
            path = directory / f"{existing.id}.md"

            fresh_text: list[bool] = []

            def add(text: str) -> str:
                # What a look tells is of this attempt: the file is read
                # again, and all of it done again, when it was saved
                # meanwhile. Only what the write that stands was made
                # with is told (below).
                at_write.clear()
                document = _parse_document(text)
                fresh = not _said_already(document, content)
                if must_say and (
                    fresh
                    or into is None
                    or (tuple(_evidence_in(document)), tuple(_outside_in(document))) != (into.evidence, into.evidence_outside)
                ):
                    raise _Rewritten("the pending entry says something else by now")
                if fresh and not may_add:
                    raise _NoPlace("the commission has no place left for a text that would be added")
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
                filed_by = _listed(document, "sources")
                others = [
                    item
                    for item in dict.fromkeys((*(known.sources_before if known is not None else ()), source))
                    if item and item not in filed_by
                ]
                another = bool(others)
                # The text is in the entry now, added or there already, so
                # its mark is kept: one that was put there in an editor has
                # none yet, and would not be known again once the entry
                # says more than it. Only in an entry that keeps marks; see
                # ``_said_already``.
                marks = _listed(document, "texts")
                unmarked = bool(marks) and _digest(content) not in marks
                # What is known of it beyond its words is kept on the same
                # terms: only in an entry the host made. One the user wrote
                # is theirs; it is given no evidence, no count and no state.
                try:
                    noted = bool(marks) and _keep_known(
                        document,
                        known,
                        allowed,
                        pending=pending,
                        judged=by_files,
                        told=at_write,
                        judged_with=judged_with,
                    )
                except _Rewritten as exc:
                    # The files that were asked about are on the record of this entry.
                    exc.of = existing.id
                    raise
                redated = False
                if dated is not None:
                    # Brought along: it was not filed today, and has not
                    # waited a day less for having come here.
                    created, updated = document.get("created"), document.get("updated")
                    if since and (not created or since < created):
                        document.set("created", since)
                        redated = True
                    if last and (not updated or last > updated):
                        document.set("updated", last)
                        redated = True
                elif pending and document.get("updated") != today:
                    # Filed again while it waits: it waits from today.
                    document.set("updated", today)
                    redated = True
                if not fresh and not another and not unmarked and not noted and not redated:
                    return text
                if fresh and dated is None:
                    document.set("updated", today)
                if unmarked:
                    document.set("texts", ", ".join([*marks, _digest(content)]))
                if another:
                    document.set("sources", ", ".join([*filed_by, *others]))
                return _render_document(document)

            # What was told of the files the entry rests on, as it was
            # read to be written.
            at_write: dict[str, bool | None] = {}
            self._edit_text(path, add)
            if told is not None:
                told.update(at_write)
            how = _ADDED if fresh_text and fresh_text[0] else _THERE
            if pending:
                # Not in effect: no index has a line for it.
                return self._entry_from(path, project, with_content=True) or existing, how
            # Its line in the index is left as it is (the user may have
            # reworded it); only a missing one is put back.
            unlisted: OSError | None = None
            try:
                self._index_add(
                    paths.index(project),
                    _index_line(existing.title, f"{paths.MEMORY_DIR}/{existing.id}.md", existing.description),
                    f"{paths.MEMORY_DIR}/{existing.id}.md",
                )
            except OSError as exc:
                unlisted = exc
            entry = self._entry_from(path, project, with_content=True) or existing
            if unlisted is not None:
                # What was added is in the entry all the same.
                raise _LineNotAdded(entry, how) from unlisted
            return entry, how
        try:
            wanted = slug(title)
        except ValueError:
            wanted = "entry"
        name = self._free_name(directory, wanted)
        # Who it came from and who has seen it, the ones of before first.
        sources = [
            item for item in dict.fromkeys((*(known.sources_before if known is not None else ()), source)) if item
        ]
        seen_by = (
            [item for item in dict.fromkeys((*known.seen_before, known.observer)) if item] if known is not None else []
        )
        document = _Document(
            meta=[
                ("name", title),
                ("description", description),
                ("type", _entry_type(kind)),
                ("created", since or today),
                ("updated", last or today),
                ("sources", ", ".join(sources)),
                # A mark of every text filed; see ``_said_already``.
                ("texts", _digest(content)),
            ],
            body=content,
            has_meta=True,
        )
        evidence = list(known.evidence[-SPACE_EVIDENCE_MAX_PATHS:]) if known is not None else []
        outside = [path for path in evidence if known is not None and path in known.outside]
        if known is not None and known.confidence:
            document.set("confidence", known.confidence)
        _set_evidence(document, evidence, outside)
        noted = _joined([], _not_among(known.noted, evidence), SPACE_EVIDENCE_MAX_PATHS) if known is not None else []
        _set_noted(document, noted)
        if seen_by:
            document.set("seen", str(len(seen_by)))
            document.set("seen_by", ", ".join(seen_by[-SPACE_SEEN_BY_KEPT:]))
        if pending:
            document.set("status", ENTRY_PENDING)
            # Said in the file itself: a task agent that comes upon it by
            # searching the directory reads there what it is.
            document.set("notice", SPACE_ENTRY_PENDING_NOTICE)
        path = directory / f"{name}.md"
        landed = True
        try:
            self._put_text(path, _render_document(document))
        except _NotAsWritten:
            # In place, and saved over at that very moment. It is a file of
            # the directory all the same and gets its line: without one it
            # would be a file that no index lists.
            landed = False
            logger.warning("a new entry does not hold what was written right after it was written", exc_info=True)
        target = f"{paths.MEMORY_DIR}/{name}.md"
        unlisted = None
        if not pending:
            try:
                self._index_add(paths.index(project), _index_line(title, target, description), target)
            except OSError as exc:
                if not landed:
                    # Neither what was written is there nor a line for it:
                    # nothing of this entry was kept.
                    raise
                unlisted = exc
        entry = self._entry_from(path, project, with_content=True)
        if entry is None:
            # Held by another program just now: told as it was written.
            entry = Entry(
                id=name,
                project=project,
                title=title,
                description=description,
                type=_entry_type(kind),
                created=since or today,
                updated=last or today,
                sources=tuple(sources),
                content=content.strip(),
                confidence=known.confidence if known is not None else "",
                evidence=tuple(evidence),
                seen=max(len(seen_by), 1),
                status=ENTRY_PENDING if pending else ENTRY_ACTIVE,
                seen_by=tuple(seen_by),
                evidence_outside=tuple(outside),
                noted=tuple(noted),
            )
        if unlisted is not None:
            # The entry is a file by now, and is not taken away again.
            raise _LineNotAdded(entry, _CREATED) from unlisted
        return entry, _CREATED

    # -- what the user removed, and what waits -----------------------------

    def _notes(self, path: Path) -> dict[str, Any]:
        """Return what a ``notes.json`` holds; raises OSError when it is there and cannot be read."""
        return _notes_of(self._text_to_change(path))

    def _change_notes(self, path: Path, change: Callable[[dict[str, Any]], None]) -> None:
        """Change a ``notes.json`` by a function of what it holds; one that changes nothing writes nothing."""

        def edit(text: str) -> str:
            data = _notes_of(text)
            before = json.dumps(data, sort_keys=True, ensure_ascii=False)
            change(data)
            if json.dumps(data, sort_keys=True, ensure_ascii=False) == before:
                return text
            return _notes_text(data)

        self._edit_text(path, edit)

    def _removed_of(self, path: Path) -> tuple[set[str], set[str]]:
        """Return what the user removed from a layer, as notes are compared to it (``_removed_from``).

        Nothing when the record cannot be read: a note that takes effect
        by itself is still kept then.
        """
        try:
            return _removed_from(self._notes(path)["removed"])
        except OSError:
            logger.warning("what the user removed from the task agent space cannot be read", exc_info=True)
            return set(), set()

    @staticmethod
    def _discard(path: Path) -> None:
        """Remove a pending entry that is not kept any longer. It has no line in any index."""
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            logger.warning("a pending entry that is not kept any longer could not be removed", exc_info=True)

    @staticmethod
    def _whole_texts(document: _Document) -> list[str] | None:
        """Return what a pending entry says as texts that can be filed elsewhere whole. None when it cannot be.

        The texts it was given, one by one, where they can still be told
        apart (``_texts_of``). Where they cannot (the user rewrote it in
        an editor), all it says as one text: nothing is taken for a
        heading of the host's that may be the user's own line. A text is
        no longer than ``SPACE_ENTRY_CONTENT_MAX_TOKENS``; a longer one
        would be cut off where it is filed, and the rest lost with the
        file it came from. Then there is no way to file it whole. A text
        that says nothing is none: an entry that was emptied in the
        console keeps the mark of nothing, and has nothing to file.
        """
        from config.nekoclaw_settings import SPACE_ENTRY_CONTENT_MAX_TOKENS
        from utils.tokenize import count_tokens

        def whole(text: str) -> bool:
            # As ``_file_entry`` keeps a text, before it cuts it.
            return count_tokens(_plain_lines(redact(text)).strip()) <= SPACE_ENTRY_CONTENT_MAX_TOKENS

        texts = _texts_of(document.body, _listed(document, "texts"))
        if texts is None:
            texts = [document.body.strip()]
        texts = [text for text in texts if _normal(text)]
        return texts if all(whole(text) for text in texts) else None

    def _pending_as_read(
        self, path: Path, project: str | None
    ) -> tuple[tuple[int, int] | None, _Document, Entry] | None:
        """Read a pending entry that is to be filed elsewhere: what the file was, what it says, and what it is by that.

        All three from this one reading. What was known of the entry
        before (its title, who has seen it) was read earlier, and the
        user may have saved it from an editor since: judged by a title
        it no longer has, it could be turned down for that title and
        removed as what it is now. None when it is not there, or says
        by now that it is no pending entry.
        """
        before = self._signature(path)
        text = self._read_text(path)
        if text is None:
            return None
        document = _parse_document(text)
        if _status_of(document) != ENTRY_PENDING:
            return None
        try:
            modified = path.stat().st_mtime
        except OSError:
            modified = self._clock()
        return before, document, self._entry_of(document, path.stem, project, modified, with_content=False)

    def _discard_as_read(self, path: Path, before: tuple[int, int] | None) -> bool:
        """Remove a pending entry whose texts were taken somewhere else, unless it was saved since they were read.

        The lock of the space does not hold off an editor. What the user
        saved meanwhile is not in what was taken along, and would be gone
        with the file: so the file stays, a pending entry that says what
        they made it say. ``before`` is what the file was when it was
        read (``_signature``), None when that could not be asked of the
        file system: then it cannot be told to be as it was read, and
        stays as well. Returns whether it was removed.
        """
        if before is None or self._signature(path) != before:
            logger.warning("a pending entry was saved while its texts were filed elsewhere; it is left where it is")
            return False
        self._discard(path)
        return True

    def _crowd_out(self, directory: Path, project: str | None, keep: str | None) -> None:
        """Remove the pending entries of a layer beyond ``SPACE_HELD_MAX_NOTES``: the ones filed longest ago.

        ``keep`` is the one that was just filed, which is never the one
        to go; None when it was brought along with its commission, since
        that one was filed some time ago and goes like any other.
        """
        from config.nekoclaw_settings import SPACE_HELD_MAX_NOTES

        waiting: list[tuple[str, float, str]] = []
        for entry in self._entries(directory, project):
            if entry.status != ENTRY_PENDING:
                continue
            try:
                modified = (directory / f"{entry.id}.md").stat().st_mtime
            except OSError:
                modified = 0.0
            waiting.append((entry.updated, modified, entry.id))
        over = len(waiting) - SPACE_HELD_MAX_NOTES
        if over <= 0:
            return
        for _updated, _modified, name in sorted(item for item in waiting if item[2] != keep)[:over]:
            self._discard(directory / f"{name}.md")

    def _bases(self, workdir: Any, project: str | None) -> list[str]:
        """Return the directories in which the files a note names are looked for. Blocking.

        Where the work was done, and where its project is. The first is
        the working directory the ledger has for the commission: the one
        it was given or, when it was given none, the one it was taken up
        in, which is on record from then on.
        """
        bases = [str(workdir)] if workdir else []
        if project and project != DEFAULT_PROJECT:
            try:
                record = self._project_from(self._paths().project_dir(project))
            except (OSError, ValueError):
                record = None
            if record is not None and record.workdir and record.workdir not in bases:
                bases.append(record.workdir)
        return bases

    async def evidence_bases(self, workdir: str | None, project: str | None) -> list[str]:
        """Return the directories in which the files an entry of a review names are looked for; see ``_bases``."""
        return await self._read(self._bases, workdir, project)

    @staticmethod
    def _kept_words(title: Any, content: Any) -> tuple[str, str]:
        """Return a note's title and text as they are kept, which is how they are compared."""
        from config.nekoclaw_settings import SPACE_ENTRY_CONTENT_MAX_TOKENS, SPACE_ENTRY_TITLE_MAX_TOKENS

        return (
            _cut(_one_line(redact(str(title or ""))), SPACE_ENTRY_TITLE_MAX_TOKENS),
            _cut(redact(str(content or "")), SPACE_ENTRY_CONTENT_MAX_TOKENS),
        )

    def _uses_no_place(self, project: str | None, observer: str, title: Any, content: Any) -> bool:
        """Whether a note looks like one that uses none of its commission's places. Reads only; blocking.

        One of the commission's own that waits to take effect: it used
        its place when it began to wait. Or one whose text is in an
        entry already: nothing of it is added. Such a note may be filed
        when the commission has no place left. The second kind matters
        when an entry took effect and its index could not be given the
        line: filed again, the same text adds the line, and no other
        filing would. Whether it does use none is told when it is judged
        (``_take_note``, with ``may_use_place`` False).
        """
        try:
            name, text = self._kept_words(title, content)
            if not _normal(text):
                return False
            same, has_text, waits = self._same_note(self._paths().memory_dir(project), project, name, text)
        except (OSError, ValueError):
            return False
        if same is not None and has_text:
            return True
        return any(has and observer in entry.seen_by for entry, has in waits)

    def _confirm(
        self,
        project: str | None,
        pending: Entry,
        known: _Known,
        allowed: bool,
        *,
        by_files: bool = False,
        told: dict[str, bool | None] | None = None,
    ) -> tuple[Entry, OSError | None]:
        """Have a pending entry take effect where it is, seen by the commissions of ``known`` as well.

        Nothing is added to what it says. Returns the entry, and what
        kept its line out of the index if anything did. One that says
        nothing (emptied in the console) has nothing that could take
        effect: it is removed, as it is when there is nothing of it to
        put into another entry (``_fold``).
        """
        paths = self._paths()
        path = paths.memory_dir(project) / f"{pending.id}.md"
        seen = _Known(confidence=pending.confidence or INFERRED, observer=known.observer, seen_before=known.seen_before)
        # Whether it said nothing, as it was read to be written.
        empty: list[bool] = []
        # What was written when it took effect.
        took: list[_Document] = []

        def change(text: str) -> str:
            took[:] = []
            # What a look tells is of this attempt (as in ``_file_entry``).
            at_write.clear()
            document = _parse_document(text)
            # Only as the host made it: one the user took over is theirs.
            # And as it was found: by its title. Given another since, it
            # is about something else, and goes on waiting.
            if _status_of(document) != ENTRY_PENDING or _normal(document.get("name") or path.stem) != _normal(
                pending.title
            ):
                return text
            empty[:] = [not _normal(document.body)]
            if empty[0]:
                return text
            changed = _keep_known(document, seen, allowed, told=at_write)
            if by_files and _speaks_no_more(known, at_write):
                # The note is in effect by its files and nothing else, and
                # one that spoke for it is gone, or not told of, as this
                # entry is asked about: nothing is written, and the note
                # is judged anew (as in ``_fold``).
                raise _Rewritten(
                    "a file the note was judged with does not speak for it by now", told=at_write, of=pending.id
                )
            if not changed:
                return text
            took[:] = [document]
            return _render_document(document)

        # What was told of the files it rests on, as it was read to be
        # written: for whoever writes what the note brings to another entry.
        at_write: dict[str, bool | None] = {}
        self._edit_text(path, change)
        if told is not None:
            told.update(at_write)
        if empty and empty[0]:
            # Removed, if it still says nothing as it is read for that.
            read = self._pending_as_read(path, project)
            if read is not None and not _normal(read[1].body):
                self._discard_as_read(path, read[0])
            return pending, None
        entry = self._entry_from(path, project, with_content=True)
        if entry is None and took:
            # Written, and not to be read back just now. Held by another
            # program, it took effect all the same, and is listed as what
            # was written: nothing else would give it its line. Removed
            # since (the user, in a file manager), it gets none: the line
            # would point at nothing, and no look would take it out.
            # Gone only when the file system says there is no such
            # file, on a drive that is there: a volume that is away for
            # a moment says nothing of the file. Nor is there a line for
            # what stands in its place and is no file.
            if _may_be_a_file(path):
                entry = self._entry_of(took[0], path.stem, project, self._clock(), with_content=True)
        if entry is None or entry.status != ENTRY_ACTIVE:
            return entry or pending, None
        target = f"{paths.MEMORY_DIR}/{entry.id}.md"
        try:
            self._index_add(paths.index(project), _index_line(entry.title, target, entry.description), target)
        except OSError as exc:
            return entry, exc
        return entry, None

    def _fold(
        self,
        project: str | None,
        pending: Entry,
        into: Entry,
        known: _Known,
        allowed: bool,
        *,
        may_add: bool = True,
        by_text: str | None = None,
        by_files: bool = False,
        told: dict[str, bool | None] | None = None,
    ) -> tuple[Entry, bool, OSError | None]:
        """Put what a pending entry says into an entry that is not pending, and remove its file.

        For one that is called like that entry, or holds a text of it:
        one title is one entry. Each text the pending one was given is
        added as it was filed, with everyone who has seen it. Returns the
        entry, whether anything was added to it, and what kept its line
        out of the index if anything did.

        ``may_add`` is as for ``_file_entry``: False when nothing may be
        added. Raises ``_NoPlace`` then, and the pending entry stays. So
        it does when the user saved it while its texts were added (in an
        editor, which no lock of the space holds off): what was read of
        it is in the other entry, and what they saved is still pending.
        And one that holds a text longer than a text may be (written so
        in an editor) is not put into the other entry at all: cut to
        fit, the end of it would be lost with the file. Nor is one the
        user gave another title since it was found to be the same note;
        nor one that was found by its text (``by_text``) and no longer
        says it: what it says now is not what the note confirmed. For
        one found by its text, raises ``_Rewritten`` then: the note was
        judged with what that entry rested on, and is judged anew
        (``_take_note``).
        """
        path = self._paths().memory_dir(project) / f"{pending.id}.md"
        read = self._pending_as_read(path, project)
        if read is None or _normal(read[2].title) != _normal(pending.title):
            # Gone, taken over, or given another title since it was found
            # to be the same note: it is about something else now, and
            # waits under that title.
            if by_text is not None:
                raise _Rewritten("the pending entry is not what it was found as")
            return into, False, None
        before, said, as_read = read
        if by_text is not None and (
            not _said_already(said, by_text)
            or (as_read.evidence, as_read.evidence_outside) != (pending.evidence, pending.evidence_outside)
        ):
            # Rewritten since, or resting on other files than the note
            # was judged with: it waits, as what it says now.
            raise _Rewritten("the pending entry says something else by now")
        pending = as_read
        # What it says of itself goes along: how it is known, and of the
        # files it rested on the ones that are still there.
        rested, untold = _recorded(pending.evidence, pending.evidence_outside, allowed)
        answers: dict[str, bool | None] = {
            path: (None if path in untold else True) if path in rested else False for path in pending.evidence
        }
        if by_files and _speaks_no_more(known, answers):
            # In effect by its files and nothing else (``by_files``), and
            # asked about once more, a file that spoke for the note is
            # gone, or nothing is told of it: of two answers the later
            # one counts, and the note is judged anew, without it.
            raise _Rewritten(
                "a file the note was judged with does not speak for it by now", told=answers, of=pending.id
            )
        if told is not None:
            # For whoever writes what the note brings along after this,
            # and tells what became of the note.
            told.update(answers)
        carried = _Known(
            confidence=pending.confidence or INFERRED,
            evidence=tuple(rested),
            untold=tuple(untold),
            outside=pending.evidence_outside,
            observer=known.observer,
            seen_before=tuple(dict.fromkeys((*pending.seen_by, *known.seen_before))),
            sources_before=pending.sources[:-1],
            noted=pending.noted,
        )
        target: Entry = into
        added = False
        unlisted: OSError | None = None
        parts = self._whole_texts(said)
        if parts is None:
            logger.warning("a pending entry holds a text longer than a text may be; it is not put into another entry")
            return into, False, None
        for part in parts:
            # What the write of this part told of the files. An entry that
            # had expired comes back by this write, by the note: what its
            # files are told as it is written is the last word on them, and
            # the note is judged anew where one of them spoke for it and
            # does not by now.
            looked: dict[str, bool | None] = {}
            try:
                target, how = self._file_entry(
                    project,
                    title=into.title,
                    content=part,
                    kind=pending.type,
                    source=pending.sources[-1] if pending.sources else None,
                    into=target,
                    known=carried,
                    may_add=may_add,
                    allowed=allowed,
                    by_files=by_files,
                    told=looked,
                    judged_with=known,
                )
            except _LineNotAdded as exc:
                # In its file all the same; the rest is still filed.
                target, how, unlisted = exc.entry, exc.how, exc
            added = added or how == _ADDED
            if told is not None:
                told.update(looked)
            # The entry is in effect by now and its files are not asked
            # about again: the next part does not bring back one this
            # write found gone, and one of which nothing was told stays
            # on record and speaks for nothing.
            carried = replace(
                carried,
                evidence=tuple(path for path in carried.evidence if looked.get(path) is not False),
                outside=tuple(path for path in carried.outside if looked.get(path) is not False),
                untold=tuple(
                    path
                    for path in carried.evidence
                    if looked.get(path) is not False and (path in carried.untold or (path in looked and looked[path] is None))
                ),
            )
        self._discard_as_read(path, before)
        return target, added, unlisted

    def _take_note(
        self, project: str | None, note: _Note, *, may_use_place: bool = True, allowed: bool = False
    ) -> _Taken:
        """Judge a note and keep it; see ``_judged``, which does it.

        Judged once more when a pending entry that had its text, and was
        to take effect by it or to go into another entry with it, says
        something else by the time it is written (the user, saving it
        from an editor at that very moment): what stands there now is
        not what the note confirmed, and what that entry rested on does
        not speak for the note. The second time nothing has that text,
        and the note is judged by itself. Raises ``SpaceError`` when
        that happens twice running.
        """
        try:
            return self._judged(project, note, may_use_place=may_use_place, allowed=allowed)
        except _Rewritten as exc:
            since = exc
        # With what the last look told (``since``): it holds for every
        # file that is not asked about again as the note is judged.
        try:
            return self._judged(project, note, may_use_place=may_use_place, allowed=allowed, since=since)
        except _Rewritten as exc:
            raise SpaceError("an entry kept changing while the note was judged") from exc

    def _judged(
        self,
        project: str | None,
        note: _Note,
        *,
        may_use_place: bool = True,
        allowed: bool = False,
        since: _Rewritten | None = None,
    ) -> _Taken:
        """Judge a note and keep it: as an entry in effect when it takes effect, as a pending one when it does not.

        No model is asked. A note takes effect when it was verified; when
        it was observed and a file it names exists; or when as many
        commissions as ``SPACE_SEEN_TO_TAKE_EFFECT`` have filed it, each
        counted once with the task agents it started. It is the same note
        as one on file when the texts are the same or the titles are (see
        ``_same_note``). One the user removed is not kept again: raises
        ``NoteRemoved``. One that says nothing is not kept either.

        A note that does not take effect is kept in a pending entry, a
        file that says so and has no line in any index: the pending entry
        that has its text or its title, or a new one. What was said of
        the same text while it waited counts with what is said later.

        A note that takes effect and whose text waits in a pending entry
        has that entry take effect: the text is what was judged. The
        other pending entries that are the same note, by its title, take
        effect with it when the note was verified (that is how a task
        agent confirms a guess it came upon: it checks it and files it
        again under its title), or when a commission that files now had
        not seen them. Each takes effect where it is; one that is called
        like an entry that is not pending, or holds a text of one, goes
        into that entry (one title is one entry). A pending entry that
        is not taken along goes on waiting, and the note is an entry of
        its own.

        The outcome says whether the note used one of the places its
        commission has (``charged``). With ``may_use_place`` False a note
        that would use one is not kept: raises ``_NoPlace``, before
        anything of it is written. Where that is found out only as the
        note is written (its text was in an entry when it was looked at,
        and is gone from it by then), the pending entries it confirms have
        taken effect already: each of those stands by itself. ``allowed``
        is whether the user lets task agents read outside the working
        directories unasked.
        """
        from config.nekoclaw_settings import SPACE_EVIDENCE_MAX_PATHS, SPACE_SEEN_TO_TAKE_EFFECT

        paths = self._paths()
        self._revive(project)
        # As they are kept, so that what is compared is what is kept.
        title, content = self._kept_words(note.title, note.content)
        # Before it is judged: one that says nothing does not wait either.
        if not title or not _normal(content):
            raise SpaceError("an entry needs a title and a text")
        directory = paths.memory_dir(project)
        removed = self._removed_of(paths.notes_file(project))
        if _removed_note(removed, _digest(content), title):
            raise NoteRemoved("the user removed this note from the space, and it is not kept again")
        same, has_text, found = self._same_note(directory, project, title, content)
        # A pending entry is the same note by its text or by its title, and
        # has the other of the two of its own: a text filed under another
        # title, a title another text was filed under. One the user
        # removed by that is not kept either. It takes effect with no
        # note, and waits no longer.
        waits: list[tuple[Entry, bool]] = []
        for pending, has in found:
            said = self._read_text(directory / f"{pending.id}.md") if any(removed) else None
            if said is not None and _removed_there(removed, said, pending.title):
                self._discard(directory / f"{pending.id}.md")
            else:
                waits.append((pending, has))
        own = next((pending for pending, has in waits if has), None)
        # Where a note that does not take effect goes: the pending entry
        # that has its text, else the one of its title.
        lead = own or (waits[0][0] if waits else None)
        observer = note.known.observer
        # How many commissions have seen it, this one counted. An entry
        # nobody is named for (one the user wrote, or filed from the
        # console) counts as seen once, by whoever put it there. A note
        # that is brought along with its commission arrives with all who
        # had seen it where it waited.
        arriving = [item for item in dict.fromkeys((*note.known.seen_before, observer)) if item]
        if same is not None:
            counted_there = same.seen_by or same.sources
            seen = same.seen + sum(1 for item in arriving if item not in counted_there)
        else:
            observers = {seen_by for pending, _has in waits for seen_by in pending.seen_by}
            seen = max(len(observers | set(arriving)), 1)
        # What was said of the same text while it waited counts with what
        # is said now. Of the files it rested on then, not the ones that
        # are gone: one that was removed since no longer speaks for it.
        # One of which nothing is told just now stays on record, and does
        # not speak for it this time.
        on_record, untold = _recorded(own.evidence, own.evidence_outside, allowed) if own is not None else ([], [])
        # Asked about just now, after the note's own files were: a file
        # that is found gone here is gone, though the note found it.
        gone = {path for path in (own.evidence if own is not None else ()) if path not in on_record}
        # Judged anew (``since``): what the look told that had it judged
        # anew holds for the files that were not asked about again just
        # now. One that was found gone then does not count, and one of
        # which nothing was told speaks for nothing.
        earlier = {
            path: stands
            for path, stands in (since.told.items() if since is not None else ())
            if own is None or path not in own.evidence
        }
        earlier_gone = {path for path, stands in earlier.items() if stands is False}
        evidence = _joined(
            on_record,
            [path for path in note.known.evidence if path not in gone and path not in earlier_gone],
            SPACE_EVIDENCE_MAX_PATHS,
        )
        apart = {*(own.evidence_outside if own is not None else ()), *note.known.outside}
        # And one that is found there here is there, though nothing was
        # told of it when the note asked.
        there_now = {
            *(path for path in on_record if path not in untold),
            *(path for path, stands in earlier.items() if stands is True),
        }
        untold_now = {
            *untold,
            *(path for path in note.known.untold if path not in there_now),
            *(path for path, stands in earlier.items() if stands is None),
        }
        known = _Known(
            confidence=_stronger(note.known.confidence, own.confidence if own is not None else ""),
            evidence=tuple(evidence),
            outside=tuple(path for path in evidence if path in apart),
            untold=tuple(path for path in evidence if path in untold_now),
            gone=tuple(path for path in gone if path not in evidence),
            observer=observer,
            seen_before=note.known.seen_before,
            sources_before=note.known.sources_before,
            # What its own pending entry noted stays in that entry's file,
            # and goes along where that goes into another (``_fold``).
            noted=tuple(_not_among(note.known.noted, evidence)),
        )
        # A place of the commission is used by what it puts on file: a note
        # that begins to wait, or one that becomes an entry. Not by what is
        # on file already, and not a second time by a note of its own that
        # waited and is filed again.
        paid = own is not None and bool(observer) and observer in own.seen_by
        there = same is not None and has_text
        # A text that an entry in effect has is in effect: there is nothing
        # to judge. What is known of it is added to the entry.
        settled = same is not None and has_text and same.status == ENTRY_ACTIVE
        # In effect by what it says of itself, whoever files it.
        vouched = known.confidence == VERIFIED or (
            note.files_vouch
            and known.confidence == OBSERVED
            and any(path not in known.untold for path in known.evidence)
        )

        # Whether it is in effect by its files and nothing else. Asked
        # about once more as it is written, they are all it stands on
        # then: one that is gone or not told of has it judged anew. A
        # note that was checked by hand, or seen by enough commissions,
        # is written whatever became of the files.
        by_files = not settled and known.confidence != VERIFIED and seen < SPACE_SEEN_TO_TAKE_EFFECT

        def spoke(later: dict[str, bool | None]) -> tuple[str, ...]:
            # Of the files the note named, the ones that spoke for it in
            # the end: there by the last answer about each. That is what
            # a look after the note was judged told (``later``), where
            # there was one: it may have found a file that was gone, or
            # not told of, as the note was judged (such a one is on
            # record: it stays where a pending entry takes effect, and
            # goes along where one goes into another entry); or found
            # one gone, or been told nothing of it. Else what was told
            # as the note was judged: found then, and told of.
            return tuple(
                path
                for path in note.known.evidence
                if (later[path] is True if path in later else path in known.evidence and path not in known.untold)
            )

        def found_gone(later: dict[str, bool | None]) -> tuple[str, ...]:
            # All that was found gone: as the note was judged, before
            # that when it is judged anew, and after, as it was written.
            # Not one of which the last look told otherwise: found
            # again, or not told of (that one stays on record, and is
            # not said to be missing).
            return tuple(
                path
                for path in dict.fromkeys(
                    (*gone, *earlier_gone, *(path for path, stands in later.items() if stands is False))
                )
                if path not in later or later[path] is False
            )

        def taken_along(pending: Entry) -> bool:
            # Checked by hand under its title, or seen by a commission that had not seen it.
            return known.confidence == VERIFIED or any(item not in pending.seen_by for item in arriving)

        if not (settled or vouched or seen >= SPACE_SEEN_TO_TAKE_EFFECT):
            if own is None and not may_use_place:
                raise _NoPlace("the commission has no place left for a note that would wait")
            # Raises OSError when the file it would wait in cannot be
            # written: there is nowhere for it to wait then, and it is
            # told so.
            entry, how = self._file_entry(
                project,
                title=title,
                content=content,
                kind=note.kind,
                description=note.description,
                source=note.source,
                into=lead,
                # Judged anew over a file that was found gone as this very
                # entry was asked about: it goes from its record.
                known=(
                    replace(known, gone=tuple(dict.fromkeys((*known.gone, *earlier_gone))))
                    if lead is not None and since is not None and lead.id == since.of
                    else known
                ),
                may_add=may_use_place or paid,
                pending=True,
                dated=note.waited,
                allowed=allowed,
            )
            # No more wait in a layer than may: the ones filed longest ago
            # go, never the one just filed. One that was brought along was
            # filed some time ago, and goes like any other.
            self._crowd_out(directory, project, None if note.waited else entry.id)
            # A place is used by what was put on file, as it turned out
            # when it was written: a new pending entry, or a text added
            # to one, unless the commission used a place for that text
            # before. The user may have saved the file between the look
            # and the writing: a text that waited and was taken out is
            # added again, and one that was not there may be by then.
            return _Taken(
                entry,
                _HELD,
                confidence=known.confidence,
                charged=not paid and how in (_CREATED, _ADDED),
                gone=found_gone({}),
                spoke=spoke({}),
            )
        charged = not paid and not there
        if charged and not may_use_place:
            raise _NoPlace("the commission has no place left for a note that would be kept")

        # In effect. Every step is one write that leaves the space as it
        # should be if the next one fails: what took effect is an entry,
        # with the commission that confirms it counted already, and what
        # did not is pending still.
        unlisted: OSError | None = None
        # What was told of the files a pending entry rested on after the
        # note was judged: as it went into another entry, or as it took
        # effect where it is.
        later: dict[str, bool | None] = {}
        # Whether its text went from a pending entry into one that is not.
        moved = False
        # Where the text of this note is, or goes, and what that was before.
        home: Entry | None
        was: Entry | None
        if own is not None and same is not None and (has_text or _normal(own.title) == _normal(same.title)):
            # Its text waits beside an entry that is not pending and has
            # that text or that title: the two are one entry from now on.
            was = same
            home, moved, unlisted = self._fold(
                project,
                own,
                same,
                known,
                allowed,
                may_add=may_use_place or paid,
                by_text=content,
                by_files=by_files,
                told=later,
            )
            waits = [item for item in waits if item[0] is not own]
        elif own is not None or same is not None:
            # The pending entry that has the text, else the entry that is
            # the same note.
            was = home = own or same
        else:
            # The pending entry of its title when this note confirms it;
            # an entry of its own when it does not.
            was = home = lead if lead is not None and taken_along(lead) else None
        for pending, _has in waits:
            if pending is home or not taken_along(pending):
                # Not confirmed by this note: it goes on waiting.
                continue
            if home is not None and home.status != ENTRY_PENDING and _normal(pending.title) == _normal(home.title):
                # What it rested on is asked about as it goes: a file
                # found gone then is not what the note brings to the entry.
                home, _added, failed = self._fold(
                    project, pending, home, known, allowed, by_files=by_files, told=later
                )
            else:
                _entry, failed = self._confirm(project, pending, known, allowed, by_files=by_files, told=later)
            unlisted = failed or unlisted
        became = home is not None and home.status == ENTRY_PENDING
        brought = known
        if home is not own:
            # What was found gone was asked about for the pending entry
            # that has the text, and goes from its record. Another entry
            # the text goes into keeps what it rests on (whether that
            # expires is told by the look at it), and is not given what
            # was found gone since the note was judged.
            later_gone = {path for path, stands in later.items() if stands is False}
            brought = replace(
                known,
                gone=(),
                evidence=tuple(path for path in known.evidence if path not in later_gone),
                outside=tuple(path for path in known.outside if path not in later_gone),
                untold=tuple(path for path in known.untold if path not in later_gone),
            )
        try:
            entry, how = self._file_entry(
                project,
                title=title,
                content=content,
                kind=note.kind,
                description=note.description,
                source=note.source,
                into=home,
                known=brought,
                may_add=may_use_place or paid,
                allowed=allowed,
                # It takes effect by having this text: see ``_take_note``.
                must_say=own is not None and home is own,
                by_files=by_files,
                # Where it takes effect where it is, the files it rested
                # on are asked about once more as it is written.
                told=later,
            )
        except _LineNotAdded as exc:
            entry, how, unlisted = exc.entry, exc.how, exc
        # As it turned out: a text that was in the entry when it was looked
        # at, and was taken out before this was written (the user, in an
        # editor), was added again. That is told as what it is and uses a
        # place like any other addition; with none left it is not written
        # (``may_add``), and what waits of it goes on waiting.
        if moved and how == _THERE:
            # Put into the entry just now, from where it waited.
            how = _ADDED
        charged = charged or (how != _THERE and not paid)
        if became:
            # It was a guess that waited, and is an entry from now on.
            how = _CREATED
        if unlisted is not None:
            raise _LineNotAdded(entry, how, charged=charged, gone=found_gone(later), spoke=spoke(later)) from unlisted
        return _Taken(
            entry,
            how,
            gone=found_gone(later),
            spoke=spoke(later),
            # Counted in the entry itself: one the user wrote keeps no count.
            counted=entry.seen > (was.seen if was is not None else 1),
            revived=was is not None and was.status == ENTRY_EXPIRED,
            charged=charged,
        )

    async def submit(self, commission: Commission, lesson: dict[str, Any]) -> str | None:
        """Keep what a task agent filed with ``note_lesson``; the ``LessonSink`` of the manager.

        ``feedback`` never arrives here: it is a task preference. Whether
        the note takes effect is told from how the task agent says it
        knows it and from the files it names; see ``_take_note``. One that
        says nothing of how it is known is taken for inferred.
        """
        from config.nekoclaw_settings import SPACE_LESSONS_PER_COMMISSION
        from config.prompts import prompts_nekoclaw_space as texts
        from config.prompts.prompts_nekoclaw_space import SPACE_LESSONS_CAP_REACHED

        scope = str(lesson.get("scope") or "project").strip().lower()
        kind = str(lesson.get("category") or "")
        # How a project stands is the project's own, whatever scope came with it.
        everywhere = scope == "global" and _entry_type(kind) != "progress"
        project = None if everywhere else (commission.project or DEFAULT_PROJECT)
        # What a task agent files goes into an index as it is, and the newest
        # lines of an index are what later task agents are shown. One
        # commission adds so many and no more, so that one that was led
        # astray cannot fill that window with lines of its own. Counted for
        # the commission she gave: a task agent it started adds to its
        # count, not to one of its own.
        counted = commission.parent_id or commission.id
        # With the ones on their way: two of its task agents can file at
        # one moment.
        added = self._added_by.get(counted, 0) + self._adding.get(counted, 0)
        # Whether a place is held for this note while it is judged.
        reserved = True

        async def turned_down() -> str:
            # Still at work, as it shows: it is not the one to be forgotten
            # next. One that has added none is not counted (the most a
            # commission may add can be set to none at all).
            if counted in self._added_by:
                self._added_by[counted] = self._added_by.pop(counted)
            await self._note_unkept(commission, lesson, "cap")
            return SPACE_LESSONS_CAP_REACHED.format(count=SPACE_LESSONS_PER_COMMISSION)

        if added >= SPACE_LESSONS_PER_COMMISSION:
            # No place is left. A note that uses none still gets through:
            # one of its own that waits to take effect, filed again with
            # more that speaks for it, as it was told it could (that used
            # its place when it began to wait); or one whose text is in an
            # entry already. It is judged without a place.
            waits = await self._read(self._uses_no_place, project, counted, lesson.get("title"), lesson.get("content"))
            # As it is by now: a place may have come free meanwhile.
            added = self._added_by.get(counted, 0) + self._adding.get(counted, 0)
            if added >= SPACE_LESSONS_PER_COMMISSION:
                if not waits:
                    return await turned_down()
                reserved = False
        if reserved:
            # A place is taken before the files are written, and settled
            # when it is known what became of the note.
            self._adding[counted] = self._adding.get(counted, 0) + 1

        def settle(was_added: bool) -> None:
            if not reserved:
                # None was taken, and none is used: see ``_take_note``.
                return
            left = self._adding.get(counted, 0) - 1
            if left > 0:
                self._adding[counted] = left
            else:
                self._adding.pop(counted, None)
            if not was_added:
                # Nothing is counted for it: a commission that added none
                # takes the place of no commission that has added some.
                return
            # Counted, and put last: of more commissions than are counted
            # the ones that have not filed for longest are forgotten, not
            # one that began early and is still at work. Notes that are
            # still on their way are not among what is counted here, so
            # none of them has a commission forgotten before it has added
            # anything; and a commission with a note on its way is not
            # forgotten while that note waits, nor is this one. There are
            # a few more than are counted for as long as that lasts.
            self._added_by[counted] = self._added_by.pop(counted, 0) + 1
            over = len(self._added_by) - _COUNTED_COMMISSIONS
            if over > 0:
                idle = [key for key in self._added_by if key != counted and key not in self._adding]
                for key in idle[:over]:
                    del self._added_by[key]

        # Whether the note used a place, once the files were written or looked at.
        charged: list[bool] = []
        # The files it named that count as evidence, and the ones that do not with why.
        counted_files: list[str] = []
        other_files: list[tuple[str, str]] = []

        # What was found of the files it names, before the write took its turn.
        looked: list[tuple[bool, tuple[list[str], list[tuple[str, str]], list[str]]]] = []

        def work(allowed: bool) -> _Taken:
            evidence, others, apart, untold = self._at_its_turn(*looked[0], allowed)
            # The files it goes into the judgment with: found, or not told of.
            named_files = list(evidence)
            counted_files[:] = [path for path in evidence if path not in untold]
            other_files[:] = others
            note = _Note(
                title=str(lesson.get("title") or ""),
                content=str(lesson.get("content") or ""),
                kind=kind,
                source=commission.id,
                known=_Known(
                    # Not said: the weakest, which takes effect last.
                    confidence=_confidence(lesson.get("confidence")) or INFERRED,
                    evidence=tuple(evidence),
                    outside=tuple(apart),
                    untold=tuple(untold),
                    observer=counted,
                ),
            )
            def as_judged(gone: tuple[str, ...], spoke: tuple[str, ...]) -> None:
                # The files that count are the ones that spoke for the note
                # in the end, as it was judged and written: one that was
                # not told of when the write's turn came may have been
                # found since, and one that was there may be gone, or not
                # told of any more. One that is gone is told with the
                # others that did not count.
                # (Also one of which nothing was told when the write's turn
                # came: found gone since, it is told like any other.)
                dropped = [path for path in named_files if path in gone and path not in spoke]
                counted_files[:] = list(spoke)
                other_files.extend((path, "not found") for path in dropped)

            try:
                taken = self._take_note(project, note, may_use_place=reserved, allowed=allowed)
            except _LineNotAdded as exc:
                charged.append(exc.charged)
                as_judged(exc.gone, exc.spoke)
                raise
            charged.append(taken.charged)
            as_judged(taken.gone, taken.spoke)
            return taken

        listed = True
        seen: int | None = None
        newly_seen = revived = False
        try:
            try:
                bases = await self._read(
                    self._bases, getattr(commission, "workdir", None), getattr(commission, "project", None)
                )
                looked.append(await self._evidence_before(lesson.get("evidence"), bases))
                taken = await self._write_judged(work)
            finally:
                # However it ended, the place is settled here and nowhere
                # else, and before anything else is waited for: refused,
                # unwritable, a failure nobody expected, or the session
                # stopped while the note waited for its turn or was written
                # (the writing is finished all the same). Only a note that
                # was added has used a place; one that never got as far
                # would leave the commission and its other task agents one
                # short for nothing. One that begins to wait to take effect
                # was added, to the ones that wait; filed again while it
                # waits, or once it takes effect, it uses no second place.
                settle(bool(charged) and charged[0])
        except _NoPlace:
            # It would have used a place after all (what waited is gone by
            # now, or is another note than it looked): turned down like any
            # note of a commission that has none left.
            return await turned_down()
        except NoteRemoved as exc:
            await self._note_unkept(commission, lesson, "removed")
            return f"not kept: {exc}"
        except (SpaceError, ValueError) as exc:
            await self._note_unkept(commission, lesson, "refused")
            return f"not kept: {exc}"
        except _LineNotAdded as exc:
            # The note is in its file, and stays there: it is told and
            # counted as what it is, kept. Said to be not kept, its place
            # would be given back with the file left behind, and a
            # commission could add more files than it may. Only its line in
            # the index is missing; that is put there the next time
            # something is added to the entry.
            logger.warning("a lesson is kept, and the index could not be given a line for it", exc_info=True)
            entry_id, how, listed = exc.entry_id, exc.how, False
            seen = getattr(exc.entry, "seen", None)
        except OSError as exc:
            logger.warning("a lesson could not be written into the task agent space (%s)", type(exc).__name__)
            await self._note_unkept(commission, lesson, "unwritable")
            return "not kept: the files of the task agent space could not be written"
        except Exception:
            # Nothing that is expected. What the task agent wrote is put on
            # the record of its commission before the failure goes on.
            await self._note_unkept(commission, lesson, "error")
            raise
        else:
            how = taken.how
            if how == _HELD:
                # Not in effect yet. Said as it is, with where it is kept
                # and what would change it.
                await self._note_unkept(commission, {**dict(lesson), "entry": taken.entry.id}, "held")
                why = (
                    texts.SPACE_NOTE_HELD_NO_EVIDENCE
                    if taken.confidence == OBSERVED
                    else texts.SPACE_NOTE_HELD_INFERRED
                )
                return texts.SPACE_NOTE_HELD.format(why=why, entry=taken.entry.id) + _uncounted(other_files)
            entry_id, seen = taken.entry.id, taken.entry.seen
            newly_seen, revived = taken.counted, taken.revived
        if self._ledger is not None:
            # On the commission's record too, so the board can show it.
            await self._ledger.append_event(
                commission.id,
                "lesson",
                {
                    **dict(lesson),
                    "kept": True,
                    "entry": entry_id,
                    "project": project,
                    "created": how == _CREATED,
                    "added": how != _THERE,
                    **({"evidence_counted": list(counted_files)} if counted_files else {}),
                    **({"seen": seen} if newly_seen and seen else {}),
                    **({"revived": True} if revived else {}),
                    **({} if listed else {"listed": False}),
                },
            )
        where = "the space of every project" if project is None else f"project {project}"
        # Of an entry that was there before it is not known whether a line
        # lists it: the index may be one that cannot be read.
        unlisted = "" if listed else "; the index could not be read or written, so it may have no line there yet"
        more = (" It had expired and is in effect again." if revived else "") + _uncounted(other_files)
        if more:
            # Sentences of their own, after the one that says what became of it.
            more = f".{more}"
        if how == _CREATED:
            return f"kept in {where} as {entry_id}.md{unlisted}{more}"
        if how == _ADDED:
            return f"added to {entry_id}.md in {where}{unlisted}{more}"
        # Said as it is: the task agent is not told something was kept that was not.
        return f"already in {entry_id}.md in {where}; nothing was added{unlisted}{more}"

    async def _note_unkept(self, commission: Commission, lesson: dict[str, Any], reason: str) -> None:
        """Put a note that was not kept on the commission's record.

        What the task agent wrote is not lost with the files that could not
        be written: it is in the ledger, as every note was before there was
        a space, and marked as not kept.
        """
        if self._ledger is None:
            return
        try:
            await self._ledger.append_event(
                commission.id, "lesson", {**dict(lesson), "kept": False, "reason": reason}
            )
        except Exception:  # noqa: BLE001 - the task agent is still told what became of its note
            logger.warning("a note that was not kept could not be put on its commission's record", exc_info=True)

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
        """Keep one entry; see ``_file_entry``. Raises ``SpaceError`` for one that cannot be kept.

        One that is in its file while its index could not be given a line
        for it is kept, and is returned like any other: the review that
        filed it names it, so it is found from there. Its line is put into
        the index the next time something is filed about the entry. Lines
        that are missing are not looked for and put back by themselves:
        the user takes lines out of an index, too.
        """
        try:
            entry, how = await self._write(
                lambda: self._file_entry(
                    project, title=title, content=content, kind=kind, description=description, source=source
                )
            )
        except ValueError as exc:
            raise SpaceError(str(exc)) from exc
        except _LineNotAdded as exc:
            logger.warning("an entry is kept, and the index could not be given a line for it", exc_info=True)
            return exc.entry, exc.how == _CREATED
        return entry, how == _CREATED

    async def propose_entry(
        self,
        project: str | None,
        *,
        title: str,
        content: str,
        kind: str = _DEFAULT_TYPE,
        description: str = "",
        source: str | None = None,
        observer: str | None = None,
        bases: Iterable[str] = (),
        evidence: Iterable[Any] = (),
        waits: list[str] | None = None,
    ) -> tuple[Entry | None, bool]:
        """Keep an entry a model made of what it read, if it takes effect: for a review, a summary for the archive.

        Such a model has checked nothing by hand, so its entry is never
        verified. The files it rests on are the ones it names, in
        ``evidence`` or in its title, description and text, that exist in
        ``bases`` (see ``evidence_of``). With one it counts as observed
        and takes effect; with none it counts as inferred and waits to be
        seen again, like a note of a task agent (``_take_note``). A global
        one (``project`` None) counts as inferred whatever files it names,
        and rests on none of them: the files of one project do not show
        that it holds for every project. They are kept with it for
        reference (``Entry.noted``), and do not have it expire either.

        Returns the entry, None for one that waits, and whether it is a
        new file. For one that waits, the name of the pending entry it is
        in is added to ``waits`` when a list is given. It waited when
        this was filed: a later filing may have had it take effect, and
        whoever made several tells which still wait by the entries that
        were given back since. Raises
        ``SpaceError`` for one that cannot be kept, among
        them one the user removed (``NoteRemoved``); an entry whose index
        could not be given a line is returned like any other, as by
        ``file_entry``.
        """
        from config.nekoclaw_settings import (
            TASK_AGENT_REVIEW_ENTRY_EVIDENCE_MAX_PATHS,
            TASK_AGENT_REVIEW_ENTRY_EVIDENCE_PATH_MAX_TOKENS,
        )
        from utils.tokenize import count_tokens

        roots = [str(base) for base in bases if base]
        # Kept in a line of names separated by commas: it is one name there.
        observer = _one_line(observer or source).replace(",", " ") or None
        # What the model gave as the files it is about: so many and no
        # more, and none too long to be a path it read.
        given = [
            item.strip()
            for item in (evidence if isinstance(evidence, (list, tuple)) else ())
            if isinstance(item, str)
            and item.strip()
            and count_tokens(item.strip()) <= TASK_AGENT_REVIEW_ENTRY_EVIDENCE_PATH_MAX_TOKENS
        ][:TASK_AGENT_REVIEW_ENTRY_EVIDENCE_MAX_PATHS]

        # The ones it gave first, then what its words name: a model does
        # not always fill in what it was asked for.
        named = list(dict.fromkeys([*given, *paths_named_in(f"{title}\n{description}\n{content}")]))
        # Looked for before the write takes its turn; see ``_evidence_before``.
        looked = await self._evidence_before(named, roots, looked_at=len(named))

        def work(allowed: bool) -> _Taken:
            found, _others, apart, untold = self._at_its_turn(*looked, allowed)
            note = _Note(
                title=title,
                content=content,
                kind=kind,
                description=description,
                source=source,
                known=(
                    _Known(
                        # Seen, when a file it names is there to be seen.
                        confidence=OBSERVED if any(path not in untold for path in found) else INFERRED,
                        evidence=tuple(found),
                        outside=tuple(apart),
                        untold=tuple(untold),
                        observer=observer,
                    )
                    if project is not None
                    # For every project: what one project's files show is
                    # noted, and nothing more.
                    else _Known(confidence=INFERRED, observer=observer, noted=tuple(found))
                ),
                files_vouch=project is not None,
            )
            return self._take_note(project, note, allowed=allowed)

        try:
            taken = await self._write_judged(work)
        except ValueError as exc:
            raise SpaceError(str(exc)) from exc
        except _LineNotAdded as exc:
            logger.warning("an entry is kept, and the index could not be given a line for it", exc_info=True)
            return exc.entry, exc.how == _CREATED
        if taken.how == _HELD:
            if waits is not None:
                waits.append(taken.entry.id)
            return None, False
        return taken.entry, taken.how == _CREATED

    async def refile_entries(
        self, commission_id: str, source: str, target: str, *, also: Iterable[str] = ()
    ) -> list[str]:
        """Move what one commission alone filed under a project to another project. Returns the ids there.

        For a commission found to belong to a project after it ended: what
        it filed while it was one of ``_daily`` goes with it, or the
        project's index would not have it. ``also`` are the commissions of
        the task agents it started: what they filed is its own. An entry
        other commissions added to stays where it is, and so does every
        entry of theirs, and one the user removed from where it would go
        (by a text of it or by its title, as ``_take_note`` tells).
        The files are moved as they are, each under a name that is free,
        and so are their lines in the index: a line the user reworded goes
        along as it is, but for the file it points at. Raises OSError, with
        nothing moved, when the index they are listed in cannot be read.

        A pending entry of theirs goes along too. It is not moved as it
        is, but judged anew where it goes (``_refile_pending``).
        """

        def work(allowed: bool) -> list[str]:
            paths = self._paths()
            self._revive(target)
            origin, destination = paths.memory_dir(source), paths.memory_dir(target)
            # Read before anything moves, and not taken for empty when it
            # cannot be read: a line the user reworded would be replaced by
            # one made anew, and theirs removed once the index reads again.
            self._text_to_change(paths.index(source))
            moved: list[str] = []
            own = {commission_id, *also}
            try:
                removed = _removed_from(self._notes(paths.notes_file(target))["removed"])
            except OSError:
                # What was removed there cannot be told: the entries go
                # along, as a note that takes effect is kept then.
                logger.warning("what was removed from a project cannot be read; entries go there unchecked", exc_info=True)
                removed = (set(), set())
            waiting: list[Entry] = []
            for entry in self._entries(origin, source):
                if not entry.sources or not set(entry.sources) <= own:
                    continue
                if entry.status == ENTRY_PENDING:
                    # Not moved as it is: judged anew where it goes, below.
                    waiting.append(entry)
                    continue
                old = origin / f"{entry.id}.md"
                name = self._free_name(destination, entry.id)
                # Written where it goes before it is removed where it was,
                # and removed there only as the file that was copied: one
                # the user saved from an editor in between is copied again.
                same = copied = stays = False
                for _attempt in range(_WRITE_ATTEMPTS):
                    before = self._signature(old)
                    text = self._read_text(old)
                    if text is None:
                        break
                    if any(removed) and _removed_there(removed, text, entry.title):
                        # The user removed this note from the project it
                        # would go to, and it is not kept there again: it
                        # stays where it is, which is a place they did not
                        # remove it from. Told of the very text that would
                        # be copied, not of one read before: it may have
                        # been saved from an editor in between.
                        stays = True
                        break
                    self._put_text(destination / f"{name}.md", text)
                    copied = True
                    same = self._signature(old) == before
                    if same:
                        break
                if text is None or stays:
                    if copied:
                        # There a moment ago, and gone, unreadable or not to
                        # go there by now: the copy an earlier try made of
                        # it is not left behind with no line.
                        try:
                            (destination / f"{name}.md").unlink()
                        except OSError:
                            logger.warning("the copy of an entry that does not go could not be removed", exc_info=True)
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
                    # The index cannot be read any more, or keeps being
                    # saved: the entry stays there as well. A line that was
                    # taken out is put back, and it is the line that goes
                    # along.
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
                    # One that has expired has no line where it was, and
                    # gets none where it goes. Told by the text that was
                    # copied: it may have come back into effect since the
                    # entries were looked through.
                    if _status_of(_parse_document(text)) == ENTRY_ACTIVE:
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
            # What it filed that is not in effect yet goes with it as well:
            # seen again by a commission of its project, it is found there.
            # What takes effect there at once is an entry of the project
            # like the ones that were moved.
            for entry in waiting:
                for name in self._refile_pending(target, origin / f"{entry.id}.md", entry, allowed):
                    if name not in moved:
                        moved.append(name)
            return moved

        return await self._write_judged(work)

    def _refile_pending(self, target: str, path: Path, entry: Entry, allowed: bool) -> list[str]:
        """Take a pending entry of a commission along to the project the commission goes to.

        It is judged anew where it goes, text by text, as if it were filed
        there by the commissions that have seen it (``_take_note``), and
        keeps the days it was filed on. So one that another commission of
        that project filed as well is seen twice and takes effect, with
        what both said of it; one the user removed there is not kept; and
        no more wait there than may. One that cannot be kept there just
        now stays where it was, and so does one the user saved while it
        was taken along (what was read of it is there; what they saved
        is here), and one that holds a text longer than a text may be
        (written so in an editor: filed there it would be cut, and the
        end of it lost with this file). Returns the ids of the entries
        in effect there that it went into.
        """
        read = self._pending_as_read(path, entry.project)
        if read is None:
            return []
        before, said, entry = read
        went: list[str] = []
        seen_by, sources = list(entry.seen_by), list(entry.sources)
        parts = self._whole_texts(said)
        if parts is None:
            logger.warning("a pending entry holds a text longer than a text may be; it stays where it is")
            return []
        # What it rested on goes along, but for what is gone; see ``_recorded``.
        rested, untold = _recorded(entry.evidence, entry.evidence_outside, allowed)
        for part in parts:
            note = _Note(
                title=entry.title,
                content=part,
                kind=entry.type,
                description=entry.description,
                source=sources[-1] if sources else None,
                # Seen by the ones that had seen it, and by nobody new.
                known=_Known(
                    confidence=entry.confidence or INFERRED,
                    evidence=tuple(rested),
                    untold=tuple(untold),
                    outside=entry.evidence_outside,
                    observer=seen_by[-1] if seen_by else None,
                    seen_before=tuple(seen_by[:-1]),
                    sources_before=tuple(sources[:-1]),
                    noted=entry.noted,
                ),
                # It waits since it was first filed, not since today.
                waited=(entry.created, entry.updated),
            )
            try:
                taken = self._take_note(target, note, allowed=allowed)
            except NoteRemoved:
                # Not kept there because the user removed it there: it does
                # not wait here any longer either.
                continue
            except _LineNotAdded as exc:
                # In its file there, with no line yet.
                went.append(exc.entry_id)
                gone, spoke = exc.gone, exc.spoke
            except (SpaceError, OSError, ValueError):
                logger.warning("a pending entry could not go with its commission to its project", exc_info=True)
                return list(dict.fromkeys(went))
            else:
                if taken.how != _HELD:
                    went.append(taken.entry.id)
                gone, spoke = taken.gone, taken.spoke
            # The next text goes with what this one found: an entry it went
            # into is in effect by now and is not asked about its files
            # again, so a file found gone is not brought back, and one that
            # did not speak for it (nothing was told of it) speaks for
            # nothing and stays on record.
            rested = [item for item in rested if item not in gone]
            untold = [item for item in rested if item not in spoke]
        self._discard_as_read(path, before)
        return list(dict.fromkeys(went))

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
        if content is not None:
            # As it is kept, so that what stands in it is told as what was filed.
            content = _plain_lines(content)

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
                    # The marks of what still stands in the new text stay:
                    # what was filed before may be in it word for word. The
                    # others go, and the new text as a whole gets a mark.
                    # In a text too long to look through they all stay: one
                    # of a text that is gone does no harm, as a text must
                    # stand in the entry as well to count as said. An entry
                    # that keeps no marks is not given any; see
                    # ``_said_already``.
                    marks = _listed(document, "texts")
                    if marks:
                        standing = _standing_marks(content, marks)
                        if standing is not None:
                            marks = [mark for mark in marks if mark in standing]
                        if _digest(content) not in marks:
                            marks.append(_digest(content))
                        document.set("texts", ", ".join(marks))
                    document.body = content
                document.set("updated", _date(self._clock()))
                return _render_document(document)

            # Made to the file as it is at the moment it is replaced: what
            # was saved from an editor meanwhile stays, but for what is
            # changed here.
            self._edit_text(path, change)
            entry = self._entry_from(path, project, with_content=True)
            # One that has expired has no line, nor has a pending one, and
            # neither is given one for being reworded: it gets its line
            # when it is in effect.
            listed = entry is not None and entry.status == ENTRY_ACTIVE
            if entry is not None and listed and (title is not None or description is not None):
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
        A line that points at an entry which is not there is removed as
        well.

        What was removed is remembered (``notes.json`` beside the index):
        the marks of the texts it was given and of what it says at that
        moment, and its title. A note that is the same, by
        either, is not kept again when a task agent or a review files it.
        Only what is removed through here is remembered; a file the user
        deletes by hand leaves nothing to tell it by. A pending entry is
        removed and remembered like any other, and the pending entries
        that are the same note as the one removed go with it.
        """
        from config.nekoclaw_settings import SPACE_REMOVED_MAX_RECORDS

        def work() -> bool:
            scope = self._scope(project, archive_year)
            name = safe_segment(entry_id)
            path = scope / SpacePaths.MEMORY_DIR / f"{name}.md"
            # Read before it goes: afterwards nothing says what it was.
            text = self._read_text(path)
            try:
                path.unlink()
            except FileNotFoundError:
                # No such entry. A line may still point at it: left by a
                # removal that could not write the index, which was told
                # to whoever asked. Asked again, the line goes.
                self._index_remove(scope / SpacePaths.INDEX_NAME, f"{SpacePaths.MEMORY_DIR}/{name}.md")
                return False
            # The file is gone from here on. That it was removed is put on
            # record whatever becomes of its line: an index that cannot be
            # written just now is told to the caller afterwards, and asked
            # again, this would find no file and remember nothing.
            unlisted: OSError | None = None
            try:
                self._index_remove(scope / SpacePaths.INDEX_NAME, f"{SpacePaths.MEMORY_DIR}/{name}.md")
            except OSError as exc:
                unlisted = exc
            if text is not None:
                document = _parse_document(text)
                record = {
                    # Of the texts it was given, and of what it says now:
                    # its text may have been replaced in an editor since,
                    # and that is what the user saw and removed.
                    # One that says nothing (emptied, then removed) has no
                    # text to remember: the mark of nothing would be that
                    # of every empty entry and every empty part of one.
                    # It may be among the marks the entry keeps as well.
                    "marks": [
                        mark
                        for mark in dict.fromkeys([*_listed(document, "texts"), _digest(document.body)])
                        if mark != _NO_TEXT
                    ],
                    "title": document.get("name") or name,
                    "at": _date(self._clock()),
                }

                def remember(data: dict[str, Any]) -> None:
                    data["removed"] = [*data["removed"], record][-SPACE_REMOVED_MAX_RECORDS:]

                try:
                    self._change_notes(scope / SpacePaths.NOTES_FILE, remember)
                except OSError:
                    logger.warning("an entry is removed, and that could not be put on record", exc_info=True)
                # Nor does the same note wait to take effect any longer: a
                # pending entry with one of its texts, or under its title
                # in other words.
                gone = _removed_from([record])
                memory = scope / SpacePaths.MEMORY_DIR
                for other in self._entries(memory, project):
                    if other.status != ENTRY_PENDING:
                        continue
                    said = self._read_text(memory / f"{other.id}.md")
                    if said is not None and _removed_there(gone, said, other.title):
                        self._discard(memory / f"{other.id}.md")
            if unlisted is not None:
                raise unlisted
            return True

        return await self._write(work)

    async def still_pending(self, project: str | None, ids: Iterable[str]) -> set[str]:
        """Return the ones of ``ids`` that are pending entries still, as far as can be told.

        Of the space (``project`` None) or of a project. One is not when
        its file is gone for good (it went into another entry, or was
        removed), is no file any more (a directory stands in its place),
        or says by now that it is in effect. One that cannot
        be read just now (held by another program, a disk that is away)
        is taken to wait still: nothing says that it does not.
        """
        names = list(ids)

        def work() -> set[str]:
            directory = self._paths().memory_dir(project)
            still: set[str] = set()
            for name in names:
                path = directory / f"{name}.md"
                text = self._read_text(path)
                if text is None:
                    # Not gone for good, nor something else by that name
                    # (a directory): a file, or one that cannot be told.
                    if _may_be_a_file(path):
                        still.add(name)
                elif _status_of(_parse_document(text)) == ENTRY_PENDING:
                    still.add(name)
            return still

        return await self._read(work)

    async def read_notes(self, project: str | None = None, *, archive_year: str | None = None) -> dict[str, Any]:
        """Return what was remembered of the entries the user removed from a project, or from the space.

        ``{"removed": [...]}``, for the console; an empty list when the
        record cannot be read. The notes that are not in effect yet are
        entries (``list_entries``, with the status ``ENTRY_PENDING``).
        """

        def work() -> dict[str, Any]:
            try:
                data = self._notes(self._scope(project, archive_year) / SpacePaths.NOTES_FILE)
            except OSError:
                return {"removed": []}
            return {"removed": list(data["removed"])}

        return await self._read(work)

    async def check_evidence(self) -> dict[str, int]:
        """Look whether the files the entries rest on are still there. Returns how many expired, came back, were dropped.

        Part of tidying; no model is asked. An entry the host made that
        rests on files, of the space or of a project in use, expires once
        none of them exists: it says so in its front matter and its line
        leaves the index, so task agents are no longer handed it; the file
        stays. One that had expired is in effect again when a file it
        rests on is back (a disk that was unplugged at the last look). An
        entry that rests on nothing does not expire, and neither does one
        the user wrote. Where it cannot be told whether a file is there,
        nothing is changed in what an entry says; one that says it expired
        and still has a line loses the line. A file outside the working
        directories is looked at only while the user lets task agents read
        there unasked, as the setting is at this moment: otherwise it is
        one that cannot be told about, and no entry expires over it.

        A pending entry does not expire: it is in no index to begin with.
        One that nobody filed again for longer than
        ``SPACE_HELD_KEPT_SECONDS``, by the day, is removed, file and all.
        It is not remembered as removed: the same note may be filed again.

        At most ``SPACE_EVIDENCE_ENTRIES_PER_UPKEEP`` entries are looked at
        in one call; the next call goes on after them.

        The files may be on a share that does not answer, and a look at
        one of those never comes back. So the entries are read first,
        which touches the space's own files only, and the pending ones
        that waited long enough are removed whatever becomes of the
        rest. Then the files are looked at in a thread of its own, entry
        by entry, for no longer than ``SPACE_EVIDENCE_LOOK_SECONDS``.
        What was found by then is used, and the next call goes on after
        the entry the look was at: a disk that answers slowly is got
        through a few entries a call, and an entry whose files never
        answer in time does not keep the looks from the ones after it.
        A look that was given up on (also when whoever tidies up was
        stopped while it waited) ends once the entry it is at answers;
        until then that entry is passed over as long as it rests on the
        same files, and with
        ``SPACE_EVIDENCE_LOOKS_AT_ONCE`` of them out none is started:
        the entries that rest on files are passed over, and the pending
        ones are still seen to.

        No file is looked at under the lock on the space's writes. An
        entry is changed when it is as it was read before the look
        (``_set_standing``), and when task agents may read outside the
        working directories as they might at the look.
        """
        from config.nekoclaw_settings import (
            SPACE_EVIDENCE_ENTRIES_PER_UPKEEP,
            SPACE_EVIDENCE_LOOK_SECONDS,
            SPACE_EVIDENCE_LOOKS_AT_ONCE,
            SPACE_HELD_KEPT_SECONDS,
        )
        from nekoclaw.host.detached import NoThread, run_detached

        allowed = await self._outside_allowed()
        # A pending entry last filed before this day has waited long enough.
        oldest = _date(self._clock() - SPACE_HELD_KEPT_SECONDS)
        # The entries a look that was given up on is still at.
        unanswered = frozenset(self._evidence_unanswered)

        def looked_for(evidence: Iterable[str], apart: Iterable[str]) -> tuple[str, ...]:
            # The files of an entry that a look really looks at: not the
            # ones outside while task agents may not read there unasked.
            out = set(apart)
            return tuple(path for path in evidence if allowed or path not in out)

        def read() -> tuple[list[_Rested], list[tuple[str | None, str]], int, int, int]:
            paths = self._paths()
            scopes: list[str | None] = [None, *(project.name for project in self._active_projects())]
            files: list[tuple[str | None, Path]] = []
            for scope in scopes:
                try:
                    files.extend(
                        (scope, item)
                        for item in sorted(paths.memory_dir(scope).iterdir())
                        if item.suffix == ".md" and item.is_file()
                    )
                except (OSError, ValueError):
                    continue
            if not files:
                return [], [], 0, 0, 0
            start = self._evidence_looked_until % len(files)
            turn = [*files[start:], *files[:start]][:SPACE_EVIDENCE_ENTRIES_PER_UPKEEP]
            # The entries of this turn that rest on files, and the pending
            # ones that have waited long enough.
            rested: list[_Rested] = []
            old: list[tuple[str | None, str]] = []
            indexes: dict[str | None, str] = {}
            for place, (scope, path) in enumerate(turn):
                text = self._read_text(path)
                if text is None:
                    continue
                document = _parse_document(text)
                if _status_of(document) == ENTRY_PENDING:
                    if (document.get("updated") or oldest) < oldest:
                        old.append((scope, path.stem))
                    continue
                evidence = _evidence_in(document)
                if not evidence or not _listed(document, "texts"):
                    continue
                apart = _outside_in(document)
                if (scope, path.stem, looked_for(evidence, apart)) in unanswered:
                    # A look at these very files is still out. (Resting on
                    # others by now, or with fewer of them to be looked at
                    # since the user took back reading outside, it is
                    # looked at.)
                    continue
                expired = document.get("status").strip().lower() == ENTRY_EXPIRED
                listed = False
                if expired:
                    # One that says it expired and still has its line: the
                    # index could not be written when it expired, or it
                    # could not be brought back and the line put there for
                    # it could not be taken out again.
                    if scope not in indexes:
                        indexes[scope] = self._read_text(paths.index(scope)) or ""
                    listed = self._index_line_in(indexes[scope], f"{paths.MEMORY_DIR}/{path.stem}.md") is not None
                rested.append(
                    _Rested(place, scope, path.stem, tuple(evidence), tuple(apart), expired, listed, _text_mark(text))
                )
            return rested, old, start, len(turn), len(files)

        rested, old, start, taken, total = await self._read(read)
        outcome = {"expired": 0, "restored": 0, "dropped": 0}
        for scope, entry_id in old:
            try:
                if await self._write(self._drop_pending, scope, entry_id, oldest):
                    outcome["dropped"] += 1
            except (OSError, ValueError):
                logger.warning("a pending entry that has waited long enough could not be removed", exc_info=True)

        # Whether the files of each entry are gone, in the order of
        # ``rested``: added to by the look, read here.
        found: list[bool | None] = []
        stop = threading.Event()

        def look() -> None:
            for item in rested:
                if stop.is_set():
                    return
                try:
                    gone = _gone(item.evidence, item.outside, allowed)
                except Exception:  # noqa: BLE001 - nothing is told of this entry; the others are looked at
                    logger.warning("the files an entry rests on could not be looked at", exc_info=True)
                    gone = None
                found.append(gone)

        # How many of ``rested`` were looked at, and how far among the
        # entries of this turn that is.
        got, reached = 0, taken
        looking: asyncio.Future[None] | None = None
        if rested and len(self._evidence_unanswered) >= SPACE_EVIDENCE_LOOKS_AT_ONCE:
            logger.warning("looks at the files the entries rest on have not come back; none is started")
        elif rested:
            try:
                looking = run_detached(look, name="nekoclaw-space-evidence")
            except NoThread:
                logger.warning("no thread could be started to look at the files the entries rest on")
        if looking is not None:
            stopped: asyncio.CancelledError | None = None
            try:
                await asyncio.wait_for(asyncio.shield(looking), SPACE_EVIDENCE_LOOK_SECONDS)
            except asyncio.TimeoutError:
                logger.warning("looking at the files the entries rest on took too long; the rest is left for later")
            except asyncio.CancelledError as exc:
                # Whoever tidies up was stopped. The look is given up on
                # like one that took too long, and counted as out.
                stopped = exc
            # It looks at no further entry, and ends once the one it is at
            # answers.
            stop.set()
            got = len(found)
            if got < len(rested):
                at = rested[got]
                # The next call goes on after it, and comes to it again in
                # its turn: one that never answers in time would have every
                # call begin with it, and get no further.
                reached = at.place + 1
                if not looking.done():
                    key = (at.scope, at.id, looked_for(at.evidence, at.outside))
                    self._evidence_unanswered.add(key)

                    def back(done: asyncio.Future[None], key: tuple[str | None, str, tuple[str, ...]] = key) -> None:
                        self._evidence_unanswered.discard(key)
                        # Looked at here, as nobody waits for it any more.
                        if not done.cancelled():
                            done.exception()

                    looking.add_done_callback(back)
            if stopped is not None:
                raise stopped
        if total:
            self._evidence_looked_until = (start + reached) % total

        for item, gone in zip(rested, found[:got]):
            # What is to become of the entry: True to expire, False to be
            # in effect again, None for its line alone to go.
            expire: bool | None
            if gone is True and (not item.expired or item.listed):
                expire = True
            elif gone is False and item.expired:
                expire = False
            elif gone is None and item.listed:
                # Whether its files are there cannot be told, so what it
                # says stays. But it says it expired, and an entry that
                # expired is handed to no task agent.
                expire = None
            else:
                continue
            try:
                if expire is None:
                    await self._write(self._unlist_expired, item.scope, item.id)
                    continue
                changed = await self._write_judged(
                    lambda may, item=item, expire=expire: may == allowed and self._set_standing(item, expire)
                )
            except (OSError, ValueError):
                logger.warning("an entry of the task agent space could not be marked as expired or in effect", exc_info=True)
                continue
            if changed:
                outcome["expired" if expire else "restored"] += 1
        return outcome

    def _drop_pending(self, project: str | None, entry_id: str, oldest: str) -> bool:
        """Remove a pending entry that was last filed before the day ``oldest``. Returns whether it was.

        Read once more under the lock, and left alone when it took effect
        or was filed again by now.
        """
        path = self._paths().memory_dir(project) / f"{safe_segment(entry_id)}.md"
        text = self._read_text(path)
        if text is None:
            return False
        document = _parse_document(text)
        if _status_of(document) != ENTRY_PENDING or (document.get("updated") or oldest) >= oldest:
            return False
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def _unlist_expired(self, project: str | None, entry_id: str) -> bool:
        """Take the line of an entry that says it expired out of the index. Returns whether it said so.

        For one whose files cannot be looked at: nothing is changed in
        what it says. Read once more under the lock, and left alone when
        it is in effect by now or cannot be read.
        """
        paths = self._paths()
        path = paths.memory_dir(project) / f"{safe_segment(entry_id)}.md"
        text = self._read_text(path)
        if text is None or _parse_document(text).get("status").strip().lower() != ENTRY_EXPIRED:
            return False
        self._index_remove(paths.index(project), f"{paths.MEMORY_DIR}/{path.stem}.md")
        return True

    def _set_standing(self, item: _Rested, expire: bool) -> bool:
        """Mark an entry as expired, or as in effect again, and see to its line in the index. Returns whether it changed.

        ``item`` is the entry as it was read before its files were looked
        at. No file is looked at here, under the lock on the space's
        writes: a share that does not answer would hold up every write.
        The entry is changed when its file says, each time it is read
        for that, the very same as it did then (``_Rested.said``). One
        that was written since is left for the next look: filed again
        meanwhile, it may rest on the same files and have been
        confirmed by a file that is back, and what was found before
        says nothing of it any more. So is one the user saved from an
        editor at that very moment, between two steps of this.
        """
        paths = self._paths()
        project, found = item.scope, (item.evidence, item.outside)
        path = paths.memory_dir(project) / f"{safe_segment(item.id)}.md"
        target = f"{paths.MEMORY_DIR}/{path.stem}.md"
        today = _date(self._clock())
        still: list[bool] = []

        def as_found(document: _Document) -> bool:
            return (
                document.has_meta
                and bool(found[0])
                and bool(_listed(document, "texts"))
                and _status_of(document) != ENTRY_PENDING
                and (tuple(_evidence_in(document)), tuple(_outside_in(document))) == found
            )

        def change(text: str) -> str:
            document = _parse_document(text)
            if _text_mark(text) != item.said or not as_found(document):
                still[:] = [False]
                return text
            still[:] = [True]
            if expire:
                if document.get("status").strip().lower() == ENTRY_EXPIRED:
                    return text
                document.set("status", ENTRY_EXPIRED)
                document.set("expired", today)
            else:
                if document.get("status").strip().lower() != ENTRY_EXPIRED:
                    return text
                document.drop("status")
                document.drop("expired")
            return _render_document(document)

        if not path.is_file():
            return False
        if expire:
            # The file first: one that says it expired while its line is
            # still there is seen to at the next look.
            changed = self._edit_text(path, change)
            if still and still[0]:
                self._index_remove(paths.index(project), target)
            return changed
        # Coming back, the line first. An entry that was said to be in
        # effect while its index could not be given the line would be
        # looked at no more, and stay out of the index for good; one that
        # has its line and still says it expired is tried again.
        text = self._read_text(path)
        if text is None or _text_mark(text) != item.said:
            return False
        document = _parse_document(text)
        if document.get("status").strip().lower() != ENTRY_EXPIRED or not as_found(document):
            return False
        line = _index_line(document.get("name") or path.stem, target, document.get("description"))
        self._index_add(paths.index(project), line, target)
        failed: OSError | None = None
        changed = False
        try:
            changed = self._edit_text(path, change)
        except OSError as exc:
            failed = exc
        if failed is not None or not (still and still[0]):
            # Not brought back, as far as can be told: the entry could not
            # be written, or is not what was looked at any more (the user
            # saved it at this very moment). An entry that expired has no
            # line, and nothing else would take the line out again. Whether
            # it still says it expired is told by its file as it is now,
            # not by how the writing ended: the writing may have gone
            # through before it failed, and an entry that is in effect
            # keeps its line. One that cannot be read keeps it as well; if
            # it still says it expired, the next look goes on from there.
            # One that is not there any more (the user deleted it at this
            # very moment) is looked at by no later look: its line goes.
            # Only one that is known to be gone: a file that cannot be
            # looked at just now may be an entry that is in effect.
            now = self._read_text(path)
            if (
                _parse_document(now).get("status").strip().lower() == ENTRY_EXPIRED
                if now is not None
                else _gone_for_good(path)
            ):
                try:
                    self._index_remove(paths.index(project), target)
                except OSError:
                    if failed is None:
                        raise
                    logger.warning(
                        "the line of an entry that could not be brought back could not be taken out", exc_info=True
                    )
        if failed is not None:
            raise failed
        # The line was made from what the entry said when it was read, and
        # the entry was written only as it still said that: the line calls
        # it what it is called.
        return changed

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
            # A project in use is called what its directory is called, which
            # is how it is made and how it is found: a name changed by hand
            # in its file would be one nothing finds it by, and renaming the
            # directory renames the project. An archived one can be in a
            # directory with a number after its name, so its file says.
            name=str(data.get("name") or directory.name) if archive_year is not None else directory.name,
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
            # With or without the mark some editors put at the beginning of a
            # file they save: the user may have changed the description there.
            data = json.loads((directory / SpacePaths.PROJECT_FILE).read_text(encoding="utf-8-sig"))
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
    ) -> Project | None:
        """Return the project of a name for a commission filed under it now.

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
                return self._ensure_project(active.name, description=description, workdir=workdir, at=at)
            archived = self._archived_of(name)
            if archived is not None:
                if unless_archived:
                    return None
                return self._ensure_project(self._restore(archived).name, at=at)
            if unless_new:
                return None
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
            _move(source, target)
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
                # Held by another program just now: told as it was written.
                review = Review(
                    commission_id=path.stem,
                    project=project,
                    archive_year=None,
                    title=document.get("title"),
                    status=document.get("status"),
                    ended=document.get("ended"),
                    entries=tuple(entries),
                    content=document.body.strip(),
                )
            return review

        return await self._write(work)

    def _review_directories(self) -> list[tuple[Path, str, str | None]]:
        """Return the ``reviews`` directory of every project: (directory, project, archive year)."""
        directories: list[tuple[Path, str, str | None]] = []
        for project in (*self._active_projects(), *self._archived_projects()):
            try:
                home = self._directory_of(project)
            except ValueError:
                # A directory someone made by hand under a name that cannot
                # be a project's. The reviews of the others are still found.
                continue
            directories.append((home / SpacePaths.REVIEWS_DIR, project.name, project.archive_year))
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
        kept = [(header, text.strip(), cap, is_index) for header, text, cap, is_index in sections if text.strip()]
        if not kept:
            return ""
        note = texts.SPACE_BRIEF_NOTE.format(root=root, project=name)

        def compose() -> str:
            # An index has no cap on its length, and counting its tokens
            # takes as long as it is long: every text is counted once, and
            # not on the event loop.
            sizes = [count_tokens(text) for _header, text, _cap, _is_index in kept]

            # How many tokens of its text each part shows, as it stands.
            shown = list(sizes)

            def part(position: int, cap: int) -> str:
                header, text, _cap, is_index = kept[position]
                if sizes[position] > cap:
                    if is_index:
                        # Whole lines, so no line ends in half a link. The
                        # first line is handed back even when it alone is
                        # over: then no line is shown, only that it was cut.
                        lines, _dropped = take_lines_within_token_budget(text.split("\n"), cap)
                        text = "\n".join(lines)
                        if count_tokens(text) > cap:
                            text = ""
                    text = truncate_to_tokens(text, cap).rstrip()
                    shown[position] = count_tokens(text)
                    text = f"{text}\n{texts.SPACE_BRIEF_CUT_NOTE}".lstrip("\n")
                return f"{header}\n{text}"

            parts = [part(position, cap) for position, (_header, _text, cap, _is_index) in enumerate(kept)]

            def whole() -> str:
                return "\n\n".join([note, *parts])

            brief = whole()
            # The parts have caps that leave room for the headings and the
            # note. The note names the directory of the space, though, and
            # the headings the project: with a long path or name the whole
            # is over its cap. What is over comes off the parts, so that
            # each still ends at a whole line and says that it was cut; cut
            # off at the end of the whole, the last part would end anywhere
            # and say nothing. It comes off the indexes first, the last
            # first, and off the rules only when the indexes are used up:
            # the rules are the user's own words, the indexes were written
            # by task agents and reviews and point at files that hold the
            # rest.
            positions = list(reversed(range(len(kept))))
            indexes_first = [at for at in positions if kept[at][3]] + [at for at in positions if not kept[at][3]]
            for position in indexes_first:
                over = count_tokens(brief) - SPACE_BRIEF_MAX_TOKENS
                if over <= 0:
                    break
                # Off what the part shows, not off its cap: an index that
                # was over its cap ends at a whole line some way below it,
                # and a cap brought down by less than that leaves it as
                # long as it was, with the rules to give way in its place.
                parts[position] = part(position, max(0, shown[position] - over - _BRIEF_CUT_ROOM))
                brief = whole()
            return truncate_to_tokens(brief, SPACE_BRIEF_MAX_TOKENS)

        return await asyncio.to_thread(compose)

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
        """Whether a shell command points at the space: the path of its directory occurs in it.

        The path as it is configured and as it resolves, with either kind
        of slash, single or doubled, in any case, and as a shell from the
        Unix world writes a Windows drive (``/d/...``). What is to be kept
        from commands is the files task agents keep for good, not a word:
        a command that only mentions the name of the directory is not
        one, as the name occurs in the sources of the application, which a
        task agent may well be working on.

        A path with steps that lead nowhere in it is seen as well: ``/./``,
        and ``/name/..``, which is read as the path without the two. So is
        one written from the home directory when the space is below it:
        ``~/...``, ``$HOME/...``, ``%USERPROFILE%``, as a shell or
        PowerShell writes it. That is how a command is most likely to
        write a path below the user's own directory.

        Not seen: a path relative to where the command runs, one that is
        put together from other variables or from wildcards, one whose
        spaces are escaped, a directory above the space. Those are left to
        whoever approves commands; there is no sandbox.
        """

        def plain(text: str) -> str:
            return re.sub(r"/+", "/", text.replace("\\", "/")).casefold()

        def straight(text: str) -> str:
            # Without the steps that lead nowhere. Read off the text alone:
            # where a link is one of the names this is another place than
            # the real one, and the worst of that is a question too many.
            text = _STEP_IN_PLACE.sub("", text)
            for _step in range(_STEPS_BACK_MAX):
                shorter = _STEP_BACK.sub("", text, count=1)
                if shorter == text:
                    break
                text = shorter
            return text

        def from_home(place: Path) -> set[str]:
            # The place as it is written from the home directory, when it is below it.
            forms: set[str] = set()
            try:
                homes = {Path.home(), Path.home().resolve()}
            except (OSError, RuntimeError):
                return forms
            for home in homes:
                try:
                    below = place.relative_to(home).as_posix()
                except ValueError:
                    continue
                if below in ("", "."):
                    continue
                forms.update(plain(f"{name}/{below}").rstrip("/") for name in _HOME_NAMES)
            return forms

        def work() -> bool:
            root = self._paths().root
            places = {root}
            try:
                places.add(root.resolve())
            except OSError:
                # Not to be resolved (its drive is gone, say): the path as
                # it is configured is still looked for.
                pass
            said = plain(command)
            # Without its quotation marks as well: they are put round a part
            # of a path as often as round all of it (``"$HOME"/...``), and
            # the shell reads the path the same. A path that is in single
            # ones is not put together by the shell; taken for one that is,
            # it is asked about once too often.
            # Made plain once more: where a mark stood between two slashes
            # they are doubled now.
            bare = plain(said.replace('"', "").replace("'", ""))
            # As it was written, and as it reads without steps that lead nowhere.
            texts = {said, straight(said), bare, straight(bare)}
            for place in places:
                written = plain(str(place)).rstrip("/")
                for form in {written, straight(written), *from_home(place)}:
                    if not form:
                        continue
                    if any(form in text for text in texts):
                        return True
                    if len(form) > 2 and form[1] == ":" and form[0].isalpha():
                        if any(f"/{form[0]}{form[2:]}" in text for text in texts):
                            return True
            return False

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
    earlier moves left under such a name is cleared away with it,
    whatever it was called before: all of it can go. Told by how the name
    begins, not by a pattern: a project's name can have ``[`` or ``*`` in it.
    """
    try:
        left_over = [item for item in source.parent.iterdir() if item.name.startswith(_GONE_PREFIX)]
    except OSError:
        left_over = []
    for left in left_over:
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

    Raises OSError when the copy does not hold the same files in the end,
    or when the original was written to meanwhile (a program that had a
    file of it open before it was set aside can still write to it); the
    original is then the only place that has it all.
    """
    before = _files_of(original)
    # What is a file in the one and a directory in the other is taken out
    # of the copy first: copied over a directory of its name, a file would
    # land inside it.
    for directory, directories, names in os.walk(original, followlinks=True):
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
    if _files_of(original) != before:
        raise OSError(f"{original.name} was written to while its copy was brought up to it")


def _files_of(root: Path) -> list[tuple[str, int, int]]:
    """Return what a directory holds: every file's path in it, when it was last written and its size.

    Raises OSError when a directory in it cannot be listed or a file
    cannot be looked at: left out, two such lists could be the same though
    the directories are not.
    """

    def fail(error: OSError) -> None:
        raise error

    found: list[tuple[str, int, int]] = []
    # Links to directories are gone into: copying goes into them too, and
    # what is behind one would otherwise be in the copy and not in this list.
    for directory, _directories, names in os.walk(root, onerror=fail, followlinks=True):
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
    source, at the target, or at both. What has a name that begins with a
    dot is no project: a copy that stopped half way and what was known to
    be of no use can go, while what was set aside and not looked at yet
    may hold what the target lacks, and is never removed. (Setting the
    source aside before the copy has its name would, stopped there, leave
    both out of sight.)
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
            try:
                _bring_up_to(target, aside)
            except OSError as exc:
                raise _LeftAside(
                    f"{source.name} changed while it was copied and could not be put back; it is at {aside.name}"
                ) from exc
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
