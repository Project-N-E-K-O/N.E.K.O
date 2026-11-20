# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Host-managed call records. Read locally with `uv run python -m nekoclaw.host.call_audit COMMISSION`.

The reader opens the existing database read-only under the user's filesystem
permissions. No HTTP route, model tool, plugin parameter or bearer token grants
access. Source references are correlation identifiers, never proof of authority.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nekoclaw.host.ledger import Ledger

logger = logging.getLogger(__name__)

RETENTION = 30 * 24 * 3600
# Identifiers are bounded below. Reserve the whole record (including a later
# run identifier and outcome) before execution. SQLite pages, WAL and indices
# are separate from this logical budget.
SOURCE_NOT_RECORDED = "source_not_recorded_capacity"
RECORD_BYTES = 1024
BUDGET_BYTES = 64 * 1024 * 1024
_SOURCE = re.compile(
    r"(?:conversation_input|chat_original|memory_message):[0-9a-f]{32}"
)
_CURRENT: ContextVar[Call | None] = ContextVar("nekoclaw_host_call", default=None)

SCHEMA = """
CREATE TABLE IF NOT EXISTS host_calls (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT NOT NULL UNIQUE, trace_id TEXT NOT NULL,
    parent_call_id TEXT, commission_id TEXT NOT NULL, session_id TEXT, kind TEXT NOT NULL,
    target TEXT NOT NULL, source_message_ref TEXT, run_id TEXT, status TEXT NOT NULL,
    started_at REAL NOT NULL, ended_at REAL, reason TEXT
);
CREATE INDEX IF NOT EXISTS host_calls_by_trace ON host_calls(trace_id, seq);
CREATE TABLE IF NOT EXISTS host_call_budget (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), records INTEGER NOT NULL, swept_at REAL
);
INSERT OR IGNORE INTO host_call_budget VALUES (1, 0, NULL);
CREATE TRIGGER IF NOT EXISTS host_calls_added AFTER INSERT ON host_calls BEGIN
    UPDATE host_call_budget SET records = records + 1 WHERE singleton=1;
END;
CREATE TRIGGER IF NOT EXISTS host_calls_removed AFTER DELETE ON host_calls BEGIN
    UPDATE host_call_budget SET records = records - 1 WHERE singleton=1;
END;
"""

# A parent may already be over while a child is still at work. Reclaim only
# whole trees whose last commission ended, including children with no calls.
_CLOSED = """
WITH RECURSIVE tree(root, id, status, ended_at) AS (
    SELECT c.id,c.id,c.status,c.ended_at FROM commissions c
    JOIN host_calls a ON a.call_id=c.id AND a.trace_id=c.id {root_filter}
    UNION
    SELECT t.root,c.id,c.status,c.ended_at FROM tree t JOIN commissions c ON c.parent_id=t.id
)
SELECT root, MAX(ended_at) AS closed_at FROM tree GROUP BY root
HAVING COUNT(*) = COUNT(ended_at) AND MIN(status IN ({ended})) = 1
AND NOT EXISTS (SELECT 1 FROM host_calls a WHERE a.trace_id=tree.root AND a.status='running')
"""


class CommissionEnded(RuntimeError):
    pass


class AuditFull(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "The host call audit is full of active work. No new action was started."
        )


def source_ref(value: Any) -> str | None:
    return value if isinstance(value, str) and _SOURCE.fullmatch(value) else None


def _identifier(value: str) -> str:
    # Registry identifiers only. Avoid control characters and oversized IDs.
    data = value.encode("utf-8")
    if len(data) <= 192 and value.isprintable():
        return value
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _closed_trees(conn: sqlite3.Connection, *, root: str | None = None) -> sqlite3.Cursor:
    from nekoclaw.host.ledger import ENDED_STATUSES

    ended = tuple(sorted(ENDED_STATUSES))
    query = _CLOSED.format(
        root_filter="WHERE a.call_id=?" if root is not None else "",
        ended=", ".join("?" for _ in ended),
    )
    params = (root, *ended) if root is not None else ended
    return conn.execute(query + " ORDER BY closed_at,root", params)


def reclaim(conn: sqlite3.Connection, now: float, *, reserve: int = 0, force: bool = False) -> None:
    count, swept = conn.execute(
        "SELECT records,swept_at FROM host_call_budget"
    ).fetchone()
    limit = BUDGET_BYTES // RECORD_BYTES
    if not force and count + reserve <= limit and swept is not None and 0 <= now - swept < 3600:
        return
    closed = _closed_trees(conn).fetchall()
    for root, ended_at in closed:
        if ended_at > now - RETENTION and count + reserve <= limit:
            break
        removed = conn.execute("DELETE FROM host_calls WHERE trace_id=?", (root,))
        count -= removed.rowcount
    conn.execute("UPDATE host_call_budget SET swept_at=?", (now,))
    if count + reserve > limit:
        raise AuditFull()


def _ancestry(conn: sqlite3.Connection, commission_id: str) -> list[sqlite3.Row]:
    chain = []
    seen = set()
    while commission_id and commission_id not in seen:
        seen.add(commission_id)
        row = conn.execute(
            "SELECT id,parent_id,session_id,source,created_at,status FROM commissions WHERE id=?",
            (commission_id,),
        ).fetchone()
        if row is None:
            break
        chain.append(row)
        commission_id = row["parent_id"]
    return chain


def _ensure_commissions(
    conn: sqlite3.Connection,
    commission_id: str,
    now: float,
    *,
    ref: str | None = None,
    reserve: int = 0,
) -> str:
    from nekoclaw.host.ledger import ENDED_STATUSES

    chain = _ancestry(conn, commission_id)
    if not chain:
        raise ValueError("unknown commission")
    if reserve and chain[0]["status"] in ENDED_STATUSES:
        raise CommissionEnded("this commission is no longer under way")
    root = chain[-1]["id"]
    missing = [
        row
        for row in reversed(chain)
        if not conn.execute(
            "SELECT 1 FROM host_calls WHERE call_id=?", (row["id"],)
        ).fetchone()
    ]
    reclaim(conn, now, reserve=len(missing) + reserve)
    for row in missing:
        origin = ref if row["id"] == commission_id else None
        reason = None
        if origin is None and row["source"] in {"dispatch", "promised"}:
            filed = conn.execute(
                "SELECT CASE WHEN json_valid(payload) THEN json_extract(payload,'$.audit') END FROM events "
                "WHERE commission_id=? AND seq=1 AND type='created'",
                (row["id"],),
            ).fetchone()
            if filed and filed[0] == SOURCE_NOT_RECORDED:
                reason = SOURCE_NOT_RECORDED
        if row["source"] == "session":
            # create_for_wakes files this commission and its first 'woken'
            # event atomically. The event names the actual wake requests.
            wake = conn.execute(
                "SELECT seq FROM events WHERE commission_id=? AND type='woken' ORDER BY seq LIMIT 1",
                (row["id"],),
            ).fetchone()
            if wake:
                origin = f"wake_event:{wake['seq']}"
        conn.execute(
            "INSERT INTO host_calls(call_id,trace_id,parent_call_id,commission_id,session_id,kind,target,"
            "source_message_ref,status,started_at,reason) VALUES(?,?,?,?,?,'commission',?,?,'filed',?,?)",
            (
                row["id"],
                root,
                row["parent_id"] if row["id"] != root else None,
                row["id"],
                row["session_id"],
                row["source"],
                origin,
                row["created_at"],
                reason,
            ),
        )
    return root


def file_source(
    conn: sqlite3.Connection, commission_id: str, ref: Any, now: float
) -> None:
    valid = source_ref(ref)
    if valid is not None:
        _ensure_commissions(conn, commission_id, now, ref=valid)


@dataclass
class Call:
    ledger: Ledger
    trace_id: str
    call_id: str
    status: str = "completed"
    reason: str | None = None

    async def bind_run(self, run_id: str, target: str) -> None:
        opaque_id = (
            run_id
            if re.fullmatch(r"[A-Za-z0-9_.:-]{1,192}", run_id)
            else ("sha256:" + hashlib.sha256(run_id.encode("utf-8")).hexdigest())
        )
        await self.ledger.run(
            lambda db: db.execute(
                "UPDATE host_calls SET run_id=?,target=? WHERE call_id=?",
                (opaque_id, _identifier(target), self.call_id),
            )
        )


@asynccontextmanager
async def call(ledger: Ledger, commission_id: str, kind: str, target: str):
    """Link to the invoking host operation in this task, never the latest call in a session."""
    parent = _CURRENT.get()
    call_id = uuid.uuid4().hex

    def begin(db: sqlite3.Connection) -> Call:
        now = ledger._clock()
        root = _ensure_commissions(db, commission_id, now, reserve=1)
        parent_id = commission_id
        if (
            parent
            and parent.ledger is ledger
            and parent.trace_id == root
            and db.execute(
                "SELECT 1 FROM host_calls WHERE call_id=?", (parent.call_id,)
            ).fetchone()
        ):
            parent_id = parent.call_id
        db.execute(
            "INSERT INTO host_calls(call_id,trace_id,parent_call_id,commission_id,session_id,kind,target,status,"
            "started_at) SELECT ?,?,?,id,session_id,?,?,'running',? FROM commissions WHERE id=?",
            (call_id, root, parent_id, kind, _identifier(target), now, commission_id),
        )
        return Call(ledger, root, call_id)

    # If cancelled while the SQLite worker is inserting, its transaction still
    # commits. Wait for the identity and close that same record before leaving.
    started = asyncio.create_task(ledger.run(begin))
    try:
        record = await asyncio.shield(started)
    except asyncio.CancelledError:
        await _finish(started, "cancelled")
        raise
    token = _CURRENT.set(record)
    try:
        yield record
    except asyncio.CancelledError:
        record.status = "cancelled"
        raise
    except BaseException:
        record.status = "failed"
        raise
    finally:
        _CURRENT.reset(token)
        await _finish(started, record.status)


async def _finish(started: asyncio.Task[Call], status: str) -> None:
    # Revocation and disconnect can both cancel the caller while SQLite is
    # still working. One independent task owns both the ID and its closing write.
    async def close_record() -> None:
        try:
            record = await started
        except Exception:  # insertion rolled back; there is no record to close
            return
        try:
            await record.ledger.run(
                lambda db: db.execute(
                    "UPDATE host_calls SET status=?,reason=?,ended_at=? WHERE call_id=? AND status='running'",
                    (status, record.reason, record.ledger._clock(), record.call_id),
                )
            )
        except (sqlite3.Error, OSError, RuntimeError) as exc:
            # The effect already happened. Preserve its result so it is not
            # retried as a failed action. Reconciliation marks unfinished rows.
            logger.warning("could not close host call %s (%s)", record.call_id, type(exc).__name__)

    closing = asyncio.create_task(close_record())
    cancelled = False
    while not closing.done():
        try:
            await asyncio.shield(closing)
        except asyncio.CancelledError:
            cancelled = True
    closing.result()
    if cancelled:
        raise asyncio.CancelledError


def read_trace(
    db: sqlite3.Connection, commission_id: str, *, now: float
) -> list[dict[str, Any]]:
    """Only identifiers and fixed outcome codes. Expired traces are hidden even while the host sleeps."""
    tree = _ancestry(db, commission_id)
    if not tree:
        return []
    root = tree[-1]["id"]
    closed = _closed_trees(db, root=root).fetchone()
    if closed and closed["closed_at"] <= now - RETENTION:
        return []
    rows = db.execute(
        "SELECT a.seq,a.call_id,a.trace_id,a.parent_call_id,a.commission_id,a.kind,a.target,a.source_message_ref,"
        "a.run_id,a.started_at,a.reason, CASE WHEN a.kind='commission' THEN c.session_id ELSE a.session_id END AS session_id,"
        "CASE WHEN a.kind='commission' THEN c.status ELSE a.status END AS status,"
        "CASE WHEN a.kind='commission' THEN c.ended_at ELSE a.ended_at END AS ended_at "
        "FROM host_calls a JOIN commissions c ON c.id=a.commission_id WHERE a.trace_id=? ORDER BY a.seq",
        (root,),
    )
    return [dict(row) for row in rows]


def main() -> None:
    import argparse
    from contextlib import closing
    from pathlib import Path

    from nekoclaw.host.paths import HostPaths

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commission")
    parser.add_argument(
        "--ledger",
        type=Path,
        help="Existing ledger (default: this user's data directory)",
    )
    args = parser.parse_args()
    path = args.ledger or HostPaths().ledger_db()
    try:
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            rows = (
                read_trace(db, args.commission, now=time.time())
                if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='host_calls'"
                ).fetchone()
                else []
            )
    except sqlite3.Error as exc:
        parser.exit(1, f"Cannot read call audit: {exc}\n")
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
