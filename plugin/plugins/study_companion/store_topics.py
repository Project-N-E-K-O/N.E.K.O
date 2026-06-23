from __future__ import annotations

from .store_common import (
    Any,
    Path,
    json,
    safe_float,
    safe_int,
)


def load_knowledge_seed(self, path: Path | str | None = None) -> int:
    seed_path = Path(path) if path is not None else self.knowledge_seed_json_path
    if seed_path is None or not seed_path.is_file():
        return 0
    try:
        payload = json.loads(seed_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        self._log_warning("study knowledge seed load failed: {}", exc)
        return 0
    topics = payload.get("topics") if isinstance(payload, dict) else None
    if not isinstance(topics, list):
        return 0
    count = 0
    with self._lock:
        for item in topics:
            if not isinstance(item, dict):
                continue
            topic_id = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not topic_id or not name:
                continue
            self.upsert_topic(
                {
                    "id": topic_id,
                    "name": name,
                    "subject": str(
                        item.get("subject") or payload.get("subject") or "math"
                    ),
                    "chapter": str(item.get("chapter") or ""),
                    "stage": str(
                        item.get("stage")
                        or item.get("grade_level")
                        or item.get("education_level")
                        or item.get("course_level")
                        or ""
                    ).strip(),
                    "depth": safe_int(item.get("depth"), 1),
                    "difficulty": safe_float(item.get("difficulty"), 0.5),
                    "prerequisites": item.get("prerequisites")
                    if isinstance(item.get("prerequisites"), list)
                    else [],
                    "related": item.get("related")
                    if isinstance(item.get("related"), list)
                    else [],
                    "typical_misconceptions": item.get("typical_misconceptions")
                    if isinstance(item.get("typical_misconceptions"), list)
                    else [],
                    "source": "seed",
                },
                commit=False,
            )
            count += 1
        self._require_conn().commit()
    return count


def upsert_topic(self, topic: dict[str, Any], *, commit: bool = True) -> None:
    topic_id = str(topic.get("id") or "").strip()
    name = str(topic.get("name") or topic_id).strip()
    if not topic_id or not name:
        return
    with self._lock:
        self._require_conn().execute(
            """
            INSERT INTO topics (
                id, name, subject, chapter, stage, depth, difficulty,
                prerequisites, related, typical_misconceptions, source, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                name = CASE WHEN topics.source = 'seed' THEN topics.name ELSE excluded.name END,
                subject = CASE WHEN topics.source = 'seed' THEN topics.subject ELSE excluded.subject END,
                chapter = CASE WHEN topics.source = 'seed' THEN topics.chapter ELSE excluded.chapter END,
                stage = CASE WHEN topics.source = 'seed' THEN topics.stage ELSE excluded.stage END,
                depth = CASE WHEN topics.source = 'seed' THEN topics.depth ELSE excluded.depth END,
                difficulty = CASE WHEN topics.source = 'seed' THEN topics.difficulty ELSE excluded.difficulty END,
                prerequisites = CASE WHEN topics.source = 'seed' THEN topics.prerequisites ELSE excluded.prerequisites END,
                related = CASE WHEN topics.source = 'seed' THEN topics.related ELSE excluded.related END,
                typical_misconceptions = CASE WHEN topics.source = 'seed' THEN topics.typical_misconceptions ELSE excluded.typical_misconceptions END,
                source = CASE WHEN topics.source = 'seed' THEN topics.source ELSE excluded.source END,
                updated_at = datetime('now')
            """,
            (
                topic_id,
                name,
                str(topic.get("subject") or "math"),
                str(topic.get("chapter") or ""),
                str(
                    topic.get("stage")
                    or topic.get("grade_level")
                    or topic.get("education_level")
                    or topic.get("course_level")
                    or ""
                ).strip(),
                safe_int(topic.get("depth"), 1),
                safe_float(topic.get("difficulty"), 0.5),
                self._json_dumps(
                    topic.get("prerequisites")
                    if isinstance(topic.get("prerequisites"), list)
                    else []
                ),
                self._json_dumps(
                    topic.get("related")
                    if isinstance(topic.get("related"), list)
                    else []
                ),
                self._json_dumps(
                    topic.get("typical_misconceptions")
                    if isinstance(topic.get("typical_misconceptions"), list)
                    else []
                ),
                str(topic.get("source") or "runtime"),
            ),
        )
        if commit:
            self._require_conn().commit()


def ensure_topic(
    self,
    *,
    topic_id: str,
    name: str,
    subject: str = "math",
    chapter: str = "runtime",
    difficulty: float = 0.5,
) -> None:
    if self.get_topic(topic_id):
        return
    self.upsert_topic(
        {
            "id": topic_id,
            "name": name or topic_id,
            "subject": subject or "math",
            "chapter": chapter or "runtime",
            "stage": "",
            "depth": 2,
            "difficulty": difficulty,
            "prerequisites": [],
            "related": [],
            "typical_misconceptions": [],
            "source": "runtime",
        }
    )


def get_topic(self, topic_id: str) -> dict[str, Any] | None:
    row = (
        self._require_read_conn()
        .execute("SELECT * FROM topics WHERE id = ?", (str(topic_id or ""),))
        .fetchone()
    )
    return self._topic_from_row(row)


def find_topic_by_name(self, name: str) -> dict[str, Any] | None:
    text = str(name or "").strip()
    if not text:
        return None
    row = (
        self._require_read_conn()
        .execute(
            "SELECT * FROM topics WHERE name = ? OR id = ? LIMIT 1",
            (text, text),
        )
        .fetchone()
    )
    return self._topic_from_row(row)


def list_topics(
    self, limit: int = 100, subject: str | None = None, stage: str | None = None
) -> list[dict[str, Any]]:
    stage_key = str(stage or "").strip()
    if subject and stage_key:
        rows = (
            self._require_read_conn()
            .execute(
                "SELECT * FROM topics WHERE subject = ? AND stage = ? ORDER BY chapter, depth, id LIMIT ?",
                (subject, stage_key, max(1, int(limit))),
            )
            .fetchall()
        )
    elif subject:
        rows = (
            self._require_read_conn()
            .execute(
                "SELECT * FROM topics WHERE subject = ? ORDER BY chapter, depth, id LIMIT ?",
                (subject, max(1, int(limit))),
            )
            .fetchall()
        )
    elif stage_key:
        rows = (
            self._require_read_conn()
            .execute(
                "SELECT * FROM topics WHERE stage = ? ORDER BY subject, chapter, depth, id LIMIT ?",
                (stage_key, max(1, int(limit))),
            )
            .fetchall()
        )
    else:
        rows = (
            self._require_read_conn()
            .execute(
                "SELECT * FROM topics ORDER BY subject, chapter, depth, id LIMIT ?",
                (max(1, int(limit)),),
            )
            .fetchall()
        )
    return [
        topic
        for topic in (self._topic_from_row(row) for row in rows)
        if topic is not None
    ]


def count_topics(self) -> int:
    row = (
        self._require_read_conn()
        .execute("SELECT COUNT(*) AS count FROM topics")
        .fetchone()
    )
    return int(row["count"] if row is not None else 0)


def count_tracked_mastery_topics(self) -> int:
    row = (
        self._require_read_conn()
        .execute("SELECT COUNT(DISTINCT topic_id) AS count FROM mastery_snapshots")
        .fetchone()
    )
    return int(row["count"] if row is not None else 0)


def average_latest_mastery(self) -> float:
    row = (
        self._require_read_conn()
        .execute(
            """
            SELECT AVG(ms.mastery) AS average_mastery
            FROM mastery_snapshots ms
            JOIN (
                SELECT topic_id, MAX(id) AS max_id
                FROM mastery_snapshots
                GROUP BY topic_id
            ) latest ON latest.max_id = ms.id
            """
        )
        .fetchone()
    )
    return float(row["average_mastery"] or 0.0) if row is not None else 0.0
