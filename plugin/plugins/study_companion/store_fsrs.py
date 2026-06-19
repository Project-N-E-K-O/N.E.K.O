from __future__ import annotations

from .store_common import (
    Any,
    _DEFAULT_APPEND_ONLY_HISTORY_LIMIT,
)


def append_mastery_snapshot(
    self,
    snapshot: dict[str, Any],
    *,
    history_limit: int = _DEFAULT_APPEND_ONLY_HISTORY_LIMIT,
) -> None:
    topic_key = str(snapshot.get("topic_id") or "")
    with self._lock:
        conn = self._require_conn()
        conn.execute(
            """
            INSERT INTO mastery_snapshots (
                topic_id, mastery, accuracy, recency, consistency,
                confidence, level, attempts, flags, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                topic_key,
                float(snapshot.get("mastery") or 0.0),
                float(snapshot.get("accuracy") or 0.0),
                float(snapshot.get("recency") or 0.0),
                float(snapshot.get("consistency") or 0.0),
                float(snapshot.get("confidence") or 0.0),
                str(snapshot.get("level") or ""),
                int(snapshot.get("attempts") or 0),
                self._json_dumps(
                    snapshot.get("flags")
                    if isinstance(snapshot.get("flags"), list)
                    else []
                ),
            ),
        )
        self._trim_append_only_rows(
            conn,
            table="mastery_snapshots",
            group_column="topic_id",
            group_value=topic_key,
            history_limit=history_limit,
        )
        conn.commit()


def get_latest_mastery(self, topic_id: str) -> dict[str, Any] | None:
    row = (
        self._require_read_conn()
        .execute(
            """
            SELECT ms.*, t.name AS topic_name, t.chapter AS chapter, t.subject AS subject
            FROM mastery_snapshots ms
            LEFT JOIN topics t ON t.id = ms.topic_id
            WHERE ms.topic_id = ?
            ORDER BY ms.id DESC
            LIMIT 1
            """,
            (str(topic_id or ""),),
        )
        .fetchone()
    )
    return self._mastery_from_row(row)


def list_mastery_overview(self, limit: int = 20) -> list[dict[str, Any]]:
    rows = (
        self._require_read_conn()
        .execute(
            """
            SELECT ms.*, t.name AS topic_name, t.chapter AS chapter, t.subject AS subject
            FROM mastery_snapshots ms
            JOIN (
                SELECT topic_id, MAX(id) AS max_id
                FROM mastery_snapshots
                GROUP BY topic_id
            ) latest ON latest.max_id = ms.id
            LEFT JOIN topics t ON t.id = ms.topic_id
            ORDER BY ms.updated_at DESC, ms.id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        )
        .fetchall()
    )
    return [
        item
        for item in (self._mastery_from_row(row) for row in rows)
        if item is not None
    ]


def get_fsrs_card(self, topic_id: str) -> dict[str, Any] | None:
    row = (
        self._require_read_conn()
        .execute(
            "SELECT * FROM fsrs_cards WHERE topic_id = ?",
            (str(topic_id or ""),),
        )
        .fetchone()
    )
    return self._fsrs_card_from_row(row)


def upsert_fsrs_card(
    self, *, topic_id: str, card: dict[str, Any], last_rating: int
) -> None:
    with self._lock:
        conn = self._require_conn()
        conn.execute(
            """
            INSERT INTO fsrs_cards (topic_id, card_data, fsrs_state, last_rating, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(topic_id) DO UPDATE SET
                card_data = excluded.card_data,
                fsrs_state = excluded.fsrs_state,
                last_rating = excluded.last_rating,
                updated_at = datetime('now')
            """,
            (
                str(topic_id or ""),
                self._json_dumps(card or {}),
                str((card or {}).get("state") or ""),
                int(last_rating or 0),
            ),
        )
        conn.commit()


def list_fsrs_cards(self, limit: int | None = 100) -> list[dict[str, Any]]:
    if limit is None:
        rows = (
            self._require_read_conn()
            .execute(
                "SELECT * FROM fsrs_cards ORDER BY updated_at DESC, id DESC",
            )
            .fetchall()
        )
    else:
        rows = (
            self._require_read_conn()
            .execute(
                "SELECT * FROM fsrs_cards ORDER BY updated_at DESC, id DESC LIMIT ?",
                (max(1, int(limit)),),
            )
            .fetchall()
        )
    return [
        {
            "id": int(row["id"]),
            "topic_id": str(row["topic_id"]),
            "card": self._json_loads(row["card_data"], {}),
            "fsrs_state": str(row["fsrs_state"] or ""),
            "last_rating": int(row["last_rating"] or 0),
            "updated_at": str(row["updated_at"] or ""),
        }
        for row in rows
    ]


def append_review_log(
    self,
    *,
    topic_id: str,
    card_id: int | None,
    rating: int,
    scheduled_days: int,
    actual_days: int,
    history_limit: int = _DEFAULT_APPEND_ONLY_HISTORY_LIMIT,
) -> None:
    topic_key = str(topic_id or "")
    with self._lock:
        conn = self._require_conn()
        conn.execute(
            """
            INSERT INTO review_log (topic_id, card_id, rating, scheduled_days, actual_days, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                topic_key,
                card_id,
                int(rating or 0),
                int(scheduled_days or 0),
                int(actual_days or 0),
            ),
        )
        self._trim_append_only_rows(
            conn,
            table="review_log",
            group_column="topic_id",
            group_value=topic_key,
            history_limit=history_limit,
        )
        conn.commit()


def list_review_log(self, limit: int = 100) -> list[dict[str, Any]]:
    rows = (
        self._require_read_conn()
        .execute(
            """
            SELECT *
            FROM review_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        )
        .fetchall()
    )
    return [
        {
            "id": int(row["id"]),
            "topic_id": str(row["topic_id"]),
            "card_id": int(row["card_id"]) if row["card_id"] is not None else None,
            "rating": int(row["rating"] or 0),
            "scheduled_days": int(row["scheduled_days"] or 0),
            "actual_days": int(row["actual_days"] or 0),
            "created_at": str(row["created_at"] or ""),
        }
        for row in reversed(rows)
    ]
