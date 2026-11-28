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

"""``TaskAgentSpace`` assembly, entry judgment, evidence checks, reviews, and brief generation.

``__init__`` is the single home of every instance attribute (locks,
counters, asking sets, unsettled/reline tracking); the three domain
mixins contribute methods only.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any
from nekopower.host.ledger import Commission
from nekopower.host.paths import DEFAULT_PROJECT, SpacePaths, safe_segment

from .models import (
    logger,
    _DEFAULT_TYPE,
    VERIFIED,
    OBSERVED,
    INFERRED,
    ENTRY_ACTIVE,
    ENTRY_PENDING,
    ENTRY_EXPIRED,
    _OUTDATED,
    _WRITE_ATTEMPTS,
    redact,
    Entry,
    Review,
    _Document,
    _Rested,
    _Known,
    _Note,
    _Taken,
    _CREATED,
    _ADDED,
    _THERE,
    _HELD,
    _BRIEF_CUT_ROOM,
    _STEP_IN_PLACE,
    _STEP_BACK,
    _STEPS_BACK_MAX,
    _HOME_NAMES,
    SpaceError,
    NoteRemoved,
    _NoPlace,
    _Rewritten,
    _NotAsWritten,
    _LineNotAdded,
    _NotMade,
    _LineToPut,
)
from .markdown import (
    _one_line,
    _plain_lines,
    _parse_document,
    _render_document,
    _date,
    _datetime,
    slug,
    normalize_name,
    _entry_type,
    _link,
    _normal,
    _text_mark,
    _digest,
    _NO_TEXT,
    _confidence,
    _stronger,
    _evidence_in,
    _noted_in,
    _set_noted,
    _seen_in,
    _not_among,
    _joined,
    _notes_of,
    _notes_text,
    _uncounted,
    _outside_in,
    _status_of,
    _set_evidence,
    _removed_from,
    _removed_note,
    _removed_there,
    _listed,
    _points_at,
    _said_already,
    _index_marks,
    _index_line,
)
from .evidence import (
    paths_named_in,
    _places_out,
    _not_looked_at,
    _outside_of,
    _gone_for_good,
    _may_be_a_file,
    _speaks_no_more,
    _keep_known,
)
from .projects import ProjectsMixin
from .storage import StorageMixin
from .tidy import TidyMixin

# Late-binding read point for symbols that tests rebind on
# ``nekopower.host.space`` via ``monkeypatch.setattr`` / ``patch``. Do NOT
# from-import those names here: a from-import snapshots the value at
# import time and the facade patch would silently miss this module.
from nekopower.host import space as _space_facade


class TaskAgentSpace(StorageMixin, ProjectsMixin, TidyMixin):
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
        self._workdir_clearing: Callable[[str], bool] | None = None
        self._wait_workdir_clear: Callable[[str], Awaitable[None]] | None = None
        self._any_workdir_clearing: Callable[[], str | None] | None = None
        self._veto_workdir_clear: Callable[[str | None], None] | None = None
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
        # Pending entries tidying merged two guesses into and could not tell
        # then whether they take effect, each with the mark of what the
        # merge wrote (``settle_tidied``). Kept while the host runs: after a
        # restart such an entry waits like any other.
        self._unsettled: dict[tuple[str | None, str], str] = {}
        # Lines of an index that tidying changed an entry for and could not
        # write; put right by a later round, by what the entry is then
        # (``settle_tidied``).
        self._relines: dict[tuple[str | None, str], _LineToPut] = {}

    def set_workdir_guard(
        self,
        is_clearing: Callable[[str], bool] | None,
        wait_clear: Callable[[str], Awaitable[None]] | None,
        *,
        any_clearing: Callable[[], str | None] | None = None,
        veto_clear: Callable[[str | None], None] | None = None,
    ) -> None:
        """Register callbacks coordinating project restoration with working-directory cleanup."""
        self._workdir_clearing = is_clearing
        self._wait_workdir_clear = wait_clear
        self._any_workdir_clearing = any_clearing
        self._veto_workdir_clear = veto_clear

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
        from config.nekopower_settings import SPACE_EVIDENCE_MAX_PATHS

        first = named[: looked_at or SPACE_EVIDENCE_MAX_PATHS]
        # One question at a time about a place, among the notes whose
        # files are looked for before their writes: several that name
        # files on one share that does not answer cost one thread, not
        # one each. The ones that waited find the place out, and are not
        # asked. (In one order, so that no two wait for each other.)
        places = {*(_space_facade._place_of(root) for root in roots), *(_space_facade._place_of(item.strip()) for item in first if os.path.isabs(item.strip()))}
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
        from config.nekopower_settings import SPACE_EVIDENCE_MAX_PATHS

        out = _places_out()
        roots = [root for root in bases if _space_facade._place_of(root) not in out]
        inside = {_space_facade._place_of(root) for root in roots}

        # With no working directory at all, a path that is not whole has
        # nothing to be read against. That is told at once, and needs no
        # look at the file system: no disk is at fault, and none is
        # waited for, however many questions are out.
        baseless = [item for item in first if not bases and not os.path.isabs(item.strip())]
        told = _space_facade.evidence_of(baseless, [], looked_at=len(baseless))[1] if baseless else []
        asking = [item for item in first if item not in baseless]

        def reached(item: str) -> bool:
            where = item.strip()
            if os.path.isabs(where):
                return _space_facade._place_of(where) not in out
            # Read against the working directories: looked for unless
            # every one of them is in a place that does not answer.
            return bool(roots)

        wanted = [item for item in asking if reached(item)]
        unasked = [item for item in asking if not reached(item)]
        unseen = [*told, *(_not_looked_at(unasked, len(unasked)) if unasked else [])]
        if not wanted:
            return [], unseen, []
        places = {*inside, *(_space_facade._place_of(item.strip()) for item in wanted if os.path.isabs(item.strip()))}

        def look(at: Callable[[Iterable[str]], None]) -> tuple[list[str], list[tuple[str, str]], list[str]]:
            # Where the work was done first, each directory where it is:
            # one on a share that does not answer is what the question is
            # out for, not the files that are looked for after it.
            for root in roots:
                at([_space_facade._place_of(root)])
                _space_facade._real_place(root)
            # Then the files one by one, saying where each is: as
            # ``evidence_of`` does them, until enough of them count.
            counted: list[str] = []
            others: list[tuple[str, str]] = []
            for item in wanted:
                if len(counted) >= SPACE_EVIDENCE_MAX_PATHS:
                    break
                at([_space_facade._place_of(item.strip())] if os.path.isabs(item.strip()) else inside)
                found, rest = _space_facade.evidence_of([item], roots, looked_at=1, outside=asked)
                counted, others = _joined(counted, found, SPACE_EVIDENCE_MAX_PATHS), [*others, *rest]
            at(inside)
            return counted, [*others, *unseen], _outside_of(counted, roots)

        try:
            return await _space_facade._asked_off_the_loop(places, look)
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
        there = _space_facade._stood(counted)
        gone = [path for path in counted if there.get(path) is False]
        if gone:
            others = [*others, *((path, "not found") for path in gone)]
            counted = [path for path in counted if path not in gone]
            apart = [path for path in apart if path not in gone]
        return counted, others, apart, [path for path in counted if not there.get(path)]

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
            outdated=document.get(_OUTDATED) if _status_of(document) == ENTRY_EXPIRED else "",
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
        from config.nekopower_settings import SPACE_SAME_TOPIC_SIMILARITY
        from nekopower.host.selection import similarity

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
        added_on: str | None = None,
        as_marked: list[str] | None = None,
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

        ``added_on`` is the day a text that is added was written, where
        that is not today: one that tidying moves from another entry. The
        section it gets is headed by that day, so that what it says of
        "yesterday" means what it meant.

        ``as_marked`` is for tidying, which may change ``into`` only as a
        model was shown it: holds the mark of all its file says
        (``_text_mark``). Raises ``_NotMade``, before anything is written,
        when the file says anything else as it is read to be written; once
        written, holds the mark of what was written.

        ``may_add`` is False for a note whose text was seen to be in
        ``into`` and whose commission has no place left: should the text
        be gone from the entry by now (taken out in an editor at that
        very moment), it is not added again. Raises ``_NoPlace`` then,
        before anything is written. ``allowed`` is whether the user lets
        task agents read outside the working directories unasked; see
        ``_keep_known``.
        """
        from config.nekopower_settings import (
            SPACE_ENTRY_CONTENT_MAX_TOKENS,
            SPACE_ENTRY_DESCRIPTION_MAX_TOKENS,
            SPACE_ENTRY_TITLE_MAX_TOKENS,
            SPACE_EVIDENCE_MAX_PATHS,
            SPACE_SEEN_BY_KEPT,
        )
        from config.prompts.prompts_nekopower_space import SPACE_ENTRY_PENDING_NOTICE

        paths = self._paths()
        self._revive(project)
        title = _space_facade._cut(_one_line(redact(title)), SPACE_ENTRY_TITLE_MAX_TOKENS)
        content = _space_facade._cut(redact(content), SPACE_ENTRY_CONTENT_MAX_TOKENS)
        # A text that is nothing but the marks of a sentence says nothing,
        # and is told from no other such text: all of them have one mark.
        if not title or not _normal(content):
            raise SpaceError("an entry needs a title and a text")
        description = _space_facade._cut(_one_line(redact(description)) or _one_line(content), SPACE_ENTRY_DESCRIPTION_MAX_TOKENS)
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
                    addition = f"## {added_on or today}\n\n{content}"
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
            if as_marked is None:
                self._edit_text(path, add)
            else:
                # Moved on only once written: a try that is made again (the
                # file was touched, or saved over right after) compares with
                # what was there before it, not with what it did not write.
                base = as_marked[0]
                results: list[str] = []

                def add_as_marked(text: str) -> str:
                    results.clear()
                    if _text_mark(text) != base:
                        raise _NotMade(f"{existing.id} changed since it was looked over")
                    result = add(text)
                    results.append(_text_mark(result))
                    return result

                self._edit_text(path, add_as_marked)
                if results:
                    as_marked[0] = results[0]
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
        from config.nekopower_settings import SPACE_ENTRY_CONTENT_MAX_TOKENS
        from utils.tokenize import count_tokens

        def whole(text: str) -> bool:
            # As ``_file_entry`` keeps a text, before it cuts it.
            return count_tokens(_plain_lines(redact(text)).strip()) <= SPACE_ENTRY_CONTENT_MAX_TOKENS

        texts = _space_facade._texts_of(document.body, _listed(document, "texts"))
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
        from config.nekopower_settings import SPACE_HELD_MAX_NOTES

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
        from config.nekopower_settings import SPACE_ENTRY_CONTENT_MAX_TOKENS, SPACE_ENTRY_TITLE_MAX_TOKENS

        return (
            _space_facade._cut(_one_line(redact(str(title or ""))), SPACE_ENTRY_TITLE_MAX_TOKENS),
            _space_facade._cut(redact(str(content or "")), SPACE_ENTRY_CONTENT_MAX_TOKENS),
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
        as_marked: list[str] | None = None,
    ) -> tuple[Entry, OSError | None]:
        """Have a pending entry take effect where it is, seen by the commissions of ``known`` as well.

        Nothing is added to what it says. Returns the entry, and what
        kept its line out of the index if anything did. One that says
        nothing (emptied in the console) has nothing that could take
        effect: it is removed, as it is when there is nothing of it to
        put into another entry (``_fold``).

        ``as_marked`` is for tidying, as for ``_file_entry``: the entry
        takes effect only while all its file says is what it holds, read
        in the same read that writes it; once written it holds the mark
        of what was written.
        """
        paths = self._paths()
        path = paths.memory_dir(project) / f"{pending.id}.md"
        seen = _Known(confidence=pending.confidence or INFERRED, observer=known.observer, seen_before=known.seen_before)
        # Whether it said nothing, as it was read to be written.
        empty: list[bool] = []
        # What was written when it took effect.
        took: list[_Document] = []

        base = as_marked[0] if as_marked is not None else None

        def change(text: str) -> str:
            took[:] = []
            # What a look tells is of this attempt (as in ``_file_entry``).
            at_write.clear()
            if base is not None and _text_mark(text) != base:
                # Saved since tidying wrote it: not what it confirmed.
                return text
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
        if as_marked is not None and took:
            as_marked[0] = _text_mark(_render_document(took[0]))
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
        rested, untold = _space_facade._recorded(pending.evidence, pending.evidence_outside, allowed)
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
        from config.nekopower_settings import SPACE_EVIDENCE_MAX_PATHS, SPACE_SEEN_TO_TAKE_EFFECT

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
        on_record, untold = _space_facade._recorded(own.evidence, own.evidence_outside, allowed) if own is not None else ([], [])
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
        from config.nekopower_settings import SPACE_LESSONS_PER_COMMISSION
        from config.prompts import prompts_nekopower_space as texts
        from config.prompts.prompts_nekopower_space import SPACE_LESSONS_CAP_REACHED

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
            over = len(self._added_by) - _space_facade._COUNTED_COMMISSIONS
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
        from config.nekopower_settings import (
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
        rested, untold = _space_facade._recorded(entry.evidence, entry.evidence_outside, allowed)
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
                        standing = _space_facade._standing_marks(content, marks)
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
        from config.nekopower_settings import SPACE_REMOVED_MAX_RECORDS

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
        from config.nekopower_settings import (
            SPACE_EVIDENCE_ENTRIES_PER_UPKEEP,
            SPACE_EVIDENCE_LOOK_SECONDS,
            SPACE_EVIDENCE_LOOKS_AT_ONCE,
            SPACE_HELD_KEPT_SECONDS,
        )
        from nekopower.host.detached import NoThread, run_detached

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

        def read() -> tuple[list[_Rested], list[tuple[str | None, str]], list[tuple[str | None, str]], int, int, int]:
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
                return [], [], [], 0, 0, 0
            start = self._evidence_looked_until % len(files)
            turn = [*files[start:], *files[:start]][:SPACE_EVIDENCE_ENTRIES_PER_UPKEEP]
            # The entries of this turn that rest on files, and the pending
            # ones that have waited long enough.
            rested: list[_Rested] = []
            old: list[tuple[str | None, str]] = []
            # Out of date by tidying, and still with a line.
            stale: list[tuple[str | None, str]] = []
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
                expired = document.get("status").strip().lower() == ENTRY_EXPIRED

                def still_listed(scope: str | None = scope, name: str = path.stem) -> bool:
                    if scope not in indexes:
                        indexes[scope] = self._read_text(paths.index(scope)) or ""
                    return self._index_line_in(indexes[scope], f"{paths.MEMORY_DIR}/{name}.md") is not None

                if expired and document.get(_OUTDATED) and _listed(document, "texts"):
                    # Tidying found it out of date (``tidy_entries``): it
                    # does not come back because its files are there, and
                    # whether it rests on any is no matter. Only a line it
                    # still has goes: the index could not be written then.
                    if still_listed():
                        stale.append((scope, path.stem))
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
                listed = False
                if expired:
                    # One that says it expired and still has its line: the
                    # index could not be written when it expired, or it
                    # could not be brought back and the line put there for
                    # it could not be taken out again.
                    listed = still_listed()
                rested.append(
                    _Rested(place, scope, path.stem, tuple(evidence), tuple(apart), expired, listed, _text_mark(text))
                )
            return rested, old, stale, start, len(turn), len(files)

        rested, old, stale, start, taken, total = await self._read(read)
        outcome = {"expired": 0, "restored": 0, "dropped": 0}
        for scope, entry_id in old:
            try:
                if await self._write(self._drop_pending, scope, entry_id, oldest):
                    outcome["dropped"] += 1
            except (OSError, ValueError):
                logger.warning("a pending entry that has waited long enough could not be removed", exc_info=True)
        for scope, entry_id in stale:
            try:
                await self._write(self._unlist_expired, scope, entry_id)
            except (OSError, ValueError):
                logger.warning("the line of an entry that is out of date could not be taken out", exc_info=True)

        # Whether the files of each entry are gone, in the order of
        # ``rested``: added to by the look, read here.
        found: list[bool | None] = []
        stop = threading.Event()

        def look() -> None:
            for item in rested:
                if stop.is_set():
                    return
                try:
                    gone = _space_facade._gone(item.evidence, item.outside, allowed)
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
                looking = run_detached(look, name="nekopower-space-evidence")
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

    async def brief(self, project: str | None, *, rules_only: bool = False) -> str:
        """Return what the space adds to a task agent's instructions; empty when it holds nothing.

        The rules and the index of the space, then those of the commission's
        project, each within its own cap. The entries are not included: the
        task agent reads the ones it needs.

        ``rules_only``: the rules and neither index, for a session that is
        to go by nothing noted before (a preset with ``memory: none``).
        The rules are the user's word to every task agent, not something
        remembered: no preset is opened without them.
        """
        from config.nekopower_settings import (
            SPACE_BRIEF_GLOBAL_INDEX_MAX_TOKENS,
            SPACE_BRIEF_GLOBAL_RULES_MAX_TOKENS,
            SPACE_BRIEF_MAX_TOKENS,
            SPACE_BRIEF_PROJECT_INDEX_MAX_TOKENS,
            SPACE_BRIEF_PROJECT_RULES_MAX_TOKENS,
        )
        from config.prompts import prompts_nekopower_space as texts
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
        kept = [
            (header, text.strip(), cap, is_index)
            for header, text, cap, is_index in sections
            if text.strip() and not (rules_only and is_index)
        ]
        if not kept:
            return ""
        # (The note that goes with the indexes says where the files of the
        # space are and how to add to them: nothing a session without memory
        # is to do.)
        note = texts.SPACE_BRIEF_RULES_NOTE if rules_only else texts.SPACE_BRIEF_NOTE.format(root=root, project=name)

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
        from nekopower.runtime.permissions import is_inside

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
