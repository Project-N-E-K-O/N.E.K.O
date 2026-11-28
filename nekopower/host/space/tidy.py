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

"""Entry and index tidying mixin for ``TaskAgentSpace``.

Method-only mixin: every instance attribute is initialized in
``TaskAgentSpace.__init__`` (``nekopower.host.space.manager``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from nekopower.host.paths import safe_segment

from .models import (
    logger,
    VERIFIED,
    INFERRED,
    ENTRY_ACTIVE,
    ENTRY_PENDING,
    ENTRY_EXPIRED,
    _OUTDATED,
    _SUPERSEDED_BY,
    redact,
    Entry,
    _Document,
    _Known,
    _LineNotAdded,
    _NotMade,
    Layer,
    _LineToPut,
)
from .markdown import (
    _one_line,
    _plain_lines,
    _parse_document,
    _render_document,
    _date,
    _normal,
    _text_mark,
    _digest,
    _days_of,
    _status_of,
    _listed,
    _points_at,
    _with_line_first,
    _lines_as_text,
    _said_already,
    _index_marks,
    _index_line,
    _said_mark,
    _added_to,
    _as_words,
    _link_title,
    _a_relative_time,
)
from .evidence import _gone_for_good, _keep_known

# Late-binding read point for symbols that tests rebind on
# ``nekopower.host.space`` via ``monkeypatch.setattr`` / ``patch``. Do NOT
# from-import those names here: a from-import snapshots the value at
# import time and the facade patch would silently miss this module.
from nekopower.host import space as _space_facade


class TidyMixin:
    """Layer inspection, duplicate merging, index relining, and outdated-entry tidying methods."""

    # -- tidying with a model --------------------------------------------

    async def tidy_layers(self) -> list[str | None]:
        """Return the layers tidying looks over: the space as a whole (None), then every project in use."""
        return await self._read(lambda: [None, *(project.name for project in self._active_projects())])

    async def read_layer(self, project: str | None) -> Layer:
        """Return the entries of a layer as tidying reads them; see ``Layer``."""

        def work() -> Layer:
            directory = self._paths().memory_dir(project)
            try:
                files = sorted(item for item in directory.iterdir() if item.suffix == ".md" and item.is_file())
            except OSError:
                files = []
            entries: list[Entry] = []
            made: set[str] = set()
            marks: dict[str, str] = {}
            said: dict[str, str] = {}
            for path in files:
                text = self._read_text(path)
                if text is None:
                    continue
                document = _parse_document(text)
                try:
                    modified = path.stat().st_mtime
                except OSError:
                    modified = self._clock()
                entries.append(self._entry_of(document, path.stem, project, modified, with_content=True))
                marks[path.stem] = _text_mark(text)
                said[path.stem] = _said_mark(document)
                if _listed(document, "texts"):
                    made.add(path.stem)
            return Layer(project, tuple(entries), frozenset(made), marks, said)

        return await self._read(work)

    async def tidy_entries(
        self, project: str | None, operation: dict[str, Any], marks: dict[str, str]
    ) -> tuple[str, dict[str, str | None]]:
        """Make one change a model asked for when it looked over a layer, if it holds. Returns what became of it.

        ``operation`` names entries by their ids, and is one of:

        * ``{"op": "merge", "from": a, "into": b}``: the texts of ``a`` go
          into ``b``, each as the section it was, and ``a`` goes. A pending
          one goes into one in effect whichever way round they are named,
          and is seen by everyone who saw either. Two pending ones become
          one, which takes effect when as many commissions as
          ``SPACE_SEEN_TO_TAKE_EFFECT`` have seen it by then: a guess that
          another commission filed in other words is found a second time.
        * ``{"op": "outdated", "entry": a, "by": b, "why": ...}``: ``a`` is
          expired, for good: it does not come back when the files it rests
          on are there, only when it is filed again. ``b`` is an entry in
          effect, written no earlier than ``a``.
        * ``{"op": "retitle", "entry": a, "apart_from": b, "title": ...}``:
          ``a`` is called otherwise, by its title with words of its own
          added, which no other entry of the layer is called.
        * ``{"op": "date", "entry": a, "old": ..., "new": ...}``: words
          that say a relative time and stand in ``a`` once are replaced
          by a date.

        Every change is checked first, under the lock on the space's
        writes: the entries it names are there, still say all they said
        when the model was shown them (``marks``, ``Layer.marks``), are the
        host's own (the user's are never changed), and have not expired.
        Nothing is removed for good: an entry that goes into another has
        its texts there, and one that is out of date stays a file. Nothing
        is remembered as removed by the user either. Returns ``made`` and
        the new marks of the entries it changed (None for one that went),
        or why it was not made and nothing.
        """

        def work(allowed: bool) -> tuple[str, dict[str, str | None]]:
            outcome, written = make(allowed)
            # A guess a merge left unsettled, changed again by tidying as
            # the merge wrote it, is settled by what this change wrote. One
            # the user saved meanwhile stays tied to what the merge wrote.
            for entry_id, mark in written.items():
                key = (project, entry_id)
                if mark is not None and key in self._unsettled and self._unsettled[key] == marks.get(entry_id):
                    self._unsettled[key] = mark
            return outcome, written

        def make(allowed: bool) -> tuple[str, dict[str, str | None]]:
            kind = operation.get("op")
            try:
                if kind == "merge":
                    return self._tidy_merge(project, str(operation["from"]), str(operation["into"]), marks, allowed)
                if kind == "outdated":
                    return self._tidy_outdated(
                        project, str(operation["entry"]), str(operation["by"]), operation.get("why"), marks
                    )
                if kind == "retitle":
                    return self._tidy_retitle(
                        project, str(operation["entry"]), str(operation["apart_from"]), operation.get("title"), marks
                    )
                if kind == "date":
                    return self._tidy_date(project, str(operation["entry"]), operation.get("old"), operation.get("new"), marks)
            except (KeyError, ValueError) as exc:
                return f"not a change that can be made ({type(exc).__name__})", {}
            except _NotMade as exc:
                return str(exc), {}
            return "not a change that can be made", {}

        return await self._write_judged(work)

    def _tidy_read(
        self, project: str | None, entry_id: str, marks: dict[str, str]
    ) -> tuple[tuple[int, int] | None, _Document, Entry]:
        """Read an entry a change names: what the file was, what it says, and what it is. Raises ``_NotMade``.

        When it is not there, says something else than when the model
        was shown it, or is one the user wrote.
        """
        path = self._paths().memory_dir(project) / f"{safe_segment(entry_id)}.md"
        before = self._signature(path)
        text = self._read_text(path)
        if text is None:
            raise _NotMade(f"{entry_id} is not there")
        if marks.get(entry_id) != _text_mark(text):
            raise _NotMade(f"{entry_id} changed since it was looked over")
        document = _parse_document(text)
        if not _listed(document, "texts"):
            raise _NotMade(f"{entry_id} was written by the user")
        try:
            modified = path.stat().st_mtime
        except OSError:
            modified = self._clock()
        return before, document, self._entry_of(document, path.stem, project, modified, with_content=True)

    def _edit_as_shown(self, path: Path, mark: str, change: Callable[[_Document], None]) -> str:
        """Change an entry by a function of it, only while all its file says is ``mark``. Returns the mark of what it says then.

        For tidying, which changes an entry only as the model was shown it.
        Raises ``_NotMade``, with nothing written, when it says anything
        else as it is read to be written (the user saved it meanwhile, or
        another commission filed into it). What is returned is the mark of
        what was written, not of the file read again afterwards: the next
        change of the same round goes by it, and the user may have saved the
        file by then.
        """
        written: list[str] = []

        def edit(text: str) -> str:
            written.clear()
            if _text_mark(text) != mark:
                return text
            document = _parse_document(text)
            change(document)
            result = _render_document(document)
            written.append(_text_mark(result))
            return result

        self._edit_text(path, edit)
        if not written:
            raise _NotMade(f"{path.stem} changed since it was looked over")
        return written[0]

    def _tidy_merge(
        self, project: str | None, source_id: str, target_id: str, marks: dict[str, str], allowed: bool
    ) -> tuple[str, dict[str, str | None]]:
        """Put the texts of one entry into another and have the first go; see ``tidy_entries``."""
        if source_id == target_id:
            raise _NotMade("an entry is not merged into itself")
        paths = self._paths()
        source_read = self._tidy_read(project, source_id, marks)
        target_read = self._tidy_read(project, target_id, marks)
        if ENTRY_EXPIRED in (source_read[2].status, target_read[2].status):
            raise _NotMade("an entry that expired is not merged")
        if source_read[2].status == ENTRY_ACTIVE and target_read[2].status == ENTRY_PENDING:
            # A guess goes into what is in effect, whichever way round they were named.
            source_read, target_read = target_read, source_read
        elif source_read[2].status == target_read[2].status and source_read[2].created < target_read[2].created:
            # Two of a state: the later goes into the older, which keeps the
            # day it was made on (the one its first text is read by), however
            # they were named. The other way round that day would move back,
            # and "yesterday" in its text would mean another day.
            source_read, target_read = target_read, source_read
        before, said, source = source_read
        _target_before, holds, target = target_read
        if source.status == ENTRY_PENDING and target.status == ENTRY_ACTIVE:
            return "made", self._tidy_count_in(project, source, target, marks)
        parts = self._whole_texts(said)
        if not parts:
            raise _NotMade(f"what {source.id} says cannot be moved whole")
        days = _days_of(said)
        if not self._fits_into(holds, said, source.updated):
            raise _NotMade(f"{target.id} would say too much with what {source.id} says")
        # Both in effect, or both pending.
        pending = target.status == ENTRY_PENDING
        rested, untold = _space_facade._recorded(source.evidence, source.evidence_outside, allowed)
        carried = _Known(
            confidence=source.confidence,
            evidence=tuple(rested),
            untold=tuple(untold),
            outside=source.evidence_outside,
            # Who saw it, as an entry counts them: one that names nobody
            # was seen by the commissions it came from, unless it waits.
            seen_before=source.seen_by if pending else (source.seen_by or source.sources),
            sources_before=source.sources,
            noted=source.noted,
        )
        into: Entry = target
        # Each text is written only into the entry as it was shown, or as
        # the text before left it; see ``_file_entry``.
        as_marked = [marks[target.id]]
        for part in parts:
            try:
                into, _how = self._file_entry(
                    project,
                    title=target.title,
                    content=part,
                    kind=target.type,
                    into=into,
                    known=carried,
                    pending=pending,
                    # The days of the two, not today: a guess waits from when
                    # it was last filed, and an entry in effect is no newer for
                    # being merged (``outdated`` goes by which one is newer).
                    dated=(source.created, source.updated),
                    allowed=allowed,
                    added_on=days.get(part),
                    as_marked=as_marked,
                )
            except _LineNotAdded as exc:
                into = exc.entry
                logger.warning("an entry tidying merged into could not be given its line in the index")
                self._line_to_put(project, target.id).missing = True
        # Its texts are in the other entry: it goes, unless the user saved it
        # since it was read. Its line first: should the file stay (held by
        # another program), it is an entry whose texts are in the other one,
        # which a later round merges again; a line that points at nothing
        # would stay for good.
        source_path = paths.memory_dir(project) / f"{source.id}.md"
        target_path = paths.memory_dir(project) / f"{target.id}.md"

        def as_merged() -> str | None:
            # The entry it went into, while it says what the merge wrote.
            # Saved meanwhile, perhaps without what was put into it, it is
            # None: this one stays, and a later round merges the two again
            # (a text there already is not added twice).
            text = self._read_text(target_path)
            return text if text is not None and _text_mark(text) == as_marked[0] else None

        merged = as_merged()
        if merged is None:
            raise _NotMade(f"{target.id} was saved as {source.id} was merged into it; {source.id} stays")
        if self._signature(source_path) == before:
            index, line_of = paths.index(project), f"{paths.MEMORY_DIR}/{source.id}.md"
            line = None
            if source.status == ENTRY_ACTIVE:
                # As the user may have worded it, to be put back as it was.
                line = self._index_remove(index, line_of, told=True)
            if as_merged() is None:
                # Saved as the line was taken out: looked at again right
                # before what holds the texts goes, and it stays with its line.
                if line is not None:
                    self._index_add(index, line, line_of)
                raise _NotMade(f"{target.id} was saved as {source.id} was merged into it; {source.id} stays")
            if not self._discard_as_read(source_path, before) and line is not None:
                # Saved by the user as its line was taken out: it stays, as
                # what they saved, with the line it had.
                self._index_add(index, line, line_of)
        else:
            logger.warning("an entry tidying merged into another was saved meanwhile; it stays as it is")
        if pending:
            # What it is by what the merge wrote, not by a reading back
            # that may have failed and left it as it was before.
            into = self._entry_of(_parse_document(merged), target.id, project, self._clock(), with_content=False)
            # Kept until it is settled: should it not be told now whether it
            # takes effect (an entry beside it cannot be read, or a file
            # cannot be written), a later round tries again, as long as it
            # says what the merge wrote (``settle_tidied``).
            self._unsettled[(project, target.id)] = as_marked[0]
            if self._tidy_confirm(project, into, allowed, as_marked):
                del self._unsettled[(project, target.id)]
        return "made", {source.id: None, target.id: as_marked[0]}

    async def settle_tidied(self, project: str | None) -> int:
        """Finish in a layer what tidying could not: lines of its index, and merged guesses. Returns how many took effect.

        By every round of tidying, with no model. A line of the index that
        could not be written when tidying changed an entry is written now,
        where the index still says what it said then (one the user changed
        since is theirs). When tidying merged two guesses into one and could
        not tell then whether it takes effect (an entry beside it could not
        be read), it is tried again: as ``_tidy_confirm``, and only while
        the entry says just what the merge wrote. One the user saved since
        is not what was merged, and waits like any other guess.
        """

        def work(allowed: bool) -> int:
            directory = self._paths().memory_dir(project)
            for (layer, entry_id), put in list(self._relines.items()):
                if layer != project:
                    continue
                try:
                    done = self._put_line_right(project, entry_id, put)
                except OSError:
                    logger.warning("a line of the index tidying changed could not be written just now", exc_info=True)
                    continue
                if done:
                    del self._relines[(layer, entry_id)]
            took = 0
            for (layer, entry_id), mark in list(self._unsettled.items()):
                if layer != project:
                    continue
                path = directory / f"{entry_id}.md"
                text = self._read_text(path)
                if text is None:
                    if _gone_for_good(path):
                        del self._unsettled[(layer, entry_id)]
                    # Else not to be read just now: tried again by a later round.
                    continue
                # What it is and what it says, from one reading. Saved by the
                # user since the merge, it is not what was merged, and
                # ``_confirm`` leaves it waiting (``as_marked``).
                entry = self._entry_of(_parse_document(text), entry_id, project, self._clock(), with_content=False)
                as_marked = [mark]
                try:
                    settled = self._tidy_confirm(project, entry, allowed, as_marked)
                except OSError:
                    # This one's trouble is not the next one's: tried again later.
                    logger.warning("a merged guess could not be settled just now", exc_info=True)
                    continue
                if settled:
                    del self._unsettled[(layer, entry_id)]
                    took += as_marked[0] != mark
            return took

        return await self._write_judged(work)

    def _line_to_put(self, project: str | None, entry_id: str) -> _LineToPut:
        """Return what is left to write into the line of an entry, to be added to; see ``settle_tidied``."""
        return self._relines.setdefault((project, entry_id), _LineToPut())

    def _put_line_right(self, project: str | None, entry_id: str, put: _LineToPut) -> bool:
        """Write into the line of an entry what tidying could not; see ``settle_tidied``. Returns whether nothing is left.

        By what the entry is called and says now, while it is in effect:
        tidying or the user may have changed it since. What the user wrote
        into the line stays (``_line_put_right``); a missing line is added.
        Should the entry say anything else once the line is written (saved
        meanwhile), what was written is the host's, and is put right again
        by a later round. Raises OSError when it cannot be yet.
        """
        paths = self._paths()
        path = paths.memory_dir(project) / f"{entry_id}.md"
        text = self._read_text(path)
        if text is None:
            if _gone_for_good(path):
                return True
            raise OSError(f"{entry_id} cannot be read just now")
        document = _parse_document(text)
        if _status_of(document) != ENTRY_ACTIVE:
            return True
        title, described = document.get("name") or entry_id, document.get("description")
        target = f"{paths.MEMORY_DIR}/{entry_id}.md"
        marks = _index_marks(target)

        # Of each text a try made: whether it put the description into the
        # line, and whether it added it.
        tried: dict[str, tuple[bool, bool]] = {}

        def change(index_text: str) -> str:
            lines = index_text.split("\n") if index_text else []
            for number, item in enumerate(lines):
                if _points_at(item, marks):
                    # It has a line, whoever added it: one the user takes
                    # out from here on is not added again.
                    put.missing = False
                    lines[number], hosts = _space_facade._line_put_right(item, target, title, described, put)
                    result = "\n".join(lines)
                    # Only a description it put: a title taken back leaves
                    # the user's description theirs.
                    tried[result] = (hosts, False)
                    return result
            if put.missing:
                result = _lines_as_text(_with_line_first(lines, _index_line(title, target, described)))
                tried[result] = (True, True)
                return result
            return index_text

        def wrote(index_text: str) -> None:
            # Told as it is written, whatever becomes of the tries after it:
            # what the host put into the line is the host's from here on.
            put_description, added = tried[index_text]
            if put_description:
                put.described.add(described)
            if added:
                put.missing = False

        self._edit_text(paths.index(project), change, wrote=wrote)
        # Saved meanwhile, the entry says something else: what it says now
        # is put into the line by a later round.
        return self._read_text(path) == text

    def _tidy_confirm(self, project: str | None, entry: Entry, allowed: bool, as_marked: list[str]) -> bool:
        """Have a pending entry take effect where it is when enough commissions have seen it by now, or it was verified.

        Only while it says all the merge wrote into it (``as_marked``, as
        for ``_confirm``): saved by the user since, it is not what was
        merged, and waits on. Returns False when whether it takes effect
        cannot be told just now (an entry beside it cannot be read).
        """
        from config.nekopower_settings import SPACE_SEEN_TO_TAKE_EFFECT

        if entry.status != ENTRY_PENDING:
            return True
        if entry.seen < SPACE_SEEN_TO_TAKE_EFFECT and entry.confidence != VERIFIED:
            return True
        directory = self._paths().memory_dir(project)
        try:
            files = sorted(item for item in directory.iterdir() if item.suffix == ".md" and item.is_file())
        except OSError:
            return False
        for path in files:
            if path.stem == entry.id:
                continue
            text = self._read_text(path)
            if text is None:
                # It may be of the same title: that cannot be told just now.
                logger.info("a guess merged by tidying waits on: an entry beside it cannot be read")
                return False
            other = _parse_document(text)
            if _status_of(other) != ENTRY_PENDING and _normal(other.get("name") or path.stem) == _normal(entry.title):
                # One title is one entry, and a third entry, which the change
                # did not name, is not changed by it: the guess goes on
                # waiting, and takes effect when a note of its title is
                # filed (``_judged``). One that expired as well: filed
                # again, it comes back, and two entries of one title would
                # be in effect.
                logger.info("a guess merged by tidying waits on: another entry has its title")
                return True
        _entry, unlisted = self._confirm(project, entry, _Known(confidence=INFERRED), allowed, as_marked=as_marked)
        if unlisted is not None:
            logger.warning("an entry that took effect by tidying could not be given its line in the index")
            self._line_to_put(project, entry.id).missing = True
        return True

    def _tidy_count_in(
        self, project: str | None, pending: Entry, into: Entry, marks: dict[str, str]
    ) -> dict[str, str | None]:
        """Have an entry in effect count the commissions that saw a guess saying the same, and the guess expire for it.

        The guess is found a second time: what it says, by the entry. Its
        text is not put into the entry, where it would be in effect with
        nobody having checked it: a guess may be what a page the task agent
        read told it, and that same text was before the model that judged
        the two the same. The guess stays a file, out of date because the
        entry says it (``outdated``, ``superseded_by``), and comes back
        only when it is filed again. Raises ``_NotMade`` when either
        changed since the model was shown them. Returns the marks of what
        both say then.
        """
        from config.nekopower_settings import SPACE_CONSOLIDATE_REASON_MAX_TOKENS

        directory = self._paths().memory_dir(project)
        today = _date(self._clock())
        seen = _Known(confidence="", seen_before=pending.seen_by)

        def count(document: _Document) -> None:
            # Nobody new to count is no failure: the guess was its own commission's.
            _keep_known(document, seen)

        def expire(document: _Document) -> None:
            document.set("status", ENTRY_EXPIRED)
            document.set("expired", today)
            document.drop("notice")
            document.set(_OUTDATED, _space_facade._cut(_one_line(f"the same as {into.title}"), SPACE_CONSOLIDATE_REASON_MAX_TOKENS))
            document.set(_SUPERSEDED_BY, into.id)

        # The entry first: a commission is counted once however often it is
        # counted, so one that is counted and whose guess could not be
        # written then is done again by a later round; a guess that expired
        # first, and whose sighting could not be counted then, would be in
        # no round again.
        entry_mark = self._edit_as_shown(directory / f"{into.id}.md", marks[into.id], count)
        guess_mark = self._edit_as_shown(directory / f"{pending.id}.md", marks[pending.id], expire)
        return {pending.id: guess_mark, into.id: entry_mark}

    @staticmethod
    def _fits_into(holds: _Document, said: _Document, updated: str) -> bool:
        """Whether an entry that says ``holds`` stays within ``SPACE_CONSOLIDATE_MERGED_MAX_TOKENS`` once the texts of ``said`` go into it.

        Each text that is new to it with the section it gets, as
        ``_file_entry`` writes it. False when the texts of ``said``
        cannot be moved whole.
        """
        from config.nekopower_settings import SPACE_CONSOLIDATE_MERGED_MAX_TOKENS
        from utils.tokenize import count_tokens

        parts = _space_facade.TaskAgentSpace._whole_texts(said)
        if not parts:
            return False
        days = _days_of(said)
        added = [part for part in parts if not _said_already(holds, part)]
        size = count_tokens(holds.body.strip()) + sum(
            count_tokens(f"\n\n## {days.get(part) or updated}\n\n{part}") for part in added
        )
        return size <= SPACE_CONSOLIDATE_MERGED_MAX_TOKENS

    def _tidy_outdated(
        self, project: str | None, entry_id: str, by_id: str, why: Any, marks: dict[str, str]
    ) -> tuple[str, dict[str, str | None]]:
        """Have an entry expire because a newer one says otherwise; see ``tidy_entries``."""
        from config.nekopower_settings import SPACE_CONSOLIDATE_REASON_MAX_TOKENS

        if entry_id == by_id:
            raise _NotMade("an entry does not make itself out of date")
        paths = self._paths()
        _before, _said, entry = self._tidy_read(project, entry_id, marks)
        _by_before, _by_said, by = self._tidy_read(project, by_id, marks)
        if entry.status == ENTRY_EXPIRED:
            raise _NotMade(f"{entry.id} has expired already")
        if by.status != ENTRY_ACTIVE:
            raise _NotMade(f"{by.id}, which says otherwise, is not in effect")
        if by.updated < entry.updated:
            raise _NotMade(f"{by.id}, which says otherwise, is older than {entry.id}")
        reason = _space_facade._cut(_one_line(redact(str(why or ""))), SPACE_CONSOLIDATE_REASON_MAX_TOKENS) or by.title
        today = _date(self._clock())

        by_path = paths.memory_dir(project) / f"{by.id}.md"

        def change(document: _Document) -> None:
            # What says otherwise, looked at again as this is written: saved
            # since the model was shown it, it may say something else now.
            said = self._read_text(by_path)
            if said is None or _text_mark(said) != marks[by_id]:
                raise _NotMade(f"{by.id}, which says otherwise, changed since it was looked over")
            document.set("status", ENTRY_EXPIRED)
            document.set("expired", today)
            document.drop("notice")
            document.set(_OUTDATED, reason)
            document.set(_SUPERSEDED_BY, by.id)

        written = self._edit_as_shown(paths.memory_dir(project) / f"{entry.id}.md", marks[entry_id], change)
        # The file first, as for one whose files are gone: one that says it
        # expired and still has its line loses it at the next look.
        if entry.status == ENTRY_ACTIVE:
            self._index_remove(paths.index(project), f"{paths.MEMORY_DIR}/{entry.id}.md")
        return "made", {entry.id: written}

    def _tidy_retitle(
        self,
        project: str | None,
        entry_id: str,
        apart_id: str,
        title: Any,
        marks: dict[str, str],
    ) -> tuple[str, dict[str, str | None]]:
        """Give an entry a title that tells it from another; see ``tidy_entries``.

        Its title with a run of its own words added (``_added_to``); what
        else its line in the index says stays.
        """
        from config.nekopower_settings import SPACE_ENTRY_TITLE_MAX_TOKENS

        if entry_id == apart_id:
            raise _NotMade("an entry is not told apart from itself")
        paths = self._paths()
        directory = paths.memory_dir(project)
        _before, _said, entry = self._tidy_read(project, entry_id, marks)
        if entry.status == ENTRY_EXPIRED:
            raise _NotMade(f"{entry.id} has expired")
        # The one it is told apart from, as the model was shown it too.
        if self._tidy_read(project, apart_id, marks)[2].status == ENTRY_EXPIRED:
            # Out of date, by an earlier change of this round, say: there is
            # nothing in effect it could be taken for.
            raise _NotMade(f"{apart_id}, which it is to be told apart from, has expired")
        name = _space_facade._cut(_one_line(redact(str(title or ""))), SPACE_ENTRY_TITLE_MAX_TOKENS)
        # The model was shown pending notes, which anyone's page may have
        # written, and a title goes into an index that task agents are
        # handed: it adds to the title words the entry has, and changes
        # nothing it said.
        if not _added_to(name, entry.title, f"{entry.description} {entry.content}"):
            raise _NotMade(f"the new title is not the title of {entry.id} with words of its own added")
        # One title is one entry; and a note of a title the user removed is
        # not kept, which this one would be called by now.
        if any(_normal(other.title) == _normal(name) for other in self._entries(directory, project) if other.id != entry.id):
            raise _NotMade("another entry is called that")
        if _normal(name) in self._removed_of(paths.notes_file(project))[0]:
            raise _NotMade("the user removed an entry of that title")

        def change(document: _Document) -> None:
            document.set("name", name)

        index, target = paths.index(project), f"{paths.MEMORY_DIR}/{entry.id}.md"
        # The line first: should it not be written, nothing is. Should the
        # entry then not be (saved meanwhile, held by another program), the
        # line is put back as it was, so the two never part. One the user
        # took out is not put in.
        swapped = self._index_retitle(index, target, name, entry.description) if entry.status == ENTRY_ACTIVE else None
        try:
            written = self._edit_as_shown(directory / f"{entry.id}.md", marks[entry_id], change)
        except (_NotMade, OSError):
            if swapped is not None:
                was, now = swapped
                try:
                    self._index_reline(index, now, was)
                except OSError:
                    logger.warning("the line of an entry that kept its title could not be put back", exc_info=True)
                    self._line_to_put(project, entry.id).titled[_link_title(now) or name] = (
                        _link_title(was) or entry.title,
                        entry.title,
                    )
            raise
        return "made", {entry.id: written}

    def _tidy_date(
        self, project: str | None, entry_id: str, old: Any, new: Any, marks: dict[str, str]
    ) -> tuple[str, dict[str, str | None]]:
        """Replace words that say a time relative to when they were written by a date; see ``tidy_entries``.

        The words are ones of ``SPACE_CONSOLIDATE_RELATIVE_TIME_WORDS`` as a
        whole, and the date is nothing but a date: the model was shown
        pending notes, which anyone's page may have written, and a change
        of a date changes when, and nothing else.
        """
        from config.nekopower_settings import SPACE_ENTRY_DESCRIPTION_MAX_TOKENS

        said, put = _plain_lines(str(old or "")).strip(), str(new or "").strip()
        if not _a_relative_time(said):
            raise _NotMade("the words to replace are none that say a relative time")
        try:
            # The whole of it: a day of the calendar, and nothing else.
            datetime.strptime(put, "%Y-%m-%d")
        except ValueError:
            raise _NotMade(f"{put} is not a day of the calendar alone") from None
        _before, document, entry = self._tidy_read(project, entry_id, marks)
        if entry.status == ENTRY_EXPIRED:
            raise _NotMade(f"{entry.id} has expired")
        # As words of their own: "today" in "todayTotal" is part of a name.
        alone = _as_words(said)
        if len(alone.findall(document.body)) != 1:
            raise _NotMade(f"the words do not stand in {entry.id} once")
        body = alone.sub(lambda _found: put, document.body, count=1)
        kept = _listed(document, "texts")
        texts = _space_facade._texts_of(document.body, kept)
        marked: list[str] | None = None
        # The text the words stand in, before and after.
        made_from: tuple[str, str] | None = None
        if texts is not None:
            at = [number for number, text in enumerate(texts) if alone.search(text)]
            if len(at) != 1:
                raise _NotMade(f"the words do not stand in one text of {entry.id}")
            changed = [*texts]
            changed[at[0]] = alone.sub(lambda _found: put, texts[at[0]], count=1)
            marked = [*kept]
            marked[at[0]] = _digest(changed[at[0]])
            if _space_facade._texts_of(body, marked) != changed:
                marked = None
            else:
                made_from = texts[at[0]], changed[at[0]]
        if marked is None:
            # Its texts cannot be told apart (rewritten in an editor): as
            # when the user gives it a new text (``edit_entry``).
            standing = _space_facade._standing_marks(body, kept)
            marked = [item for item in kept if standing is None or item in standing]
            if _digest(body) not in marked:
                marked.append(_digest(body))

        # A description the host made from that very text says the same
        # "yesterday": it is made again from the text as it is now, and so
        # is the line of the index, where it still is what the host wrote.
        # One the user wrote or reworded is theirs, and may mean another day.
        described = document.get("description")
        redescribed = described
        if made_from is not None and described == _space_facade._cut(_one_line(made_from[0]), SPACE_ENTRY_DESCRIPTION_MAX_TOKENS):
            redescribed = _space_facade._cut(_one_line(made_from[1]), SPACE_ENTRY_DESCRIPTION_MAX_TOKENS)

        def change(replaced: _Document) -> None:
            replaced.body = body
            replaced.set("texts", ", ".join(marked))
            if redescribed != described:
                replaced.set("description", redescribed)

        paths = self._paths()
        written = self._edit_as_shown(paths.memory_dir(project) / f"{entry.id}.md", marks[entry_id], change)
        if redescribed != described and entry.status == ENTRY_ACTIVE:
            target = f"{paths.MEMORY_DIR}/{entry.id}.md"
            was, now = _index_line(entry.title, target, described), _index_line(entry.title, target, redescribed)
            try:
                self._index_reline(paths.index(project), was, now)
            except OSError:
                logger.warning("the line of an entry whose date was put could not be changed", exc_info=True)
                self._line_to_put(project, entry.id).described.add(described)
        return "made", {entry.id: written}
