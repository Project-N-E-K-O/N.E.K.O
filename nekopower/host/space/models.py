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

"""Data structures, constants, regex patterns, exceptions, and shared state for the space package."""

from __future__ import annotations

import logging
import re
import threading

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any, TypeVar

logger = logging.getLogger("nekopower.host.space")

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

# The lines of the front matter of an entry that tidying found out of date
# (``TaskAgentSpace.tidy_entries``): why, and the entry that says otherwise.
# It is expired like one whose files are gone, and does not come back when
# they are there: only when it is filed again.
_OUTDATED = "outdated"

_SUPERSEDED_BY = "superseded_by"

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
    # For one that tidying found out of date: why. Empty for any other,
    # one that expired because its files are gone among them.
    outdated: str = ""

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["sources"] = list(self.sources)
        data["evidence"] = list(self.evidence)
        data["seen_by"] = list(self.seen_by)
        data["evidence_outside"] = list(self.evidence_outside)
        data["noted"] = list(self.noted)
        return data

# Who may have written what a project is for; see ``Project.description_by``.
DESCRIPTION_AUTHORS: tuple[str, ...] = ("creator", "review", "user")


@dataclass(frozen=True, slots=True)
class Project:
    """One project: ``project.json`` of its directory."""

    name: str
    description: str = ""
    # Where its files are: the working directory of the commission it began with.
    workdir: str | None = None
    # Who wrote ``description`` (``DESCRIPTION_AUTHORS``): whoever made the
    # project, the review of one of its commissions, or the user, whose
    # words no review writes over (``TaskAgentSpace.describe_project``).
    # Empty while there is none, and in a record written before this.
    description_by: str = ""
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

# What became of an entry that was filed: a new file, added to one, or there already.
_CREATED, _ADDED, _THERE = "created", "added", "there"

# What became of a note that does not take effect yet: it is in a pending entry.
_HELD = "held"

# What the target of a link is made of when it can be written as it is:
# letters and digits of any script, and the few marks the paths here have.
_PLAIN_TARGET = re.compile(r"[\w./~-]+")

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


class _NobodyWaits(Exception):
    """Ends a look for files that nobody waits for any more; see ``_asked_off_the_loop``."""

# The heading the host gives a text that is added to an entry: the day.
_ADDITION = re.compile(r"## \d{4}-\d{2}-\d{2}")

# How much of a text given in the console is looked through for what still
# stands in it. Every part is marked with all that follows it, line by
# line, so the work is the parts times the lines, and the parts times the
# characters: about a third of a second at either limit. The text itself
# has no limit; it is the user's.
_STANDING_STEPS_MAX = 1_000_000

_STANDING_CHARACTERS_MAX = 200_000_000

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


class _NotMade(SpaceError):
    """A change tidying asked for is not made; the message says why (``TaskAgentSpace.tidy_entries``)."""


@dataclass(frozen=True, slots=True)
class Layer:
    """The entries of one layer (the space as a whole, or one project), as tidying reads them.

    ``entries`` are all of them with their texts, the user's own among them;
    ``made`` are the ids of the ones the host made, which keep marks of
    their texts. ``marks`` tell, by id, all each file said as it was read
    (``_text_mark``): a change is made only to an entry that still says
    it. ``said`` tell, by id, what each entry says, its title and
    description with its text, and nothing of how it stands: tidying asks
    for a look at a layer again only when one of these changed.
    """

    project: str | None
    entries: tuple[Entry, ...]
    made: frozenset[str]
    marks: dict[str, str]
    said: dict[str, str]


@dataclass
class _LineToPut:
    """What tidying could not write into the line of an entry in its index; see ``TaskAgentSpace.settle_tidied``."""

    # It has no line, and is to be given one.
    missing: bool = False
    # Descriptions the host wrote into its line, which the entry no longer
    # says: the line is to say what it says now.
    described: set[str] = field(default_factory=set)
    # Titles tidying put into its line and could not take out again, each
    # with what the line called it before and what the entry was called.
    titled: dict[str, tuple[str, str]] = field(default_factory=dict)
