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

"""Making the record of a new session from that of another.

A session is what was said in it: a transcript of entries, each naming the
one before it, and the system prompt and tool definitions it was opened
with (``snapshot.py``). A fork copies both into the directory of a new
session. Opened, the new session goes on from where the other stood, and
begins its requests exactly as the other does: a provider that kept the
head of the other's requests has the same head for the new one's.

What is copied:

* The entries of the other's conversation from its first to one entry
  (``upto``; its last, when none is named), in the order they follow one
  another. Entries that were written and are no longer part of the
  conversation (an entry whose write failed left others behind it attached
  elsewhere) are left out.
* Not past the last place where the conversation is whole. The other
  session may be at work: a reply that calls tools is in its record before
  the results are, and a request that is sent with a call and no result is
  refused. So the copy ends in front of a reply some of whose calls have no
  result yet, and in front of replies at its end that failed or were
  stopped.
* The snapshot, as it is, with the number of summaries the copy holds.

What is not copied: the tool output the other kept in files (``outputs/``).
What was said names those files where they are; the new session may read
them there without asking (the copy says where that is), and writes its own
into its own directory.

A copy ends with an entry that says it is one. A session that is opened
from a copy starts with no to-do list and no goal: both were the other's.
The text that opened the other's commission stays where it is, and is kept
word for word through a summary until the new session is handed one of its
own.

The other session's record is only read, under the lock its own writes
take, and nothing of it changes.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from nekoclaw.runtime.loop.messages import AssistantMessage, ToolResultMessage
from nekoclaw.runtime.loop.session import CustomEntry, JsonlSessionStorage, SessionEntry
from nekoclaw.runtime.loop.session.jsonl import entries_from_json_lines
from nekoclaw.runtime.loop.session.tree import path_to_entry
from nekoclaw.runtime.snapshot import read_snapshot, write_snapshot

logger = logging.getLogger(__name__)

# As ``session.TRANSCRIPT_FILE`` and ``session.OUTPUTS_DIRECTORY`` (that
# module imports this one).
TRANSCRIPT_FILE = "transcript.jsonl"
OUTPUTS_DIRECTORY = "outputs"
# The entry a copy ends with: where it was made from, and up to which entry.
FORK_NAMESPACE = "nekoclaw.fork"
REASON_FORKED = "forked"

_FAILED_REPLIES = ("error", "aborted")


class ForkError(Exception):
    """The record of a session could not be copied; the message says why."""


@dataclass(frozen=True, slots=True)
class ForkResult:
    # The entry of the other session's record the copy ends with.
    upto: str
    # How many of its entries were copied.
    entries: int


def _drive(coroutine: object) -> object:
    """Run a storage coroutine that never suspends; see ``session._drive``."""
    try:
        coroutine.send(None)  # type: ignore[attr-defined]
    except StopIteration as stop:
        return stop.value
    coroutine.close()  # type: ignore[attr-defined]
    raise RuntimeError("the storage coroutine suspended unexpectedly")


class _OthersTranscript(JsonlSessionStorage):
    """The transcript of a session that may be at work, for reading only."""

    def read_whole(self) -> list[SessionEntry]:
        """Return its entries as they stand; the file is not changed.

        Every entry is one line that ends in a newline. A last line
        without one is an entry that was not written whole: left out
        here, unless it is complete JSON all the same. (The session that
        owns the file removes such a fragment itself when it next writes;
        nobody else does.)
        """
        if not self.path.is_file():
            # Before the lock is taken, which would make the directory.
            return []
        with self._locked(exclusive=False):
            try:
                data = self.path.read_bytes()
            except FileNotFoundError:
                return []
        lines = data.split(b"\n")
        if lines and lines[-1]:
            try:
                whole = isinstance(json.loads(lines[-1].decode("utf-8")), dict)
            except ValueError:
                whole = False
            if not whole:
                lines.pop()
        return entries_from_json_lines([line.decode("utf-8") for line in lines])


def _reply(entry: SessionEntry) -> AssistantMessage | None:
    message = entry.message if entry.type == "message" else None
    return message if isinstance(message, AssistantMessage) else None


def _answered(entries: list[SessionEntry]) -> set[str]:
    """Return the tool calls that have a result among some entries."""
    return {
        entry.message.tool_call_id
        for entry in entries
        if entry.type == "message" and isinstance(entry.message, ToolResultMessage)
    }


def whole_prefix(path: list[SessionEntry]) -> int:
    """Return how many entries of a conversation, from its first, can be gone on from.

    Fewer than all of them when it ends in the middle of something: a
    reply some of whose tool calls have no result after it, or replies at
    its very end that failed or were stopped (a session that is opened
    with one of those takes its last request for a failed one).
    """
    kept = len(path)
    while kept:
        # The last reply that calls tools, and the results that follow it.
        calling = next(
            (index for index in range(kept - 1, -1, -1) if (reply := _reply(path[index])) and reply.tool_calls),
            None,
        )
        if calling is not None:
            reply = _reply(path[calling])
            assert reply is not None
            if not {call.id for call in reply.tool_calls} <= _answered(path[calling + 1 : kept]):
                kept = calling
                continue
        last = next((index for index in range(kept - 1, -1, -1) if path[index].type == "message"), None)
        if last is not None:
            reply = _reply(path[last])
            if reply is not None and reply.stop_reason in _FAILED_REPLIES:
                kept = last
                continue
        break
    return kept


def fork_record(source_dir: Path, target_dir: Path, *, upto: str | None = None) -> ForkResult:
    """Copy what was said in the session kept in ``source_dir`` into ``target_dir``. Blocking.

    See the module docstring for what is copied. Raises ``ForkError`` when
    there is nothing to copy, when ``upto`` is no entry of the
    conversation, or when ``target_dir`` holds a record already.
    """
    source_path = source_dir / TRANSCRIPT_FILE
    target_path = target_dir / TRANSCRIPT_FILE
    try:
        if target_path.stat().st_size > 0:
            raise ForkError("the session to be made has a record already")
    except FileNotFoundError:
        pass
    try:
        entries = _OthersTranscript(source_path).read_whole()
    except ValueError as exc:
        raise ForkError(f"the record of the session cannot be read: {exc}") from exc
    leaf = next((entry.id for entry in reversed(entries) if entry.type != "leaf"), None)
    if leaf is None:
        raise ForkError("nothing was said in the session yet")
    try:
        path = path_to_entry(entries, leaf)
    except ValueError as exc:
        raise ForkError(f"the record of the session cannot be read: {exc}") from exc
    if upto is not None:
        end = next((index for index, entry in enumerate(path) if entry.id == upto), None)
        if end is None:
            raise ForkError(f'the session has no entry "{upto}" in its conversation')
        path = path[: end + 1]
    path = path[: whole_prefix(path)]
    if not any(entry.type in ("message", "compaction") for entry in path):
        raise ForkError("nothing was said in the session up to there")
    last = path[-1].id
    marker = CustomEntry(
        namespace=FORK_NAMESPACE,
        parent_id=last,
        data={"source_dir": str(source_dir), "outputs": str(source_dir / OUTPUTS_DIRECTORY), "upto": last},
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    # All of it or none: a copy that stopped half way would be opened as a
    # conversation that ends in the middle of something.
    _drive(JsonlSessionStorage(target_path).append_batch([*path, marker]))
    snapshot = read_snapshot(source_dir)
    if snapshot is not None:
        # As the other session begins its requests, so does this one. How
        # often the copy was summed up is counted anew: one that ends
        # before the other's last summary holds fewer.
        compactions = sum(1 for entry in path if entry.type == "compaction")
        try:
            write_snapshot(
                target_dir,
                dataclasses.replace(snapshot, reason=REASON_FORKED, made_at=time.time(), compactions=compactions),
            )
        except OSError:
            # Opened without one, it puts its own together.
            logger.warning("the system snapshot of a copied session could not be written", exc_info=True)
    return ForkResult(upto=last, entries=len(path))
