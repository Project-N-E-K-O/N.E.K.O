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

``<data root>/nekoclaw/ledger.db`` holds five tables:

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
import logging
import sqlite3
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

SOURCES: tuple[str, ...] = ("dispatch", "promised", "task_agent")
ROUTES: tuple[str, ...] = ("plugin", "task_agent")

# Why a commission left unfinished by an earlier host process failed.
HOST_ENDED_REASON = "the host process ended before this finished"

_SCHEMA_VERSION = 4
# The first version in which a commission is worked on in a session that is a
# row of its own; a ledger of an earlier one has its commissions given theirs.
_SESSIONS_SINCE_VERSION = 3
_BUSY_TIMEOUT_SECONDS = 5.0

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
    ("requests", "INTEGER NOT NULL DEFAULT 0"),
    ("tokens_in", "INTEGER NOT NULL DEFAULT 0"),
    ("tokens_out", "INTEGER NOT NULL DEFAULT 0"),
    ("tokens_cache_read", "INTEGER NOT NULL DEFAULT 0"),
    ("tokens_cache_write", "INTEGER NOT NULL DEFAULT 0"),
    ("usage_allowed", "INTEGER"),
)
# What is counted of the model requests, with a commission and with a session.
_USAGE_COLUMNS: tuple[str, ...] = ("requests", "tokens_in", "tokens_out", "tokens_cache_read", "tokens_cache_write")
# Columns ``update`` may write. The status is not among them, nor is what
# was used (``add_usage``) and what may be (``allow_usage``).
_UPDATABLE: frozenset[str] = (
    frozenset(_COMMISSION_COLUMNS)
    - {"id", "status", "created_at", "host_instance", "usage_allowed"}
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
            self._reconcile(conn)
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

    def _reconcile(self, conn: sqlite3.Connection) -> None:
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
        """
        conn.execute("BEGIN IMMEDIATE")
        try:
            marks = ", ".join("?" for _ in LIVE_SESSION_STATUSES)
            conn.execute(
                f"UPDATE sessions SET status = 'closed' WHERE status IN ({marks})", sorted(LIVE_SESSION_STATUSES)
            )
            rows = conn.execute(
                "SELECT id FROM commissions WHERE status IN ('queued','working','waiting_user') "
                "AND COALESCE(host_instance, '') != ? "
                "AND deferred_until IS NULL",
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
        if rows:
            logger.info("ledger: %d unfinished commission(s) of an earlier run marked failed", len(rows))

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

    async def run(self, work: Callable[[sqlite3.Connection], T]) -> T:
        """Run ``work(connection)`` on the ledger thread, inside one transaction."""
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
        assignments = ", ".join(f"{name} = ?" for name in changes)
        conn.execute(
            f"UPDATE commissions SET {assignments} WHERE id = ?",
            [*changes.values(), commission_id],
        )
        self._event(conn, commission_id, "status", {"from": current.status, "to": status, "reason": reason})
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
        """

        def work(conn: sqlite3.Connection) -> None:
            row = conn.execute("SELECT status FROM commissions WHERE id = ?", (commission_id,)).fetchone()
            status = row["status"] if row is not None else None
            if status in ("queued", "working"):
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
