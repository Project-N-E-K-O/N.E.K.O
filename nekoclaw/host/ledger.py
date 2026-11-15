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

``<data root>/nekoclaw/ledger.db`` holds four tables:

* ``commissions`` - one row per commission (design section 5).
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
    # transcript is on disk.
    # Queued again when no task agent place is free to take them up at once.
    "blocked": frozenset({"working", "queued", "failed", "cancelled"}),
    "failed": frozenset({"working", "queued"}),
    "done": frozenset(),
    # Stopped stays stopped.
    "cancelled": frozenset(),
}

SOURCES: tuple[str, ...] = ("dispatch", "promised", "task_agent")
ROUTES: tuple[str, ...] = ("plugin", "task_agent")

# Why a commission left unfinished by an earlier host process failed.
HOST_ENDED_REASON = "the host process ended before this finished"

_SCHEMA_VERSION = 2
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
    session_dir TEXT
);
CREATE INDEX IF NOT EXISTS commissions_by_owner ON commissions (lanlan_name, status);
CREATE INDEX IF NOT EXISTS commissions_by_parent ON commissions (parent_id);
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

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

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
_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (("session_dir", "TEXT"),)
# Columns ``update`` may write. The status is not among them.
_UPDATABLE: frozenset[str] = frozenset(_COMMISSION_COLUMNS) - {"id", "status", "created_at", "host_instance"}


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


def _commission(row: sqlite3.Row | None) -> Commission | None:
    if row is None:
        return None
    return Commission(**{name: row[name] for name in _COMMISSION_COLUMNS})


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
            conn.executescript(_SCHEMA)
            self._add_columns(conn)
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
        """Give a ledger made by an earlier build the columns added since (``_ADDED_COLUMNS``)."""
        present = {row["name"] for row in conn.execute("PRAGMA table_info(commissions)")}
        for name, kind in _ADDED_COLUMNS:
            if name not in present:
                conn.execute(f"ALTER TABLE commissions ADD COLUMN {name} {kind}")

    def _reconcile(self, conn: sqlite3.Connection) -> None:
        """Fail what a previous host process left unfinished.

        Nobody is working on those commissions any more. A blocked one stays
        blocked, and a deferred one stays as it is: until its deadline its
        plugin's result may still arrive, and past it the task brain's sweep
        fails it with the same report as when the host kept running.
        Questions of the failed ones that were never sent are dropped. With a
        ``reconcile_report``, the row it builds for a failed commission is
        stored along with the change, so she learns of it. All of it is one
        transaction, so no status changes without its event.
        """
        conn.execute("BEGIN IMMEDIATE")
        try:
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
        updated_since: float | None = None,
        limit: int | None = None,
        oldest_first: bool = False,
    ) -> list[Commission]:
        clauses: list[str] = []
        params: list[Any] = []
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
        ``updated_since``, ``limit``, ``oldest_first``.
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
