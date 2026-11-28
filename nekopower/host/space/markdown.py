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

"""Front-matter parsing, markdown formatting, index lines, normalization, and digest helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any
from nekopower.host.paths import safe_segment

from .models import (
    ENTRY_TYPES,
    _DEFAULT_TYPE,
    CONFIDENCES,
    ENTRY_ACTIVE,
    ENTRY_PENDING,
    ENTRY_EXPIRED,
    _NOTES_FILE_VERSION,
    _SLUG_MAX_CHARS,
    _SLUG_UNSAFE,
    _FRONT_MATTER_KEY,
    _WHITESPACE,
    _Document,
    _PLAIN_TARGET,
    _WORD_OPENERS,
    _WORD_CLOSERS,
    _MARKS_OF_UNSPACED,
    _MARKS_INSIDE_UNSPACED,
    _ADDITION,
    _LineToPut,
)

# Late-binding read point for symbols that tests rebind on
# ``nekopower.host.space`` via ``monkeypatch.setattr`` / ``patch``. Do NOT
# from-import those names here: a from-import snapshots the value at
# import time and the facade patch would silently miss this module.
from nekopower.host import space as _space_facade


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
    return _space_facade._cut(".../" + parts[-1], max_tokens)


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
    from nekopower.host.selection import _PAIRED_CHAR_RE

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
    dated = _dated_texts_of(body, marks)
    return None if dated is None else [text for _day, text in dated]


def _dated_texts_of(body: str, marks: Iterable[str]) -> list[tuple[str, str]] | None:
    """Return the texts an entry was given as ``_texts_of`` does, each with the day of the heading it follows.

    Empty for the first text, which follows none.
    """
    expected = list(marks)
    texts: list[str] = []
    days: list[str] = [""]
    lines: list[str] = []

    def next_text() -> bool:
        return len(texts) < len(expected) and _digest("\n".join(lines)) == expected[len(texts)]

    for line in body.split("\n"):
        if _ADDITION.fullmatch(line.strip()) and next_text():
            texts.append("\n".join(lines).strip())
            days.append(line.strip()[3:])
            lines = []
        else:
            lines.append(line)
    if not next_text():
        return None
    texts.append("\n".join(lines).strip())
    return list(zip(days, texts, strict=True)) if len(texts) == len(expected) else None


def _days_of(document: _Document) -> dict[str, str]:
    """Return the day each text of an entry was written on, by the text: the first on the day the entry was made.

    For a text that tidying moves into another entry, where it gets a
    section of its own. An entry whose texts cannot be told apart is one
    text, of the day it was made.
    """
    created = document.get("created")
    dated = _dated_texts_of(document.body, _listed(document, "texts"))
    if dated is None:
        return {document.body.strip(): created} if created else {}
    return {text: day or created for day, text in dated if day or created}


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
    from config.prompts.prompts_nekopower_space import SPACE_EVIDENCE_NOT_COUNTED

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
    return bool(_space_facade._standing_marks(document.body, wanted))


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
    if len(firsts) * len(lines) > _space_facade._STANDING_STEPS_MAX or len(firsts) * len(body) > _space_facade._STANDING_CHARACTERS_MAX:
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
    standing = _space_facade._standing_marks(document.body, [mark])
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


def _said_mark(document: _Document) -> str:
    """Return a mark of what an entry says (``Layer.said``): its title, its description and its text."""
    return _text_mark("\n".join((document.get("name"), document.get("description"), document.body.strip())))


def _added_to(name: str, title: str, own: str) -> bool:
    """Whether ``name`` is ``title`` with words of ``own`` added before or after it, and nothing else.

    For a title tidying gives an entry. The model that asks for it was
    shown pending notes, which a page may have written: the title an
    entry had stays in it whole and as it is written, case and all (a
    "never" it began with is not lost, nor is "MODE=SAFE" made
    "MODE=safe"), and what is added is one run of the entry's own words,
    as they stand in its text (compared as ``_normal`` leaves texts).
    """
    old = title.strip()
    if not _normal(old) or old not in name:
        return False
    at = name.find(old)
    before, after = _normal(name[:at]), _normal(name[at + len(old) :])
    if bool(before) == bool(after):
        # Nothing added, or added on both sides.
        return False
    return (before or after) in _normal(own)


def _as_words(words: str) -> re.Pattern[str]:
    """Return a pattern that finds ``words`` where they stand as words of their own.

    Not inside a longer run of letters and digits of a script that puts
    spaces between words ("today" in "todayTotal", "вчера" in
    "позавчера"). Text that puts no spaces between words (Chinese,
    Japanese) is matched where it stands, but for where it is part of
    a longer one of ``SPACE_CONSOLIDATE_RELATIVE_TIME_WORDS``, which says
    another time (the Japanese for "yesterday" in that for "the day before
    yesterday"). A mark that combines with the letter before it (a stress
    mark over a Russian vowel) is part of that letter's word.
    """
    from config.prompts.prompts_nekopower_space import SPACE_CONSOLIDATE_RELATIVE_TIME_WORDS
    from nekopower.host.selection import _PAIRED_RANGES

    letter = rf"(?:[^\W{_PAIRED_RANGES}]|[\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f])"
    before = rf"(?<!{letter})" if _spaced_letter(words[:1]) else ""
    after = rf"(?!{letter})" if _spaced_letter(words[-1:]) else ""
    inside = ""
    for longer in SPACE_CONSOLIDATE_RELATIVE_TIME_WORDS:
        if len(longer) <= len(words):
            continue
        for found in re.finditer(re.escape(words), longer, re.IGNORECASE):
            ahead, behind = longer[: found.start()], longer[found.end() :]
            # Not where what stands before and after it makes the longer one.
            looked_behind = rf"(?<=(?i:{re.escape(ahead)}))" if ahead else ""
            inside += rf"(?!{looked_behind}(?i:{re.escape(words + behind)}))"
    return re.compile(before + inside + re.escape(words) + after)


def _link_title(line: str) -> str | None:
    """Return what a line of an index calls the entry it links, or None for a line with no link."""
    link = line.find("](")
    opening = line.rfind("[", 0, link)
    return line[opening + 1 : link] if link >= 0 and opening >= 0 else None


def _line_put_right(line: str, target: str, title: str, described: str, put: _LineToPut) -> tuple[str, bool]:
    """Return a line of an index that links ``target`` as it is to be by ``put``, for an entry of ``title`` and ``described``.

    And whether the description in it is the host's: one the user wrote,
    or a title the user gave, stays as it is.
    """
    shown = _link_title(line)
    if shown is None:
        return line, False
    hosts = any(line == _index_line(shown, target, item) for item in put.described)
    if hosts:
        line = _index_line(shown, target, described)
    called = shown
    # Put in by tidying, while the entry is still called what it was; one
    # title after another, back to the first.
    for _step in range(len(put.titled)):
        back = put.titled.get(called)
        if back is None or back[1] != title:
            break
        called = back[0]
    if called != shown:
        link = line.find("](")
        line = line[: line.rfind("[", 0, link) + 1] + called + line[link:]
    return line, hosts


def _a_relative_time(words: str) -> bool:
    """Whether ``words`` are, as a whole, ones that say a time relative to when they were written (``SPACE_CONSOLIDATE_RELATIVE_TIME_WORDS``)."""
    from config.prompts.prompts_nekopower_space import SPACE_CONSOLIDATE_RELATIVE_TIME_WORDS

    said = " ".join(words.split()).casefold()
    return any(said == item.casefold() for item in SPACE_CONSOLIDATE_RELATIVE_TIME_WORDS)
