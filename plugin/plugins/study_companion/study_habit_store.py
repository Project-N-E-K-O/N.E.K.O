from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
import uuid
from typing import Any

from .store import StudyStore, safe_float, safe_int


GOAL_TARGET_TYPES = {"subject", "deck", "passage", "custom"}
GOAL_STATUSES = {"active", "completed", "cancelled"}
CHECKIN_STATUSES = {"checked_in", "missed", "makeup"}
CHECKIN_SOURCES = {"manual", "session_derived"}
FOCUS_MODES = {"focus", "short_break", "long_break"}
FOCUS_STATUSES = {"active", "paused", "completed", "cancelled"}


class StudyHabitStoreError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _date_from_timestamp(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return _now_iso()[:10]
    return text.split("T", 1)[0][:10]


class StudyHabitStore:
    """Phase 6 local habit tables stored in the user's StudyStore database."""

    def __init__(self, store: StudyStore) -> None:
        self._store = store
        self.ensure_tables()

    def ensure_tables(self) -> None:
        with self._store.transaction() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_goals (
                    id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT,
                    subject TEXT,
                    target_amount REAL NOT NULL,
                    progress_amount REAL NOT NULL DEFAULT 0,
                    unit TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkins (
                    id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS focus_sessions (
                    id TEXT PRIMARY KEY,
                    goal_id TEXT,
                    mode TEXT NOT NULL,
                    planned_minutes REAL NOT NULL,
                    actual_minutes REAL NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    pause_count INTEGER NOT NULL DEFAULT 0,
                    interrupt_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_goals_date ON daily_goals(date, status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_checkins_date ON checkins(date, status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_focus_sessions_started ON focus_sessions(started_at, status)"
            )

    @staticmethod
    def _goal_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "date": str(row["date"]),
            "target_type": str(row["target_type"]),
            "target_id": str(row["target_id"] or ""),
            "subject": str(row["subject"] or ""),
            "target_amount": safe_float(row["target_amount"], 0.0),
            "progress_amount": safe_float(row["progress_amount"], 0.0),
            "unit": str(row["unit"] or ""),
            "status": str(row["status"] or ""),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }

    @staticmethod
    def _checkin_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "date": str(row["date"]),
            "status": str(row["status"]),
            "source": str(row["source"]),
            "note": str(row["note"] or ""),
            "created_at": str(row["created_at"] or ""),
        }

    @staticmethod
    def _focus_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "goal_id": str(row["goal_id"] or ""),
            "mode": str(row["mode"]),
            "planned_minutes": safe_float(row["planned_minutes"], 0.0),
            "actual_minutes": safe_float(row["actual_minutes"], 0.0),
            "started_at": str(row["started_at"]),
            "ended_at": str(row["ended_at"] or ""),
            "date": _date_from_timestamp(str(row["started_at"])),
            "pause_count": safe_int(row["pause_count"], 0),
            "interrupt_count": safe_int(row["interrupt_count"], 0),
            "status": str(row["status"]),
        }

    def create_goal(
        self,
        *,
        date: str,
        target_type: str,
        subject: str,
        target_amount: float,
        unit: str,
        target_id: str = "",
    ) -> dict[str, Any]:
        target_type = str(target_type or "custom").strip()
        if target_type not in GOAL_TARGET_TYPES:
            target_type = "custom"
        goal_id = str(uuid.uuid4())
        now = _now_iso()
        with self._store.transaction() as conn:
            conn.execute(
                """
                INSERT INTO daily_goals (
                    id, date, target_type, target_id, subject, target_amount,
                    progress_amount, unit, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'active', ?, ?)
                """,
                (
                    goal_id,
                    str(date or _now_iso()[:10])[:10],
                    target_type,
                    str(target_id or ""),
                    str(subject or ""),
                    max(0.0, float(target_amount or 0.0)),
                    str(unit or "task"),
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM daily_goals WHERE id = ?", (goal_id,)
            ).fetchone()
        goal = self._goal_from_row(row)
        if goal is None:
            raise StudyHabitStoreError("failed to create daily goal")
        return goal

    def get_goal(self, goal_id: str) -> dict[str, Any] | None:
        with self._store.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM daily_goals WHERE id = ?", (str(goal_id or ""),)
            ).fetchone()
        return self._goal_from_row(row)

    def list_goals(
        self, *, date: str | None = None, include_cancelled: bool = False
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        clauses: list[str] = []
        if date:
            clauses.append("date = ?")
            params.append(str(date)[:10])
        if not include_cancelled:
            clauses.append("status != 'cancelled'")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._store.transaction() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM daily_goals
                {where}
                ORDER BY date DESC, created_at ASC
                """,
                tuple(params),
            ).fetchall()
        return [
            goal
            for goal in (self._goal_from_row(row) for row in rows)
            if goal is not None
        ]

    def update_goal(
        self,
        goal_id: str,
        *,
        target_amount: float | None = None,
        progress_amount: float | None = None,
        progress_delta: float | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_goal(goal_id)
        if current is None:
            raise StudyHabitStoreError("daily goal not found")
        new_target = (
            current["target_amount"]
            if target_amount is None
            else max(0.0, float(target_amount))
        )
        if progress_amount is not None:
            new_progress = max(0.0, float(progress_amount))
        else:
            new_progress = max(
                0.0, float(current["progress_amount"]) + float(progress_delta or 0.0)
            )
        new_status = str(status or current["status"])
        if new_status not in GOAL_STATUSES:
            new_status = current["status"]
        if new_status == "active" and new_target > 0 and new_progress >= new_target:
            new_status = "completed"
        with self._store.transaction() as conn:
            conn.execute(
                """
                UPDATE daily_goals
                SET target_amount = ?, progress_amount = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_target, new_progress, new_status, _now_iso(), str(goal_id)),
            )
        updated = self.get_goal(goal_id)
        if updated is None:
            raise StudyHabitStoreError("daily goal update failed")
        return updated

    def delete_goal(self, goal_id: str) -> bool:
        key = str(goal_id or "")
        with self._store.transaction() as conn:
            conn.execute("DELETE FROM focus_sessions WHERE goal_id = ?", (key,))
            cursor = conn.execute("DELETE FROM daily_goals WHERE id = ?", (key,))
            return cursor.rowcount > 0

    def record_checkin(
        self,
        *,
        date: str,
        status: str = "checked_in",
        source: str = "manual",
        note: str = "",
    ) -> dict[str, Any]:
        status = status if status in CHECKIN_STATUSES else "checked_in"
        source = source if source in CHECKIN_SOURCES else "manual"
        checkin_id = str(uuid.uuid4())
        with self._store.transaction() as conn:
            conn.execute(
                """
                INSERT INTO checkins (id, date, status, source, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    checkin_id,
                    str(date or _now_iso()[:10])[:10],
                    status,
                    source,
                    str(note or ""),
                    _now_iso(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM checkins WHERE id = ?", (checkin_id,)
            ).fetchone()
        checkin = self._checkin_from_row(row)
        if checkin is None:
            raise StudyHabitStoreError("failed to record checkin")
        return checkin

    def list_checkins(
        self, *, date: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if date:
            where = "WHERE date = ?"
            params.append(str(date)[:10])
        params.append(max(1, int(limit)))
        with self._store.transaction() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM checkins
                {where}
                ORDER BY date DESC, created_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [
            item
            for item in (self._checkin_from_row(row) for row in rows)
            if item is not None
        ]

    def checked_dates(self, *, through_date: str, limit: int | None = None) -> set[str]:
        limit_clause = ""
        params: list[Any] = [str(through_date)[:10]]
        if limit is not None:
            limit_clause = "LIMIT ?"
            params.append(max(1, int(limit)))
        with self._store.transaction() as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT date
                FROM checkins
                WHERE date <= ?
                  AND status IN ('checked_in', 'makeup')
                ORDER BY date DESC
                {limit_clause}
                """,
                tuple(params),
            ).fetchall()
        return {str(row["date"]) for row in rows}

    def create_focus_session(
        self,
        *,
        goal_id: str = "",
        mode: str,
        planned_minutes: float,
        started_at: str,
    ) -> dict[str, Any]:
        mode = mode if mode in FOCUS_MODES else "focus"
        session_id = str(uuid.uuid4())
        with self._store.transaction() as conn:
            conn.execute(
                """
                INSERT INTO focus_sessions (
                    id, goal_id, mode, planned_minutes, actual_minutes,
                    started_at, ended_at, pause_count, interrupt_count, status
                )
                VALUES (?, ?, ?, ?, 0, ?, '', 0, 0, 'active')
                """,
                (
                    session_id,
                    str(goal_id or ""),
                    mode,
                    max(0.0, float(planned_minutes or 0.0)),
                    str(started_at),
                ),
            )
            row = conn.execute(
                "SELECT * FROM focus_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        focus = self._focus_from_row(row)
        if focus is None:
            raise StudyHabitStoreError("failed to create focus session")
        return focus

    def finish_focus_session(
        self,
        session_id: str,
        *,
        ended_at: str,
        actual_minutes: float,
        status: str,
        pause_count: int = 0,
        interrupt_count: int = 0,
    ) -> dict[str, Any]:
        status = status if status in FOCUS_STATUSES else "completed"
        with self._store.transaction() as conn:
            conn.execute(
                """
                UPDATE focus_sessions
                SET ended_at = ?,
                    actual_minutes = ?,
                    pause_count = ?,
                    interrupt_count = ?,
                    status = ?
                WHERE id = ?
                """,
                (
                    str(ended_at),
                    max(0.0, float(actual_minutes or 0.0)),
                    max(0, int(pause_count or 0)),
                    max(0, int(interrupt_count or 0)),
                    status,
                    str(session_id or ""),
                ),
            )
            row = conn.execute(
                "SELECT * FROM focus_sessions WHERE id = ?", (str(session_id or ""),)
            ).fetchone()
        focus = self._focus_from_row(row)
        if focus is None:
            raise StudyHabitStoreError("focus session not found")
        return focus

    def list_focus_sessions(
        self, *, date: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        clauses = []
        if date:
            clauses.append("substr(started_at, 1, 10) = ?")
            params.append(str(date)[:10])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        with self._store.transaction() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM focus_sessions
                {where}
                ORDER BY started_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [
            item
            for item in (self._focus_from_row(row) for row in rows)
            if item is not None
        ]

    def focus_minutes_for_date(self, date: str) -> float:
        with self._store.transaction() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(actual_minutes), 0) AS total
                FROM focus_sessions
                WHERE substr(started_at, 1, 10) = ?
                  AND mode = 'focus'
                  AND status = 'completed'
                """,
                (str(date)[:10],),
            ).fetchone()
        return safe_float(row["total"] if row is not None else 0.0, 0.0)
