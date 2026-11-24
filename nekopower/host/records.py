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

"""Reading the record of a session a stretch at a time, for another session.

A session's record (``transcript.jsonl``, written by the built-in runtime) is
everything that was said and done in it: long, and mostly beside the point
for whoever looks into it from another session. So it is never handed over
whole. ``search_record`` names the places where some words occur, one line
each; ``read_record`` hands back one stretch from a place on, within a cap,
and says where the next one begins. A place is the number of the line an
entry is on, which stays the same as the record grows: a record is only
ever added to.

Nothing here calls a model, and nothing is written. The functions block (they
read a file) and are run on a thread by ``TaskAgentManager``. A record that
is being written while it is read is read as far as its last whole line.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class RecordError(Exception):
    """A record cannot be read as asked; the text is told to the task agent that asked."""


class RecordUnreadable(RecordError):
    """A record is there and cannot be opened now (held by another program, not permitted)."""


class NoSuchPlace(RecordError):
    """A page asked for a place in a record where no entry begins."""


class EntryTooLarge(RecordError):
    """An entry of a record is too large to be shown whole."""


@dataclass(frozen=True, slots=True)
class Hit:
    """One place in a record where the words looked for occur."""

    position: int
    label: str
    excerpt: str


@dataclass(frozen=True, slots=True)
class Stretch:
    """A run of entries of a record, as text, and where it lies."""

    text: str
    first: int
    last: int
    # Where the stretch after this one begins; None at the end of the record.
    following: int | None
    # How many entries with something to show the record has.
    entries: int


def _key(mapping: dict[str, Any], snake: str) -> Any:
    """Return a field of a stored message, however its name is spelled there (``tool_name`` or ``toolName``)."""
    if snake in mapping:
        return mapping[snake]
    head, *rest = snake.split("_")
    return mapping.get(head + "".join(part.capitalize() for part in rest))


def _text_of(content: Any) -> str:
    """Return the text of what a message holds; a picture is named, not shown."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif kind == "image":
            parts.append("[a picture]")
        elif kind == "toolCall":
            arguments = json.dumps(block.get("arguments") or {}, ensure_ascii=False, separators=(",", ":"))
            parts.append(f"[calls {block.get('name')} {arguments}]")
    return "\n".join(part for part in parts if part)


def _shown(entry: dict[str, Any]) -> tuple[str, str] | None:
    """Return ``(who or what, text)`` for an entry of a record, or None for one with nothing to show."""
    kind = entry.get("type")
    if kind == "message" and isinstance(entry.get("message"), dict):
        message = entry["message"]
        role = message.get("role")
        text = _text_of(message.get("content"))
        if role == "user":
            label = "said to it"
        elif role == "assistant":
            label = "it said"
        elif role == "toolResult":
            label = f"result of {_key(message, 'tool_name') or 'a tool'}"
            if _key(message, "is_error"):
                label += " (failed)"
        elif role == "custom":
            label = f"notice ({_key(message, 'custom_type') or 'host'})"
        elif role in ("compactionSummary", "branchSummary"):
            label = "summary of what came before"
            text = text or str(message.get("summary") or "")
        else:
            return None
    elif kind == "custom_message":
        label = f"notice ({_key(entry, 'custom_type') or 'host'})"
        text = _text_of(entry.get("content"))
    elif kind == "compaction":
        label = "summary of what came before"
        text = str(entry.get("summary") or "")
    else:
        return None
    text = text.strip()
    return (label, text) if text else None


def _entries(path: Path, max_bytes: int) -> Iterator[tuple[int, str, str]]:
    """Yield ``(position, label, text)`` for every entry of a record that has something to show."""
    import os

    try:
        handle = path.open("rb")
    except OSError as exc:
        raise RecordError("this session has no record that can be read: nothing was said in it, or it was removed") from exc
    try:
        with handle:
            # As long as it is when it is opened, and no further: what is
            # added to it while it is read is not read, however much that is.
            size = os.fstat(handle.fileno()).st_size
            if size > max_bytes:
                raise RecordError("the record of this session is too large to look through; read its results instead")
            read = 0
            for position, raw in enumerate(handle, 1):
                read += len(raw)
                if read > size or not raw.endswith(b"\n"):
                    # Added since it was opened, or the line being written
                    # this moment.
                    break
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                shown = _shown(entry) if isinstance(entry, dict) else None
                if shown is not None:
                    yield position, shown[0], shown[1]
    except OSError as exc:
        raise RecordError(f"the record of this session cannot be read ({type(exc).__name__})") from exc


def _words(query: str) -> list[str]:
    return [word.casefold() for word in str(query or "").split() if word]


def _excerpt(text: str, word: str, max_tokens: int) -> str:
    """Return one line of ``text`` around where ``word`` occurs, within the cap."""
    from utils.tokenize import count_tokens, truncate_to_tokens

    folded_at = text.casefold().find(word)
    # Where that is in the text as it is written: folded, a character may
    # become more than one, and the place found is counted in those.
    at, folded = len(text), 0
    for index, char in enumerate(text):
        if folded >= folded_at:
            at = index
            break
        folded += len(char.casefold())
    # Roughly the cap in characters; cut to the cap below.
    reach = max_tokens * 4
    # A third of it for what stands before the word, and that counted in
    # tokens: where a character is a token or more (Chinese, say), as many
    # characters as English would have there take up the whole cap, and
    # the word itself would be cut off.
    start = max(0, at - reach // 3)
    while start < at and count_tokens(text[start:at]) > max_tokens // 3:
        start += (at - start + 1) // 2
    piece = " ".join(text[start : start + reach].split())
    if start > 0:
        piece = "…" + piece
    return truncate_to_tokens(piece, max_tokens)


def search_record(path: Path, query: str, *, hits: int, excerpt_tokens: int, max_bytes: int) -> tuple[list[Hit], int]:
    """Return the places of a record where every word of ``query`` occurs, the latest first, and how many there are.

    At most ``hits`` places come back; the count is of all of them, so the
    caller can say that there are more. Upper and lower case are the same.
    """
    words = _words(query)
    if not words:
        raise RecordError("give the words to look for")
    found: list[tuple[int, str, str]] = []
    for position, label, text in _entries(path, max_bytes):
        folded = text.casefold()
        if all(word in folded for word in words):
            found.append((position, label, text))
    found.reverse()
    # An excerpt is made for the places that come back, not for all there are.
    shown = [Hit(position, label, _excerpt(text, words[0], excerpt_tokens)) for position, label, text in found[:hits]]
    return shown, len(found)


def read_record(
    path: Path, at: int | None, *, segment_tokens: int, entry_tokens: int, max_bytes: int
) -> Stretch:
    """Return one stretch of a record: from position ``at`` on, or its last stretch when ``at`` is None.

    As many whole entries as fit ``segment_tokens``, and one at least. An
    entry longer than ``entry_tokens`` keeps its beginning and its end.
    """
    from config.prompts.prompts_nekopower_sessions import RECORD_ENTRY_CUT_MARK
    from utils.tokenize import count_tokens, truncate_head_tail_tokens

    def block(position: int, label: str, text: str) -> str:
        mark_tokens = count_tokens(RECORD_ENTRY_CUT_MARK)
        kept = max(entry_tokens - mark_tokens, 0)
        cut = truncate_head_tail_tokens(text, kept - kept // 2 + mark_tokens, kept // 2, separator=RECORD_ENTRY_CUT_MARK)
        return f"#{position} {label}:\n{cut}"

    entries = list(_entries(path, max_bytes))
    if not entries:
        raise RecordError("this session has no record that can be read: nothing was said in it, or it was removed")
    made: dict[int, tuple[str, int]] = {}

    def cut(index: int) -> tuple[str, int]:
        # An entry as it is shown, and its size: made for those that are
        # looked at for this stretch, not for the whole record every time.
        if index not in made:
            shown = block(*entries[index])
            made[index] = (shown, count_tokens(shown))
        return made[index]

    # What stands between two entries of a stretch counts towards its cap.
    between = count_tokens("\n\n")
    if at is None:
        # The last stretch: as many entries from the end as fit.
        start, used = len(entries), 0
        while start > 0 and (start == len(entries) or used + between + cut(start - 1)[1] <= segment_tokens):
            start -= 1
            used += cut(start)[1] + (between if start < len(entries) - 1 else 0)
        end = len(entries)
    else:
        start = next((index for index, entry in enumerate(entries) if entry[0] >= at), len(entries))
        if start >= len(entries):
            raise RecordError(f"the record ends before position {at}; its last entry is at {entries[-1][0]}")
        end, used = start, 0
        while end < len(entries) and (end == start or used + between + cut(end)[1] <= segment_tokens):
            used += cut(end)[1] + (between if end > start else 0)
            end += 1
    return Stretch(
        text="\n\n".join(cut(index)[0] for index in range(start, end)),
        first=entries[start][0],
        last=entries[end - 1][0],
        following=entries[end][0] if end < len(entries) else None,
        entries=len(entries),
    )


# ---------------------------------------------------------------------------
# For a page that shows a session's record to the user
# ---------------------------------------------------------------------------
#
# Read from the end backwards, a page at a time: what is looked at first is
# what was said last, and a long record is not read whole for it. A place
# here is where the line of an entry begins in the file, counted in bytes:
# a record is only ever added to (a line that was cut short by a crash is
# dropped, never one that is whole), so the place of a whole line stays.

_BACKWARD_CHUNK_BYTES = 256 * 1024


def _cut(text: str, max_tokens: int) -> tuple[str, bool]:
    """Return ``text`` within ``max_tokens``, and whether some of it was left out."""
    from utils.tokenize import truncate_to_tokens

    # A token is one byte at least: what is no longer than that in bytes fits.
    if len(text.encode("utf-8", "replace")) <= max_tokens:
        return text, False
    kept = truncate_to_tokens(text, max_tokens)
    return kept, kept != text


def _plain(content: Any) -> str:
    """Return the words of what a message holds: neither its pictures nor the tools it calls."""
    if isinstance(content, str):
        return content
    parts = [
        block["text"]
        for block in (content if isinstance(content, list) else [])
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    ]
    return "\n".join(part for part in parts if part)


def _page_entry(entry: dict[str, Any], max_tokens: int) -> dict[str, Any] | None:
    """Return an entry of a record as a page shows it, or None for one with nothing to show.

    ``role`` is ``user``, ``assistant``, ``tool_result``, ``notice`` or
    ``summary``; ``text`` what was said, within ``max_tokens`` (``cut``
    says some was left out); ``calls`` the tools a message of the task
    agent calls, each with its arguments as JSON text within the same cap;
    ``tool`` the tool a result is of, or the kind of a notice; ``failed``
    whether the tool failed; ``pictures`` how many pictures it holds (none
    is passed on); ``time`` when it was said, in seconds, where the record
    says.
    """
    kind = entry.get("type")
    message = entry.get("message") if kind == "message" and isinstance(entry.get("message"), dict) else None
    role, tool, failed, content, stamp = None, None, False, None, None
    if message is not None:
        said_by = message.get("role")
        content, stamp = message.get("content"), message.get("timestamp")
        if said_by in ("user", "assistant"):
            role = said_by
        elif said_by == "toolResult":
            role, tool, failed = "tool_result", _key(message, "tool_name"), bool(_key(message, "is_error"))
        elif said_by == "custom":
            role, tool = "notice", _key(message, "custom_type")
        elif said_by in ("compactionSummary", "branchSummary"):
            role = "summary"
            if not _plain(content):
                content = str(message.get("summary") or "")
    elif kind == "custom_message":
        role, tool, content = "notice", _key(entry, "custom_type"), entry.get("content")
    elif kind == "compaction":
        role, content = "summary", str(entry.get("summary") or "")
    if role is None:
        return None
    blocks = content if isinstance(content, list) else []
    calls = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "toolCall":
            arguments = json.dumps(block.get("arguments") or {}, ensure_ascii=False, separators=(",", ":"))
            shown, left_out = _cut(arguments, max_tokens)
            calls.append({"name": str(block.get("name") or ""), "arguments": shown, "cut": left_out})
    text, left_out = _cut(_plain(content).strip(), max_tokens)
    pictures = sum(1 for block in blocks if isinstance(block, dict) and block.get("type") == "image")
    if not text and not calls and not pictures:
        return None
    return {
        "role": role,
        "text": text,
        "cut": left_out,
        "calls": calls,
        "tool": str(tool) if tool else None,
        "failed": failed,
        "pictures": pictures,
        "time": _said_at(entry, stamp),
    }


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def _said_at(entry: dict[str, Any], of_message: Any) -> float | None:
    """Return when an entry was written, in seconds; None where the record does not say.

    Every entry carries the time it was written, in seconds. A message
    carries a time of its own as well, in milliseconds, which is used for
    an entry that has none.
    """
    written = _number(entry.get("timestamp"))
    if written is not None:
        return written
    of_message = _number(of_message)
    return of_message / 1000 if of_message is not None else None


def _whole_end(handle: Any, size: int) -> int:
    """Return where the last whole line of a file of ``size`` bytes ends: just past its last newline."""
    position = size
    while position > 0:
        step = min(_BACKWARD_CHUNK_BYTES, position)
        position -= step
        handle.seek(position)
        found = handle.read(step).rfind(b"\n")
        if found >= 0:
            return position + found + 1
    return 0


def _lines_before(handle: Any, end: int, budget: int) -> Iterator[tuple[int, bytes]]:
    """Yield ``(place, line)`` for the whole lines that end at ``end`` and before it, the last one first.

    ``end`` is where a line begins (or the end of the last whole line).
    Stops once ``budget`` bytes were read, though never before one line
    was given: a line longer than the budget is read whole all the same,
    or whoever reads a page at a time would be handed the same place again
    and never get past it.

    A line that reaches over many blocks (a picture kept in the record) is
    put together once, when the block it begins in is read: its blocks
    wait in a list till then.
    """
    # The end of a line whose beginning is not read yet, the block read last at the end.
    waiting: list[bytes] = []
    position, read, given = end, 0, False
    while position > 0 and (read < budget or not given):
        step = min(_BACKWARD_CHUNK_BYTES, position)
        position -= step
        handle.seek(position)
        block = handle.read(step)
        read += step
        # What stands before the first newline began before ``position``,
        # unless the file begins there: it waits for the block before this.
        cut = -1 if position == 0 else block.find(b"\n")
        if position > 0 and cut < 0:
            waiting.append(block)
            continue
        start = position + cut + 1
        whole = b"".join([block[cut + 1 :], *reversed(waiting)])
        waiting = [block[: cut + 1]] if position > 0 else []
        places = []
        for piece in whole.split(b"\n")[:-1]:
            places.append((start, piece))
            start += len(piece) + 1
        for found in reversed(places):
            given = True
            yield found


def _at_line_start(handle: Any, place: int) -> bool:
    if place == 0:
        return True
    handle.seek(place - 1)
    return handle.read(1) == b"\n"


def read_page(
    path: Path, before: int | None, limit: int, *, entry_tokens: int, scan_bytes: int
) -> dict[str, Any]:
    """Return a page of a record for showing it: the ``limit`` entries before the place ``before``, in order.

    ``{"entries": [...], "before": place | None}``. ``before`` None reads
    the last entries of the record; the ``before`` that comes back is to
    be passed for the page before this one, and is None once the beginning
    of the record is reached. Each entry is as ``_page_entry`` says, with
    ``at``, its place. A page ends early when ``scan_bytes`` were read for
    it (very long entries): what it has comes back, with where to go on.
    A place that is none of this record raises ``RecordError``.
    """
    import os

    try:
        handle = path.open("rb")
    except FileNotFoundError as exc:
        raise RecordError("this session has no record that can be read: nothing was said in it, or it was removed") from exc
    except OSError as exc:
        raise RecordUnreadable(f"the record of this session cannot be read now ({type(exc).__name__})") from exc
    try:
        with handle:
            size = os.fstat(handle.fileno()).st_size
            if before is None:
                end = _whole_end(handle, size)
            else:
                end = int(before)
                if not 0 <= end <= size or not _at_line_start(handle, end):
                    raise NoSuchPlace("no such place in this record")
            found: list[dict[str, Any]] = []
            reached = end
            for place, raw in _lines_before(handle, end, scan_bytes):
                reached = place
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                shown = _page_entry(entry, entry_tokens) if isinstance(entry, dict) else None
                if shown is not None:
                    found.append({"at": place, **shown})
                    if len(found) >= limit:
                        break
    except OSError as exc:
        raise RecordUnreadable(f"the record of this session cannot be read now ({type(exc).__name__})") from exc
    found.reverse()
    return {"entries": found, "before": reached if reached > 0 else None}


def search_page(path: Path, query: str, *, hits: int, excerpt_tokens: int, max_bytes: int) -> dict[str, Any]:
    """Return the entries of a record where every word of ``query`` occurs, for showing them; the latest first.

    ``{"hits": [...], "total": n, "whole": bool}``: at most ``hits`` of
    them, each ``{"at", "role", "tool", "excerpt", "time"}`` (``at`` is
    the place ``read_whole_entry`` reads it at, ``role`` and ``tool`` as
    ``_page_entry`` says); ``total`` how many there are; ``whole`` False
    when the record is longer than ``max_bytes`` and only its last
    ``max_bytes`` were looked through. What a message says and the
    arguments of the tools it calls are looked through. Upper and lower
    case are the same. Raises ``RecordError`` with no words to look for,
    and as ``read_page`` for a record that cannot be read.
    """
    import os

    words = _words(query)
    if not words:
        raise RecordError("give the words to look for")
    try:
        handle = path.open("rb")
    except FileNotFoundError as exc:
        raise RecordError("this session has no record that can be read: nothing was said in it, or it was removed") from exc
    except OSError as exc:
        raise RecordUnreadable(f"the record of this session cannot be read now ({type(exc).__name__})") from exc
    found: list[tuple[int, dict[str, Any], str]] = []
    try:
        with handle:
            size = os.fstat(handle.fileno()).st_size
            # Where that is in the middle of a line, the rest of that line is no JSON and is passed over.
            begin = max(0, size - max_bytes)
            handle.seek(begin)
            place = begin
            for raw in handle:
                at, place = place, place + len(raw)
                if place > size or not raw.endswith(b"\n"):
                    # Added since it was opened, or the line being written this moment.
                    break
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                # Whole: a cap this large is never reached, and nothing is counted for it.
                shown = _page_entry(entry, 1 << 62) if isinstance(entry, dict) else None
                if shown is None:
                    continue
                text = "\n".join([shown["text"], *(call["arguments"] for call in shown["calls"])])
                folded = text.casefold()
                if all(word in folded for word in words):
                    found.append((at, shown, text))
    except OSError as exc:
        raise RecordUnreadable(f"the record of this session cannot be read now ({type(exc).__name__})") from exc
    found.reverse()
    # An excerpt is made for the places that come back, not for all there are.
    listed = [
        {
            "at": at, "role": shown["role"], "tool": shown["tool"],
            "excerpt": _excerpt(text, words[0], excerpt_tokens), "time": shown["time"],
        }
        for at, shown, text in found[:hits]
    ]
    return {"hits": listed, "total": len(found), "whole": begin == 0}


def read_whole_entry(path: Path, at: int, *, entry_tokens: int, max_bytes: int) -> dict[str, Any]:
    """Return the entry at the place ``at`` for showing it, within the larger cap ``entry_tokens``.

    Raises ``RecordError`` for a place where no entry with something to
    show begins, and for a line longer than ``max_bytes``.
    """
    try:
        handle = path.open("rb")
    except FileNotFoundError as exc:
        raise RecordError("this session has no record that can be read: nothing was said in it, or it was removed") from exc
    except OSError as exc:
        raise RecordUnreadable(f"the record of this session cannot be read now ({type(exc).__name__})") from exc
    try:
        with handle:
            # Past the end no line begins either: nothing is read there.
            if at < 0 or not _at_line_start(handle, at):
                raise NoSuchPlace("no such place in this record")
            handle.seek(at)
            raw = handle.readline(max_bytes + 1)
    except OSError as exc:
        raise RecordUnreadable(f"the record of this session cannot be read now ({type(exc).__name__})") from exc
    if len(raw) > max_bytes:
        raise EntryTooLarge("this entry is too large to show")
    if not raw.endswith(b"\n"):
        # The line being written this moment.
        raise NoSuchPlace("no such place in this record")
    try:
        entry = json.loads(raw)
    except ValueError:
        entry = None
    shown = _page_entry(entry, entry_tokens) if isinstance(entry, dict) else None
    if shown is None:
        raise NoSuchPlace("no such place in this record")
    return {"at": at, **shown}
