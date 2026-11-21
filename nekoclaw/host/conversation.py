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

"""What she and the user said to each other lately, kept in memory for task agents.

A commission comes with one sentence she dispatched and the user's last
words. What "that one" or "as I said" in them refers to was said a few
messages earlier, and a task agent that starts in a new session has none
of that. So the last few messages of the main dialogue are kept here, and
a commission that opens a new session is handed an excerpt of them, as
background (``TaskAgentManager._opening``).

Where they come from: the main dialogue tells the agent process of every
message as it is said (the ``conversation_turn_observed`` event, which
plugins are fed from as well). Taking them from there costs no request to
the main server when a commission is dispatched, so a dispatch is not held
up by it.

Kept in memory only, a few messages for each character, and only while
NekoClaw is switched on (whoever feeds this decides that). Nothing here is
written to the disk; an excerpt that is handed to a task agent becomes part
of that session's record, and is removed with it.

This module imports nothing of the host: it is fed before the host is built.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

ROLE_USER = "user"
ROLE_CHARACTER = "character"

# The events that carry something one of the two said, by who said it. An
# instruction the host wrote for a turn of hers is neither.
_ROLES = {"user_message": ROLE_USER, "assistant_message": ROLE_CHARACTER, "proactive_reply": ROLE_CHARACTER}


@dataclass(frozen=True, slots=True)
class Turn:
    """One message of the main dialogue."""

    role: str
    text: str
    # When it was said (epoch seconds).
    at: float
    event_id: str = ""
    # How many times what was kept for the character had been dropped
    # when this was kept (``RecentConversation.times_dropped``): by
    # this, and not by a clock, one that was taken from here before
    # the last time is told from one that was kept after it.
    kept_in: int = 0


class RecentConversation:
    """The last messages of the main dialogue, for each character."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._turns: dict[str, deque[Turn]] = {}
        # When what was kept was last dropped, and how many times it
        # was: for each character, and (under None) for all of them at
        # once.
        self._forgotten_at: dict[str | None, float] = {}
        self._dropped: dict[str | None, int] = {}

    def note(self, lanlan_name: str, role: str, text: str, *, at: float | None = None, event_id: str = "") -> bool:
        """Keep one message; returns whether it was kept.

        Not kept: an empty one, and one that is here already (told twice,
        by its id or, without one, by being what the same side said just
        before it, going by when each was said).
        A long one is kept cut: an excerpt takes a part of it at the most.
        """
        from config.nekoclaw_settings import (
            TASK_AGENT_EXCERPT_KEPT_CHARS,
            TASK_AGENT_EXCERPT_KEPT_MESSAGES,
            TASK_AGENT_EXCERPT_MAX_AGE_SECONDS,
        )

        text = " ".join(str(text or "").split())
        if not lanlan_name or role not in (ROLE_USER, ROLE_CHARACTER) or not text:
            return False
        turns = self._turns.get(lanlan_name)
        if turns is None or turns.maxlen != TASK_AGENT_EXCERPT_KEPT_MESSAGES:
            turns = deque(turns or (), maxlen=TASK_AGENT_EXCERPT_KEPT_MESSAGES)
            self._turns[lanlan_name] = turns
        text = text[:TASK_AGENT_EXCERPT_KEPT_CHARS]
        if event_id and any(turn.event_id == event_id for turn in turns):
            return False
        now = self._clock()
        said_at = now
        if at is not None:
            try:
                said_at = float(at)
            except (OverflowError, TypeError, ValueError):
                # (A whole number too large to be one of seconds, say.)
                said_at = now
            if not math.isfinite(said_at) or said_at > now + TASK_AGENT_EXCERPT_MAX_AGE_SECONDS:
                # A time that is no time: as if it was said now. Kept as
                # it is, nothing compares with it, and what comes after
                # it would no longer be kept in the order it was said.
                # And one that lies further ahead than a message is kept
                # for is not one of seconds (it counts something smaller,
                # say): kept as it is, the message would be the newest
                # there is, and never old, for as long as this runs.
                said_at = now
        if said_at < self.forgotten_at(lanlan_name):
            # Said before what was kept was dropped, and told of only
            # now: it goes the way of what was said then.
            return False
        if not event_id:
            # Told twice without an id: the same words by the same side as
            # the message said just before it. By when it was said, and
            # not by what was told of last: one that is told of late does
            # not repeat something that was said after it.
            # Among what is still kept for an excerpt: one that is old by
            # now is not what this one repeats.
            oldest = now - TASK_AGENT_EXCERPT_MAX_AGE_SECONDS
            before = next((turn for turn in reversed(turns) if oldest <= turn.at <= said_at), None)
            if before is not None and (before.role, before.text) == (role, text):
                return False
        if said_at < now - TASK_AGENT_EXCERPT_MAX_AGE_SECONDS:
            # Told of when it is old already: it would take the place of one that is not.
            return False
        if turns and said_at < turns[-1].at:
            # Told of late: put where it was said, so that the ones that
            # make room for a new one are the ones said first.
            late = Turn(role, text, said_at, event_id, self.times_dropped(lanlan_name))
            ordered = sorted([*turns, late], key=lambda turn: turn.at)[-TASK_AGENT_EXCERPT_KEPT_MESSAGES:]
            self._turns[lanlan_name] = deque(ordered, maxlen=TASK_AGENT_EXCERPT_KEPT_MESSAGES)
            # Not kept when it was said before everything there is room for.
            return any(turn is late for turn in ordered)
        turns.append(Turn(role, text, said_at, event_id, self.times_dropped(lanlan_name)))
        return True

    def note_event(self, event: dict[str, Any]) -> bool:
        """Keep the message a ``conversation_turn_observed`` event carries, if it carries one of the two sides."""
        role = _ROLES.get(str(event.get("turn_type") or ""))
        content = event.get("content")
        if role is None or not isinstance(content, str):
            return False
        at = event.get("ts")
        if isinstance(at, bool) or not isinstance(at, (int, float)):
            # (A number that is no time is seen to where it is kept.)
            at = None
        return self.note(
            str(event.get("lanlan_name") or ""), role, content, at=at, event_id=str(event.get("event_id") or "")
        )

    def recent(self, lanlan_name: str) -> list[Turn]:
        """Return the messages kept for a character that are not old, the oldest first.

        By when each was said, which is how they are kept (``note``):
        one that was told of late stands where it was said, so that
        the newest, which an excerpt keeps first, are the ones said
        last.
        """
        from config.nekoclaw_settings import TASK_AGENT_EXCERPT_MAX_AGE_SECONDS

        since = self._clock() - TASK_AGENT_EXCERPT_MAX_AGE_SECONDS
        return [turn for turn in self._turns.get(lanlan_name, ()) if turn.at >= since]

    def forget(self, lanlan_name: str | None = None) -> None:
        """Drop what is kept for a character, or for all of them.

        And count it: what was taken from here before is not handed
        on by whoever took it (``times_dropped``; by the count, which
        no clock that is set back or stands still gets wrong). And
        note when: what was said before this is not kept when it is
        told of only later (``note``; of that, when it was said is
        all there is to go by).
        """
        now = self._clock()
        if lanlan_name is None:
            self._turns.clear()
            self._forgotten_at = {None: now}
        else:
            self._turns.pop(lanlan_name, None)
            self._forgotten_at[lanlan_name] = now
        self._dropped[lanlan_name] = self._dropped.get(lanlan_name, 0) + 1

    def times_dropped(self, lanlan_name: str) -> int:
        """Return how many times what was kept for a character has been dropped (``Turn.kept_in``)."""
        return self._dropped.get(None, 0) + self._dropped.get(lanlan_name, 0)

    def forgotten_at(self, lanlan_name: str) -> float:
        """Return when what was kept for a character was last dropped (epoch seconds); 0.0 for never."""
        return max(self._forgotten_at.get(None, 0.0), self._forgotten_at.get(lanlan_name, 0.0))


def excerpt_lines(
    turns: list[Turn], *, lanlan_name: str, max_tokens: int, message_max_tokens: int, already_told: str = ""
) -> list[str]:
    """Return the newest of some messages as lines that fit ``max_tokens`` together, the oldest first.

    One line for each message, begun with who said it. A message longer
    than ``message_max_tokens`` is cut. Lines are taken from the newest
    back until the next would not fit: what was said last is what "that
    one" refers to. ``already_told`` is what the commission states of the
    user's words itself: the newest message of the user is left out when
    it is just that.
    """
    from utils.tokenize import count_tokens, truncate_to_tokens

    chosen = list(turns)
    told = " ".join(str(already_told or "").split())
    if told:
        for index in range(len(chosen) - 1, -1, -1):
            if chosen[index].role == ROLE_USER:
                if chosen[index].text == told:
                    del chosen[index]
                break
    lines: list[str] = []
    used = 0
    for turn in reversed(chosen):
        speaker = "User" if turn.role == ROLE_USER else lanlan_name
        line = f"{speaker}: {truncate_to_tokens(turn.text, message_max_tokens)}"
        cost = count_tokens(line) + 1
        if used + cost > max_tokens:
            break
        used += cost
        lines.append(line)
    lines.reverse()
    return lines


_recent = RecentConversation()


def recent_conversation() -> RecentConversation:
    """Return what this process keeps of the main dialogue."""
    return _recent
