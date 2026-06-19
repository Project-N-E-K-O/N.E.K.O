from __future__ import annotations

from .store_common import (
    Any,
    uuid,
    _DEFAULT_APPEND_ONLY_HISTORY_LIMIT,
)


def upsert_candidate_item(
    self,
    *,
    item_type: str,
    payload: dict[str, Any],
    source: str,
    dedupe_key: str,
    status: str = "candidate",
) -> dict[str, Any]:
    item_type = str(item_type or "").strip()
    dedupe_key = str(dedupe_key or "").strip()
    if not item_type or not dedupe_key:
        raise ValueError("candidate item_type and dedupe_key are required")
    source_value = str(source or "runtime").strip() or "runtime"
    payload_json = self._json_dumps(payload or {})
    with self._lock:
        conn = self._require_conn()
        existing = conn.execute(
            "SELECT * FROM candidate_knowledge_items WHERE item_type = ? AND dedupe_key = ? LIMIT 1",
            (item_type, dedupe_key),
        ).fetchone()
        if existing is None:
            item_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO candidate_knowledge_items (
                    id, item_type, dedupe_key, payload_json, source, status, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    item_id,
                    item_type,
                    dedupe_key,
                    payload_json,
                    source_value,
                    status,
                ),
            )
        else:
            item_id = str(existing["id"])
            conn.execute(
                """
                UPDATE candidate_knowledge_items
                SET payload_json = ?,
                    source = CASE WHEN source = '' THEN ? ELSE source END,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (payload_json, source_value, item_id),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM candidate_knowledge_items WHERE id = ?", (item_id,)
        ).fetchone()
    candidate = self._candidate_from_row(row)
    if candidate is None:
        raise RuntimeError("candidate upsert failed")
    return candidate


def add_knowledge_evidence(
    self,
    *,
    item_id: str,
    event_type: str,
    weight: float,
    context: dict[str, Any] | None = None,
    history_limit: int = _DEFAULT_APPEND_ONLY_HISTORY_LIMIT,
) -> dict[str, Any]:
    item_key = str(item_id or "").strip()
    if not item_key:
        raise ValueError("item_id is required")
    with self._lock:
        conn = self._require_conn()
        cursor = conn.execute(
            """
            INSERT INTO knowledge_evidence (item_id, event_type, weight, context_json, created_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            """,
            (
                item_key,
                str(event_type or ""),
                float(weight or 0.0),
                self._json_dumps(context or {}),
            ),
        )
        self._trim_append_only_rows(
            conn,
            table="knowledge_evidence",
            group_column="item_id",
            group_value=item_key,
            history_limit=history_limit,
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM knowledge_evidence WHERE id = ?",
            (int(cursor.lastrowid),),
        ).fetchone()
    evidence = self._evidence_from_row(row)
    if evidence is None:
        raise RuntimeError("knowledge evidence insert failed")
    return evidence


def get_candidate_item(self, item_id: str) -> dict[str, Any] | None:
    row = (
        self._require_read_conn()
        .execute(
            "SELECT * FROM candidate_knowledge_items WHERE id = ?",
            (str(item_id or ""),),
        )
        .fetchone()
    )
    return self._candidate_from_row(row)


def get_candidate_by_key(
    self, *, item_type: str, dedupe_key: str
) -> dict[str, Any] | None:
    row = (
        self._require_read_conn()
        .execute(
            "SELECT * FROM candidate_knowledge_items WHERE item_type = ? AND dedupe_key = ? LIMIT 1",
            (str(item_type or ""), str(dedupe_key or "")),
        )
        .fetchone()
    )
    return self._candidate_from_row(row)


def list_candidate_items(
    self,
    *,
    statuses: tuple[str, ...] | list[str] | None = None,
    item_type: str | None = None,
    topic_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    clauses: list[str] = []
    status_values = tuple(str(item) for item in (statuses or ()) if str(item))
    if status_values:
        placeholders = ",".join("?" for _ in status_values)
        clauses.append(f"status IN ({placeholders})")
        params.extend(status_values)
    if item_type:
        clauses.append("item_type = ?")
        params.append(str(item_type))
    topic_value = str(topic_id or "").strip()
    if topic_value:
        clauses.append(
            """
            (
                (item_type = 'edge' AND (
                    json_extract(payload_json, '$.from_topic_id') = ?
                    OR json_extract(payload_json, '$.to_topic_id') = ?
                ))
                OR (item_type != 'edge' AND (
                    json_extract(payload_json, '$.topic_id') = ?
                    OR json_extract(payload_json, '$.id') = ?
                ))
            )
            """
        )
        params.extend([topic_value, topic_value, topic_value, topic_value])
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(max(1, int(limit)))
    rows = (
        self._require_read_conn()
        .execute(
            # `where` is assembled only from static clause templates above; all
            # external values remain bound through `params`.
            f"""
            SELECT *
            FROM candidate_knowledge_items
            {where}
            ORDER BY updated_at DESC, created_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        )
        .fetchall()
    )
    return [
        item
        for item in (self._candidate_from_row(row) for row in rows)
        if item is not None
    ]


def list_knowledge_evidence(
    self, item_id: str | None = None, limit: int = 1000
) -> list[dict[str, Any]]:
    item_key = str(item_id or "").strip()
    if not item_key:
        rows = (
            self._require_read_conn()
            .execute(
                """
                SELECT *
                FROM knowledge_evidence
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            )
            .fetchall()
        )
        return [
            item
            for item in (self._evidence_from_row(row) for row in reversed(rows))
            if item is not None
        ]
    rows = (
        self._require_read_conn()
        .execute(
            """
            SELECT *
            FROM knowledge_evidence
            WHERE item_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (item_key, max(1, int(limit))),
        )
        .fetchall()
    )
    return [
        item
        for item in (self._evidence_from_row(row) for row in reversed(rows))
        if item is not None
    ]


def list_recent_knowledge_evidence(self, limit: int = 20) -> list[dict[str, Any]]:
    rows = (
        self._require_read_conn()
        .execute(
            """
            SELECT *
            FROM knowledge_evidence
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        )
        .fetchall()
    )
    return [
        item
        for item in (self._evidence_from_row(row) for row in rows)
        if item is not None
    ]


def update_candidate_score_status(
    self,
    *,
    item_id: str,
    score: float,
    status: str,
    evidence_count: int,
    positive_count: int,
    negative_count: int,
    conflict_count: int,
) -> None:
    with self._lock:
        self._require_conn().execute(
            """
            UPDATE candidate_knowledge_items
            SET score = ?,
                status = ?,
                evidence_count = ?,
                positive_count = ?,
                negative_count = ?,
                conflict_count = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                float(score),
                str(status or "candidate"),
                int(evidence_count or 0),
                int(positive_count or 0),
                int(negative_count or 0),
                int(conflict_count or 0),
                str(item_id or ""),
            ),
        )
        self._require_conn().commit()


def candidate_status_counts(self) -> dict[str, Any]:
    rows = (
        self._require_read_conn()
        .execute(
            """
            SELECT status, item_type, COUNT(*) AS count
            FROM candidate_knowledge_items
            GROUP BY status, item_type
            """
        )
        .fetchall()
    )
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    total = 0
    for row in rows:
        status = str(row["status"] or "candidate")
        item_type = str(row["item_type"] or "")
        count = int(row["count"] or 0)
        total += count
        by_status[status] = by_status.get(status, 0) + count
        by_type[item_type] = by_type.get(item_type, 0) + count
    return {
        "total": total,
        "by_status": by_status,
        "by_type": by_type,
    }
