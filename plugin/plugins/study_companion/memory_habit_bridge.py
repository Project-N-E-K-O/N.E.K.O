from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .memory_deck_store import MemoryDeckStore
from .memory_queries import active_item_card_rows
from .study_habit_store import StudyHabitStore
from .store import StudyStore, safe_float


DECK_GOAL_UNITS = frozenset({"cards", "minutes", "attempts"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _date_key(value: str) -> str:
    return str(value or _now_iso()[:10])[:10]


def _date_utc_bounds(value: str, timezone_name: str) -> tuple[str, str]:
    zone_name = str(timezone_name or "local").strip()
    zone = datetime.now().astimezone().tzinfo or timezone.utc
    if zone_name and zone_name.lower() != "local":
        try:
            zone = ZoneInfo(zone_name)
        except ZoneInfoNotFoundError:
            zone = datetime.now().astimezone().tzinfo or timezone.utc
    target_date = datetime.strptime(_date_key(value), "%Y-%m-%d").date()
    start = datetime.combine(target_date, time.min, tzinfo=zone)
    end = start + timedelta(days=1)
    return (
        start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )


class MemoryHabitBridge:
    """Phase 7 memory deck integration for Phase 6 habit tables."""

    def __init__(
        self,
        *,
        store: StudyStore,
        memory: MemoryDeckStore,
        habits: StudyHabitStore,
        checkin_timezone: str = "local",
    ) -> None:
        self._store = store
        self._memory = memory
        self._habits = habits
        self._checkin_timezone = str(checkin_timezone or "local").strip() or "local"
        self.ensure_tables()

    def ensure_tables(self) -> None:
        with self._store._lock:  # noqa: SLF001 - plugin-local schema extension.
            conn = self._store._require_conn()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_habit_progress (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    goal_id TEXT NOT NULL,
                    deck_id TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    UNIQUE(source_type, source_id, goal_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_habit_progress_deck
                ON memory_habit_progress(deck_id, applied_at)
                """
            )
            conn.commit()

    def status(self) -> dict[str, Any]:
        return {
            "available": True,
            "supports_deck_goals": True,
            "supports_deck_focus": True,
            "supports_memory_summary": True,
        }

    def create_deck_goal(
        self,
        *,
        date: str,
        deck_id: str,
        target_amount: float,
        unit: str = "cards",
    ) -> dict[str, Any]:
        deck = self._memory.get_deck(deck_id)
        if deck is None:
            raise ValueError("memory deck not found")
        unit_key = str(unit or "cards").strip().lower()
        if unit_key not in DECK_GOAL_UNITS:
            unit_key = "cards"
        target_date = _date_key(date)
        target = max(1.0, safe_float(target_amount, 1.0))
        existing = self._find_deck_goal(
            date=target_date, deck_id=str(deck["id"]), unit=unit_key
        )
        if existing is not None:
            goal = self._habits.update_goal(
                str(existing["id"]),
                target_amount=target,
                status="active",
            )
            return {"goal": goal, "deck": deck, "created": False}
        goal = self._habits.create_goal(
            date=target_date,
            target_type="deck",
            target_id=str(deck["id"]),
            subject=str(deck.get("name") or ""),
            target_amount=target,
            unit=unit_key,
        )
        return {"goal": goal, "deck": deck, "created": True}

    def resolve_focus_goal(
        self,
        *,
        date: str,
        deck_id: str,
        focus_minutes: float,
    ) -> dict[str, Any]:
        deck = self._memory.get_deck(deck_id)
        if deck is None:
            raise ValueError("memory deck not found")
        existing = self._find_deck_goal(
            date=_date_key(date), deck_id=str(deck["id"]), unit="minutes"
        )
        if existing is not None:
            return {"goal": existing, "deck": deck, "created": False}
        return self.create_deck_goal(
            date=date,
            deck_id=deck_id,
            target_amount=max(1.0, safe_float(focus_minutes, 1.0)),
            unit="minutes",
        )

    def apply_review_progress(
        self, payload: dict[str, Any], *, date: str
    ) -> dict[str, Any]:
        review = payload.get("review_record") if isinstance(payload, dict) else {}
        item = payload.get("item") if isinstance(payload, dict) else {}
        return self._apply_progress(
            date=date,
            deck_id=str((item or {}).get("deck_id") or ""),
            source_type="review_record",
            source_id=str((review or {}).get("id") or ""),
            unit_deltas={"card": 1.0, "cards": 1.0, "task": 1.0},
        )

    def apply_recitation_progress(
        self, payload: dict[str, Any], *, date: str
    ) -> dict[str, Any]:
        attempt = payload.get("attempt") if isinstance(payload, dict) else {}
        review = payload.get("review") if isinstance(payload, dict) else {}
        item = review.get("item") if isinstance(review, dict) else {}
        return self._apply_progress(
            date=date,
            deck_id=str((item or {}).get("deck_id") or ""),
            source_type="recitation_attempt",
            source_id=str((attempt or {}).get("id") or ""),
            unit_deltas={
                "attempt": 1.0,
                "attempts": 1.0,
                "card": 1.0,
                "cards": 1.0,
                "task": 1.0,
            },
        )

    def memory_summary(self, *, date: str) -> dict[str, Any]:
        target_date = _date_key(date)
        decks: dict[str, dict[str, Any]] = {}
        for row in self._review_rows(target_date):
            deck = self._deck_summary_item(
                decks,
                str(row["deck_id"] or ""),
                str(row["deck_name"] or ""),
            )
            deck["reviewed_items"] += int(row["reviewed_items"] or 0)
            deck["correct_items"] += int(row["correct_items"] or 0)
        for row in self._recitation_rows(target_date):
            deck = self._deck_summary_item(
                decks,
                str(row["deck_id"] or ""),
                str(row["deck_name"] or ""),
            )
            deck["recitation_attempts"] += int(row["recitation_attempts"] or 0)
        for row in self._focus_rows(target_date):
            deck = self._deck_summary_item(
                decks,
                str(row["deck_id"] or ""),
                str(row["deck_name"] or ""),
            )
            deck["focus_minutes"] += safe_float(row["focus_minutes"], 0.0)
        for row in self._goal_rows(target_date):
            self._deck_summary_item(
                decks,
                str(row["deck_id"] or ""),
                str(row["deck_name"] or ""),
            )
        due_counts = self._due_review_counts(date=target_date)
        for due in due_counts.values():
            self._deck_summary_item(
                decks,
                str(due["deck_id"]),
                str(due["deck_name"]),
            )
        for deck in decks.values():
            due = due_counts.get(str(deck["deck_id"]), {})
            deck["due_remaining"] = int(due.get("due_remaining") or 0)
            deck["correct_rate"] = (
                deck["correct_items"] / deck["reviewed_items"]
                if deck["reviewed_items"]
                else 0.0
            )
        ordered = sorted(decks.values(), key=lambda item: str(item["name"]))
        reviewed = sum(int(item["reviewed_items"]) for item in ordered)
        correct = sum(int(item["correct_items"]) for item in ordered)
        recitations = sum(int(item["recitation_attempts"]) for item in ordered)
        focus = sum(float(item["focus_minutes"]) for item in ordered)
        due = sum(int(item["due_remaining"]) for item in ordered)
        return {
            "available": True,
            "date": target_date,
            "deck_count": len(ordered),
            "reviewed_items": reviewed,
            "correct_items": correct,
            "correct_rate": correct / reviewed if reviewed else 0.0,
            "recitation_attempts": recitations,
            "focus_minutes": focus,
            "due_remaining": due,
            "decks": ordered,
        }

    def _find_deck_goal(
        self, *, date: str, deck_id: str, unit: str = ""
    ) -> dict[str, Any] | None:
        for goal in self._habits.list_goals(date=date):
            if str(goal.get("target_type") or "") != "deck":
                continue
            if str(goal.get("target_id") or "") != str(deck_id):
                continue
            if unit and str(goal.get("unit") or "") != unit:
                continue
            return goal
        return None

    def _apply_progress(
        self,
        *,
        date: str,
        deck_id: str,
        source_type: str,
        source_id: str,
        unit_deltas: dict[str, float],
    ) -> dict[str, Any]:
        if not deck_id or not source_id:
            return {"applied": 0, "goals": [], "reason": "missing_source"}
        target_date = _date_key(date)
        now = _now_iso()
        applied_ids: list[str] = []
        with self._store._lock:  # noqa: SLF001 - single DB transaction.
            conn = self._store._require_conn()
            rows = conn.execute(
                """
                SELECT *
                FROM daily_goals
                WHERE date = ?
                  AND target_type = 'deck'
                  AND target_id = ?
                  AND status != 'cancelled'
                ORDER BY created_at ASC
                """,
                (target_date, str(deck_id)),
            ).fetchall()
            with conn:
                for row in rows:
                    unit = str(row["unit"] or "").strip().lower()
                    delta = float(unit_deltas.get(unit, 0.0))
                    if delta <= 0:
                        continue
                    goal_id = str(row["id"])
                    cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO memory_habit_progress (
                            source_type, source_id, goal_id, deck_id, applied_at
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (source_type, source_id, goal_id, str(deck_id), now),
                    )
                    if cursor.rowcount <= 0:
                        continue
                    target_amount = safe_float(row["target_amount"], 0.0)
                    progress = safe_float(row["progress_amount"], 0.0) + delta
                    status = str(row["status"] or "active")
                    if status == "active" and target_amount > 0 and progress >= target_amount:
                        status = "completed"
                    conn.execute(
                        """
                        UPDATE daily_goals
                        SET progress_amount = ?, status = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (progress, status, now, goal_id),
                    )
                    applied_ids.append(goal_id)
                if applied_ids:
                    conn.execute(
                        """
                        INSERT INTO checkins (id, date, status, source, note, created_at)
                        VALUES (?, ?, 'checked_in', 'session_derived', ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            target_date,
                            f"memory:{source_type}:{source_id}",
                            now,
                        ),
                    )
        return {
            "applied": len(applied_ids),
            "goal_ids": applied_ids,
            "goals": [
                goal
                for goal in (self._habits.get_goal(goal_id) for goal_id in applied_ids)
                if goal is not None
            ],
        }

    def _review_rows(self, date: str) -> list[Any]:
        start_utc, end_utc = _date_utc_bounds(date, self._checkin_timezone)
        with self._store._lock:  # noqa: SLF001
            return (
                self._store._require_conn()
                .execute(
                    """
                    SELECT mi.deck_id,
                           d.name AS deck_name,
                           COUNT(rr.id) AS reviewed_items,
                           COALESCE(SUM(CASE WHEN rr.correct = 1 THEN 1 ELSE 0 END), 0) AS correct_items
                    FROM review_records rr
                    JOIN memory_items mi ON mi.id = rr.item_id
                    LEFT JOIN decks d ON d.id = mi.deck_id
                    WHERE datetime(rr.reviewed_at) >= ?
                      AND datetime(rr.reviewed_at) < ?
                    GROUP BY mi.deck_id, d.name
                    """,
                    (start_utc, end_utc),
                )
                .fetchall()
            )

    def _recitation_rows(self, date: str) -> list[Any]:
        start_utc, end_utc = _date_utc_bounds(date, self._checkin_timezone)
        with self._store._lock:  # noqa: SLF001
            return (
                self._store._require_conn()
                .execute(
                    """
                    SELECT mi.deck_id,
                           d.name AS deck_name,
                           COUNT(ra.id) AS recitation_attempts
                    FROM recitation_attempts ra
                    JOIN memory_items mi ON mi.id = ra.passage_item_id
                    LEFT JOIN decks d ON d.id = mi.deck_id
                    WHERE datetime(ra.reviewed_at) >= ?
                      AND datetime(ra.reviewed_at) < ?
                    GROUP BY mi.deck_id, d.name
                    """,
                    (start_utc, end_utc),
                )
                .fetchall()
            )

    def _focus_rows(self, date: str) -> list[Any]:
        with self._store._lock:  # noqa: SLF001
            return (
                self._store._require_conn()
                .execute(
                    """
                    SELECT dg.target_id AS deck_id,
                           COALESCE(d.name, dg.subject, dg.target_id) AS deck_name,
                           COALESCE(SUM(fs.actual_minutes), 0) AS focus_minutes
                    FROM focus_sessions fs
                    JOIN daily_goals dg ON dg.id = fs.goal_id
                    LEFT JOIN decks d ON d.id = dg.target_id
                    WHERE substr(fs.started_at, 1, 10) = ?
                      AND fs.mode = 'focus'
                      AND fs.status = 'completed'
                      AND dg.target_type = 'deck'
                    GROUP BY dg.target_id, d.name, dg.subject
                    """,
                    (date,),
                )
                .fetchall()
            )

    def _goal_rows(self, date: str) -> list[Any]:
        with self._store._lock:  # noqa: SLF001
            return (
                self._store._require_conn()
                .execute(
                    """
                    SELECT dg.target_id AS deck_id,
                           COALESCE(d.name, dg.subject, dg.target_id) AS deck_name
                    FROM daily_goals dg
                    LEFT JOIN decks d ON d.id = dg.target_id
                    WHERE dg.date = ?
                      AND dg.target_type = 'deck'
                      AND dg.status != 'cancelled'
                    GROUP BY dg.target_id, d.name, dg.subject
                    """,
                    (date,),
                )
                .fetchall()
            )

    def _due_review_counts(self, *, date: str) -> dict[str, dict[str, Any]]:
        _, end_utc = _date_utc_bounds(date, self._checkin_timezone)
        target_now = datetime.strptime(end_utc, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        with self._store._lock:  # noqa: SLF001
            rows = active_item_card_rows(self._store._require_conn())
            deck_cards: dict[str, list[dict[str, Any]]] = {}
            deck_names: dict[str, str] = {}
            for row in rows:
                deck_id = str(row["deck_id"] or "")
                if not deck_id:
                    continue
                deck_cards.setdefault(deck_id, []).append(
                    self._store._json_loads(row["card_data"], {})  # noqa: SLF001
                )
                deck_names[deck_id] = str(row["deck_name"] or deck_id)
        counts: dict[str, dict[str, Any]] = {}
        for deck_id, cards in deck_cards.items():
            due_count = len(self._memory.fsrs.get_due_reviews(cards, now=target_now))
            if due_count <= 0:
                continue
            counts[deck_id] = {
                "deck_id": deck_id,
                "deck_name": deck_names.get(deck_id, deck_id),
                "due_remaining": due_count,
            }
        return counts

    @staticmethod
    def _deck_summary_item(
        decks: dict[str, dict[str, Any]], deck_id: str, name: str
    ) -> dict[str, Any]:
        key = str(deck_id or "")
        if not key:
            key = f"unknown:{name}"
        if key not in decks:
            decks[key] = {
                "deck_id": deck_id,
                "name": name or deck_id,
                "reviewed_items": 0,
                "correct_items": 0,
                "correct_rate": 0.0,
                "recitation_attempts": 0,
                "focus_minutes": 0.0,
                "due_remaining": 0,
            }
        return decks[key]
