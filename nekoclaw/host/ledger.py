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

"""The commission ledger: one SQLite file, written only by the host.

``<data root>/nekoclaw/ledger.db`` holds seven tables:

* ``commissions`` - one row per commission (design section 5).
* ``sessions``    - one row per task agent session: a conversation that can
  be gone on with. A commission is given to a session (``session_id``), and
  a session takes several, one after the other: the session of a project
  takes every commission filed under it. See ``Session``. What the model
  requests of a session used is added up with the session and with the
  commission worked on in it (``Ledger.add_usage``).
* ``events``      - what happened to a commission, in order; the commission
  board replays it.
* ``outbox``      - reports for the main server. A report is written here
  first and sent afterwards, so a failed send can be retried.
* ``preferences`` - task preferences (section 5.1); see ``preferences.py``.
* ``messages``    - what one session wrote to another, and what the host
  tells a session of another's turn. Written here first and handed over
  afterwards, as a report is. See ``Message``.
* ``wake_requests`` - who is to be woken, why and when: a message that is
  to be handed over, a time a task agent set for itself. See ``WakeRequest``
  and ``wakes.py``, which carries them out.

A commission's status changes in exactly one place, ``Ledger.transition``,
which allows only the moves in ``ALLOWED_TRANSITIONS``. A stopped commission
has no way out of ``cancelled``: a result that arrives late is refused there
and nowhere else needs to check.

The file is opened on first use, never at startup. All SQLite work runs on
one worker thread, so the event loop never waits for the disk and there is a
single writer.

``subscribe`` registers a callable that is told, on the event loop and after
the change is stored, whenever a commission is filed or its status changes.
The task HUD is fed from it.
"""

from __future__ import annotations

import asyncio
import json
import os
import logging
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

STATUSES: tuple[str, ...] = (
    "queued",
    "working",
    "waiting_user",
    "done",
    "blocked",
    "failed",
    "cancelled",
)
# Statuses in which a commission still counts as under way.
ACTIVE_STATUSES: frozenset[str] = frozenset({"queued", "working", "waiting_user", "blocked"})
# Statuses in which someone is working on it right now and holds a slot.
RUNNING_STATUSES: frozenset[str] = frozenset({"working", "waiting_user"})
ENDED_STATUSES: frozenset[str] = frozenset({"done", "failed", "cancelled", "blocked"})
_ENDED_MARKS = ", ".join("?" for _ in ENDED_STATUSES)

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"working", "failed", "cancelled"}),
    # Queued again when its idle session was closed while its own task agents
    # worked, and one of their results is to take it up again.
    "working": frozenset({"waiting_user", "queued", "done", "blocked", "failed", "cancelled"}),
    # Queued when its session was closed while it waited and the answer
    # finds no free place.
    "waiting_user": frozenset({"working", "queued", "failed", "cancelled"}),
    # Blocked and failed commissions can be taken up again: the task agent's
    # transcript is on disk. That of a failed one is removed 30 days after
    # the commission last changed (``SpaceKeeper``); taken up after that, it
    # starts over in its working directory. That of a blocked one stays.
    # Queued again when no task agent place is free to take them up at once.
    "blocked": frozenset({"working", "queued", "failed", "cancelled"}),
    "failed": frozenset({"working", "queued"}),
    "done": frozenset(),
    # Stopped stays stopped.
    "cancelled": frozenset(),
}

SESSION_STATUSES: tuple[str, ...] = ("idle", "running", "waiting", "compacting", "closed", "cleared")
# Open in a runtime, or kept for an answer: only a host process that runs
# can have a session so. What an earlier one left so is closed on opening.
LIVE_SESSION_STATUSES: frozenset[str] = frozenset({"idle", "running", "waiting", "compacting"})
# "project": the one session of a project, for one character. "single": of
# one commission that belongs to no project, and of what follows it up.
# "child": of a task agent that another task agent started. "fork": made
# from another session by copying what was said in it (``forked_from``).
SESSION_KINDS: tuple[str, ...] = ("project", "single", "child", "fork")

# "session": filed for a session that another session wrote to while no
# commission was under way in it; what was written is what it is about.
# "ask": a copy of a session answers a question another session asked it
# (``ask_session``); a commission under the one that asked.
SOURCES: tuple[str, ...] = ("dispatch", "promised", "task_agent", "session", "ask")
ROUTES: tuple[str, ...] = ("plugin", "task_agent")

# "note": what a task agent wrote to another session. "done": the host
# telling the sender that the turn its note began is over. (That no such
# word came in time is told from the wake request that says so, with no
# message of its own.)
# "ask": a question one session asked another (``ask_session``). Kept only to
# count what a session may ask: it is never handed over, and is written as
# delivered (a copy of the other session answers it).
MESSAGE_KINDS: tuple[str, ...] = ("note", "done", "ask")
MESSAGE_STATUSES: tuple[str, ...] = ("queued", "delivered", "void")
# "message": a row of ``messages`` is to be handed over. "timer": a task
# agent asked to be woken then. "notice_expiry": the sender of a note that
# asked to be told when it was dealt with is told that it waited in vain.
# "external": something outside the host asks (kept for later; nothing
# writes it).
WAKE_REASONS: tuple[str, ...] = ("message", "timer", "notice_expiry", "external")
WAKE_STATUSES: tuple[str, ...] = ("pending", "fired", "void")
# Why what a commission asked to be woken for is dropped with it.
COMMISSION_OVER_REASON = "its commission is over"

# Why a commission left unfinished by an earlier host process failed.
HOST_ENDED_REASON = "the host process ended before this finished"

# (5: the notes sessions write to one another and what a session asked to be
# woken for have tables of their own, and a commission may rest.)
_SCHEMA_VERSION = 5
# The first version in which a commission is worked on in a session that is a
# row of its own; a ledger of an earlier one has its commissions given theirs.
_SESSIONS_SINCE_VERSION = 3
_BUSY_TIMEOUT_SECONDS = 5.0
# In place of the number of a wake request, where the ledger was not opened
# yet when a stop of everything asked up to which one it is to drop
# (``Ledger.notes_mark``): all that is in the file when it is opened. This
# process had written nothing by then.
AS_OPENED = -1


def _covers(stop: int, other: int) -> bool:
    """Return whether a stop up to ``stop`` drops all that one up to ``other`` drops.

    Both as they are noted next to the ledger (``Ledger.note_stop_owed``).
    ``AS_OPENED`` there is a stop that came while the file could not be
    read: it stands for all that is in the file when it is next opened,
    which is no less than any number noted before.
    """
    if stop == AS_OPENED:
        return True
    return other != AS_OPENED and stop >= other

_SCHEMA = """
CREATE TABLE IF NOT EXISTS commissions (
    id TEXT PRIMARY KEY,
    lanlan_name TEXT NOT NULL,
    title TEXT NOT NULL,
    dispatch_text TEXT NOT NULL,
    user_text TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    route TEXT,
    project TEXT,
    workdir TEXT,
    status TEXT NOT NULL,
    status_reason TEXT NOT NULL DEFAULT '',
    runtime TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    started_at REAL,
    ended_at REAL,
    result_summary TEXT,
    pending_question TEXT,
    delivery TEXT,
    parent_id TEXT,
    conversation_id TEXT,
    lang TEXT,
    plugin_id TEXT,
    entry_id TEXT,
    name TEXT,
    deferred_until REAL,
    host_instance TEXT,
    session_dir TEXT,
    session_id TEXT,
    continues TEXT,
    resting_since REAL,
    chain_depth INTEGER,
    requests INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    tokens_cache_read INTEGER NOT NULL DEFAULT 0,
    tokens_cache_write INTEGER NOT NULL DEFAULT 0,
    usage_allowed INTEGER
);
CREATE INDEX IF NOT EXISTS commissions_by_owner ON commissions (lanlan_name, status);
CREATE INDEX IF NOT EXISTS commissions_by_parent ON commissions (parent_id);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lanlan_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    project TEXT,
    workdir TEXT,
    session_dir TEXT,
    runtime TEXT,
    parent_id TEXT,
    forked_from TEXT,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_active_at REAL NOT NULL,
    compacted_at REAL,
    forked_at TEXT,
    requests INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    tokens_cache_read INTEGER NOT NULL DEFAULT 0,
    tokens_cache_write INTEGER NOT NULL DEFAULT 0,
    context_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS sessions_by_name ON sessions (lanlan_name, name);
CREATE INDEX IF NOT EXISTS sessions_by_project ON sessions (lanlan_name, project, kind);
CREATE INDEX IF NOT EXISTS sessions_by_activity ON sessions (status, last_active_at);
CREATE TABLE IF NOT EXISTS events (
    commission_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (commission_id, seq)
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commission_id TEXT,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL,
    sent_at REAL,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox (sent_at, next_attempt_at);
CREATE TABLE IF NOT EXISTS preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    origin TEXT NOT NULL,
    observations INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 0,
    lanlan_name TEXT,
    source_commission TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_session TEXT NOT NULL,
    from_commission TEXT,
    to_session TEXT NOT NULL,
    kind TEXT NOT NULL,
    body TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    depth INTEGER NOT NULL DEFAULT 1,
    notify_when_done INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    status_reason TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    delivered_at REAL,
    delivered_to TEXT,
    notified_at REAL
);
CREATE INDEX IF NOT EXISTS messages_by_sender ON messages (from_session, created_at);
CREATE INDEX IF NOT EXISTS messages_by_target ON messages (to_session, status);
CREATE INDEX IF NOT EXISTS messages_by_delivery ON messages (delivered_to, notify_when_done);
CREATE TABLE IF NOT EXISTS wake_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    commission_id TEXT,
    reason TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    message_id INTEGER,
    due_at REAL NOT NULL,
    status TEXT NOT NULL,
    status_reason TEXT NOT NULL DEFAULT '',
    depth INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    fired_at REAL,
    fired_into TEXT,
    taken_at REAL,
    fired_order INTEGER
);
CREATE INDEX IF NOT EXISTS wakes_pending ON wake_requests (status, due_at);
CREATE INDEX IF NOT EXISTS wakes_by_session ON wake_requests (session_id, status);
CREATE INDEX IF NOT EXISTS wakes_by_commission ON wake_requests (commission_id, status);
"""


@dataclass(frozen=True, slots=True)
class Commission:
    """One row of ``commissions``."""

    id: str
    lanlan_name: str
    title: str
    # The sentence she dispatched, and the user's own words.
    dispatch_text: str
    user_text: str
    # "dispatch": she called the tool. "promised": filed for her after she
    # agreed out loud. "task_agent": started by another task agent.
    source: str
    # "plugin" or "task_agent"; None until the second round has decided.
    route: str | None
    project: str | None
    workdir: str | None
    status: str
    status_reason: str
    runtime: str | None
    created_at: float
    updated_at: float
    started_at: float | None
    ended_at: float | None
    result_summary: str | None
    # JSON text of what the user is being asked, while ``waiting_user``.
    pending_question: str | None
    # The delivery mode set for this commission; None leaves it to the report.
    delivery: str | None
    parent_id: str | None
    conversation_id: str | None
    lang: str | None
    # The entry a direct call goes to.
    plugin_id: str | None
    entry_id: str | None
    # How a parent task agent addresses this one.
    name: str | None
    # Until when a deferred plugin result may still arrive.
    deferred_until: float | None
    host_instance: str | None
    # Where its task agent's session (the conversation so far) is kept, as
    # ``workdir`` says where it works: written when it is first taken up.
    # The data root may be moved later; the ledger goes along and the
    # sessions stay, so the place is on record and not worked out anew.
    session_dir: str | None = None
    # The session a task agent works on it in (``sessions.id``); None until it
    # is handed to a task agent. Several commissions can name one session.
    session_id: str | None = None
    # The commission this one follows up: one that was done or stopped, and
    # is gone on with as a commission of its own in the same session.
    continues: str | None = None
    # Since when it rests: ``working``, with nobody at work on it, until
    # something it asked to be woken for comes (``Ledger.rest``). No runtime
    # has its session open meanwhile, and it does not hold its session. None
    # for one that does not rest; any change of its status ends the rest.
    resting_since: float | None = None
    # How far it is from what the user asked for, counted in sessions that
    # wrote to one another: None (0) for one she dispatched, and for one
    # filed for a note, the depth that note had. A note it writes is one
    # deeper, and one that would be too deep is refused.
    chain_depth: int | None = None
    # What the model requests made for it used so far, those of the task
    # agents it started included (``Ledger.add_usage``): how many requests,
    # and their tokens as the provider counted them. ``tokens_in`` is the
    # input that was not read from a cache.
    requests: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache_read: int = 0
    tokens_cache_write: int = 0
    # Up to how many tokens it may use before the user is asked whether it
    # is to go on; None for the setting (``TASK_AGENT_USAGE_LIMIT_TOKENS``).
    # Raised when the user allows more.
    usage_allowed: int | None = None

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def resting(self) -> bool:
        return self.status == "working" and self.resting_since is not None

    @property
    def tokens(self) -> int:
        """Return the tokens it used: what every request sent and got back, read from a cache or not."""
        return self.tokens_in + self.tokens_out + self.tokens_cache_read + self.tokens_cache_write

    def question(self) -> dict[str, Any] | None:
        """Return the pending question as a dict, or None."""
        if not self.pending_question:
            return None
        try:
            value = json.loads(self.pending_question)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None


_COMMISSION_COLUMNS: tuple[str, ...] = tuple(item.name for item in fields(Commission))
# Columns of ``commissions`` that the first schema did not have, with their
# types: added to a ledger that was made before them when it is opened. Its
# rows read as NULL there.
_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("session_dir", "TEXT"),
    ("session_id", "TEXT"),
    ("continues", "TEXT"),
    ("resting_since", "REAL"),
    ("chain_depth", "INTEGER"),
    ("requests", "INTEGER NOT NULL DEFAULT 0"),
    ("tokens_in", "INTEGER NOT NULL DEFAULT 0"),
    ("tokens_out", "INTEGER NOT NULL DEFAULT 0"),
    ("tokens_cache_read", "INTEGER NOT NULL DEFAULT 0"),
    ("tokens_cache_write", "INTEGER NOT NULL DEFAULT 0"),
    ("usage_allowed", "INTEGER"),
)
# What is counted of the model requests, with a commission and with a session.
_USAGE_COLUMNS: tuple[str, ...] = ("requests", "tokens_in", "tokens_out", "tokens_cache_read", "tokens_cache_write")
# Columns ``update`` may write. The status is not among them, nor is whether
# the commission rests, which is written where it is decided (``rest``), nor
# what was used (``add_usage``) and what may be (``allow_usage``).
_UPDATABLE: frozenset[str] = (
    frozenset(_COMMISSION_COLUMNS)
    - {"id", "status", "created_at", "host_instance", "resting_since", "usage_allowed"}
    - frozenset(_USAGE_COLUMNS)
)


@dataclass(frozen=True, slots=True)
class Session:
    """One row of ``sessions``: a task agent's conversation, which outlasts the commissions worked on in it.

    What was said is kept in ``session_dir`` by the runtime that holds it
    (``runtime``), and the task agent works in ``workdir``; they are set
    when the session is first opened. ``status`` says where the session
    stands (``SESSION_STATUSES``): ``running`` while a commission is worked
    on in it, ``waiting`` while one waits for the user, ``idle`` while it
    is open in a runtime with no run under way, ``closed`` when no runtime
    has it open (what was said is kept, and it can be gone on with),
    ``cleared`` once what was said was removed, being old. ``compacting``
    while the host has it summed up with no commission at work in it
    (``TaskAgentManager``): it is closed again when that is done, or cut
    short by a commission that is to be worked on in it.
    """

    id: str
    # How it is addressed; no two sessions of one character share a name.
    name: str
    lanlan_name: str
    kind: str
    project: str | None
    workdir: str | None
    session_dir: str | None
    runtime: str | None
    # The session of the task agent that started this one's.
    parent_id: str | None
    # The session this one was made from by copying what was said in it
    # (``kind`` ``"fork"``), and the entry of that one's record the copy
    # ends with (``forked_at``, further down).
    forked_from: str | None
    status: str
    created_at: float
    # When a commission of it last changed its status.
    last_active_at: float
    compacted_at: float | None
    forked_at: str | None = None
    # What the model requests made in it used so far, as with a commission.
    requests: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache_read: int = 0
    tokens_cache_write: int = 0
    # How long its conversation was as of its last request, with what every
    # request begins with: what the next one sends, about.
    context_tokens: int = 0

    @property
    def tokens(self) -> int:
        """Return the tokens it used: what every request sent and got back, read from a cache or not."""
        return self.tokens_in + self.tokens_out + self.tokens_cache_read + self.tokens_cache_write


_SESSION_COLUMNS: tuple[str, ...] = tuple(item.name for item in fields(Session))
# Columns of ``sessions`` that its first schema did not have; see ``_ADDED_COLUMNS``.
_ADDED_SESSION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("forked_at", "TEXT"),
    ("requests", "INTEGER NOT NULL DEFAULT 0"),
    ("tokens_in", "INTEGER NOT NULL DEFAULT 0"),
    ("tokens_out", "INTEGER NOT NULL DEFAULT 0"),
    ("tokens_cache_read", "INTEGER NOT NULL DEFAULT 0"),
    ("tokens_cache_write", "INTEGER NOT NULL DEFAULT 0"),
    ("context_tokens", "INTEGER NOT NULL DEFAULT 0"),
)
# Columns ``update_session`` may write. What was used is written by
# ``add_usage``, and where a copy was made from when it is made.
_SESSION_UPDATABLE: frozenset[str] = (
    frozenset(_SESSION_COLUMNS)
    - {"id", "lanlan_name", "kind", "created_at", "forked_from", "forked_at", "context_tokens"}
    - frozenset(_USAGE_COLUMNS)
)


@dataclass(frozen=True, slots=True)
class UsageStanding:
    """How much a commission's work has used, and how much it may.

    Of the commission no task agent started (``root``): what the task
    agents it started use is its own, and it is the one the user allows
    more for.
    """

    root: str
    used: int
    # None: nothing was allowed beyond the setting yet.
    allowed: int | None


@dataclass(frozen=True, slots=True)
class Transition:
    """Result of asking for a status change."""

    applied: bool
    commission: Commission | None
    previous: str | None
    # Why it was refused; empty when applied.
    refusal: str = ""
    # Outbox rows stored together with an accepted move.
    outbox_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    commission_id: str
    seq: int
    ts: float
    type: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class OutboxItem:
    id: int
    commission_id: str | None
    kind: str
    payload: dict[str, Any]
    created_at: float
    attempts: int
    next_attempt_at: float
    sent_at: float | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class Message:
    """One row of ``messages``: what a session wrote to another, or what the host tells a session of one.

    ``queued`` until it is handed to the session it is for, ``delivered``
    from then on, ``void`` when it never will be. ``delivered_to`` is the
    commission that was at work in that session, or was filed for it, when
    it was handed over. A note whose sender asked to be told
    (``notify_when_done``) is answered for once: ``notified_at`` says when
    the sender was told that the turn it began is over, or that it waited
    in vain, or that nobody waits for word of it any more.
    """

    id: int
    from_session: str
    from_commission: str | None
    to_session: str
    kind: str
    body: str
    body_hash: str
    # Of a note: one more than the depth of the commission that wrote it.
    depth: int
    notify_when_done: bool
    status: str
    status_reason: str
    created_at: float
    delivered_at: float | None
    delivered_to: str | None
    notified_at: float | None


@dataclass(frozen=True, slots=True)
class WakeRequest:
    """One row of ``wake_requests``: a session is to be given something, at a time.

    ``commission_id`` names the commission that asked for it (a time it
    set, word of a note it sent): it is that commission's, handed to it
    alone and dropped when it is over. A request without one is for the
    session, whatever it is at then: a note another session wrote.

    ``fired`` says that it was handed to a commission (``fired_into``);
    ``taken_at`` says when that commission's session was seen to have it
    in its conversation. Between the two it is on its way: a session that
    closes before taking it has it put back, and what a process leaves
    like that when it ends is handed over again by the next.
    """

    id: int
    session_id: str
    commission_id: str | None
    reason: str
    note: str
    message_id: int | None
    due_at: float
    status: str
    status_reason: str
    depth: int
    created_at: float
    fired_at: float | None
    fired_into: str | None
    taken_at: float | None


@dataclass(frozen=True, slots=True)
class Resting:
    """What ``Ledger.rest`` wrote: until when the commission rests, and since when."""

    until: float
    since: float


@dataclass(frozen=True, slots=True)
class MessageLimits:
    """How much one session may write to others; see ``Ledger.message_add``."""

    # Seconds between two notes of one session to the same other one.
    target_interval: float
    # Notes one session may write within an hour.
    per_hour: int
    # Notes that may wait for one session at a time.
    queue_max: int
    # Within so many seconds the same words to the same session are sent once.
    dedup_seconds: float


@dataclass(frozen=True, slots=True)
class SessionRow:
    """A session with the commissions last worked on in it, the newest first; see ``Ledger.find_sessions``."""

    session: Session
    commissions: tuple[Commission, ...]


# Told about a commission that was filed (``previous`` None) or whose status
# changed (``previous`` is the status it had).
Listener = Callable[[Commission, "str | None"], None]
# Given a commission that reconciling failed, returns the ``(kind, payload)``
# outbox row that reports it, or None. Runs on the ledger thread.
ReconcileReport = Callable[[Commission], "tuple[str, dict[str, Any]] | None"]


def new_commission_id() -> str:
    """Return a new commission id. It doubles as the task agent's session id."""
    return uuid.uuid4().hex


def new_session_id() -> str:
    """Return a new session id."""
    return uuid.uuid4().hex


def _commission(row: sqlite3.Row | None) -> Commission | None:
    if row is None:
        return None
    return Commission(**{name: row[name] for name in _COMMISSION_COLUMNS})


def _session(row: sqlite3.Row | None) -> Session | None:
    if row is None:
        return None
    return Session(**{name: row[name] for name in _SESSION_COLUMNS})


_MESSAGE_COLUMNS: tuple[str, ...] = tuple(item.name for item in fields(Message))
_WAKE_COLUMNS: tuple[str, ...] = tuple(item.name for item in fields(WakeRequest))


def _message(row: sqlite3.Row | None) -> Message | None:
    if row is None:
        return None
    values = {name: row[name] for name in _MESSAGE_COLUMNS}
    values["notify_when_done"] = bool(values["notify_when_done"])
    return Message(**values)


def _wake(row: sqlite3.Row | None) -> WakeRequest | None:
    if row is None:
        return None
    return WakeRequest(**{name: row[name] for name in _WAKE_COLUMNS})


def body_hash(text: str) -> str:
    """Return what tells two notes with the same words apart from two with others: blank space does not count."""
    import hashlib

    return hashlib.sha256(" ".join(str(text).split()).encode("utf-8")).hexdigest()


def _hour_used(conn: sqlite3.Connection, from_session: str, kinds: tuple[str, ...], now: float, limit: int) -> float | None:
    """Return in how many seconds a session may write again, when it wrote ``limit`` of these kinds within the hour; else None.

    What was written in the hour before ``now``, and, as a clock that was
    set back by a little leaves it, in the hour after (see ``message_add``).
    """
    marks = ", ".join("?" for _ in kinds)
    # (A question that was not asked after all, which is void, counts for
    # nothing; a note that was dropped was written all the same.)
    within = (
        f"FROM messages WHERE from_session = ? AND kind IN ({marks}) AND created_at >= ? AND created_at <= ? "
        "AND NOT (kind = 'ask' AND status = 'void')"
    )
    window = (from_session, *kinds, now - 3600.0, now + 3600.0)
    sent = conn.execute(f"SELECT COUNT(*) AS n {within}", window).fetchone()
    if sent is None or sent["n"] < limit:
        return None
    # Until so many of them are an hour old that fewer than ``limit`` are
    # left: the one that leaves ``limit - 1`` once it is. (More than
    # ``limit`` are in the hour where the clock was set back.)
    freed = conn.execute(
        f"SELECT created_at {within} ORDER BY created_at LIMIT 1 OFFSET ?", (*window, int(sent["n"]) - int(limit))
    ).fetchone()
    return min(3600.0, max(0.0, float(freed["created_at"]) + 3600.0 - now))


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _load(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except ValueError:
        return {"text": text}
    return value if isinstance(value, dict) else {"value": value}


class Ledger:
    """Asynchronous face of the ledger file. See the module docstring."""

    def __init__(
        self,
        path: Path | Callable[[], Path],
        *,
        clock: Callable[[], float] = time.time,
        reconcile_report: ReconcileReport | None = None,
    ) -> None:
        self._path_source = path
        self._clock = clock
        self._reconcile_report = reconcile_report
        self._listeners: list[Listener] = []
        # Held while the note of a stop that could not be written, next to
        # the ledger, is read and written or removed: by the ledger's
        # thread as it is opened, and by whoever notes or forgets a stop.
        self._stop_note_lock = threading.Lock()
        # Told that a note was written (``subscribe_notes``).
        self._note_listeners: list[Callable[[], None]] = []
        # Counts the times wake requests were put on record as handed over
        # (``fired_order``): what was handed over earlier and what later is
        # told by it, and not by the clock of the computer, which is set
        # back now and then. Goes on from what the file has when it is
        # opened. See ``fired_mark``.
        self._fired_high = 0
        # The number of the last wake request in the file when it was
        # opened, and of the last one this ledger is known to have written
        # for a note since (``notes_mark``); None until it is opened.
        self._opened_high: int | None = None
        self._notes_high: int | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._conn: sqlite3.Connection | None = None
        self._open_lock = asyncio.Lock()
        self._closed = False
        # Tells this process's rows from those a previous process left behind.
        self.instance = uuid.uuid4().hex
        self.path: Path | None = None

    # -- listeners --------------------------------------------------------

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Call ``listener(commission, previous_status)`` after every filing and status change.

        It runs on the event loop once the change is stored; ``previous`` is
        None for a commission just filed. What it raises is logged and
        dropped. Returns a callable that removes it again.
        """
        self._listeners.append(listener)

        def unsubscribe() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass  # Removed already; unsubscribing twice is fine.

        return unsubscribe

    def _notify(self, commission: Commission | None, previous: str | None) -> None:
        if commission is None:
            return
        for listener in list(self._listeners):
            try:
                listener(commission, previous)
            except Exception:  # noqa: BLE001 - a listener must not undo or block a stored change
                logger.exception("a ledger listener failed")

    def subscribe_notes(self, listener: Callable[[], None]) -> None:
        """Call ``listener()`` on the event loop whenever a note of one session for another is on record, or a time a commission set.

        Whoever hands the notes over is told by this that there is one to
        hand over, or something due at a time it may not know of: not by
        whoever asked for it, who may have been cancelled while it was
        written and is not there to say so.
        """
        self._note_listeners.append(listener)

    def _note_written(self) -> None:
        for listener in list(self._note_listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001 - a listener must not undo or block a stored note
                logger.exception("a listener for written notes failed")

    # -- lifecycle --------------------------------------------------------

    @property
    def opened(self) -> bool:
        return self._conn is not None

    def _connect(self) -> None:
        source = self._path_source
        path = source() if callable(source) else source
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(path),
            timeout=_BUSY_TIMEOUT_SECONDS,
            isolation_level=None,
            check_same_thread=False,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            # As the ledger was left: 0 for one that is made now.
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            conn.executescript(_SCHEMA)
            self._add_columns(conn)
            # Not in the script above: in a ledger of an earlier build the
            # column is only there once it was added.
            conn.execute("CREATE INDEX IF NOT EXISTS commissions_by_session ON commissions (session_id)")
            if version < _SESSIONS_SINCE_VERSION:
                # Only a ledger of a build without sessions. In one of this
                # build a commission without a session was never given one
                # (the process ended in between, or was shutting down): it
                # is given one when it is taken up, its project's if it has
                # a project, which a session under its own id would not be.
                self._give_sessions(conn)
            conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            # Where the file stands, before this process writes to it.
            high = conn.execute(
                "SELECT COALESCE(MAX(id), 0) AS written, COALESCE(MAX(fired_order), 0) AS fired FROM wake_requests"
            ).fetchone()
            # (What was there when it was first read, should it be read
            # again: a stop that came before then is not to reach further
            # because the file could only be opened later.)
            if self._opened_high is None:
                self._opened_high = int(high["written"])
            self._notes_high = max(self._notes_high or 0, int(high["written"]))
            self._fired_high = max(self._fired_high, int(high["fired"]))
            self._reconcile(conn, path)
        except BaseException:
            # Not kept half-opened: the next ``open`` starts over.
            conn.close()
            raise
        self.path = path
        self._conn = conn

    @staticmethod
    def _add_columns(conn: sqlite3.Connection) -> None:
        """Give a ledger made by an earlier build the columns added since (``_ADDED_COLUMNS``, ``_ADDED_SESSION_COLUMNS``)."""
        for table, added in (("commissions", _ADDED_COLUMNS), ("sessions", _ADDED_SESSION_COLUMNS)):
            present = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, kind in added:
                if name not in present:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
        # Likewise the wake requests of a build that did not yet tell handed over from taken.
        wakes = {row["name"] for row in conn.execute("PRAGMA table_info(wake_requests)")}
        if wakes and "taken_at" not in wakes:
            # Both or neither: with the column there and its rows not
            # filled in (the process ended in between), the next start
            # would find nothing to do, and take what that build had handed
            # over for something that was on its way.
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("ALTER TABLE wake_requests ADD COLUMN taken_at REAL")
                # What that build had handed over it took for taken: it stays
                # so, and is not handed over a second time as something that
                # was on its way.
                conn.execute("UPDATE wake_requests SET taken_at = fired_at WHERE status = 'fired'")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        if wakes and "fired_order" not in wakes:
            # In what order they were handed over, apart from when
            # (``_fire``). Rows without it were handed over before all that
            # have it.
            conn.execute("ALTER TABLE wake_requests ADD COLUMN fired_order INTEGER")

    def _give_sessions(self, conn: sqlite3.Connection) -> None:
        """Give every task agent commission of an earlier build the session it had.

        Before sessions were rows of their own, a commission's id was the id
        of its session. Such a commission gets a session of that very id,
        with the directories the commission has on record, so nothing on
        the disk moves and it is taken up again where it was. A session
        whose transcript was removed since, being old, is ``cleared``.
        All in one transaction; a ledger opened again has nothing left to do.
        """
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                "SELECT * FROM commissions WHERE route = 'task_agent' AND session_id IS NULL "
                "ORDER BY created_at, rowid"
            ).fetchall()
            for row in rows:
                if conn.execute("SELECT 1 FROM sessions WHERE id = ?", (row["id"],)).fetchone() is None:
                    cleaned = conn.execute(
                        "SELECT MAX(ts) AS ts FROM events WHERE commission_id = ? AND type = 'session_cleaned'",
                        (row["id"],),
                    ).fetchone()
                    # Cleaned, and not taken up again since.
                    cleared = cleaned["ts"] is not None and cleaned["ts"] >= row["updated_at"]
                    self._insert_session(
                        conn,
                        {
                            "id": row["id"],
                            "name": f"{row['title']}-{row['id'][:6]}",
                            "lanlan_name": row["lanlan_name"],
                            "kind": "child" if row["parent_id"] else "single",
                            "project": row["project"],
                            "workdir": row["workdir"],
                            "session_dir": row["session_dir"],
                            "runtime": row["runtime"],
                            "parent_id": row["parent_id"],
                            "status": "cleared" if cleared else "closed",
                            "created_at": row["created_at"],
                            "last_active_at": row["updated_at"],
                        },
                    )
                self._set_session(conn, row["id"], row["id"])
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        if rows:
            logger.info("ledger: %d commission(s) of an earlier build given their sessions", len(rows))

    def _reconcile(self, conn: sqlite3.Connection, path: Path) -> None:
        """Fail what a previous host process left unfinished.

        Nobody is working on those commissions any more. A blocked one stays
        blocked, and a deferred one stays as it is: until its deadline its
        plugin's result may still arrive, and past it the task brain's sweep
        fails it with the same report as when the host kept running.
        Questions of the failed ones that were never sent are dropped. With a
        ``reconcile_report``, the row it builds for a failed commission is
        stored along with the change, so she learns of it. All of it is one
        transaction, so no status changes without its event. No runtime of
        this process has opened a session yet: every session an earlier one
        left open is ``closed``, what was said in it being on the disk.

        A commission that rests stays as it is, too: nobody was at work on
        it when the process ended, and what it waits for is on record
        (``wake_requests``) and wakes it in this process as it would have
        in that one.

        What an earlier process had put on record as handed to a
        commission, and never saw taken, was on its way when that process
        ended: it is pending again, before anything is failed, so that
        what belongs to a commission that is failed here goes with it and
        the notes other sessions wrote are handed over anew (to whatever
        their session is at in this process).
        """
        conn.execute("BEGIN IMMEDIATE")
        try:
            marks = ", ".join("?" for _ in LIVE_SESSION_STATUSES)
            conn.execute(
                f"UPDATE sessions SET status = 'closed' WHERE status IN ({marks})", sorted(LIVE_SESSION_STATUSES)
            )
            # A stop of everything that could not drop what waited, in a
            # process that ended before it made up for that: dropped now,
            # before any of it is made pending again or handed over.
            owed = self._stop_owed(path)
            if owed is not None:
                self._void_all(conn, owed[1], self._upto(owed[0]))
            on_its_way = conn.execute(
                "SELECT wake_requests.id AS id FROM wake_requests "
                "JOIN commissions ON commissions.id = wake_requests.fired_into "
                "WHERE wake_requests.status = 'fired' AND wake_requests.taken_at IS NULL "
                "AND COALESCE(commissions.host_instance, '') != ?",
                (self.instance,),
            ).fetchall()
            self._unfire(conn, [int(row["id"]) for row in on_its_way], None, "")
            rows = conn.execute(
                "SELECT id FROM commissions WHERE status IN ('queued','working','waiting_user') "
                "AND COALESCE(host_instance, '') != ? "
                "AND deferred_until IS NULL "
                "AND NOT (status = 'working' AND resting_since IS NOT NULL)",
                (self.instance,),
            ).fetchall()
            for row in rows:
                moved = self._transition(conn, row["id"], "failed", HOST_ENDED_REASON, {})
                if moved.applied and moved.commission is not None and self._reconcile_report is not None:
                    report = self._reconcile_report(moved.commission)
                    if report is not None:
                        self._outbox_insert(conn, row["id"], report[0], report[1])
                # A question it asked and that never went out can no longer be
                # answered; it must not reach her now.
                conn.execute(
                    "DELETE FROM outbox WHERE commission_id = ? AND kind = 'question' AND sent_at IS NULL",
                    (row["id"],),
                )
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        if owed is not None:
            # Only the note that was made up for here: one of a later stop
            # written meanwhile (a stop that was cancelled as this was being
            # opened) is that stop's, and stays for whoever makes up for it.
            with self._stop_note_lock:
                if self._stop_owed(path) == owed:
                    self._forget_stop_owed(path)
        if rows:
            logger.info("ledger: %d unfinished commission(s) of an earlier run marked failed", len(rows))

    # -- a stop of everything that could not be written ---------------------
    #
    # Dropping what waits is a write to this file. Where that write fails
    # (the disk is full, the file is locked), the host keeps the stop in
    # mind and makes up for it before it hands anything over; a process
    # that ends before then would leave nothing of the stop. So it is
    # noted in a file next to the ledger, and settled when the ledger is
    # next opened.

    @staticmethod
    def _stop_owed_file(path: Path) -> Path:
        return path.with_name(path.name + ".stop-owed")

    def note_stop_owed(self, upto: int, reason: str) -> None:
        """Note, outside the ledger, that the notes written up to one are to be dropped. Blocking.

        ``upto`` is what ``notes_mark`` said when the stop came. A note of
        a later stop is not replaced by one of an earlier stop (two stops
        may get here in the other order), and the note that is there stays
        whole until the new one is: written beside it first, then put in
        its place. Cut off half way, the next process would find no note
        at all, and none of the stops.
        """
        # Next to where the ledger is, opened or not: a ledger that could
        # not be opened (its file is locked, say) is the very case in which
        # the stop cannot be written into it.
        path = self._where()
        target = self._stop_owed_file(path)
        # By its number, where that is known: the next process is not to
        # take "all that was there when the ledger was opened" for all
        # that is there by then. (Where the file was never read, that is
        # what stays noted, and the next process, which reads it, drops
        # what is there: this one wrote nothing.)
        upto = self._marked(upto)
        # Read, compared and written with nothing else at the note
        # meanwhile (``_reconcile`` removes the one it made up for).
        with self._stop_note_lock:
            noted = self._stop_owed(path)
            if noted is not None and _covers(noted[0], upto):
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            beside = target.with_name(target.name + ".tmp")
            with beside.open("w", encoding="utf-8") as file:
                file.write(json.dumps({"upto": upto, "reason": str(reason)}))
                file.flush()
                os.fsync(file.fileno())
            os.replace(beside, target)

    def clear_stop_owed(self, upto: int | None = None) -> None:
        """Forget that note: the stop is on record in the ledger. Blocking.

        With ``upto`` (the stop that was made up for), only a note of
        that stop or an earlier one: what a later stop noted meanwhile is
        that stop's to forget.
        """
        path = self._where()
        with self._stop_note_lock:
            if upto is not None:
                noted = self._stop_owed(path)
                if noted is not None and noted[0] > self._marked(upto):
                    return
            self._forget_stop_owed(path)

    def _where(self) -> Path:
        """Return where the file of the ledger is, whether or not it was opened."""
        if self.path is not None:
            return self.path
        source = self._path_source
        return Path(source() if callable(source) else source)

    def _marked(self, mark: int) -> int:
        """Return a mark as it is noted next to the ledger: by its number once the file was read, else as it is."""
        return self._upto(mark) if self._opened_high is not None else int(mark)

    def _upto(self, mark: int) -> int:
        """Return the number of the wake request that a mark of ``notes_mark`` stands for."""
        if int(mark) == AS_OPENED:
            return self._opened_high or 0
        return int(mark)

    def notes_mark(self) -> int:
        """Return a mark of all the notes this ledger holds so far. Not blocking.

        The number of the last wake request written for a note (they are
        numbered in the order they are written): a stop of everything
        drops the notes up to it, and none that is written afterwards.
        Not a time: the clock of the computer is set back now and then,
        and a note written before a stop would then pass for one written
        after it. Asked without reading the file, which a stop cannot wait
        for and may not be able to read; before the ledger is opened it is
        ``AS_OPENED``, all that is in the file when it is.
        """
        return AS_OPENED if self._notes_high is None else self._notes_high

    def _forget_stop_owed(self, path: Path) -> None:
        try:
            self._stop_owed_file(path).unlink(missing_ok=True)
        except OSError:
            logger.warning("removing the note of a stop that was made up for failed", exc_info=True)

    def _stop_owed(self, path: Path) -> tuple[int, str] | None:
        try:
            noted = json.loads(self._stop_owed_file(path).read_text(encoding="utf-8"))
            return int(noted["upto"]), str(noted.get("reason") or "")
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, KeyError):
            logger.warning("the note of a stop that was not written cannot be read; it is left alone", exc_info=True)
            return None

    async def open(self) -> None:
        """Open the file if it is not open yet."""
        if self._conn is not None:
            return
        async with self._open_lock:
            if self._conn is not None:
                return
            if self._closed:
                raise RuntimeError("the ledger is closed")
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nekoclaw-ledger")
            await asyncio.get_running_loop().run_in_executor(self._executor, self._connect)

    async def close(self) -> None:
        """Close the file. A closed ledger cannot be reopened."""
        self._closed = True
        executor, self._executor = self._executor, None
        if executor is None:
            return

        def shut() -> None:
            conn, self._conn = self._conn, None
            if conn is not None:
                conn.close()

        try:
            await asyncio.get_running_loop().run_in_executor(executor, shut)
        finally:
            executor.shutdown(wait=False)

    async def run(
        self, work: Callable[[sqlite3.Connection], T], *, written: Callable[[T], None] | None = None
    ) -> T:
        """Run ``work(connection)`` on the ledger thread, inside one transaction.

        ``written`` is called with what it returned once the transaction
        is on record, on that thread: for what is to be noted of a write
        whether or not whoever asked for it is still there to hear of it
        (cancelled meanwhile, the thread goes on and writes all the same).
        """
        await self.open()
        executor = self._executor
        if executor is None:
            raise RuntimeError("the ledger is closed")

        def call() -> T:
            conn = self._conn
            if conn is None:
                raise RuntimeError("the ledger is closed")
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = work(conn)
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            if written is not None:
                written(result)
            return result

        return await asyncio.get_running_loop().run_in_executor(executor, call)

    # -- commissions ------------------------------------------------------

    def _insert(self, conn: sqlite3.Connection, values: dict[str, Any]) -> Commission:
        now = self._clock()
        row: dict[str, Any] = {name: None for name in _COMMISSION_COLUMNS}
        row.update(
            id=new_commission_id(),
            user_text="",
            status="queued",
            status_reason="",
            created_at=now,
            updated_at=now,
            host_instance=self.instance,
            **{name: 0 for name in _USAGE_COLUMNS},
        )
        row.update(values)
        if row["status"] not in STATUSES:
            raise ValueError(f"unknown status {row['status']!r}")
        if row["source"] not in SOURCES:
            raise ValueError(f"unknown source {row['source']!r}")
        columns = ", ".join(_COMMISSION_COLUMNS)
        marks = ", ".join("?" for _ in _COMMISSION_COLUMNS)
        conn.execute(
            f"INSERT INTO commissions ({columns}) VALUES ({marks})",
            [row[name] for name in _COMMISSION_COLUMNS],
        )
        self._event(conn, row["id"], "created", {"source": row["source"], "title": row["title"]})
        commission = _commission(conn.execute("SELECT * FROM commissions WHERE id = ?", (row["id"],)).fetchone())
        assert commission is not None
        return commission

    async def create(
        self,
        *,
        unless: Callable[[list[Commission]], Commission | None] | None = None,
        **values: Any,
    ) -> tuple[Commission, bool]:
        """File a commission. Returns ``(commission, created)``.

        ``unless`` is given the owner's active commissions and may return one
        of them; then nothing is filed and that one is returned with
        ``created`` False. Check and insert happen in one transaction, so two
        dispatches of the same request cannot both be filed.
        """
        unknown = set(values) - set(_COMMISSION_COLUMNS)
        if unknown:
            raise ValueError(f"unknown commission fields: {sorted(unknown)}")

        def work(conn: sqlite3.Connection) -> tuple[Commission, bool]:
            if unless is not None:
                active = self._select(conn, lanlan_name=values.get("lanlan_name"), statuses=ACTIVE_STATUSES)
                existing = unless(active)
                if existing is not None:
                    return existing, False
            return self._insert(conn, values), True

        commission, created = await self.run(work)
        if created:
            self._notify(commission, None)
        return commission, created

    async def get(self, commission_id: str) -> Commission | None:
        return await self.run(
            lambda conn: _commission(
                conn.execute("SELECT * FROM commissions WHERE id = ?", (commission_id,)).fetchone()
            )
        )

    @staticmethod
    def _select(
        conn: sqlite3.Connection,
        *,
        lanlan_name: str | None = None,
        statuses: Iterable[str] | None = None,
        parent_id: str | None = None,
        session_id: str | None = None,
        updated_since: float | None = None,
        limit: int | None = None,
        oldest_first: bool = False,
    ) -> list[Commission]:
        clauses: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if lanlan_name is not None:
            clauses.append("lanlan_name = ?")
            params.append(lanlan_name)
        if statuses is not None:
            wanted = sorted(set(statuses))
            clauses.append(f"status IN ({', '.join('?' for _ in wanted)})")
            params.extend(wanted)
        if parent_id is not None:
            clauses.append("parent_id = ?")
            params.append(parent_id)
        if updated_since is not None:
            clauses.append("updated_at >= ?")
            params.append(updated_since)
        sql = "SELECT * FROM commissions"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at " + ("ASC" if oldest_first else "DESC") + ", rowid " + ("ASC" if oldest_first else "DESC")
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [item for item in (_commission(row) for row in conn.execute(sql, params).fetchall()) if item]

    async def select(self, **criteria: Any) -> list[Commission]:
        """Return commissions, newest first unless ``oldest_first`` is set.

        Criteria: ``lanlan_name``, ``statuses``, ``parent_id``,
        ``session_id``, ``updated_since``, ``limit``, ``oldest_first``.
        """
        return await self.run(lambda conn: self._select(conn, **criteria))

    def _transition(
        self,
        conn: sqlite3.Connection,
        commission_id: str,
        status: str,
        reason: str,
        values: dict[str, Any],
        only_from: frozenset[str] | None = None,
    ) -> Transition:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        unknown = set(values) - _UPDATABLE
        if unknown:
            raise ValueError(f"fields a transition cannot set: {sorted(unknown)}")
        current = _commission(conn.execute("SELECT * FROM commissions WHERE id = ?", (commission_id,)).fetchone())
        if current is None:
            return Transition(False, None, None, "unknown commission")
        if status == current.status:
            return Transition(False, current, current.status, f"already {status}")
        if only_from is not None and current.status not in only_from:
            return Transition(False, current, current.status, f"{current.status}, not {sorted(only_from)}")
        if status not in ALLOWED_TRANSITIONS[current.status]:
            refusal = f"{current.status} cannot become {status}"
            self._event(conn, commission_id, "transition_refused", {"from": current.status, "to": status, "reason": reason})
            return Transition(False, current, current.status, refusal)

        now = self._clock()
        changes: dict[str, Any] = dict(values)
        changes.update(status=status, status_reason=reason, updated_at=now, host_instance=self.instance)
        if status == "working":
            if current.started_at is None:
                changes.setdefault("started_at", now)
            changes["ended_at"] = None
        if status in ENDED_STATUSES:
            changes.setdefault("ended_at", now)
        if status != "waiting_user":
            changes.setdefault("pending_question", None)
        # Whatever it becomes, it no longer rests: it is taken up, or over.
        changes["resting_since"] = None
        assignments = ", ".join(f"{name} = ?" for name in changes)
        conn.execute(
            f"UPDATE commissions SET {assignments} WHERE id = ?",
            [*changes.values(), commission_id],
        )
        self._event(conn, commission_id, "status", {"from": current.status, "to": status, "reason": reason})
        if status in ENDED_STATUSES:
            # What it asked to be woken for goes with it, in the same
            # transaction: nothing wakes a commission that is over, and
            # nothing of it is left to be found by a stop that comes later.
            conn.execute(
                "UPDATE wake_requests SET status = 'void', status_reason = ? "
                "WHERE commission_id = ? AND status = 'pending'",
                (COMMISSION_OVER_REASON, commission_id),
            )
            # What the host had to tell it and had not yet handed over.
            conn.execute(
                "UPDATE messages SET status = 'void', status_reason = ? WHERE status = 'queued' "
                "AND kind != 'note' AND id IN (SELECT message_id FROM wake_requests "
                "WHERE commission_id = ? AND message_id IS NOT NULL)",
                (COMMISSION_OVER_REASON, commission_id),
            )
            # (What was handed over and is not on record as taken stays as
            # it is: the session may have taken it, and say so in a moment.
            # Where it did not, it is dropped as it would be made pending
            # again: ``_unfire``.)
        session_id = changes.get("session_id", current.session_id)
        if session_id:
            # A session is as old as the last change of any commission in it,
            # and at work, or waiting for the user, when a commission in it
            # is. (That it is closed, no runtime having it open any more, is
            # written by whoever closed it.)
            stands = {"working": "running", "waiting_user": "waiting"}.get(status)
            conn.execute(
                "UPDATE sessions SET last_active_at = ?, status = COALESCE(?, status) WHERE id = ?",
                (now, stands, session_id),
            )
        if current.status == "waiting_user":
            # No longer waiting: a question of it not yet delivered is out of
            # date (answered, stopped or failed) and must not reach her now.
            conn.execute(
                "DELETE FROM outbox WHERE commission_id = ? AND kind = 'question' AND sent_at IS NULL",
                (commission_id,),
            )
        updated = _commission(conn.execute("SELECT * FROM commissions WHERE id = ?", (commission_id,)).fetchone())
        return Transition(True, updated, current.status)

    async def transition(
        self,
        commission_id: str,
        status: str,
        *,
        reason: str = "",
        outbox: Iterable[tuple[str, dict[str, Any]]] = (),
        only_from: Iterable[str] | None = None,
        **values: Any,
    ) -> Transition:
        """Move a commission to ``status`` if ``ALLOWED_TRANSITIONS`` permits it.

        With ``only_from``, the move is also refused unless the commission is
        in one of those statuses, checked in the same transaction.

        This is the only way a status changes. A refused move changes nothing
        and is recorded as a ``transition_refused`` event; in particular a
        cancelled commission stays cancelled. ``values`` are further columns
        written together with an accepted move.

        ``outbox`` holds ``(kind, payload)`` reports for the main server. They
        are stored in the same transaction as an accepted move, and not at all
        when the move is refused, so a commission never ends without the
        report that says so, nor is a report left for a move that did not
        happen. Their row ids are in ``Transition.outbox_ids``.
        """
        reports = [(kind, dict(payload)) for kind, payload in outbox]
        sources = None if only_from is None else frozenset(only_from)

        def work(conn: sqlite3.Connection) -> Transition:
            moved = self._transition(conn, commission_id, status, reason, values, sources)
            if not moved.applied or not reports:
                return moved
            ids = tuple(self._outbox_insert(conn, commission_id, kind, payload) for kind, payload in reports)
            return Transition(moved.applied, moved.commission, moved.previous, moved.refusal, ids)

        result = await self.run(work)
        if result.applied:
            self._notify(result.commission, result.previous)
        return result

    async def update(self, commission_id: str, **values: Any) -> Commission | None:
        """Write columns other than the status."""
        unknown = set(values) - _UPDATABLE
        if unknown:
            raise ValueError(f"fields that cannot be updated: {sorted(unknown)}")

        def work(conn: sqlite3.Connection) -> Commission | None:
            if values:
                changes = {**values, "updated_at": self._clock()}
                assignments = ", ".join(f"{name} = ?" for name in changes)
                conn.execute(
                    f"UPDATE commissions SET {assignments} WHERE id = ?",
                    [*changes.values(), commission_id],
                )
            return _commission(conn.execute("SELECT * FROM commissions WHERE id = ?", (commission_id,)).fetchone())

        return await self.run(work)

    # -- sessions ---------------------------------------------------------

    @staticmethod
    def _free_name(conn: sqlite3.Connection, lanlan_name: str, wanted: Any) -> str:
        """Return ``wanted`` as a session name no other session of the character has: numbered when it is taken."""
        base = " ".join(str(wanted or "").split()) or "session"
        name, number = base, 1
        while conn.execute(
            "SELECT 1 FROM sessions WHERE lanlan_name = ? AND name = ?", (lanlan_name, name)
        ).fetchone():
            number += 1
            name = f"{base}-{number}"
        return name

    def _insert_session(self, conn: sqlite3.Connection, values: dict[str, Any]) -> Session:
        now = self._clock()
        row: dict[str, Any] = {name: None for name in _SESSION_COLUMNS}
        row.update(id=new_session_id(), kind="single", status="closed", created_at=now, last_active_at=now)
        row.update({name: 0 for name in (*_USAGE_COLUMNS, "context_tokens")})
        row.update(values)
        if row["status"] not in SESSION_STATUSES:
            raise ValueError(f"unknown session status {row['status']!r}")
        if row["kind"] not in SESSION_KINDS:
            raise ValueError(f"unknown session kind {row['kind']!r}")
        if not row["lanlan_name"]:
            raise ValueError("a session belongs to a character")
        row["name"] = self._free_name(conn, row["lanlan_name"], row["name"])
        columns = ", ".join(_SESSION_COLUMNS)
        marks = ", ".join("?" for _ in _SESSION_COLUMNS)
        conn.execute(f"INSERT INTO sessions ({columns}) VALUES ({marks})", [row[name] for name in _SESSION_COLUMNS])
        session = _session(conn.execute("SELECT * FROM sessions WHERE id = ?", (row["id"],)).fetchone())
        assert session is not None
        return session

    async def create_session(self, **values: Any) -> Session:
        """Make a session. ``name`` is what it is to be called; a name that is taken gets a number."""
        unknown = set(values) - set(_SESSION_COLUMNS)
        if unknown:
            raise ValueError(f"unknown session fields: {sorted(unknown)}")
        return await self.run(lambda conn: self._insert_session(conn, values))

    async def get_session(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        return await self.run(
            lambda conn: _session(conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone())
        )

    async def project_session(self, lanlan_name: str, project: str, **values: Any) -> tuple[Session, bool]:
        """Return the session of a project for one character, made now when there is none; and whether it was.

        A project has one session for each character, which takes the
        commissions filed under it one after the other. Looking and making
        are one transaction, so two commissions of a project that are
        handed over at the same time get the same session. ``values`` are
        for the session that is made.
        """
        unknown = set(values) - set(_SESSION_COLUMNS)
        if unknown:
            raise ValueError(f"unknown session fields: {sorted(unknown)}")

        def work(conn: sqlite3.Connection) -> tuple[Session, bool]:
            found = _session(
                conn.execute(
                    "SELECT * FROM sessions WHERE lanlan_name = ? AND project = ? AND kind = 'project' "
                    "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (lanlan_name, project),
                ).fetchone()
            )
            if found is not None:
                return found, False
            made = {"name": project, **values, "lanlan_name": lanlan_name, "project": project, "kind": "project"}
            return self._insert_session(conn, made), True

        return await self.run(work)

    @staticmethod
    def _set_session(conn: sqlite3.Connection, commission_id: str, session_id: str) -> None:
        """Write which session a commission is worked on in; nothing else of the commission changes."""
        conn.execute("UPDATE commissions SET session_id = ? WHERE id = ?", (session_id, commission_id))

    async def give_session(
        self, commission_id: str, *, kind: str, name: Any, parent_id: str | None = None
    ) -> tuple[Commission, Session]:
        """Give a commission the session it is worked on in, and return both.

        One that has a session keeps it. Otherwise, for ``kind``
        ``"project"``, it gets the session of its project for its
        character (``project_session``), and for the other kinds a session
        of its own. A session that is made here takes the commission's id
        as its own, as every session did when a commission and its session
        were one, so its directory is named as it always was. All in one
        transaction: asked twice at the same time, a commission gets one
        session, and two commissions of a project the same one.
        """
        if kind not in SESSION_KINDS:
            raise ValueError(f"unknown session kind {kind!r}")

        def work(conn: sqlite3.Connection) -> tuple[Commission, Session]:
            commission = _commission(
                conn.execute("SELECT * FROM commissions WHERE id = ?", (commission_id,)).fetchone()
            )
            if commission is None:
                raise ValueError(f"unknown commission {commission_id!r}")
            session = None
            if commission.session_id:
                session = _session(
                    conn.execute("SELECT * FROM sessions WHERE id = ?", (commission.session_id,)).fetchone()
                )
            if session is None and kind == "project" and commission.project:
                session = _session(
                    conn.execute(
                        "SELECT * FROM sessions WHERE lanlan_name = ? AND project = ? AND kind = 'project' "
                        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                        (commission.lanlan_name, commission.project),
                    ).fetchone()
                )
            if session is None:
                # One of that id is there already when the commission lost
                # its session id without losing its session.
                session = _session(conn.execute("SELECT * FROM sessions WHERE id = ?", (commission_id,)).fetchone())
            if session is None:
                session = self._insert_session(
                    conn,
                    {
                        "id": commission_id,
                        "name": name,
                        "lanlan_name": commission.lanlan_name,
                        "kind": kind,
                        "project": commission.project,
                        "parent_id": parent_id,
                    },
                )
            if commission.session_id != session.id:
                self._set_session(conn, commission_id, session.id)
                commission = _commission(
                    conn.execute("SELECT * FROM commissions WHERE id = ?", (commission_id,)).fetchone()
                )
                assert commission is not None
            return commission, session

        return await self.run(work)

    async def update_session(self, session_id: str, **values: Any) -> Session | None:
        """Write columns of a session; ``active=True`` also notes that it was in use just now."""
        touched = bool(values.pop("active", False))
        unknown = set(values) - _SESSION_UPDATABLE
        if unknown:
            raise ValueError(f"session fields that cannot be updated: {sorted(unknown)}")
        if "status" in values and values["status"] not in SESSION_STATUSES:
            raise ValueError(f"unknown session status {values['status']!r}")

        def work(conn: sqlite3.Connection) -> Session | None:
            changes = dict(values)
            if touched:
                changes["last_active_at"] = self._clock()
            if "name" in changes:
                current = conn.execute("SELECT lanlan_name, name FROM sessions WHERE id = ?", (session_id,)).fetchone()
                if current is not None and current["name"] != changes["name"]:
                    changes["name"] = self._free_name(conn, current["lanlan_name"], changes["name"])
            if changes:
                assignments = ", ".join(f"{name} = ?" for name in changes)
                conn.execute(f"UPDATE sessions SET {assignments} WHERE id = ?", [*changes.values(), session_id])
            return _session(conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone())

        return await self.run(work)

    async def settle_session(self, session_id: str, commission_id: str) -> None:
        """Write where a session stands once the commission that holds it has left its runtime.

        ``waiting`` when that commission waits for the user, ``closed``
        when it is over. Read and written together: a commission that was
        taken up again meanwhile (the user answered, say) is queued or at
        work, which was written of the session with its own status, and
        that stands; a look that began before it is not to write over it.

        A session whose conversation was removed, being old, and that no
        runtime had open since stays ``cleared``: a commission that gets
        to work in it writes that it is at work, so one that is still
        ``cleared`` was held by a commission that failed, or was stopped,
        before that. Nothing is in it.

        A commission that rests has left the runtime for as long as it
        rests: its session is ``closed`` like that of one that is over,
        and is opened again when it is woken.
        """

        def work(conn: sqlite3.Connection) -> None:
            row = conn.execute(
                "SELECT status, resting_since FROM commissions WHERE id = ?", (commission_id,)
            ).fetchone()
            status = row["status"] if row is not None else None
            resting = status == "working" and row["resting_since"] is not None
            if status in ("queued", "working") and not resting:
                return
            if status == "waiting_user":
                conn.execute("UPDATE sessions SET status = 'waiting' WHERE id = ?", (session_id,))
            else:
                conn.execute("UPDATE sessions SET status = 'closed' WHERE id = ? AND status != 'cleared'", (session_id,))

        await self.run(work)

    async def mark_session_cleared(self, session_id: str) -> None:
        """Put on record that what was said in a session is gone, being old.

        With the session, which is ``cleared``, and with each commission
        of it, which gets a ``session_cleaned`` event that names the
        session: written together, or not at all.
        """

        def work(conn: sqlite3.Connection) -> None:
            conn.execute("UPDATE sessions SET status = 'cleared' WHERE id = ?", (session_id,))
            rows = conn.execute(
                "SELECT id FROM commissions WHERE session_id = ? ORDER BY created_at, rowid", (session_id,)
            ).fetchall()
            for row in rows:
                self._event(conn, row["id"], "session_cleaned", {"session_id": session_id})

        await self.run(work)

    async def find_sessions(
        self,
        *,
        lanlan_name: str | None = None,
        project: str | None = None,
        kind: str | None = None,
        kinds: Iterable[str] | None = None,
        status: str | None = None,
        limit: int = 200,
        commissions_each: int = 3,
    ) -> list[SessionRow]:
        """Return sessions, the one last in use first, each with the commissions last worked on in it.

        Of every character unless ``lanlan_name`` names one; ``project``,
        ``kind`` (or ``kinds``, any of several) and ``status`` narrow it
        down the same way. ``limit`` is how many sessions are looked at,
        the ones last in use among those that are asked for: what a
        caller looks through for words (``TaskAgentManager``) is that many
        rows and no more, however many sessions there are, and the kinds
        it does not want take up none of them.
        """
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("lanlan_name", lanlan_name), ("project", project), ("kind", kind), ("status", status)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if kinds is not None:
            wanted = [str(item) for item in kinds]
            clauses.append(f"kind IN ({', '.join('?' for _ in wanted)})" if wanted else "0")
            params.extend(wanted)
        sql = "SELECT * FROM sessions"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY last_active_at DESC, rowid DESC LIMIT ?"
        params.append(int(limit))

        def work(conn: sqlite3.Connection) -> list[SessionRow]:
            found = [item for item in (_session(row) for row in conn.execute(sql, params).fetchall()) if item]
            rows: list[SessionRow] = []
            for session in found:
                last = conn.execute(
                    "SELECT * FROM commissions WHERE session_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (session.id, int(commissions_each)),
                ).fetchall()
                rows.append(SessionRow(session, tuple(item for item in (_commission(row) for row in last) if item)))
            return rows

        return await self.run(work)

    async def sessions_named(self, name: str) -> list[Session]:
        """Return the sessions addressed by ``name``: the one whose id it is, or those called so or whose id begins with it.

        An id addresses one session and nothing else, whatever any session
        is called. Short of that, a name belongs to one session of a
        character, two characters may each have a session of the same
        name, and what a session is called may be how another's id
        begins (eight characters of an id or more are taken for its
        beginning): every one of these comes back, and with more than one
        the caller says so and has the whole id named.
        """
        wanted = " ".join(str(name or "").split())
        if not wanted:
            return []

        def work(conn: sqlite3.Connection) -> list[Session]:
            exact = conn.execute("SELECT * FROM sessions WHERE id = ?", (wanted,)).fetchall()
            if exact:
                return [item for item in (_session(row) for row in exact) if item]
            rows = conn.execute(
                "SELECT * FROM sessions WHERE name = ? ORDER BY last_active_at DESC, rowid DESC", (wanted,)
            ).fetchall()
            if len(wanted) >= 8 and all(char in "0123456789abcdefABCDEF" for char in wanted):
                seen = {row["id"] for row in rows}
                begun = conn.execute(
                    "SELECT * FROM sessions WHERE id LIKE ? ORDER BY last_active_at DESC, rowid DESC",
                    (wanted.lower() + "%",),
                ).fetchall()
                rows = [*rows, *(row for row in begun if row["id"] not in seen)]
            return [item for item in (_session(row) for row in rows) if item]

        return await self.run(work)

    async def session_places(self) -> list[tuple[str, str | None, str | None]]:
        """Return ``(id, workdir, session_dir)`` of every session that has either on record."""

        def work(conn: sqlite3.Connection) -> list[tuple[str, str | None, str | None]]:
            rows = conn.execute(
                "SELECT id, workdir, session_dir FROM sessions WHERE workdir IS NOT NULL OR session_dir IS NOT NULL"
            ).fetchall()
            return [(row["id"], row["workdir"], row["session_dir"]) for row in rows]

        return await self.run(work)

    async def last_commission(self, session_id: str) -> Commission | None:
        """Return the commission that was last given to a session, or None."""
        found = await self.select(session_id=session_id, limit=1)
        return found[0] if found else None

    async def resting_in(self, session_id: str) -> Commission | None:
        """Return the commission that rests in a session, the one that came to rest last; None when none does."""

        def work(conn: sqlite3.Connection) -> Commission | None:
            return _commission(
                conn.execute(
                    "SELECT * FROM commissions WHERE session_id = ? AND status = 'working' "
                    "AND resting_since IS NOT NULL ORDER BY resting_since DESC, rowid DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
            )

        return await self.run(work)

    # -- resting, messages and wake requests ------------------------------

    async def rest(self, commission_id: str) -> Resting | None:
        """Let a commission rest if something it asked for is still to wake it; return until when, and since when.

        Looked at and written together: only a commission that is
        ``working`` and has a wake request of its own pending rests, and
        one whose last request was carried out, or dropped, just now does
        not (None: the caller ends it as a run without a report is ended).
        From here on it waits for that request with nobody at work on it;
        see ``Commission.resting_since``.
        """

        def work(conn: sqlite3.Connection) -> Resting | None:
            row = conn.execute("SELECT status FROM commissions WHERE id = ?", (commission_id,)).fetchone()
            if row is None or row["status"] != "working":
                return None
            due = conn.execute(
                "SELECT MIN(due_at) AS due FROM wake_requests WHERE commission_id = ? AND status = 'pending'",
                (commission_id,),
            ).fetchone()
            if due is None or due["due"] is None:
                return None
            now = self._clock()
            conn.execute(
                "UPDATE commissions SET resting_since = ?, updated_at = ? WHERE id = ?", (now, now, commission_id)
            )
            self._event(conn, commission_id, "resting", {"until": float(due["due"])})
            return Resting(float(due["due"]), now)

        return await self.run(work)

    async def rest_over(self, commission_id: str, since: float) -> bool:
        """Take back that a commission rests since ``since``: it was given more work as that was written down.

        Only the rest that was written then: one it came to later, after
        the run that work started, stays. Returns whether it was taken
        back.
        """

        def work(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                "UPDATE commissions SET resting_since = NULL, updated_at = ? "
                "WHERE id = ? AND status = 'working' AND resting_since = ?",
                (self._clock(), commission_id, float(since)),
            )
            if not cursor.rowcount:
                return False
            self._event(conn, commission_id, "rest_over", {"since": float(since)})
            return True

        return await self.run(work)

    def _message_insert(self, conn: sqlite3.Connection, values: dict[str, Any]) -> Message:
        row: dict[str, Any] = {name: None for name in _MESSAGE_COLUMNS}
        row.update(kind="note", depth=1, notify_when_done=0, status="queued", status_reason="", created_at=self._clock())
        row.update(values)
        row["body_hash"] = body_hash(row["body"])
        row["notify_when_done"] = 1 if row["notify_when_done"] else 0
        if row["kind"] not in MESSAGE_KINDS:
            raise ValueError(f"unknown message kind {row['kind']!r}")
        columns = [name for name in _MESSAGE_COLUMNS if name != "id"]
        cursor = conn.execute(
            f"INSERT INTO messages ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            [row[name] for name in columns],
        )
        message = _message(conn.execute("SELECT * FROM messages WHERE id = ?", (cursor.lastrowid,)).fetchone())
        assert message is not None
        return message

    def _wake_insert(self, conn: sqlite3.Connection, values: dict[str, Any]) -> WakeRequest:
        now = self._clock()
        row: dict[str, Any] = {name: None for name in _WAKE_COLUMNS}
        row.update(note="", due_at=now, status="pending", status_reason="", depth=0, created_at=now)
        row.update(values)
        if row["reason"] not in WAKE_REASONS:
            raise ValueError(f"unknown wake reason {row['reason']!r}")
        columns = [name for name in _WAKE_COLUMNS if name != "id"]
        cursor = conn.execute(
            f"INSERT INTO wake_requests ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            [row[name] for name in columns],
        )
        wake = _wake(conn.execute("SELECT * FROM wake_requests WHERE id = ?", (cursor.lastrowid,)).fetchone())
        assert wake is not None
        return wake

    async def message_add(
        self,
        *,
        from_session: str,
        from_commission: str | None,
        to_session: str,
        body: str,
        depth: int,
        notify_when_done: bool,
        notice_after: float,
        limits: MessageLimits,
    ) -> tuple[Message | None, str, float]:
        """Put a note of one session for another on record, to be handed over; ``(message, refusal, wait)``.

        Written first and handed over afterwards (``wake_requests`` gets
        the row that has it handed over, due now), as a report is. What
        one session may write is counted here, from what is on record,
        so it is counted the same after the host was started anew:
        ``refusal`` is ``"over"`` (the commission that writes is not
        under way any more: it was stopped while this was on its way, and
        what it wrote would start work after the stop), ``"same"`` (these
        words went to that session a moment ago, and are sent once), ``"too_soon"`` (``wait`` says how
        many seconds are left), ``"too_many"`` (this session wrote its
        share for the hour; ``wait`` again), ``"queue_full"`` (too much
        waits for that session already), or empty with the message.

        With ``notify_when_done`` the sending commission is woken when the
        turn the note begins is over, or after ``notice_after`` seconds
        without that: the second is written here, as a wake request of
        the commission's own, and dropped when the first comes.
        """

        def work(conn: sqlite3.Connection) -> tuple[Message | None, str, float]:
            now = self._clock()
            if from_commission is not None:
                # As it stands now, in this transaction, and not as it stood
                # when the call came in.
                writer = conn.execute("SELECT status FROM commissions WHERE id = ?", (from_commission,)).fetchone()
                if writer is None or writer["status"] not in ("working", "waiting_user"):
                    return None, "over", 0.0
            digest = body_hash(body)
            # What counts against a note is what was written in the time
            # before it. A note with a later time than now was written
            # before the clock of the computer was set back. Set back by a
            # little, the note is of a moment ago and counts as that: each
            # of these looks as far ahead as it looks back. Set back by
            # more, it is left out: counted, it would keep its session
            # from writing for as long as the clock was set back by.
            same = conn.execute(
                "SELECT 1 FROM messages WHERE from_session = ? AND to_session = ? AND kind = 'note' "
                "AND body_hash = ? AND created_at >= ? AND created_at <= ? AND status != 'void' LIMIT 1",
                (from_session, to_session, digest, now - limits.dedup_seconds, now + limits.dedup_seconds),
            ).fetchone()
            if same is not None:
                return None, "same", 0.0
            last = conn.execute(
                "SELECT MAX(created_at) AS at FROM messages WHERE from_session = ? AND to_session = ? "
                "AND kind = 'note' AND created_at <= ?",
                (from_session, to_session, now + limits.target_interval),
            ).fetchone()
            if last is not None and last["at"] is not None and now - last["at"] < limits.target_interval:
                # (Never longer than the whole of it: a note of a time to
                # come is no further back than now.)
                return None, "too_soon", min(limits.target_interval, limits.target_interval - (now - last["at"]))
            # (The questions it asked count as well: ``ask_add``.)
            wait = _hour_used(conn, from_session, ("note", "ask"), now, limits.per_hour)
            if wait is not None:
                return None, "too_many", wait
            # The notes that wait, and not what else waits for the session
            # (word that a turn is over is a message too).
            waiting = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE to_session = ? AND status = 'queued' AND kind = 'note'",
                (to_session,),
            ).fetchone()
            if waiting is not None and waiting["n"] >= limits.queue_max:
                return None, "queue_full", 0.0
            message = self._message_insert(
                conn,
                {
                    "from_session": from_session,
                    "from_commission": from_commission,
                    "to_session": to_session,
                    "kind": "note",
                    "body": body,
                    "depth": int(depth),
                    "notify_when_done": notify_when_done,
                    "created_at": now,
                },
            )
            # With what hands it over, in this transaction: a stop of
            # everything drops the notes by the numbers of these
            # (``notes_mark``), a note and what hands it over together.
            written.append(
                self._wake_insert(
                    conn,
                    {
                        "session_id": to_session,
                        "reason": "message",
                        "message_id": message.id,
                        "depth": int(depth),
                        "due_at": now,
                        "created_at": now,
                    },
                ).id
            )
            if notify_when_done and from_commission:
                self._wake_insert(
                    conn,
                    {
                        "session_id": from_session,
                        "commission_id": from_commission,
                        "reason": "notice_expiry",
                        "message_id": message.id,
                        "due_at": now + notice_after,
                        "created_at": now,
                    },
                )
            if from_commission:
                self._event(
                    conn,
                    from_commission,
                    "message_sent",
                    {"message_id": message.id, "to_session": to_session, "notify_when_done": bool(notify_when_done)},
                )
            return message, "", 0.0

        written: list[int] = []

        def on_record(result: tuple[Message | None, str, float]) -> None:
            # On record now (a transaction that failed wrote nothing, and
            # its number is given out again): a stop from here on drops it.
            # Noted on the ledger's thread, as soon as it is written:
            # whoever asked for the note may have been cancelled meanwhile,
            # and the note is written all the same.
            if written and result[0] is not None:
                self._notes_high = max(self._notes_high or 0, written[-1])
                try:
                    # And whoever hands notes over is told, likewise without
                    # the one that asked for it.
                    loop.call_soon_threadsafe(self._note_written)
                except RuntimeError:
                    pass  # The loop is gone: nobody is left to hand anything over.

        loop = asyncio.get_running_loop()
        return await self.run(work, written=on_record)

    async def ask_add(
        self,
        *,
        from_session: str,
        from_commission: str,
        to_session: str,
        body: str,
        per_hour: int,
        asks_per_hour: int,
    ) -> tuple[Message | None, str, float]:
        """Put on record that a session asks another a question; ``(message, refusal, wait)``.

        Counted from what is on record, as a note is (``message_add``):
        ``refusal`` is ``"over"`` (the commission that asks is not under
        way any more), ``"too_many"`` (the session wrote ``per_hour``
        notes and questions within the hour), ``"too_many_asks"`` (it
        asked ``asks_per_hour`` questions within the hour), or empty with
        the message; ``wait`` says how many seconds are left. Written as
        delivered: nothing hands it over, a copy of the other session
        answers it (``TaskAgentManager._cb_session_ask``).
        """

        def work(conn: sqlite3.Connection) -> tuple[Message | None, str, float]:
            now = self._clock()
            writer = conn.execute("SELECT status FROM commissions WHERE id = ?", (from_commission,)).fetchone()
            if writer is None or writer["status"] not in ("working", "waiting_user"):
                return None, "over", 0.0
            wait = _hour_used(conn, from_session, ("note", "ask"), now, per_hour)
            if wait is not None:
                return None, "too_many", wait
            wait = _hour_used(conn, from_session, ("ask",), now, asks_per_hour)
            if wait is not None:
                return None, "too_many_asks", wait
            message = self._message_insert(
                conn,
                {
                    "from_session": from_session,
                    "from_commission": from_commission,
                    "to_session": to_session,
                    "kind": "ask",
                    "body": body,
                    "status": "delivered",
                    "created_at": now,
                    "delivered_at": now,
                },
            )
            self._event(conn, from_commission, "session_asked", {"message_id": message.id, "to_session": to_session})
            return message, "", 0.0

        return await self.run(work)

    async def message_void(self, message_id: int, reason: str) -> None:
        """Put on record that a message comes to nothing (a question that was not asked after all)."""
        await self.run(
            lambda conn: conn.execute(
                "UPDATE messages SET status = 'void', status_reason = ? WHERE id = ?", (str(reason), int(message_id))
            )
        )

    async def message_get(self, message_id: int) -> Message | None:
        return await self.run(
            lambda conn: _message(conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone())
        )

    async def messages_opening(self, commission_id: str) -> list[Message]:
        """Return the notes a commission was filed for, in the order they were written.

        A commission that is filed because a session was written to
        (``source`` ``"session"``) is about what was written: the notes
        that were handed to it as it was filed (``create_for_wakes``),
        which its first ``woken`` event names. What was handed to it
        later is not among them, whenever that was. Nor is a note that is
        its own no longer: one it never took (its opening message was
        refused, say) was put back and handed to another commission, and
        is not said again in this one.
        """

        def work(conn: sqlite3.Connection) -> list[Message]:
            ids = self._opening_wakes(conn, commission_id)
            if not ids:
                return []
            rows = conn.execute(
                "SELECT messages.* FROM messages JOIN wake_requests ON wake_requests.message_id = messages.id "
                f"WHERE wake_requests.id IN ({', '.join('?' for _ in ids)}) AND wake_requests.reason = 'message' "
                "AND messages.kind = 'note' AND messages.delivered_to = ? ORDER BY messages.id",
                [*ids, commission_id],
            ).fetchall()
            return [item for item in (_message(row) for row in rows) if item]

        return await self.run(work)

    async def turn_over(
        self,
        commission_id: str,
        *,
        word: Callable[[Message], str],
        state: str = "",
        taken: Iterable[int] = (),
        only: Iterable[int] | None = None,
    ) -> int:
        """Tell the senders who asked for it that the turn their notes began in a commission is over.

        For every note that was handed to the commission, whose sender
        asked to be told and was not yet: a ``done`` message from this
        commission's session to the sender's, with a wake request of the
        sending commission's own, due now; and the request that would
        have told the sender it waited in vain is dropped. A sender whose
        commission is over is told nothing. ``word`` words the message
        for one note; ``state`` says how the commission stood when the
        turn ended, and is kept with the request (its ``note``), since by
        the time it is handed over the commission may be at something
        else. ``taken`` are wake requests the session is known to have
        taken that could not be put on record as such when it took them:
        they are, here, before the notes are looked at, so that a note
        the turn did answer is told of. ``only`` are the wake requests
        whose notes the turn took, where the caller knows them: none
        but those is told of. A turn whose end could not be written when
        it came is then told of later with its own notes alone, and
        takes none of a turn that came after it; nor does a later turn,
        written first, take its. Returns how many were written. No model
        is called for any of it.
        """

        confirmed = [int(item) for item in taken]
        among = None if only is None else sorted({int(item) for item in only})

        def work(conn: sqlite3.Connection) -> int:
            now = self._clock()
            if confirmed:
                self._taken(conn, confirmed, now)
            # (With none of them given, the list is empty, and no note is among them.)
            of_these = "" if among is None else f" AND wake_requests.id IN ({', '.join('?' for _ in among)})"
            asked = [commission_id, *(among or ())]
            # Those its session took. A note that was handed over and is
            # still on its way (steered in after the run's last request,
            # say) was not answered by this turn: it is told of when the
            # turn that takes it is over, or put back with the session.
            rows = conn.execute(
                "SELECT * FROM messages WHERE delivered_to = ? AND notify_when_done = 1 "
                "AND notified_at IS NULL AND kind = 'note' AND EXISTS ("
                "SELECT 1 FROM wake_requests WHERE wake_requests.message_id = messages.id "
                "AND wake_requests.reason = 'message' AND wake_requests.status = 'fired' "
                f"AND wake_requests.taken_at IS NOT NULL{of_these}) ORDER BY id",
                asked,
            ).fetchall()
            written = 0
            for row in rows:
                note = _message(row)
                assert note is not None
                conn.execute("UPDATE messages SET notified_at = ? WHERE id = ?", (now, note.id))
                # The word that it waited in vain is not said any more: not
                # one that waits, nor one that is on its way to the sender
                # and may yet be put back (said after this, it would be
                # wrong; taken already, the note counts as answered above).
                conn.execute(
                    "UPDATE wake_requests SET status = 'void', status_reason = 'word of it came' "
                    "WHERE message_id = ? AND reason = 'notice_expiry' "
                    "AND (status = 'pending' OR (status = 'fired' AND taken_at IS NULL))",
                    (note.id,),
                )
                sender = (
                    conn.execute("SELECT status FROM commissions WHERE id = ?", (note.from_commission,)).fetchone()
                    if note.from_commission
                    else None
                )
                if sender is None or sender["status"] not in ACTIVE_STATUSES - {"blocked"}:
                    continue
                notice = self._message_insert(
                    conn,
                    {
                        "from_session": note.to_session,
                        "from_commission": commission_id,
                        "to_session": note.from_session,
                        "kind": "done",
                        "body": word(note),
                        "depth": note.depth,
                    },
                )
                self._wake_insert(
                    conn,
                    {
                        "session_id": note.from_session,
                        "commission_id": note.from_commission,
                        "reason": "message",
                        "message_id": notice.id,
                        "note": state,
                    },
                )
                written += 1
            return written

        return await self.run(work)

    async def wake_add(
        self,
        *,
        session_id: str,
        commission_id: str,
        note: str,
        due_at: float,
        depth: int = 0,
        pending_max: int,
        daily_max: int,
    ) -> tuple[WakeRequest | None, str]:
        """Put on record that a commission asked to be woken at a time; ``(request, refusal)``.

        ``refusal`` is ``"too_many_pending"`` when the session has
        ``pending_max`` such times set already, ``"daily_limit"`` when it
        was woken at its own asking, or is to be, ``daily_max`` times
        within a day, ``"over"`` when the commission is not under way; or
        empty with the request. Counted here, from what is on record.
        """

        def work(conn: sqlite3.Connection) -> tuple[WakeRequest | None, str]:
            now = self._clock()
            row = conn.execute("SELECT status FROM commissions WHERE id = ?", (commission_id,)).fetchone()
            if row is None or row["status"] not in ("working", "waiting_user"):
                return None, "over"
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM wake_requests WHERE session_id = ? AND reason = 'timer' AND status = 'pending'",
                (session_id,),
            ).fetchone()
            if pending["n"] >= pending_max:
                return None, "too_many_pending"
            fired = conn.execute(
                "SELECT COUNT(*) AS n FROM wake_requests WHERE session_id = ? AND reason = 'timer' "
                "AND status = 'fired' AND fired_at >= ?",
                (session_id, now - 86400.0),
            ).fetchone()
            if fired["n"] + pending["n"] >= daily_max:
                return None, "daily_limit"
            wake = self._wake_insert(
                conn,
                {
                    "session_id": session_id,
                    "commission_id": commission_id,
                    "reason": "timer",
                    "note": note,
                    "due_at": float(due_at),
                    "depth": int(depth),
                },
            )
            self._event(conn, commission_id, "wake_set", {"wake_id": wake.id, "due_at": wake.due_at, "note": note})
            return wake, ""

        def on_record(result: tuple[WakeRequest | None, str]) -> None:
            # Whoever hands over what is due is told as soon as the time is
            # on record, as for a note (``message_add``): not by whoever
            # asked for it, who may have been cancelled meanwhile (a tool
            # call of dsh that timed out) while the time was written all
            # the same.
            if result[0] is not None:
                try:
                    loop.call_soon_threadsafe(self._note_written)
                except RuntimeError:
                    pass  # The loop is gone: nobody is left to hand anything over.

        loop = asyncio.get_running_loop()
        return await self.run(work, written=on_record)

    async def wakes_next_due(self, *, after: float | None = None) -> float | None:
        """Return when the first pending wake request is due, or None when none is pending.

        With ``after``, the first that is due later than that: what is due
        already and kept back (its session waits for the user, say) does
        not stand in front of what is still to come.
        """

        def work(conn: sqlite3.Connection) -> float | None:
            if after is None:
                row = conn.execute("SELECT MIN(due_at) AS due FROM wake_requests WHERE status = 'pending'").fetchone()
            else:
                row = conn.execute(
                    "SELECT MIN(due_at) AS due FROM wake_requests WHERE status = 'pending' AND due_at > ?", (after,)
                ).fetchone()
            return None if row is None or row["due"] is None else float(row["due"])

        return await self.run(work)

    async def wakes_due(
        self, *, due_by: float, limit: int = 1000, after: tuple[float, int] | None = None
    ) -> list[WakeRequest]:
        """Return the pending wake requests due by a time, the first due first, ``limit`` at most.

        With ``after`` (when the last one of the rows before was due, and
        its id), those that follow it: for reading on where there are
        more than ``limit``.
        """

        def work(conn: sqlite3.Connection) -> list[WakeRequest]:
            if after is None:
                rows = conn.execute(
                    "SELECT * FROM wake_requests WHERE status = 'pending' AND due_at <= ? ORDER BY due_at, id LIMIT ?",
                    (due_by, int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM wake_requests WHERE status = 'pending' AND due_at <= ? "
                    "AND (due_at > ? OR (due_at = ? AND id > ?)) ORDER BY due_at, id LIMIT ?",
                    (due_by, float(after[0]), float(after[0]), int(after[1]), int(limit)),
                ).fetchall()
            return [item for item in (_wake(row) for row in rows) if item]

        return await self.run(work)

    async def wakes_pending(
        self, *, commission_id: str | None = None, session_id: str | None = None
    ) -> list[WakeRequest]:
        """Return the pending wake requests of a commission's own, or of everything for a session; the first due first."""

        def work(conn: sqlite3.Connection) -> list[WakeRequest]:
            if commission_id is not None:
                rows = conn.execute(
                    "SELECT * FROM wake_requests WHERE commission_id = ? AND status = 'pending' ORDER BY due_at, id",
                    (commission_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM wake_requests WHERE session_id = ? AND status = 'pending' ORDER BY due_at, id",
                    (session_id,),
                ).fetchall()
            return [item for item in (_wake(row) for row in rows) if item]

        return await self.run(work)

    async def messages_of(self, wake_ids: Iterable[int]) -> dict[int, Message]:
        """Return the messages that wake requests are to hand over, by the id of the request."""
        ids = [int(item) for item in wake_ids]
        if not ids:
            return {}

        def work(conn: sqlite3.Connection) -> dict[int, Message]:
            rows = conn.execute(
                "SELECT wake_requests.id AS wake_id, messages.* FROM wake_requests "
                "JOIN messages ON messages.id = wake_requests.message_id "
                f"WHERE wake_requests.id IN ({', '.join('?' for _ in ids)})",
                ids,
            ).fetchall()
            found: dict[int, Message] = {}
            for row in rows:
                message = _message(row)
                if message is not None:
                    found[int(row["wake_id"])] = message
            return found

        return await self.run(work)

    def _fire(self, conn: sqlite3.Connection, ids: list[int], into: str) -> list[int]:
        """Put the pending ones of some wake requests on record as handed to a commission; returns which."""
        if not ids:
            return []
        now = self._clock()
        # Counted apart from when it was (``fired_at``, which the counts of
        # a day go by): what was handed over before a given moment is told
        # from what was handed over since by this (``fired_mark``),
        # whatever the clock of the computer does in between.
        order = self._fired_high = self._fired_high + 1
        marks = ", ".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT * FROM wake_requests WHERE id IN ({marks}) AND status = 'pending' ORDER BY id", ids
        ).fetchall()
        fired: list[int] = []
        for row in rows:
            if row["reason"] == "message" and row["message_id"] is not None:
                note = conn.execute(
                    "SELECT status, status_reason FROM messages WHERE id = ?", (row["message_id"],)
                ).fetchone()
                if note is None or note["status"] == "void":
                    # What it was to hand over was dropped (by a stop of
                    # everything, say): it goes with it, and hands nothing over.
                    conn.execute(
                        "UPDATE wake_requests SET status = 'void', status_reason = ? WHERE id = ?",
                        (note["status_reason"] if note is not None else "its note is gone", row["id"]),
                    )
                    continue
            conn.execute(
                "UPDATE wake_requests SET status = 'fired', fired_at = ?, fired_order = ?, fired_into = ? WHERE id = ?",
                (now, order, into, row["id"]),
            )
            fired.append(int(row["id"]))
            if row["message_id"] is None:
                continue
            if row["reason"] == "message":
                conn.execute(
                    "UPDATE messages SET status = 'delivered', delivered_at = ?, delivered_to = ? "
                    "WHERE id = ? AND status = 'queued'",
                    (now, into, row["message_id"]),
                )
        if fired:
            self._event(conn, into, "woken", {"wake_ids": fired})
        return fired

    async def wakes_fire(self, wake_ids: Iterable[int], *, into: str) -> list[int]:
        """Put on record that wake requests were carried out: what they bring was handed to a commission.

        Only those still pending (returned): one that was dropped
        meanwhile, by a stop, stays dropped. A message one of them was to
        hand over is ``delivered``, to that commission. One that tells a
        sender it waited in vain marks the note as answered for, so the
        turn it began, ending late, tells nothing more.
        """
        ids = [int(item) for item in wake_ids]
        if not ids:
            return []
        return await self.run(lambda conn: self._fire(conn, ids, into))

    async def create_for_wakes(
        self, wake_ids: Iterable[int], *, wanted: Callable[[], bool] | None = None, **values: Any
    ) -> Commission | None:
        """File a commission for wake requests and put them on record as handed to it; None when nothing is filed.

        Together, or not at all: with the commission filed and its notes
        left pending (the second write failing), another would be filed
        for the same notes while the first waited for nobody. And where
        the requests were dropped meanwhile (everything was stopped),
        nothing is filed. Nor where ``wanted``, asked as the commission
        would be written, says it is wanted no longer (everything is being
        stopped, or the host is shutting down): the requests stay as they
        are.
        """
        unknown = set(values) - set(_COMMISSION_COLUMNS)
        if unknown:
            raise ValueError(f"unknown commission fields: {sorted(unknown)}")
        ids = [int(item) for item in wake_ids]

        def work(conn: sqlite3.Connection) -> Commission | None:
            if not ids or (wanted is not None and not wanted()):
                return None
            marks = ", ".join("?" for _ in ids)
            pending = conn.execute(
                f"SELECT 1 FROM wake_requests WHERE id IN ({marks}) AND status = 'pending' LIMIT 1", ids
            ).fetchone()
            if pending is None:
                return None
            commission = self._insert(conn, values)
            self._fire(conn, ids, commission.id)
            return commission

        commission = await self.run(work)
        if commission is not None:
            self._notify(commission, None)
        return commission

    def _unfire(self, conn: sqlite3.Connection, ids: list[int], stopped_upto: int | None, reason: str) -> None:
        """Make the wake requests among ``ids`` pending again that are on record as handed over and not as taken."""
        if not ids:
            return
        marks = ", ".join("?" for _ in ids)
        on_its_way = f"id IN ({marks}) AND status = 'fired' AND taken_at IS NULL"
        if stopped_upto is not None:
            upto = self._upto(stopped_upto)
            stopped = f"{on_its_way} AND commission_id IS NULL AND id <= ?"
            conn.execute(
                "UPDATE messages SET status = 'void', status_reason = ?, delivered_at = NULL, delivered_to = NULL "
                "WHERE status = 'delivered' AND kind = 'note' AND id IN "
                f"(SELECT message_id FROM wake_requests WHERE {stopped} "
                "AND reason = 'message' AND message_id IS NOT NULL)",
                [reason, *ids, upto],
            )
            conn.execute(
                "UPDATE wake_requests SET status = 'void', status_reason = ?, "
                f"fired_at = NULL, fired_order = NULL, fired_into = NULL WHERE {stopped}",
                [reason, *ids, upto],
            )
        # What a commission asked for itself (a time it set, word of a note
        # it sent), where that commission is over meanwhile: dropped, as
        # what was still pending was when it ended. Made pending again, it
        # would be handed to the commission once more should the user take
        # it up again, as a wake of a round that is over.
        over = f"{on_its_way} AND commission_id IN (SELECT id FROM commissions WHERE status IN ({_ENDED_MARKS}))"
        conn.execute(
            "UPDATE messages SET status = 'void', status_reason = ?, delivered_at = NULL, delivered_to = NULL "
            "WHERE status = 'delivered' AND kind != 'note' AND id IN "
            f"(SELECT message_id FROM wake_requests WHERE {over} AND message_id IS NOT NULL)",
            [COMMISSION_OVER_REASON, *ids, *sorted(ENDED_STATUSES)],
        )
        conn.execute(
            "UPDATE wake_requests SET status = 'void', status_reason = ?, "
            f"fired_at = NULL, fired_order = NULL, fired_into = NULL WHERE {over}",
            [COMMISSION_OVER_REASON, *ids, *sorted(ENDED_STATUSES)],
        )
        conn.execute(
            "UPDATE messages SET status = 'queued', delivered_at = NULL, delivered_to = NULL "
            "WHERE status = 'delivered' AND id IN "
            f"(SELECT message_id FROM wake_requests WHERE {on_its_way} "
            "AND reason = 'message' AND message_id IS NOT NULL)",
            ids,
        )
        conn.execute(
            "UPDATE wake_requests SET status = 'pending', fired_at = NULL, fired_order = NULL, fired_into = NULL "
            f"WHERE {on_its_way}",
            ids,
        )

    async def wakes_unfire(self, wake_ids: Iterable[int], *, stopped_upto: int | None = None, reason: str = "") -> None:
        """Make wake requests pending again that were put on record as handed over and were not taken.

        Their text went into a session that closed before taking it, or
        the session could not take it after all. The message one of them
        was to hand over is queued again. They are handed over anew, to
        whatever their session is at by then; one whose commission is
        over meanwhile is dropped there. One that is on record as taken
        stays as it is: what a session has in its conversation is not
        handed over twice.

        A stop of everything drops the notes that wait. With
        ``stopped_upto`` (what ``notes_mark`` said when everything was last
        stopped), a note written by then is not made pending behind the
        stop's back: it is dropped, with ``reason``, as the stop would
        have dropped it had it still been waiting.
        """
        ids = [int(item) for item in wake_ids]
        if not ids:
            return
        await self.run(lambda conn: self._unfire(conn, ids, stopped_upto, reason))

    async def wakes_taken(self, wake_ids: Iterable[int]) -> None:
        """Put on record that what wake requests brought is in the conversation of the session it was handed to."""
        ids = [int(item) for item in wake_ids]
        if not ids:
            return

        await self.run(lambda conn: self._taken(conn, ids, self._clock()))

    @staticmethod
    def _taken(conn: sqlite3.Connection, ids: list[int], now: float) -> None:
        marks = ", ".join("?" for _ in ids)
        # A sender that has the word that it waited in vain is answered: the
        # note is, from here on, one its sender was told of. Not before: until
        # it has that word, the turn the note began may still end and tell it
        # what came of it instead.
        conn.execute(
            "UPDATE messages SET notified_at = ? WHERE notified_at IS NULL AND id IN "
            f"(SELECT message_id FROM wake_requests WHERE id IN ({marks}) AND status = 'fired' "
            "AND taken_at IS NULL AND reason = 'notice_expiry' AND message_id IS NOT NULL)",
            [now, *ids],
        )
        conn.execute(
            f"UPDATE wake_requests SET taken_at = ? WHERE id IN ({marks}) AND status = 'fired' AND taken_at IS NULL",
            [now, *ids],
        )

    async def opening_wakes(self, commission_id: str) -> list[int]:
        """Return the wake requests a commission was filed for that are still its own: those its opening message brings."""

        def work(conn: sqlite3.Connection) -> list[int]:
            ids = self._opening_wakes(conn, commission_id)
            if not ids:
                return []
            rows = conn.execute(
                f"SELECT id FROM wake_requests WHERE id IN ({', '.join('?' for _ in ids)}) "
                "AND status = 'fired' AND fired_into = ? ORDER BY id",
                [*ids, commission_id],
            ).fetchall()
            return [int(row["id"]) for row in rows]

        return await self.run(work)

    async def opening_handed_on(self, commission_id: str) -> bool:
        """Whether a commission was filed for notes of which none is its own any more.

        They were put back and handed to another commission, or dropped:
        the commission, taken up again, is not told them a second time.
        """

        def work(conn: sqlite3.Connection) -> bool:
            ids = self._opening_wakes(conn, commission_id)
            if not ids:
                return False
            own = conn.execute(
                f"SELECT 1 FROM wake_requests WHERE id IN ({', '.join('?' for _ in ids)}) "
                "AND status = 'fired' AND fired_into = ? LIMIT 1",
                [*ids, commission_id],
            ).fetchone()
            return own is None

        return await self.run(work)

    def fired_mark(self) -> int:
        """Return a mark of all that is on record as handed over so far. Not blocking.

        What is handed over from here on is later than the mark, also
        where the clock was set back meanwhile (``_fire`` counts, and does
        not go by the time): asked as a commission ends, it tells what the
        commission was handed until then from what it is handed once it
        is taken up again.
        """
        return self._fired_high

    async def wakes_on_their_way(self, commission_id: str, by: int) -> list[int]:
        """Return the wake requests that are on record as handed to a commission and not as taken.

        Those handed over by ``by`` (a ``fired_mark``): what a commission
        was handed before it ended, and not what it is handed once it is
        taken up again.
        """

        def work(conn: sqlite3.Connection) -> list[int]:
            rows = conn.execute(
                "SELECT id FROM wake_requests WHERE fired_into = ? AND status = 'fired' AND taken_at IS NULL "
                "AND COALESCE(fired_order, 0) <= ? ORDER BY id",
                (commission_id, int(by)),
            ).fetchall()
            return [int(row["id"]) for row in rows]

        return await self.run(work)

    @staticmethod
    def _opening_wakes(conn: sqlite3.Connection, commission_id: str) -> list[int]:
        # What was handed to a commission as it was filed is what its first
        # ``woken`` event names.
        first = conn.execute(
            "SELECT payload FROM events WHERE commission_id = ? AND type = 'woken' ORDER BY seq LIMIT 1",
            (commission_id,),
        ).fetchone()
        named = _load(first["payload"]).get("wake_ids") if first is not None else None
        return [int(item) for item in named or [] if isinstance(item, int) and not isinstance(item, bool)]

    async def wakes_void(self, wake_ids: Iterable[int], reason: str) -> None:
        """Drop pending wake requests, and the messages they were to hand over, with the reason."""
        ids = [int(item) for item in wake_ids]
        if not ids:
            return

        def work(conn: sqlite3.Connection) -> None:
            marks = ", ".join("?" for _ in ids)
            conn.execute(
                "UPDATE messages SET status = 'void', status_reason = ? WHERE status = 'queued' AND id IN "
                f"(SELECT message_id FROM wake_requests WHERE id IN ({marks}) AND status = 'pending' "
                "AND reason = 'message' AND message_id IS NOT NULL)",
                [reason, *ids],
            )
            conn.execute(
                f"UPDATE wake_requests SET status = 'void', status_reason = ? WHERE id IN ({marks}) "
                "AND status = 'pending'",
                [reason, *ids],
            )

        await self.run(work)

    async def wakes_drop(self, wake_ids: Iterable[int], reason: str) -> None:
        """Drop wake requests that wait or are on their way, and the messages they were to hand over, with the reason.

        Like ``wakes_void``, and also for one that is on record as handed
        over and not as taken: what it was to bring cannot be said (it is
        gone from the record), so it is neither left as handed over nor
        handed over again.
        """
        ids = [int(item) for item in wake_ids]
        if not ids:
            return

        def work(conn: sqlite3.Connection) -> None:
            marks = ", ".join("?" for _ in ids)
            open_ones = f"id IN ({marks}) AND (status = 'pending' OR (status = 'fired' AND taken_at IS NULL))"
            conn.execute(
                "UPDATE messages SET status = 'void', status_reason = ?, delivered_at = NULL, delivered_to = NULL "
                "WHERE status IN ('queued', 'delivered') AND id IN "
                f"(SELECT message_id FROM wake_requests WHERE {open_ones} "
                "AND reason = 'message' AND message_id IS NOT NULL)",
                [reason, *ids],
            )
            conn.execute(
                f"UPDATE wake_requests SET status = 'void', status_reason = ? WHERE {open_ones}", [reason, *ids]
            )

        await self.run(work)

    def _void_all(self, conn: sqlite3.Connection, reason: str, upto: int | None) -> int:
        """Drop the notes that wait, and those that are on their way; returns how many requests went with them."""
        # Which notes, where only those up to one are dropped, is told by
        # what hands them over: a note and that are written together, and
        # that is what is numbered in the order of writing (``notes_mark``).
        until = " AND id <= ?" if upto is not None else ""
        extra = [self._upto(upto)] if upto is not None else []
        theirs = "commission_id IS NULL AND reason = 'message' AND message_id IS NOT NULL"
        among = f" AND id IN (SELECT message_id FROM wake_requests WHERE {theirs}{until})" if upto is not None else ""
        conn.execute(
            f"UPDATE messages SET status = 'void', status_reason = ? WHERE status = 'queued' AND kind = 'note'{among}",
            [reason, *extra],
        )
        # On record as handed to a commission and not as taken by its
        # session: put back later, it would be pending behind the stop's
        # back, and a process that ends before that would leave it to be
        # handed over by the next. Dropped here, where the stop is written.
        on_its_way = "status = 'fired' AND taken_at IS NULL AND commission_id IS NULL AND reason = 'message'"
        conn.execute(
            "UPDATE messages SET status = 'void', status_reason = ?, delivered_at = NULL, delivered_to = NULL "
            "WHERE status = 'delivered' AND kind = 'note' AND id IN "
            f"(SELECT message_id FROM wake_requests WHERE {on_its_way} AND message_id IS NOT NULL{until})",
            [reason, *extra],
        )
        cursor = conn.execute(
            "UPDATE wake_requests SET status = 'void', status_reason = ? "
            f"WHERE commission_id IS NULL AND (status = 'pending' OR ({on_its_way})){until}",
            [reason, *extra],
        )
        return int(cursor.rowcount or 0)

    async def wakes_void_all(self, reason: str, *, upto: int | None = None) -> int:
        """Drop every note that waits to be handed over, and what would hand it over; returns how many.

        For a stop of everything. What is dropped here is what no
        commission asked for: the notes sessions wrote to one another,
        which would have a commission filed, or one taken up, after the
        stop. Those that wait, and those that are on record as handed to
        a commission whose session has not taken them: the stop ends that
        commission, and the note would be put back afterwards. What a
        commission asked for itself (a time it set, word of a note it
        sent) is dropped with that commission, in the transaction that
        ends it (``_transition``): the stop ends every commission under
        way, a resting one included, and one it could not end keeps what
        would wake it.

        With ``upto`` (what ``notes_mark`` said when the stop came), only
        the notes written by then: a stop is not to take what is written
        after it, also where its dropping is made up for later (the
        ledger could not be written when it came).
        """
        return await self.run(lambda conn: self._void_all(conn, reason, upto))

    async def waiting_view(self, commission_id: str) -> tuple[Commission | None, list[WakeRequest], int]:
        """Return a commission, what it asked to be woken for, and how many notes wait for its session, read together.

        For telling of it in one line: whether it rests and what would
        wake it are read in one look, so that the line does not say of a
        commission that was just woken that it rests until the next of
        its times.
        """

        def work(conn: sqlite3.Connection) -> tuple[Commission | None, list[WakeRequest], int]:
            commission = _commission(
                conn.execute("SELECT * FROM commissions WHERE id = ?", (commission_id,)).fetchone()
            )
            if commission is None:
                return None, [], 0
            own = conn.execute(
                "SELECT * FROM wake_requests WHERE commission_id = ? AND status = 'pending' ORDER BY due_at, id",
                (commission_id,),
            ).fetchall()
            notes = 0
            if commission.session_id:
                notes = int(
                    conn.execute(
                        "SELECT COUNT(*) AS n FROM wake_requests WHERE session_id = ? AND status = 'pending' "
                        "AND commission_id IS NULL AND reason = 'message'",
                        (commission.session_id,),
                    ).fetchone()["n"]
                )
            return commission, [item for item in (_wake(row) for row in own) if item], notes

        return await self.run(work)

    async def forks_of(self, session_id: str) -> list[Session]:
        """Return the sessions that were made from a session by copying what was said in it, the oldest first."""

        def work(conn: sqlite3.Connection) -> list[Session]:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE forked_from = ? ORDER BY created_at, rowid", (session_id,)
            ).fetchall()
            return [item for item in (_session(row) for row in rows) if item]

        return await self.run(work)

    async def begin_compacting(self, session_id: str) -> bool:
        """Write a closed session as being summed up by the host; False for one that is not closed.

        Looked at and written together: a session a commission has got to
        work in since, or one that holds nothing any more, stays as it is.
        """

        def work(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                "UPDATE sessions SET status = 'compacting' WHERE id = ? AND status = 'closed'", (session_id,)
            )
            return cursor.rowcount > 0

        return await self.run(work)

    async def end_compacting(self, session_id: str, *, compacted: bool) -> None:
        """Write a session the host had summed up as closed again, and when it was summed up if it was.

        Only one that still stands as being summed up: a commission that
        got to work in it meanwhile has written where it stands, and that
        is not written over.
        """

        def work(conn: sqlite3.Connection) -> None:
            conn.execute("UPDATE sessions SET status = 'closed' WHERE id = ? AND status = 'compacting'", (session_id,))
            if compacted:
                conn.execute("UPDATE sessions SET compacted_at = ? WHERE id = ?", (self._clock(), session_id))

        await self.run(work)

    # -- usage ------------------------------------------------------------

    @staticmethod
    def _root(conn: sqlite3.Connection, commission_id: str) -> sqlite3.Row | None:
        """Return the commission no task agent started that a commission descends from (itself, if it is one)."""
        seen: set[str] = set()
        current: str | None = commission_id
        row = None
        while current and current not in seen:
            seen.add(current)
            found = conn.execute("SELECT * FROM commissions WHERE id = ?", (current,)).fetchone()
            if found is None:
                break
            row = found
            current = found["parent_id"]
        return row

    async def add_usage(
        self,
        *,
        session_id: str | None = None,
        commission_id: str | None = None,
        requests: int = 1,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
        context_tokens: int | None = None,
    ) -> None:
        """Add what model requests used to a session and to the commission they were made for.

        With the session (``session_id``), and with the commission
        (``commission_id``) and every commission it descends from: what a
        task agent started by another uses is that one's as well, up to
        the commission no task agent started. Either may be left out: a
        request made for no commission (the host summing up a session
        nothing is at work in) is the session's alone. ``context_tokens``,
        when given, is how long the session's conversation was as of the
        request. Nothing else of a commission changes, and nobody is told.
        """
        # The columns that are counted, and no other: in the order of ``_USAGE_COLUMNS``.
        amounts = (requests, tokens_in, tokens_out, cache_read, cache_write)
        added = {name: max(0, int(amount)) for name, amount in zip(_USAGE_COLUMNS, amounts, strict=True)}
        assignments = ", ".join(f"{name} = {name} + ?" for name in added)

        def work(conn: sqlite3.Connection) -> None:
            if session_id:
                conn.execute(f"UPDATE sessions SET {assignments} WHERE id = ?", [*added.values(), session_id])
                if context_tokens is not None and context_tokens > 0:
                    conn.execute(
                        "UPDATE sessions SET context_tokens = ? WHERE id = ?", (int(context_tokens), session_id)
                    )
            seen: set[str] = set()
            current = commission_id
            while current and current not in seen:
                seen.add(current)
                conn.execute(f"UPDATE commissions SET {assignments} WHERE id = ?", [*added.values(), current])
                row = conn.execute("SELECT parent_id FROM commissions WHERE id = ?", (current,)).fetchone()
                current = row["parent_id"] if row is not None else None

        await self.run(work)

    async def usage_standing(self, commission_id: str) -> UsageStanding | None:
        """Return how much the work a commission belongs to has used and how much it may; None for an unknown one."""

        def work(conn: sqlite3.Connection) -> UsageStanding | None:
            root = _commission(self._root(conn, commission_id))
            if root is None:
                return None
            return UsageStanding(root.id, root.tokens, root.usage_allowed)

        return await self.run(work)

    async def allow_usage(self, commission_id: str, tokens: int) -> UsageStanding | None:
        """Let the work a commission belongs to use ``tokens`` more than it has used by now.

        Written with the commission no task agent started, from what it
        has used when this is written: what was used while the user was
        being asked is not taken from what they allowed.
        """

        def work(conn: sqlite3.Connection) -> UsageStanding | None:
            root = _commission(self._root(conn, commission_id))
            if root is None:
                return None
            allowed = root.tokens + max(0, int(tokens))
            conn.execute("UPDATE commissions SET usage_allowed = ? WHERE id = ?", (allowed, root.id))
            self._event(conn, root.id, "usage_allowed", {"used": root.tokens, "allowed": allowed})
            return UsageStanding(root.id, root.tokens, allowed)

        return await self.run(work)

    # -- events -----------------------------------------------------------

    def _event(self, conn: sqlite3.Connection, commission_id: str, kind: str, payload: dict[str, Any]) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM events WHERE commission_id = ?", (commission_id,)
        ).fetchone()
        seq = int(row["next"])
        conn.execute(
            "INSERT INTO events (commission_id, seq, ts, type, payload) VALUES (?, ?, ?, ?, ?)",
            (commission_id, seq, self._clock(), kind, _dump(payload)),
        )
        return seq

    async def append_event(self, commission_id: str, kind: str, payload: dict[str, Any] | None = None) -> int:
        """Append one event to a commission and return its sequence number."""
        return await self.run(lambda conn: self._event(conn, commission_id, kind, payload or {}))

    async def append_events(self, commission_id: str, items: list[tuple[str, dict[str, Any]]]) -> None:
        """Append several events in one transaction."""
        if not items:
            return

        def work(conn: sqlite3.Connection) -> None:
            for kind, payload in items:
                self._event(conn, commission_id, kind, payload)

        await self.run(work)

    async def ended_lately(self, lanlan_name: str, *, since: float, limit: int) -> list[Commission]:
        """Return her commissions a task agent worked on in a session that ended since ``since``, the last to end first.

        Done, failed or stopped; her own (not one a task agent started for
        itself). Chosen and ordered here, before the limit: taken from the
        newest by when they were filed, one that was long at work and has
        just ended would be left out behind those filed after it.
        """
        statuses = sorted(ENDED_STATUSES - {"blocked"})

        def work(conn: sqlite3.Connection) -> list[Commission]:
            rows = conn.execute(
                "SELECT * FROM commissions WHERE lanlan_name = ? AND route = 'task_agent' "
                "AND parent_id IS NULL AND session_id IS NOT NULL "
                f"AND status IN ({', '.join('?' for _ in statuses)}) AND updated_at >= ? "
                "ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (lanlan_name, *statuses, since, int(limit)),
            ).fetchall()
            return [item for item in (_commission(row) for row in rows) if item]

        return await self.run(work)

    async def opened_last(self, session_id: str) -> str | None:
        """Return the commission whose opening message a session took last; None when it took none.

        A session is at one commission at a time, and which one is told by
        the opening message it took last. A commission that got stuck lets
        its session go on with the next; taken up again after that, it is
        no longer the one its session is at.
        """

        def work(conn: sqlite3.Connection) -> str | None:
            row = conn.execute(
                "SELECT events.commission_id FROM events "
                "JOIN commissions ON commissions.id = events.commission_id "
                "WHERE commissions.session_id = ? AND events.type = 'opened' "
                "ORDER BY events.rowid DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            return str(row["commission_id"]) if row is not None else None

        return await self.run(work)

    async def has_event(self, commission_id: str, kind: str) -> bool:
        """Return whether a commission has an event of a kind."""
        return await self.run(
            lambda conn: conn.execute(
                "SELECT 1 FROM events WHERE commission_id = ? AND type = ? LIMIT 1", (commission_id, kind)
            ).fetchone()
            is not None
        )

    async def events(self, commission_id: str, *, after: int = 0, limit: int = 500) -> list[LedgerEvent]:
        def work(conn: sqlite3.Connection) -> list[LedgerEvent]:
            rows = conn.execute(
                "SELECT * FROM events WHERE commission_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (commission_id, after, limit),
            ).fetchall()
            return [
                LedgerEvent(row["commission_id"], row["seq"], row["ts"], row["type"], _load(row["payload"]))
                for row in rows
            ]

        return await self.run(work)

    # -- outbox -----------------------------------------------------------

    def _outbox_insert(
        self, conn: sqlite3.Connection, commission_id: str | None, kind: str, payload: dict[str, Any]
    ) -> int:
        now = self._clock()
        cursor = conn.execute(
            "INSERT INTO outbox (commission_id, kind, payload, created_at, next_attempt_at) VALUES (?, ?, ?, ?, ?)",
            (commission_id, kind, _dump(payload), now, now),
        )
        return int(cursor.lastrowid or 0)

    async def outbox_add(self, commission_id: str | None, kind: str, payload: dict[str, Any]) -> int:
        """Store a report for the main server; it is sent afterwards."""
        return await self.run(lambda conn: self._outbox_insert(conn, commission_id, kind, payload))

    @staticmethod
    def _outbox_item(row: sqlite3.Row) -> OutboxItem:
        return OutboxItem(
            id=row["id"],
            commission_id=row["commission_id"],
            kind=row["kind"],
            payload=_load(row["payload"]),
            created_at=row["created_at"],
            attempts=row["attempts"],
            next_attempt_at=row["next_attempt_at"],
            sent_at=row["sent_at"],
            last_error=row["last_error"],
        )

    async def outbox_pending(self, *, due_by: float | None = None, limit: int = 50) -> list[OutboxItem]:
        """Return unsent reports, oldest first; with ``due_by`` only those due by then."""

        def work(conn: sqlite3.Connection) -> list[OutboxItem]:
            sql = "SELECT * FROM outbox WHERE sent_at IS NULL"
            params: list[Any] = []
            if due_by is not None:
                sql += " AND next_attempt_at <= ?"
                params.append(due_by)
            sql += " ORDER BY id LIMIT ?"
            params.append(limit)
            return [self._outbox_item(row) for row in conn.execute(sql, params).fetchall()]

        return await self.run(work)

    async def outbox_next_due(self) -> float | None:
        """Return the earliest ``next_attempt_at`` of all unsent reports, or None."""

        def work(conn: sqlite3.Connection) -> float | None:
            row = conn.execute("SELECT MIN(next_attempt_at) AS due FROM outbox WHERE sent_at IS NULL").fetchone()
            return None if row is None or row["due"] is None else float(row["due"])

        return await self.run(work)

    async def outbox_question_state(self, item_id: int) -> str:
        """Say whether an unsent question may still go out, read in one transaction.

        ``"current"``: it is stored and unsent and its commission waits.
        ``"gone"``: it was removed or sent already. ``"stale"``: it is still
        stored, but its commission no longer waits.
        """

        def work(conn: sqlite3.Connection) -> str:
            row = conn.execute(
                "SELECT c.status AS status FROM outbox o LEFT JOIN commissions c ON c.id = o.commission_id "
                "WHERE o.id = ? AND o.sent_at IS NULL",
                (item_id,),
            ).fetchone()
            if row is None:
                return "gone"
            return "current" if row["status"] == "waiting_user" else "stale"

        return await self.run(work)

    async def outbox_drop(self, item_id: int) -> None:
        """Remove an unsent report that is out of date."""
        await self.run(lambda conn: conn.execute("DELETE FROM outbox WHERE id = ? AND sent_at IS NULL", (item_id,)))

    async def outbox_mark_sent(self, item_id: int) -> None:
        await self.run(
            lambda conn: conn.execute(
                "UPDATE outbox SET sent_at = ?, attempts = attempts + 1, last_error = NULL WHERE id = ?",
                (self._clock(), item_id),
            )
            and None
        )

    async def outbox_mark_failed(self, item_id: int, error: str, next_attempt_at: float) -> None:
        await self.run(
            lambda conn: conn.execute(
                "UPDATE outbox SET attempts = attempts + 1, last_error = ?, next_attempt_at = ? WHERE id = ?",
                (error, next_attempt_at, item_id),
            )
            and None
        )
