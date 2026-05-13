from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .mode_manager import normalize_mode
from .models import STORE_CONFIG, STORE_STATE, StudyConfig, StudyState, build_config, json_copy

_DROP = object()
_STATE_ITEM_FLOAT_KEYS = {"at", "created_at", "updated_at", "expires_at", "lock_until"}
_DEFAULT_APPEND_ONLY_HISTORY_LIMIT = 5000


def safe_float(value: Any, default: Any = 0.0) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _sanitize_suggestion_cooldowns(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, float] = {}
    for key, raw in value.items():
        coerced = safe_float(raw, _DROP)
        if coerced is not _DROP:
            cleaned[str(key)] = coerced
    return cleaned


def _sanitize_state_item_list(value: Any, *, required_float_key: str | None = None) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in json_copy(value):
        if not isinstance(item, dict):
            continue
        sanitized = dict(item)
        if required_float_key is not None:
            coerced = safe_float(sanitized.get(required_float_key), _DROP)
            if coerced is _DROP:
                continue
            sanitized[required_float_key] = coerced
        valid = True
        for key in _STATE_ITEM_FLOAT_KEYS.intersection(sanitized.keys()):
            coerced = safe_float(sanitized.get(key), _DROP)
            if coerced is _DROP:
                valid = False
                break
            sanitized[key] = coerced
        if valid:
            cleaned.append(sanitized)
    return cleaned


class StudyStore:
    """SQLite main store with JSON import/export support for seeds and backups."""

    def __init__(self, db_path: Path, seed_json_path: Path, logger: Any, knowledge_seed_json_path: Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.seed_json_path = Path(seed_json_path)
        self.knowledge_seed_json_path = Path(knowledge_seed_json_path) if knowledge_seed_json_path is not None else None
        self._logger = logger
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        with self._lock:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10.0)
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.row_factory = sqlite3.Row
            self._init_db()
            self._load_seed_if_empty()
            self.load_knowledge_seed()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.open()
        assert self._conn is not None
        return self._conn

    def _init_db(self) -> None:
        conn = self._require_conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                input_text TEXT NOT NULL,
                output_text TEXT NOT NULL,
                metadata TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS topics (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                subject TEXT NOT NULL,
                chapter TEXT,
                depth INTEGER DEFAULT 1,
                difficulty REAL DEFAULT 0.5,
                prerequisites TEXT NOT NULL DEFAULT '[]',
                related TEXT NOT NULL DEFAULT '[]',
                typical_misconceptions TEXT NOT NULL DEFAULT '[]',
                source TEXT NOT NULL DEFAULT 'runtime',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mastery_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id TEXT NOT NULL REFERENCES topics(id),
                mastery REAL NOT NULL,
                accuracy REAL,
                recency REAL,
                consistency REAL,
                confidence REAL,
                level TEXT,
                attempts INTEGER DEFAULT 0,
                flags TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wrong_questions (
                id TEXT PRIMARY KEY,
                topic_id TEXT NOT NULL REFERENCES topics(id),
                question TEXT NOT NULL,
                user_answer TEXT NOT NULL,
                expected_answer TEXT NOT NULL,
                error_type TEXT NOT NULL,
                verdict TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                retry_count INTEGER DEFAULT 0,
                consecutive_correct INTEGER DEFAULT 0,
                max_correct_difficulty INTEGER DEFAULT 0,
                last_error_at TEXT DEFAULT (datetime('now')),
                last_retry_at TEXT,
                resolved_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fsrs_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id TEXT NOT NULL UNIQUE REFERENCES topics(id),
                card_data TEXT NOT NULL,
                fsrs_state TEXT,
                last_rating INTEGER,
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT (datetime('now')),
                ended_at TEXT,
                duration_minutes REAL,
                question_count INTEGER DEFAULT 0,
                topics_touched TEXT NOT NULL DEFAULT '[]',
                summary_markdown TEXT,
                notes_exported INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS qa_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id),
                topic_id TEXT REFERENCES topics(id),
                question TEXT,
                user_answer TEXT,
                eval_result TEXT,
                mode TEXT NOT NULL,
                response_time_ms INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id TEXT NOT NULL REFERENCES topics(id),
                card_id INTEGER REFERENCES fsrs_cards(id),
                rating INTEGER,
                scheduled_days INTEGER,
                actual_days INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS candidate_knowledge_items (
                id TEXT PRIMARY KEY,
                item_type TEXT NOT NULL,
                dedupe_key TEXT,
                payload_json TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT DEFAULT 'candidate',
                score REAL DEFAULT 0.0,
                evidence_count INTEGER DEFAULT 0,
                positive_count INTEGER DEFAULT 0,
                negative_count INTEGER DEFAULT 0,
                conflict_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL REFERENCES candidate_knowledge_items(id),
                event_type TEXT NOT NULL,
                weight REAL NOT NULL,
                context_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS anonymous_knowledge_stats (
                id TEXT PRIMARY KEY,
                stat_type TEXT NOT NULL,
                stat_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                sample_count INTEGER DEFAULT 0,
                outcome_json TEXT NOT NULL DEFAULT '{}',
                min_sample_met INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_contribution_queue (
                id TEXT PRIMARY KEY,
                stats_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'preview',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        self._ensure_column(conn, "candidate_knowledge_items", "dedupe_key", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mastery_topic_updated ON mastery_snapshots(topic_id, updated_at DESC, id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wrong_topic_status ON wrong_questions(topic_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_qa_topic_created ON qa_records(topic_id, created_at DESC, id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_review_topic_created ON review_log(topic_id, created_at DESC, id DESC)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_knowledge_dedupe ON candidate_knowledge_items(item_type, dedupe_key) WHERE dedupe_key IS NOT NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_candidate_knowledge_status ON candidate_knowledge_items(status, item_type, updated_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_evidence_item ON knowledge_evidence(item_id, created_at DESC)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_anonymous_knowledge_stats_key ON anonymous_knowledge_stats(stat_type, stat_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_contribution_queue_status ON knowledge_contribution_queue(status, updated_at DESC)")
        conn.commit()

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if column in {str(row["name"]) for row in rows}:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _trim_append_only_rows(
        conn: sqlite3.Connection,
        *,
        table: str,
        group_column: str,
        group_value: str,
        history_limit: int,
    ) -> None:
        limit = max(1, int(history_limit))
        conn.execute(
            f"""
            DELETE FROM {table}
            WHERE {group_column} = ?
              AND id NOT IN (
                  SELECT id
                  FROM {table}
                  WHERE {group_column} = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
            """,
            (group_value, group_value, limit),
        )

    def _load_seed_if_empty(self) -> None:
        if not self.seed_json_path.is_file():
            return
        if self.get_raw(STORE_CONFIG) is not None or self.get_raw(STORE_STATE) is not None:
            return
        if self.get_raw("interactions") or self._has_interactions():
            return
        try:
            payload = json.loads(self.seed_json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            self._log_warning("study seed load failed: {}", exc)
            return
        if not isinstance(payload, dict):
            return
        for key in (STORE_CONFIG, STORE_STATE):
            value = payload.get(key)
            if isinstance(value, dict):
                self.set_raw(key, value)

    @staticmethod
    def _json_loads(value: object, fallback: Any) -> Any:
        try:
            parsed = json.loads(str(value or ""))
        except (ValueError, TypeError):
            return json_copy(fallback)
        return parsed

    @staticmethod
    def _json_dumps(value: Any) -> str:
        return json.dumps(json_copy(value), ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _topic_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "subject": str(row["subject"]),
            "chapter": str(row["chapter"] or ""),
            "depth": safe_int(row["depth"] or 1, 1),
            "difficulty": safe_float(row["difficulty"] or 0.5, 0.5),
            "prerequisites": StudyStore._json_loads(row["prerequisites"], []),
            "related": StudyStore._json_loads(row["related"], []),
            "typical_misconceptions": StudyStore._json_loads(row["typical_misconceptions"], []),
            "source": str(row["source"] or ""),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }

    @staticmethod
    def _candidate_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "item_type": str(row["item_type"]),
            "dedupe_key": str(row["dedupe_key"] or ""),
            "payload": StudyStore._json_loads(row["payload_json"], {}),
            "source": str(row["source"] or ""),
            "status": str(row["status"] or "candidate"),
            "score": float(row["score"] or 0.0),
            "evidence_count": int(row["evidence_count"] or 0),
            "positive_count": int(row["positive_count"] or 0),
            "negative_count": int(row["negative_count"] or 0),
            "conflict_count": int(row["conflict_count"] or 0),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }

    @staticmethod
    def _evidence_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "item_id": str(row["item_id"]),
            "event_type": str(row["event_type"]),
            "weight": float(row["weight"] or 0.0),
            "context": StudyStore._json_loads(row["context_json"], {}),
            "created_at": str(row["created_at"] or ""),
        }

    @staticmethod
    def _anonymous_stat_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "stat_type": str(row["stat_type"]),
            "stat_key": str(row["stat_key"]),
            "payload": StudyStore._json_loads(row["payload_json"], {}),
            "sample_count": int(row["sample_count"] or 0),
            "outcome": StudyStore._json_loads(row["outcome_json"], {}),
            "min_sample_met": bool(row["min_sample_met"]),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }

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
                        "subject": str(item.get("subject") or payload.get("subject") or "math"),
                        "chapter": str(item.get("chapter") or ""),
                        "depth": safe_int(item.get("depth") or 1, 1),
                        "difficulty": safe_float(item.get("difficulty") or 0.5, 0.5),
                        "prerequisites": item.get("prerequisites") if isinstance(item.get("prerequisites"), list) else [],
                        "related": item.get("related") if isinstance(item.get("related"), list) else [],
                        "typical_misconceptions": item.get("typical_misconceptions") if isinstance(item.get("typical_misconceptions"), list) else [],
                        "source": "seed",
                    }
                )
                count += 1
        return count

    def _log_warning(self, message: str, *args: Any) -> None:
        warning = getattr(self._logger, "warning", None)
        if callable(warning):
            try:
                warning(message, *args)
            except Exception:
                pass

    def _has_interactions(self) -> bool:
        with self._lock:
            row = self._require_conn().execute("SELECT 1 FROM interactions LIMIT 1").fetchone()
            return row is not None

    def get_raw(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._require_conn().execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
            if row is None:
                return None
            try:
                value = json.loads(str(row["value"]))
            except (ValueError, TypeError):
                return None
            return value if isinstance(value, dict) else None

    def set_raw(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            now = time.time()
            self._require_conn().execute(
                """
                INSERT INTO kv (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, json.dumps(json_copy(value), ensure_ascii=False, sort_keys=True), now),
            )
            self._require_conn().commit()

    def load_config(self, fallback: StudyConfig) -> StudyConfig:
        raw = self.get_raw(STORE_CONFIG)
        if not raw:
            return fallback
        merged = fallback.to_dict()
        merged.update(raw)
        return build_config(merged)

    def save_config(self, config: StudyConfig) -> None:
        self.set_raw(STORE_CONFIG, config.to_dict())

    def load_state(self, fallback: StudyState) -> StudyState:
        raw = self.get_raw(STORE_STATE)
        if not raw:
            return fallback
        merged = fallback.to_dict()
        merged.update(raw)
        merged["active_mode"] = normalize_mode(merged.get("active_mode") or fallback.active_mode)
        merged["mode_started_at"] = safe_float(merged.get("mode_started_at"), 0.0)
        merged["recent_mode_switches"] = _sanitize_state_item_list(
            merged.get("recent_mode_switches"),
            required_float_key="at",
        )
        merged["suggestion_cooldowns"] = _sanitize_suggestion_cooldowns(merged.get("suggestion_cooldowns"))
        merged["session_suggestions"] = _sanitize_state_item_list(merged.get("session_suggestions"))
        merged["mode_lock_until"] = safe_float(merged.get("mode_lock_until"), 0.0)
        return StudyState(**{key: merged[key] for key in fallback.to_dict().keys()})

    def save_state(self, state: StudyState) -> None:
        self.set_raw(STORE_STATE, state.to_dict())

    def append_interaction(
        self,
        *,
        kind: str,
        input_text: str,
        output_text: str,
        metadata: dict[str, Any] | None = None,
        history_limit: int = 50,
    ) -> None:
        with self._lock:
            conn = self._require_conn()
            conn.execute(
                """
                INSERT INTO interactions (kind, input_text, output_text, metadata, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    kind,
                    input_text,
                    output_text,
                    json.dumps(json_copy(metadata or {}), ensure_ascii=False, sort_keys=True),
                    time.time(),
                ),
            )
            conn.execute(
                """
                DELETE FROM interactions
                WHERE id NOT IN (
                    SELECT id FROM interactions ORDER BY id DESC LIMIT ?
                )
                """,
                (max(1, int(history_limit)),),
            )
            conn.commit()

    def list_interactions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._require_conn().execute(
                """
                SELECT id, kind, input_text, output_text, metadata, created_at
                FROM interactions
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                metadata = json.loads(str(row["metadata"]))
            except (ValueError, TypeError):
                metadata = {}
            result.append(
                {
                    "id": int(row["id"]),
                    "kind": str(row["kind"]),
                    "input_text": str(row["input_text"]),
                    "output_text": str(row["output_text"]),
                    "metadata": metadata if isinstance(metadata, dict) else {},
                    "created_at": float(row["created_at"]),
                }
            )
        return result

    def upsert_topic(self, topic: dict[str, Any]) -> None:
        topic_id = str(topic.get("id") or "").strip()
        name = str(topic.get("name") or topic_id).strip()
        if not topic_id or not name:
            return
        with self._lock:
            self._require_conn().execute(
                """
                INSERT INTO topics (
                    id, name, subject, chapter, depth, difficulty,
                    prerequisites, related, typical_misconceptions, source, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    subject = excluded.subject,
                    chapter = excluded.chapter,
                    depth = excluded.depth,
                    difficulty = excluded.difficulty,
                    prerequisites = excluded.prerequisites,
                    related = excluded.related,
                    typical_misconceptions = excluded.typical_misconceptions,
                    source = CASE WHEN topics.source = 'seed' THEN topics.source ELSE excluded.source END,
                    updated_at = datetime('now')
                """,
                (
                    topic_id,
                    name,
                    str(topic.get("subject") or "math"),
                    str(topic.get("chapter") or ""),
                    safe_int(topic.get("depth") or 1, 1),
                    safe_float(topic.get("difficulty") or 0.5, 0.5),
                    self._json_dumps(topic.get("prerequisites") if isinstance(topic.get("prerequisites"), list) else []),
                    self._json_dumps(topic.get("related") if isinstance(topic.get("related"), list) else []),
                    self._json_dumps(
                        topic.get("typical_misconceptions")
                        if isinstance(topic.get("typical_misconceptions"), list)
                        else []
                    ),
                    str(topic.get("source") or "runtime"),
                ),
            )
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
                "depth": 2,
                "difficulty": difficulty,
                "prerequisites": [],
                "related": [],
                "typical_misconceptions": [],
                "source": "runtime",
            }
        )

    def get_topic(self, topic_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._require_conn().execute("SELECT * FROM topics WHERE id = ?", (str(topic_id or ""),)).fetchone()
        return self._topic_from_row(row)

    def find_topic_by_name(self, name: str) -> dict[str, Any] | None:
        text = str(name or "").strip()
        if not text:
            return None
        with self._lock:
            row = self._require_conn().execute(
                "SELECT * FROM topics WHERE name = ? OR id = ? LIMIT 1",
                (text, text),
            ).fetchone()
        return self._topic_from_row(row)

    def list_topics(self, limit: int = 100, subject: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if subject:
                rows = self._require_conn().execute(
                    "SELECT * FROM topics WHERE subject = ? ORDER BY chapter, depth, id LIMIT ?",
                    (subject, max(1, int(limit))),
                ).fetchall()
            else:
                rows = self._require_conn().execute(
                    "SELECT * FROM topics ORDER BY subject, chapter, depth, id LIMIT ?",
                    (max(1, int(limit)),),
                ).fetchall()
        return [topic for topic in (self._topic_from_row(row) for row in rows) if topic is not None]

    def count_topics(self) -> int:
        with self._lock:
            row = self._require_conn().execute("SELECT COUNT(*) AS count FROM topics").fetchone()
        return int(row["count"] if row is not None else 0)

    def count_tracked_mastery_topics(self) -> int:
        with self._lock:
            row = self._require_conn().execute(
                "SELECT COUNT(DISTINCT topic_id) AS count FROM mastery_snapshots"
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    def average_latest_mastery(self) -> float:
        with self._lock:
            row = self._require_conn().execute(
                """
                SELECT AVG(ms.mastery) AS average_mastery
                FROM mastery_snapshots ms
                JOIN (
                    SELECT topic_id, MAX(id) AS max_id
                    FROM mastery_snapshots
                    GROUP BY topic_id
                ) latest ON latest.max_id = ms.id
                """
            ).fetchone()
        return float(row["average_mastery"] or 0.0) if row is not None else 0.0

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
                    (item_id, item_type, dedupe_key, payload_json, source_value, status),
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
            row = conn.execute("SELECT * FROM candidate_knowledge_items WHERE id = ?", (item_id,)).fetchone()
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
            row = conn.execute("SELECT * FROM knowledge_evidence WHERE id = ?", (int(cursor.lastrowid),)).fetchone()
        evidence = self._evidence_from_row(row)
        if evidence is None:
            raise RuntimeError("knowledge evidence insert failed")
        return evidence

    def get_candidate_item(self, item_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._require_conn().execute(
                "SELECT * FROM candidate_knowledge_items WHERE id = ?",
                (str(item_id or ""),),
            ).fetchone()
        return self._candidate_from_row(row)

    def get_candidate_by_key(self, *, item_type: str, dedupe_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._require_conn().execute(
                "SELECT * FROM candidate_knowledge_items WHERE item_type = ? AND dedupe_key = ? LIMIT 1",
                (str(item_type or ""), str(dedupe_key or "")),
            ).fetchone()
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
        with self._lock:
            rows = self._require_conn().execute(
                f"""
                SELECT *
                FROM candidate_knowledge_items
                {where}
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [item for item in (self._candidate_from_row(row) for row in rows) if item is not None]

    def list_knowledge_evidence(self, item_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._require_conn().execute(
                """
                SELECT *
                FROM knowledge_evidence
                WHERE item_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (str(item_id or ""), max(1, int(limit))),
            ).fetchall()
        return [item for item in (self._evidence_from_row(row) for row in rows) if item is not None]

    def list_recent_knowledge_evidence(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._require_conn().execute(
                """
                SELECT *
                FROM knowledge_evidence
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [item for item in (self._evidence_from_row(row) for row in rows) if item is not None]

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
        with self._lock:
            rows = self._require_conn().execute(
                """
                SELECT status, item_type, COUNT(*) AS count
                FROM candidate_knowledge_items
                GROUP BY status, item_type
                """
            ).fetchall()
            total_row = self._require_conn().execute("SELECT COUNT(*) AS count FROM candidate_knowledge_items").fetchone()
        by_status: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for row in rows:
            status = str(row["status"] or "candidate")
            item_type = str(row["item_type"] or "")
            count = int(row["count"] or 0)
            by_status[status] = by_status.get(status, 0) + count
            by_type[item_type] = by_type.get(item_type, 0) + count
        return {
            "total": int(total_row["count"] if total_row is not None else 0),
            "by_status": by_status,
            "by_type": by_type,
        }

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
            rows = self._require_conn().execute(
                """
                SELECT *
                FROM anonymous_knowledge_stats
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [item for item in (self._anonymous_stat_from_row(row) for row in rows) if item is not None]

    def anonymous_knowledge_stats_summary(self) -> dict[str, Any]:
        with self._lock:
            rows = self._require_conn().execute(
                """
                SELECT stat_type, min_sample_met, COUNT(*) AS count, COALESCE(SUM(sample_count), 0) AS samples
                FROM anonymous_knowledge_stats
                GROUP BY stat_type, min_sample_met
                """
            ).fetchall()
            queue_row = self._require_conn().execute("SELECT COUNT(*) AS count FROM knowledge_contribution_queue").fetchone()
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

    def enqueue_knowledge_contribution_snapshot(self, *, stats: list[dict[str, Any]], status: str) -> dict[str, Any]:
        queue_id = str(uuid.uuid4())
        with self._lock:
            conn = self._require_conn()
            conn.execute(
                """
                INSERT INTO knowledge_contribution_queue (id, stats_json, status, created_at, updated_at)
                VALUES (?, ?, ?, datetime('now'), datetime('now'))
                """,
                (queue_id, self._json_dumps(stats or []), str(status or "preview")),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM knowledge_contribution_queue WHERE id = ?", (queue_id,)).fetchone()
        return {
            "id": str(row["id"]),
            "stats": self._json_loads(row["stats_json"], []),
            "status": str(row["status"] or ""),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }

    def list_knowledge_contribution_queue(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._require_conn().execute(
                """
                SELECT *
                FROM knowledge_contribution_queue
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
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
            cursor = self._require_conn().execute("DELETE FROM knowledge_contribution_queue")
            self._require_conn().commit()
        return int(cursor.rowcount or 0)

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
                    self._json_dumps(snapshot.get("flags") if isinstance(snapshot.get("flags"), list) else []),
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
        with self._lock:
            row = self._require_conn().execute(
                """
                SELECT ms.*, t.name AS topic_name, t.chapter AS chapter, t.subject AS subject
                FROM mastery_snapshots ms
                LEFT JOIN topics t ON t.id = ms.topic_id
                WHERE ms.topic_id = ?
                ORDER BY ms.id DESC
                LIMIT 1
                """,
                (str(topic_id or ""),),
            ).fetchone()
        return self._mastery_from_row(row)

    def list_mastery_overview(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._require_conn().execute(
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
            ).fetchall()
        return [item for item in (self._mastery_from_row(row) for row in rows) if item is not None]

    @staticmethod
    def _mastery_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "topic_id": str(row["topic_id"]),
            "topic_name": str(row["topic_name"] or row["topic_id"]),
            "chapter": str(row["chapter"] or ""),
            "subject": str(row["subject"] or ""),
            "mastery": float(row["mastery"] or 0.0),
            "accuracy": float(row["accuracy"] or 0.0),
            "recency": float(row["recency"] or 0.0),
            "consistency": float(row["consistency"] or 0.0),
            "confidence": float(row["confidence"] or 0.0),
            "level": str(row["level"] or ""),
            "attempts": int(row["attempts"] or 0),
            "flags": StudyStore._json_loads(row["flags"], []),
            "updated_at": str(row["updated_at"] or ""),
        }

    def ensure_session(self, *, session_id: str, mode: str) -> None:
        session_key = str(session_id or "default")
        with self._lock:
            self._require_conn().execute(
                """
                INSERT INTO sessions (id, mode, started_at, topics_touched)
                VALUES (?, ?, datetime('now'), '[]')
                ON CONFLICT(id) DO NOTHING
                """,
                (session_key, str(mode or "companion")),
            )
            self._require_conn().commit()

    def add_qa_record(
        self,
        *,
        session_id: str,
        topic_id: str,
        question: dict[str, Any],
        user_answer: str,
        eval_result: dict[str, Any],
        mode: str,
        response_time_ms: int | None = None,
        history_limit: int = _DEFAULT_APPEND_ONLY_HISTORY_LIMIT,
    ) -> None:
        session_key = str(session_id or "default")
        topic_key = str(topic_id or "").strip()
        db_topic_key = topic_key or None
        with self._lock:
            conn = self._require_conn()
            conn.execute(
                """
                INSERT INTO qa_records (
                    session_id, topic_id, question, user_answer,
                    eval_result, mode, response_time_ms, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    session_key,
                    db_topic_key,
                    self._json_dumps(question or {}),
                    str(user_answer or ""),
                    self._json_dumps(eval_result or {}),
                    str(mode or "companion"),
                    int(response_time_ms) if response_time_ms is not None else None,
                ),
            )
            row = conn.execute("SELECT topics_touched FROM sessions WHERE id = ?", (session_key,)).fetchone()
            touched = self._json_loads(row["topics_touched"], []) if row is not None else []
            if topic_key and topic_key not in touched:
                touched.append(topic_key)
            conn.execute(
                """
                UPDATE sessions
                SET question_count = question_count + 1, topics_touched = ?
                WHERE id = ?
                """,
                (self._json_dumps(touched), session_key),
            )
            if db_topic_key is not None:
                self._trim_append_only_rows(
                    conn,
                    table="qa_records",
                    group_column="topic_id",
                    group_value=db_topic_key,
                    history_limit=history_limit,
                )
            conn.commit()

    def list_qa_records_for_topic(self, topic_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._require_conn().execute(
                """
                SELECT *
                FROM qa_records
                WHERE topic_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (str(topic_id or ""), max(1, int(limit))),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in reversed(rows):
            result.append(
                {
                    "id": int(row["id"]),
                    "session_id": str(row["session_id"]),
                    "topic_id": str(row["topic_id"] or ""),
                    "question": self._json_loads(row["question"], {}),
                    "user_answer": str(row["user_answer"] or ""),
                    "eval_result": self._json_loads(row["eval_result"], {}),
                    "mode": str(row["mode"] or ""),
                    "response_time_ms": int(row["response_time_ms"] or 0),
                    "created_at": str(row["created_at"] or ""),
                }
            )
        return result

    def add_wrong_question(
        self,
        *,
        topic_id: str,
        question: dict[str, Any],
        user_answer: str,
        expected_answer: str,
        error_type: str,
        verdict: str,
    ) -> str:
        question_id = str(uuid.uuid4())
        with self._lock:
            self._require_conn().execute(
                """
                INSERT INTO wrong_questions (
                    id, topic_id, question, user_answer, expected_answer,
                    error_type, verdict, status, retry_count, consecutive_correct,
                    max_correct_difficulty, last_error_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 0, 0, 0, datetime('now'), datetime('now'), datetime('now'))
                """,
                (
                    question_id,
                    str(topic_id or ""),
                    self._json_dumps(question or {}),
                    str(user_answer or ""),
                    str(expected_answer or ""),
                    str(error_type or "unknown"),
                    str(verdict or "wrong"),
                ),
            )
            self._require_conn().commit()
        return question_id

    def get_retry_wrong_question(self, topic_id: str) -> dict[str, Any] | None:
        rows = self.list_wrong_questions(limit=1, topic_id=topic_id, statuses=("active", "retrying"))
        return rows[0] if rows else None

    def list_wrong_questions(
        self,
        *,
        limit: int = 20,
        topic_id: str | None = None,
        statuses: tuple[str, ...] = ("active", "retrying", "resolved"),
    ) -> list[dict[str, Any]]:
        status_values = tuple(str(item) for item in statuses if str(item))
        if not status_values:
            status_values = ("active", "retrying", "resolved")
        placeholders = ",".join("?" for _ in status_values)
        params: list[Any] = list(status_values)
        where = f"status IN ({placeholders})"
        if topic_id:
            where += " AND topic_id = ?"
            params.append(str(topic_id))
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._require_conn().execute(
                f"""
                SELECT *
                FROM wrong_questions
                WHERE {where}
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._wrong_question_from_row(row) for row in rows]

    def _wrong_question_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "topic_id": str(row["topic_id"]),
            "question": self._json_loads(row["question"], {}),
            "user_answer": str(row["user_answer"] or ""),
            "expected_answer": str(row["expected_answer"] or ""),
            "error_type": str(row["error_type"] or ""),
            "verdict": str(row["verdict"] or ""),
            "status": str(row["status"] or ""),
            "retry_count": int(row["retry_count"] or 0),
            "consecutive_correct": int(row["consecutive_correct"] or 0),
            "max_correct_difficulty": int(row["max_correct_difficulty"] or 0),
            "last_error_at": str(row["last_error_at"] or ""),
            "last_retry_at": str(row["last_retry_at"] or ""),
            "resolved_at": str(row["resolved_at"] or ""),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }

    def mark_wrong_question_resolved(self, question_id: str) -> None:
        with self._lock:
            self._require_conn().execute(
                """
                UPDATE wrong_questions
                SET status = 'resolved', resolved_at = datetime('now'), updated_at = datetime('now')
                WHERE id = ?
                """,
                (str(question_id or ""),),
            )
            self._require_conn().commit()

    def record_wrong_question_correct(self, *, topic_id: str, error_type: str, difficulty: int) -> None:
        with self._lock:
            rows = self._require_conn().execute(
                """
                SELECT *
                FROM wrong_questions
                WHERE topic_id = ? AND status IN ('active', 'retrying')
                ORDER BY
                    CASE WHEN status = 'retrying' THEN 0 ELSE 1 END,
                    last_retry_at DESC,
                    created_at DESC,
                    id DESC
                LIMIT 5
                """,
                (str(topic_id or ""),),
            ).fetchall()
            matched_generic_correct = False
            current_error_type = str(error_type or "none").strip()
            for row in rows:
                if current_error_type in {"", "none"}:
                    if matched_generic_correct:
                        continue
                    matched_generic_correct = True
                elif current_error_type != str(row["error_type"] or ""):
                    continue
                consecutive = int(row["consecutive_correct"] or 0) + 1
                max_difficulty = max(int(row["max_correct_difficulty"] or 0), int(difficulty or 0))
                old_enough = bool(
                    self._require_conn()
                    .execute(
                        "SELECT (julianday('now') - julianday(?)) >= 1.0 AS ok",
                        (str(row["last_error_at"] or ""),),
                    )
                    .fetchone()["ok"]
                )
                status = "retrying"
                if consecutive >= 3 and max_difficulty >= 3 and old_enough:
                    status = "resolved"
                if status == "resolved":
                    self._require_conn().execute(
                        """
                        UPDATE wrong_questions
                        SET status = 'resolved',
                            retry_count = retry_count + 1,
                            consecutive_correct = ?,
                            max_correct_difficulty = ?,
                            last_retry_at = datetime('now'),
                            resolved_at = datetime('now'),
                            updated_at = datetime('now')
                        WHERE id = ?
                        """,
                        (consecutive, max_difficulty, str(row["id"])),
                    )
                else:
                    self._require_conn().execute(
                        """
                        UPDATE wrong_questions
                        SET status = ?,
                            retry_count = retry_count + 1,
                            consecutive_correct = ?,
                            max_correct_difficulty = ?,
                            last_retry_at = datetime('now'),
                            updated_at = datetime('now')
                        WHERE id = ?
                        """,
                        (status, consecutive, max_difficulty, str(row["id"])),
                    )
            self._require_conn().commit()

    def get_fsrs_card(self, topic_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._require_conn().execute(
                "SELECT * FROM fsrs_cards WHERE topic_id = ?",
                (str(topic_id or ""),),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "topic_id": str(row["topic_id"]),
            "card": self._json_loads(row["card_data"], {}),
            "fsrs_state": str(row["fsrs_state"] or ""),
            "last_rating": int(row["last_rating"] or 0),
            "updated_at": str(row["updated_at"] or ""),
        }

    def upsert_fsrs_card(self, *, topic_id: str, card: dict[str, Any], last_rating: int) -> None:
        with self._lock:
            self._require_conn().execute(
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
            self._require_conn().commit()

    def list_fsrs_cards(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._require_conn().execute(
                "SELECT * FROM fsrs_cards ORDER BY updated_at DESC, id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
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
                (topic_key, card_id, int(rating or 0), int(scheduled_days or 0), int(actual_days or 0)),
            )
            self._trim_append_only_rows(
                conn,
                table="review_log",
                group_column="topic_id",
                group_value=topic_key,
                history_limit=history_limit,
            )
            conn.commit()

    def export_json(self) -> dict[str, Any]:
        return {
            STORE_CONFIG: self.get_raw(STORE_CONFIG) or {},
            STORE_STATE: self.get_raw(STORE_STATE) or {},
            "interactions": self.list_interactions(limit=1000),
            "topics": self.list_topics(limit=5000),
            "mastery_overview": self.list_mastery_overview(limit=5000),
            "wrong_questions": self.list_wrong_questions(limit=5000),
            "fsrs_cards": self.list_fsrs_cards(limit=5000),
            "candidate_knowledge_items": self.list_candidate_items(limit=5000),
            "anonymous_knowledge_stats": self.list_anonymous_knowledge_stats(limit=5000),
            "knowledge_contribution_queue": self.list_knowledge_contribution_queue(limit=5000),
        }
