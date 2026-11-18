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
    from config.prompts.prompts_nekoclaw_sessions import RECORD_ENTRY_CUT_MARK
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
