from __future__ import annotations

from .store_common import (
    Any,
    Path,
    json,
    safe_float,
    safe_int,
)


def load_knowledge_seed(
    self, path: Path | str | None = None, _visited: set[str] | None = None
) -> int:
    seed_path = Path(path) if path is not None else self.knowledge_seed_json_path
    if seed_path is None or not seed_path.is_file():
        return 0
    visited = _visited if _visited is not None else set()
    try:
        normalized_seed_path = str(seed_path.resolve())
    except OSError:
        normalized_seed_path = str(seed_path.absolute())
    if normalized_seed_path in visited:
        return 0
    visited.add(normalized_seed_path)
    try:
        payload = json.loads(seed_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        self._log_warning("study knowledge seed load failed: {}", exc)
        return 0
    seed_files = payload.get("files") if isinstance(payload, dict) else None
    if isinstance(seed_files, list):
        count = 0
        for item in seed_files:
            if isinstance(item, dict):
                raw_path = item.get("path") or item.get("file")
            else:
                raw_path = item
            child_name = str(raw_path or "").strip()
            if not child_name:
                continue
            child_path = Path(child_name)
            if not child_path.is_absolute():
                child_path = seed_path.parent / child_path
            count += self.load_knowledge_seed(child_path, visited)
        return count
    topics = payload.get("topics") if isinstance(payload, dict) else None
    if not isinstance(topics, list):
        return 0
    default_subject = str(payload.get("subject") or "math")
    default_stage = str(
        payload.get("stage")
        or payload.get("grade_level")
        or payload.get("education_level")
        or payload.get("course_level")
        or ""
    ).strip()
    count = 0
    with self._lock:
        for item in topics:
            if not isinstance(item, dict):
                continue
            topic_id = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            subject = str(item.get("subject") or default_subject).strip()
            chapter = str(item.get("chapter") or item.get("unit") or "general").strip()
            unit = str(
                item.get("unit")
                or item.get("section")
                or item.get("module")
                or chapter
            ).strip()
            stage = str(
                item.get("stage")
                or item.get("grade_level")
                or item.get("education_level")
                or item.get("course_level")
                or default_stage
            ).strip()
            missing_fields = [
                field
                for field, value in (
                    ("id", topic_id),
                    ("name", name),
                    ("subject", subject),
                    ("chapter", chapter),
                    ("stage", stage),
                    ("unit", unit),
                )
                if not value
            ]
            if missing_fields:
                self._log_warning(
                    "study knowledge seed skipped incomplete topic: id={} missing={}",
                    topic_id or "<missing>",
                    ",".join(missing_fields),
                )
                continue
            self.upsert_topic(
                {
                    "id": topic_id,
                    "name": name,
                    "subject": subject,
                    "chapter": chapter,
                    "stage": stage,
                    "unit": unit,
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
                    "skills": item.get("skills")
                    if isinstance(item.get("skills"), list)
                    else [],
                    "question_types": item.get("question_types")
                    if isinstance(item.get("question_types"), list)
                    else [],
                    "examples": (
                        item.get("examples")
                        if isinstance(item.get("examples"), list)
                        else item.get("typical_examples")
                        if isinstance(item.get("typical_examples"), list)
                        else []
                    ),
                    "course_family": str(item.get("course_family") or "").strip(),
                    "curriculum_version": item.get("curriculum_version")
                    if isinstance(item.get("curriculum_version"), (str, list))
                    else [],
                    "exam_region": item.get("exam_region")
                    if isinstance(item.get("exam_region"), (str, list))
                    else [],
                    "exam_type": item.get("exam_type")
                    if isinstance(item.get("exam_type"), (str, list))
                    else [],
                    "aliases": item.get("aliases")
                    if isinstance(item.get("aliases"), list)
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
                id, name, subject, chapter, stage, unit, depth, difficulty,
                prerequisites, related, typical_misconceptions, skills,
                question_types, examples, course_family, curriculum_version,
                exam_region, exam_type, aliases, source, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                name = CASE WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.name ELSE excluded.name END,
                subject = CASE WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.subject ELSE excluded.subject END,
                chapter = CASE WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.chapter ELSE excluded.chapter END,
                stage = CASE
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' AND topics.stage = '' AND excluded.stage != '' THEN excluded.stage
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.stage
                    ELSE excluded.stage
                END,
                unit = CASE
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' AND (topics.unit = '' OR topics.unit = topics.chapter) AND excluded.unit != '' THEN excluded.unit
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.unit
                    ELSE excluded.unit
                END,
                depth = CASE WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.depth ELSE excluded.depth END,
                difficulty = CASE WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.difficulty ELSE excluded.difficulty END,
                prerequisites = CASE WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.prerequisites ELSE excluded.prerequisites END,
                related = CASE WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.related ELSE excluded.related END,
                typical_misconceptions = CASE WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.typical_misconceptions ELSE excluded.typical_misconceptions END,
                skills = CASE
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' AND topics.skills = '[]' AND excluded.skills != '[]' THEN excluded.skills
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.skills
                    ELSE excluded.skills
                END,
                question_types = CASE
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' AND topics.question_types = '[]' AND excluded.question_types != '[]' THEN excluded.question_types
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.question_types
                    ELSE excluded.question_types
                END,
                examples = CASE
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' AND topics.examples = '[]' AND excluded.examples != '[]' THEN excluded.examples
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.examples
                    ELSE excluded.examples
                END,
                course_family = CASE
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' AND topics.course_family = '' AND excluded.course_family != '' THEN excluded.course_family
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.course_family
                    ELSE excluded.course_family
                END,
                curriculum_version = CASE
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' AND topics.curriculum_version = '[]' AND excluded.curriculum_version != '[]' THEN excluded.curriculum_version
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.curriculum_version
                    ELSE excluded.curriculum_version
                END,
                exam_region = CASE
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' AND topics.exam_region = '[]' AND excluded.exam_region != '[]' THEN excluded.exam_region
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.exam_region
                    ELSE excluded.exam_region
                END,
                exam_type = CASE
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' AND topics.exam_type = '[]' AND excluded.exam_type != '[]' THEN excluded.exam_type
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.exam_type
                    ELSE excluded.exam_type
                END,
                aliases = CASE
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' AND topics.aliases = '[]' AND excluded.aliases != '[]' THEN excluded.aliases
                    WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.aliases
                    ELSE excluded.aliases
                END,
                source = CASE WHEN topics.source = 'seed' AND excluded.source != 'seed' THEN topics.source ELSE excluded.source END,
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
                str(topic.get("unit") or topic.get("chapter") or ""),
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
                self._json_dumps(
                    topic.get("skills") if isinstance(topic.get("skills"), list) else []
                ),
                self._json_dumps(
                    topic.get("question_types")
                    if isinstance(topic.get("question_types"), list)
                    else []
                ),
                self._json_dumps(
                    topic.get("examples") if isinstance(topic.get("examples"), list) else []
                ),
                str(topic.get("course_family") or "").strip(),
                self._json_dumps(
                    topic.get("curriculum_version")
                    if isinstance(topic.get("curriculum_version"), (str, list))
                    else []
                ),
                self._json_dumps(
                    topic.get("exam_region")
                    if isinstance(topic.get("exam_region"), (str, list))
                    else []
                ),
                self._json_dumps(
                    topic.get("exam_type")
                    if isinstance(topic.get("exam_type"), (str, list))
                    else []
                ),
                self._json_dumps(
                    topic.get("aliases") if isinstance(topic.get("aliases"), list) else []
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
            "unit": chapter or "runtime",
            "depth": 2,
            "difficulty": difficulty,
            "prerequisites": [],
            "related": [],
            "typical_misconceptions": [],
            "skills": [],
            "question_types": [],
            "examples": [],
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
