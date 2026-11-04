"""Durable local admission for independently retried chat-sync channels.

Only timing, safe error codes and account/character keys are stored here. Raw
chat bytes and fixed batch identities remain in the original ledger.
"""

from contextlib import closing
from pathlib import Path
import random
import sqlite3

from services.chat_sync.writer import store_connection
import time


PULL_INTERVAL_MS = 120_000
RETRY_BASE_MS = 5_000
RETRY_CAP_MS = 30 * 60_000


def now_ms() -> int:
    """Use local queue/attempt time without interpreting message occurrence time."""
    return time.time_ns() // 1_000_000


class ChatSyncScheduler:
    """Keep per-channel admission and the pinned origin's retry deadline."""

    def __init__(self, path: Path, *, clock=now_ms, uniform=random.uniform):
        """Allow deterministic clocks and jitter without changing production timers."""
        self.path, self.clock, self.uniform = Path(path), clock, uniform
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS scheduler_device (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    origin_retry_at INTEGER NOT NULL, wake_at INTEGER NOT NULL DEFAULT 0);
                -- wake_at is no longer read. Older tables declare it NOT NULL
                -- without a default and keep three more unused columns, so
                -- the insert names its columns and still supplies wake_at.
                INSERT OR IGNORE INTO scheduler_device(singleton,origin_retry_at,wake_at) VALUES (1,0,0);
                CREATE TABLE IF NOT EXISTS scheduler_tasks (
                    domain TEXT NOT NULL, channel TEXT NOT NULL,
                    last_attempt INTEGER, retry_at INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0, blocked_generation INTEGER,
                    blocked_session TEXT, last_error TEXT, due_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(domain,channel));
            """)

    def _connect(self):
        """Persist admission before HTTP; a killed process cannot erase its gate."""
        conn = store_connection(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def request_check(self, domain, channel, *, verified_login=None) -> None:
        """Coalesce hints; a fresh subscription may repair a local login pause."""
        if channel not in {"pull", "profile", "long_term"}:
            raise ValueError("invalid_chat_sync_channel")
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT OR IGNORE INTO scheduler_tasks(domain,channel) VALUES (?,?)", (domain, channel))
            if verified_login is not None:
                # Only the old local resolver error is recoverable here. HTTP
                # rejection, integrity errors and retry deadlines remain intact.
                generation, session = verified_login
                conn.execute("UPDATE scheduler_tasks SET blocked_generation=NULL,blocked_session=NULL,last_error=NULL "
                             "WHERE domain=? AND channel=? AND last_error='chat_sync_login_required' "
                             "AND blocked_generation=? AND blocked_session=?", (domain, channel, generation, session))
            conn.execute("UPDATE scheduler_tasks SET due_at=0 WHERE domain=? AND channel=?", (domain, channel))

    def origin_ready(self) -> bool:
        """A scope-free Retry-After applies to every channel's requests."""
        with closing(self._connect()) as conn:
            deadline = conn.execute("SELECT origin_retry_at FROM scheduler_device WHERE singleton=1").fetchone()[0]
        return self.clock() >= deadline

    def next_wake_at(self) -> int | None:
        """Expose future admission deadlines so an idle host can sleep longer."""
        now = self.clock()
        with closing(self._connect()) as conn:
            origin = conn.execute("SELECT origin_retry_at FROM scheduler_device WHERE singleton=1").fetchone()[0]
            rows = conn.execute("SELECT retry_at,due_at FROM scheduler_tasks WHERE blocked_generation IS NULL").fetchall()
        deadlines = [max(origin, row['retry_at'], row['due_at']) for row in rows]
        return min((value for value in [origin, *deadlines] if value > now), default=None)

    def _ready(self, conn, domain, channel, generation, session, manual):
        """Check persisted device and task gates in the admission transaction."""
        now = self.clock()
        origin = conn.execute("SELECT origin_retry_at FROM scheduler_device WHERE singleton=1").fetchone()[0]
        if now < origin:
            return False
        task = conn.execute("SELECT * FROM scheduler_tasks WHERE domain=? AND channel=?", (domain, channel)).fetchone()
        if task is not None:
            if now < task["retry_at"]:
                return False
            if task["blocked_generation"] == generation:
                # Authentication rejection may recover after a fresh login;
                # schema/integrity errors require explicit re-enabling instead.
                if task["blocked_session"] is None or task["blocked_session"] == session:
                    return False
            if channel in {"pull", "profile", "long_term"} and not manual and now < task["due_at"]:
                return False
        return True

    def ready(self, domain, channel, generation, session, *, manual=False) -> bool:
        """Query admission without recording an attempt."""
        with closing(self._connect()) as conn:
            return self._ready(conn, domain, channel, generation, session, manual)

    def claim(self, domain, channel, generation, session, *, manual=False) -> bool:
        """Atomically admit an attempt across roles before any outgoing operation."""
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if not self._ready(conn, domain, channel, generation, session, manual):
                return False
            now = self.clock()
            conn.execute("INSERT OR IGNORE INTO scheduler_tasks(domain,channel) VALUES (?,?)", (domain, channel))
            # A crash before a response leaves a small retry fence as well as the
            # durable periodic poll interval. Success clears only this fence.
            conn.execute("UPDATE scheduler_tasks SET last_attempt=?,retry_at=? WHERE domain=? AND channel=?", (
                now, now + RETRY_BASE_MS, domain, channel,
            ))
            if channel in {"pull", "profile", "long_term"}:
                # The next periodic poll survives a restart.
                conn.execute("UPDATE scheduler_tasks SET due_at=MAX(due_at,?) WHERE domain=? AND channel=?", (
                    now + PULL_INTERVAL_MS, domain, channel,
                ))
            return True

    def success(self, domain, channel) -> None:
        """Clear retry/error state only after the exact channel receipt was accepted."""
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE scheduler_tasks SET retry_at=0,failures=0,blocked_generation=NULL,"
                         "blocked_session=NULL,last_error=NULL WHERE domain=? AND channel=?", (domain, channel))

    def failure(self, domain, channel, generation, session, exc) -> None:
        """Retain safe retry metadata without storing server bodies or credentials."""
        code, status = str(exc), getattr(exc, "status_code", None)
        transient = isinstance(exc, OSError) or code in {"chat_sync_unavailable", "chat_sync_session_unavailable", "chat_sync_retry_later"}
        transient = transient or status in {408, 425, 429} or (status is not None and status >= 500)
        auth = status in {401, 403} or code in {"chat_sync_login_required", "chat_sync_session_changed"}
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT failures FROM scheduler_tasks WHERE domain=? AND channel=?", (domain, channel)).fetchone()
            failures = (row[0] if row else 0) + 1
            conn.execute("INSERT OR IGNORE INTO scheduler_tasks(domain,channel) VALUES (?,?)", (domain, channel))
            retry, blocked, blocked_session = 0, None, None
            if transient:
                base = min(RETRY_CAP_MS, RETRY_BASE_MS * 2 ** min(failures - 1, 20))
                delay = min(RETRY_CAP_MS, base + int(self.uniform(0, base / 4)))
                # New pure HTTP transport failures recover promptly; server
                # status codes, Retry-After and older persisted deadlines stay.
                retry = self.clock() + (min(delay, 3000) if code == "chat_sync_unavailable" else delay)
            else:
                blocked, blocked_session = generation, session if auth else None
            if status == 429:
                supplied = getattr(exc, "retry_after_ms", None)
                retry = max(retry, self.clock() + supplied) if supplied is not None else retry
                conn.execute("UPDATE scheduler_device SET origin_retry_at=MAX(origin_retry_at,?) WHERE singleton=1", (retry,))
            # Exceptions outside the fixed adapter vocabulary may contain PII.
            safe_codes = {
                "chat_sync_account_mismatch", "chat_sync_binding_changed", "chat_sync_login_required",
                "chat_sync_session_changed", "chat_sync_session_unavailable", "chat_sync_unavailable",
                "chat_sync_service_rejected", "chat_sync_retry_later", "invalid_chat_sync_response",
                "chat_sync_local_ca_invalid", "chat_sync_local_ca_loopback_required",
            }
            safe_code = code if code in safe_codes else "chat_sync_unavailable"
            conn.execute("UPDATE scheduler_tasks SET retry_at=?,failures=?,blocked_generation=?,"
                         "blocked_session=?,last_error=? WHERE domain=? AND channel=?", (
                             retry, failures, blocked, blocked_session, safe_code, domain, channel,
                         ))
            if code in {"chat_sync_session_unavailable", "chat_sync_unavailable"}:
                # Retry temporary login verification at its error deadline,
                # without also waiting for the ordinary two-minute poll.
                conn.execute("UPDATE scheduler_tasks SET due_at=0 WHERE domain=? AND channel=?", (domain, channel))

    def last_error(self, domain) -> str | None:
        """Expose only the latest fixed error for local progress UI."""
        with closing(self._connect()) as conn:
            # Retired body/marker rows can keep an old error that no pass clears.
            row = conn.execute("SELECT last_error FROM scheduler_tasks WHERE domain=? AND last_error IS NOT NULL "
                               "AND channel NOT IN ('body','deletion') "
                               "ORDER BY last_attempt DESC LIMIT 1", (domain,)).fetchone()
        return row[0] if row else None
