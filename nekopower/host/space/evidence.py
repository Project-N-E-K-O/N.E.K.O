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

"""Path matching and background evidence verification helpers for space entries."""

from __future__ import annotations

import asyncio
import os
import re

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .models import (
    logger,
    T,
    ENTRY_PENDING,
    ENTRY_EXPIRED,
    _OUTDATED,
    _SUPERSEDED_BY,
    _Document,
    _Known,
    _WORD_BREAKS,
    _PLAIN_PATH,
    _WEB_ADDRESS,
    _QUOTED,
    _HAS_EXTENSION,
    _CLAUSE_MARKS,
    _A_NUMBER,
    _Question,
    _ASKING,
    _OUT,
    _NobodyWaits,
    _Rewritten,
)
from .markdown import (
    _one_line,
    _shown,
    _confidence,
    _stronger,
    _evidence_in,
    _noted_in,
    _set_noted,
    _seen_in,
    _not_among,
    _joined,
    _outside_in,
    _status_of,
    _set_evidence,
    _listed,
)

# Late-binding read point for symbols that tests rebind on
# ``nekopower.host.space`` via ``monkeypatch.setattr`` / ``patch``. Do NOT
# from-import those names here: a from-import snapshots the value at
# import time and the facade patch would silently miss this module.
from nekopower.host import space as _space_facade


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
    from config.nekopower_settings import SPACE_EVIDENCE_MAX_PATHS, SPACE_EVIDENCE_PATH_MAX_TOKENS
    from nekopower.runtime.permissions import is_inside
    from utils.tokenize import count_tokens

    # Each base as it is written, and where it really is. Only the base
    # itself is looked at for that, never a path that was named.
    roots = [(str(base), _space_facade._real_place(str(base))) for base in bases if base]
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
            others.append((_space_facade._cut(text if "\n" in named or "\r" in named else named, 24), "not a usable path"))
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
    from config.nekopower_settings import SPACE_EVIDENCE_NAMED_MAX_PATHS

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


def _places_out() -> set[str]:
    """Return the places a question that was given up on is at: nothing is asked about them until it is answered."""
    with _ASKING:
        return {place for other in _OUT if other.given_up for place in other.at}


def _question(places: Iterable[str]) -> _Question:
    """Return a question about ``places`` that may be asked, counted as out. Raises TimeoutError when it may not; see ``_asked``."""
    from config.nekopower_settings import SPACE_EVIDENCE_ASKED_AT_ONCE

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
    from config.nekopower_settings import SPACE_EVIDENCE_ASK_SECONDS
    from nekopower.host.detached import NoThread, wait_detached

    question = _question(places)
    try:
        return wait_detached(_asking(question, look), name="nekopower-space-file", seconds=SPACE_EVIDENCE_ASK_SECONDS)
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
    from config.nekopower_settings import SPACE_EVIDENCE_BEFORE_SECONDS
    from nekopower.host.detached import NoThread, run_detached

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
        asking = run_detached(_asking(question, lambda: look(at)), name="nekopower-space-file")
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


def _not_looked_at(named: list[str], looked_at: int | None = None) -> list[tuple[str, str]]:
    """Return what is said of the files a note names when the file system did not answer: none was looked at."""
    from config.nekopower_settings import SPACE_EVIDENCE_MAX_PATHS

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
        by_place.setdefault(_space_facade._place_of(path), []).append(path)
    there: dict[str, bool | None] = {}
    for place, files in by_place.items():
        try:
            there.update(_space_facade._asked([place], lambda files=files: {path: _space_facade._stands(path) for path in files}))
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
    there = _space_facade._stood([path for path in named if allowed or path not in apart])
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
        there = None if (path in apart and not allowed) else _space_facade._stands(path)
        if there:
            return False
        if there is None:
            unknown = True
    return None if unknown else True


def _outside_of(paths: Iterable[str], bases: Iterable[str]) -> list[str]:
    """Return the files among ``paths`` that lie in none of ``bases``, going by where each really is."""
    places = [_space_facade._real_place(str(base)) for base in bases if base]
    return [path for path in paths if not any(_lexically_inside(path, place) for place in places)]


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
    from config.nekopower_settings import SPACE_EVIDENCE_MAX_PATHS, SPACE_SEEN_BY_KEPT

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
        # Filed again, one that tidying found out of date holds after all.
        document.drop(_OUTDATED)
        document.drop(_SUPERSEDED_BY)
        rested = _evidence_in(document)
        # Asked under the lock on the space's writes, so only for so long;
        # one of which nothing is told stays, like one that cannot be told.
        asked = [path for path in rested if allowed or path not in apart]
        there = _space_facade._stood(asked)
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
