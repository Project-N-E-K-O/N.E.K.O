from __future__ import annotations

from .store_common import (
    Any,
    uuid,
    _DEFAULT_APPEND_ONLY_HISTORY_LIMIT,
)


def upsert_anonymous_knowledge_stat(
    self,
    *,
    stat_type: str,
    stat_key: str,
    payload: dict[str, Any],
    sample_count: int,
    outcome: dict[str, Any],
    min_sample_met: bool,
) -> dict[str, Any]:
    stat_type = str(stat_type or "").strip()
    stat_key = str(stat_key or "").strip()
    if not stat_type or not stat_key:
        raise ValueError("stat_type and stat_key are required")
    stat_id = f"{stat_type}:{stat_key}"
    with self._lock:
        conn = self._require_conn()
        conn.execute(
            """
            INSERT INTO anonymous_knowledge_stats (
                id, stat_type, stat_key, payload_json, sample_count, outcome_json, min_sample_met, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(stat_type, stat_key) DO UPDATE SET
                payload_json = excluded.payload_json,
                sample_count = excluded.sample_count,
                outcome_json = excluded.outcome_json,
                min_sample_met = excluded.min_sample_met,
                updated_at = datetime('now')
            """,
            (
                stat_id,
                stat_type,
                stat_key,
                self._json_dumps(payload or {}),
                int(sample_count or 0),
                self._json_dumps(outcome or {}),
                1 if min_sample_met else 0,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM anonymous_knowledge_stats WHERE stat_type = ? AND stat_key = ?",
            (stat_type, stat_key),
        ).fetchone()
    stat = self._anonymous_stat_from_row(row)
    if stat is None:
        raise RuntimeError("anonymous stat upsert failed")
    return stat


def list_anonymous_knowledge_stats(self, limit: int = 100) -> list[dict[str, Any]]:
    with self._lock:
        rows = (
            self._require_conn()
            .execute(
                """
            SELECT *
            FROM anonymous_knowledge_stats
            ORDER BY updated_at DESC, created_at DESC
            LIMIT ?
            """,
                (max(1, int(limit)),),
            )
            .fetchall()
        )
    return [
        item
        for item in (self._anonymous_stat_from_row(row) for row in rows)
        if item is not None
    ]


def anonymous_knowledge_stats_summary(self) -> dict[str, Any]:
    with self._lock:
        rows = (
            self._require_conn()
            .execute(
                """
            SELECT stat_type, min_sample_met, COUNT(*) AS count, COALESCE(SUM(sample_count), 0) AS samples
            FROM anonymous_knowledge_stats
            GROUP BY stat_type, min_sample_met
            """
            )
            .fetchall()
        )
        queue_row = (
            self._require_conn()
            .execute("SELECT COUNT(*) AS count FROM knowledge_contribution_queue")
            .fetchone()
        )
    by_type: dict[str, int] = {}
    min_sample_met = 0
    sample_count = 0
    total = 0
    for row in rows:
        count = int(row["count"] or 0)
        stat_type = str(row["stat_type"] or "")
        total += count
        by_type[stat_type] = by_type.get(stat_type, 0) + count
        sample_count += int(row["samples"] or 0)
        if bool(row["min_sample_met"]):
            min_sample_met += count
    return {
        "total": total,
        "by_type": by_type,
        "min_sample_met": min_sample_met,
        "sample_count": sample_count,
        "queue_count": int(queue_row["count"] if queue_row is not None else 0),
    }


def enqueue_knowledge_contribution_snapshot(
    self,
    *,
    stats: list[dict[str, Any]],
    status: str,
    history_limit: int = _DEFAULT_APPEND_ONLY_HISTORY_LIMIT,
) -> dict[str, Any]:
    queue_id = str(uuid.uuid4())
    status_value = str(status or "preview")
    with self._lock:
        conn = self._require_conn()
        conn.execute(
            """
            INSERT INTO knowledge_contribution_queue (id, stats_json, status, created_at, updated_at)
            VALUES (?, ?, ?, datetime('now'), datetime('now'))
            """,
            (queue_id, self._json_dumps(stats or []), status_value),
        )
        self._trim_append_only_rows(
            conn,
            table="knowledge_contribution_queue",
            group_column="status",
            group_value=status_value,
            history_limit=history_limit,
            order_by="rowid DESC",
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM knowledge_contribution_queue WHERE id = ?", (queue_id,)
        ).fetchone()
    return {
        "id": str(row["id"]),
        "stats": self._json_loads(row["stats_json"], []),
        "status": str(row["status"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def list_knowledge_contribution_queue(self, limit: int = 50) -> list[dict[str, Any]]:
    with self._lock:
        rows = (
            self._require_conn()
            .execute(
                """
            SELECT *
            FROM knowledge_contribution_queue
            ORDER BY updated_at DESC, created_at DESC
            LIMIT ?
            """,
                (max(1, int(limit)),),
            )
            .fetchall()
        )
    return [
        {
            "id": str(row["id"]),
            "stats": self._json_loads(row["stats_json"], []),
            "status": str(row["status"] or ""),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }
        for row in rows
    ]


def clear_knowledge_contribution_queue(self) -> int:
    with self._lock:
        cursor = self._require_conn().execute(
            "DELETE FROM knowledge_contribution_queue"
        )
        self._require_conn().commit()
    return int(cursor.rowcount or 0)
