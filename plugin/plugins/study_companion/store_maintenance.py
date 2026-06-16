from __future__ import annotations

from contextlib import contextmanager
import sqlite3
from typing import Any, Iterator


_PURGE_TABLES = (
    "memory_habit_progress",
    "recitation_attempts",
    "review_records",
    "memory_review_log",
    "memory_fsrs_cards",
    "memory_items",
    "decks",
    "focus_sessions",
    "checkins",
    "daily_goals",
    "qa_records",
    "sessions",
    "review_log",
    "fsrs_cards",
    "wrong_questions",
    "mastery_snapshots",
    "knowledge_evidence",
    "candidate_knowledge_items",
    "anonymous_knowledge_stats",
    "knowledge_contribution_queue",
    "interactions",
    "topics",
    "notes",
    "notebooks",
    "kv",
)


@contextmanager
def transaction(self) -> Iterator[sqlite3.Connection]:
    """Hold the store lock around a SQLite transaction."""
    with self._lock:
        conn = self._require_conn()
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()


def json_loads(self, value: object, fallback: Any = None) -> Any:
    """Public JSON loader for store extension modules."""
    if fallback is None:
        fallback = {}
    return self._json_loads(value, fallback)


def purge_all(self) -> dict[str, int]:
    """Delete all user data rows while preserving the database schema."""
    deleted: dict[str, int] = {}
    with self._lock:
        conn = self._require_conn()
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            for table in _PURGE_TABLES:
                try:
                    cursor = conn.execute(f"DELETE FROM {table}")
                except sqlite3.Error:
                    deleted[table] = 0
                else:
                    deleted[table] = max(0, int(cursor.rowcount or 0))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
    return deleted
