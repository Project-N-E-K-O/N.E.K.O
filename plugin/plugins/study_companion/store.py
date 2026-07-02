from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .knowledge_quality import (
    DEFAULT_TRUSTED_NEGATIVE_THRESHOLD,
    KnowledgeCandidateStatus,
    KnowledgeQualityStore,
)
from .memory_deck_store import MemoryDeckStore, ensure_memory_schema
from .mode_manager import normalize_mode
from .models import (
    STORE_CONFIG,
    STORE_STATE,
    StudyConfig,
    StudyState,
    build_config,
    json_copy,
)

_DROP = object()
_STATE_ITEM_FLOAT_KEYS = {"at", "created_at", "updated_at", "expires_at", "lock_until"}
_DEFAULT_APPEND_ONLY_HISTORY_LIMIT = 5000


class _LockedReadCursor:
    def __init__(self, cursor: sqlite3.Cursor, lock: Any) -> None:
        self._cursor = cursor
        self._lock = lock
        self._released = False

    def _release(self) -> None:
        if self._released:
            return
        self._released = True
        self._lock.release()

    def fetchone(self) -> sqlite3.Row | tuple[Any, ...] | None:
        try:
            return self._cursor.fetchone()
        finally:
            self._release()

    def fetchall(self) -> list[Any]:
        try:
            return self._cursor.fetchall()
        finally:
            self._release()

    def fetchmany(self, size: int | None = None) -> list[Any]:
        try:
            if size is None:
                return self._cursor.fetchmany()
            return self._cursor.fetchmany(size)
        finally:
            self._release()

    def close(self) -> None:
        try:
            self._cursor.close()
        finally:
            self._release()

    def __iter__(self) -> Any:
        try:
            yield from self._cursor
        finally:
            self._release()

    def __enter__(self) -> "_LockedReadCursor":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        self.close()
        return False

    def __getattr__(self, name: str) -> object:
        return getattr(self._cursor, name)

    def __del__(self) -> None:
        try:
            self._release()
        except Exception:
            pass


class _LockedReadConnection:
    def __init__(self, conn: sqlite3.Connection, lock: Any) -> None:
        self._conn = conn
        self._lock = lock

    def execute(self, *args: Any, **kwargs: Any) -> _LockedReadCursor:
        self._lock.acquire()
        try:
            cursor = self._conn.execute(*args, **kwargs)
        except Exception:
            self._lock.release()
            raise
        return _LockedReadCursor(cursor, self._lock)

    def __getattr__(self, name: str) -> object:
        return getattr(self._conn, name)


def _next_candidate_status(
    item: dict[str, Any],
    score_parts: dict[str, Any],
    *,
    trusted_negative_threshold: int = DEFAULT_TRUSTED_NEGATIVE_THRESHOLD,
) -> str:
    current = str(item.get("status") or KnowledgeCandidateStatus.CANDIDATE.value)
    score = float(score_parts.get("score") or 0.0)
    evidence_count = int(score_parts.get("evidence_count") or 0)
    positive_count = int(score_parts.get("positive_count") or 0)
    negative_count = int(score_parts.get("negative_count") or 0)
    conflict_count = int(score_parts.get("conflict_count") or 0)
    user_rejected_count = int(score_parts.get("user_rejected_count") or 0)
    if current == KnowledgeCandidateStatus.TRUSTED.value:
        if user_rejected_count > 0 or negative_count >= trusted_negative_threshold:
            return KnowledgeCandidateStatus.DEPRECATED.value
        return current
    if score <= -0.20 or negative_count >= 2 or conflict_count >= 2:
        return KnowledgeCandidateStatus.DEPRECATED.value
    if score >= 0.72 and positive_count >= 4 and conflict_count == 0:
        return KnowledgeCandidateStatus.TRUSTED.value
    if score >= 0.35 and evidence_count >= 2:
        return KnowledgeCandidateStatus.ACTIVE.value
    if current in {
        KnowledgeCandidateStatus.ACTIVE.value,
        KnowledgeCandidateStatus.CANDIDATE.value,
    }:
        return current
    return KnowledgeCandidateStatus.CANDIDATE.value


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


def _sanitize_state_item_list(
    value: Any, *, required_float_key: str | None = None
) -> list[dict[str, Any]]:
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

    _INTERACTION_TRIM_INTERVAL = 10
    _supports_batch_answer = True

    def __init__(
        self,
        db_path: Path,
        seed_json_path: Path,
        logger: Any,
        knowledge_seed_json_path: Path | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.seed_json_path = Path(seed_json_path)
        self.knowledge_seed_json_path = (
            Path(knowledge_seed_json_path)
            if knowledge_seed_json_path is not None
            else None
        )
        self._logger = logger
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._read_conn: sqlite3.Connection | None = None
        self._read_local = threading.local()
        self._read_conns: set[sqlite3.Connection] = set()
        self._read_connections_enabled = True
        self._fallback_read_conn: _LockedReadConnection | None = None
        self._interaction_count = 0

    def open(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn: sqlite3.Connection | None = None
            try:
                conn = sqlite3.connect(
                    str(self.db_path), check_same_thread=False, timeout=10.0
                )
                self._conn = conn
                journal_mode = self._configure_connection_journal(conn, role="write")
                self._read_connections_enabled = journal_mode == "wal"
                conn.execute("PRAGMA foreign_keys = ON")
                conn.row_factory = sqlite3.Row
                self._interaction_count = 0
                self._init_db()
                self._load_seed_if_empty()
                self.load_knowledge_seed()
            except Exception:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                self._conn = None
                self._read_connections_enabled = True
                self._fallback_read_conn = None
                self._interaction_count = 0
                raise

    def close(self) -> None:
        with self._lock:
            for conn in list(self._read_conns):
                conn.close()
            self._read_conns.clear()
            self._read_conn = None
            self._read_local = threading.local()
            self._read_connections_enabled = True
            self._fallback_read_conn = None
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.open()
        assert self._conn is not None
        return self._conn

    def _require_read_conn(self) -> sqlite3.Connection | _LockedReadConnection:
        if not self._read_connections_enabled:
            with self._lock:
                if self._conn is None:
                    self.open()
                conn = self._require_conn()
                if (
                    self._fallback_read_conn is None
                    or self._fallback_read_conn._conn is not conn
                ):
                    self._fallback_read_conn = _LockedReadConnection(conn, self._lock)
                return self._fallback_read_conn
        conn = getattr(self._read_local, "conn", None)
        if conn is None:
            with self._lock:
                if self._conn is None:
                    self.open()
                conn = sqlite3.connect(
                    str(self.db_path), check_same_thread=False, timeout=5.0
                )
                self._configure_connection_journal(conn, role="read")
                conn.execute("PRAGMA foreign_keys = ON")
                conn.row_factory = sqlite3.Row
                self._read_conns.add(conn)
                self._read_local.conn = conn
                if self._read_conn is None:
                    self._read_conn = conn
        return conn

    def answer_write_lock(self) -> threading.RLock:
        return self._lock

    def _configure_connection_journal(
        self, conn: sqlite3.Connection, *, role: str
    ) -> str:
        try:
            mode = self._journal_mode_from_cursor(conn.execute("PRAGMA journal_mode=WAL"))
        except sqlite3.DatabaseError as exc:
            self._log_warning(
                "StudyStore %s connection WAL unavailable; falling back to DELETE journal mode: %s",
                role,
                exc,
            )
            return self._configure_delete_journal(conn, role=role)
        if mode == "wal":
            return mode
        self._log_warning(
            "StudyStore %s connection requested WAL but sqlite returned %s; falling back to DELETE journal mode",
            role,
            mode or "unknown",
        )
        return self._configure_delete_journal(conn, role=role)

    def _configure_delete_journal(self, conn: sqlite3.Connection, *, role: str) -> str:
        try:
            mode = self._journal_mode_from_cursor(conn.execute("PRAGMA journal_mode=DELETE"))
            return mode or "delete"
        except sqlite3.DatabaseError as fallback_exc:
            self._log_warning(
                "StudyStore %s connection DELETE journal fallback failed: %s",
                role,
                fallback_exc,
            )
            return ""

    @staticmethod
    def _journal_mode_from_cursor(cursor: Any) -> str:
        try:
            row = cursor.fetchone()
        except Exception:
            return ""
        if row is None:
            return ""
        try:
            value = row[0]
        except Exception:
            try:
                value = row["journal_mode"]
            except Exception:
                value = ""
        return str(value or "").strip().lower()

    def load_answer_write_state(
        self, topic_id: str, *, recent_limit: int = 10
    ) -> dict[str, Any]:
        topic_key = str(topic_id or "").strip()
        safe_limit = max(1, int(recent_limit))
        with self._lock:
            conn = self._require_conn()
            if topic_key:
                recent_rows = conn.execute(
                    """
                    SELECT *
                    FROM qa_records
                    WHERE topic_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (topic_key, safe_limit),
                ).fetchall()
            else:
                recent_rows = conn.execute(
                    """
                    SELECT *
                    FROM qa_records
                    WHERE topic_id IS NULL
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (safe_limit,),
                ).fetchall()
            fsrs_row = conn.execute(
                "SELECT * FROM fsrs_cards WHERE topic_id = ?", (topic_key,)
            ).fetchone()
        return {
            "recent": [
                item
                for item in (
                    self._qa_record_from_row(row) for row in reversed(recent_rows)
                )
                if item is not None
            ],
            "fsrs_card": self._fsrs_card_from_row(fsrs_row),
        }

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
            "stage": str(row["stage"] or ""),
            "unit": str(row["unit"] or ""),
            "depth": safe_int(row["depth"], 1),
            "difficulty": safe_float(row["difficulty"], 0.5),
            "prerequisites": StudyStore._json_loads(row["prerequisites"], []),
            "related": StudyStore._json_loads(row["related"], []),
            "typical_misconceptions": StudyStore._json_loads(
                row["typical_misconceptions"], []
            ),
            "skills": StudyStore._json_loads(row["skills"], []),
            "question_types": StudyStore._json_loads(row["question_types"], []),
            "examples": StudyStore._json_loads(row["examples"], []),
            "course_family": str(row["course_family"] or ""),
            "aliases": StudyStore._json_loads(row["aliases"], []),
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

    def _log_warning(self, message: str, *args: Any) -> None:
        warning = getattr(self._logger, "warning", None)
        if callable(warning):
            try:
                warning(message, *args)
            except Exception:
                pass

    def _has_interactions(self) -> bool:
        row = (
            self._require_read_conn()
            .execute("SELECT 1 FROM interactions LIMIT 1")
            .fetchone()
        )
        return row is not None

    def get_raw(self, key: str) -> dict[str, Any] | None:
        row = (
            self._require_read_conn()
            .execute("SELECT value FROM kv WHERE key = ?", (key,))
            .fetchone()
        )
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
                (
                    key,
                    json.dumps(json_copy(value), ensure_ascii=False, sort_keys=True),
                    now,
                ),
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
        merged["active_mode"] = normalize_mode(
            merged.get("active_mode") or fallback.active_mode
        )
        merged["mode_started_at"] = safe_float(merged.get("mode_started_at"), 0.0)
        merged["recent_mode_switches"] = _sanitize_state_item_list(
            merged.get("recent_mode_switches"),
            required_float_key="at",
        )
        merged["suggestion_cooldowns"] = _sanitize_suggestion_cooldowns(
            merged.get("suggestion_cooldowns")
        )
        merged["session_suggestions"] = _sanitize_state_item_list(
            merged.get("session_suggestions")
        )
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
                    json.dumps(
                        json_copy(metadata or {}), ensure_ascii=False, sort_keys=True
                    ),
                    time.time(),
                ),
            )
            self._interaction_count += 1
            if self._interaction_count >= int(self._INTERACTION_TRIM_INTERVAL):
                conn.execute(
                    """
                    DELETE FROM interactions
                    WHERE id NOT IN (
                        SELECT id FROM interactions ORDER BY id DESC LIMIT ?
                    )
                    """,
                    (max(1, int(history_limit)),),
                )
                self._interaction_count = 0
            conn.commit()

    def batch_write_answer_data(
        self,
        *,
        session_id: str,
        mode: str,
        topic_id: str,
        question: dict[str, Any],
        user_answer: str,
        eval_result: dict[str, Any],
        response_time_ms: int | None,
        mastery_snapshot: dict[str, Any] | None = None,
        wrong_question_data: dict[str, Any] | None = None,
        fsrs_card: dict[str, Any] | None = None,
        fsrs_rating: int | None = None,
        review_log_data: dict[str, Any] | None = None,
        error_candidate_data: list[dict[str, Any]] | None = None,
        positive_candidate_data: dict[str, Any] | None = None,
        positive_evidence_data: dict[str, Any] | None = None,
        topic_upsert_data: dict[str, Any] | None = None,
        topic_candidate_data: dict[str, Any] | None = None,
        history_limit: int = _DEFAULT_APPEND_ONLY_HISTORY_LIMIT,
    ) -> dict[str, Any]:
        session_key = str(session_id or "default")
        topic_key = str(topic_id or "").strip()
        db_topic_key = topic_key or None
        wrong_question_id = ""
        with self._lock:
            conn = self._require_conn()
            conn.execute("BEGIN IMMEDIATE")
            step = "begin"
            try:
                if topic_upsert_data:
                    step = "topic_upsert"
                    self._batch_upsert_topic(conn, topic_upsert_data)
                if topic_candidate_data:
                    step = "topic_candidate"
                    self._batch_upsert_candidate_with_evidence(
                        conn, topic_candidate_data
                    )
                step = "session"
                self._batch_write_session(conn, session_key, mode)
                step = "qa_record"
                self._batch_write_qa_record(
                    conn,
                    session_key=session_key,
                    topic_key=db_topic_key,
                    question=question,
                    user_answer=user_answer,
                    eval_result=eval_result,
                    mode=mode,
                    response_time_ms=response_time_ms,
                    history_limit=history_limit,
                )
                step = "session_topics"
                self._batch_update_session_topics(conn, session_key, topic_key)
                step = "mastery"
                self._batch_write_mastery(
                    conn,
                    mastery_snapshot=mastery_snapshot,
                    topic_key=topic_key,
                    history_limit=history_limit,
                )
                step = "wrong_question"
                wrong_question_id = self._batch_write_wrong_question(
                    conn,
                    wrong_question_data=wrong_question_data,
                    topic_key=topic_key,
                )
                step = "fsrs_card"
                self._batch_write_fsrs_card(
                    conn,
                    fsrs_card=fsrs_card,
                    fsrs_rating=fsrs_rating,
                    topic_key=topic_key,
                )
                step = "review_log"
                self._batch_write_review_log(
                    conn,
                    review_log_data=review_log_data,
                    topic_key=topic_key,
                    history_limit=history_limit,
                )
                step = "answer_candidates"
                self._batch_write_answer_candidates_noncritical(
                    conn,
                    session_key=session_key,
                    topic_key=topic_key,
                    error_candidate_data=error_candidate_data,
                    positive_candidate_data=positive_candidate_data,
                    positive_evidence_data=positive_evidence_data,
                )
                step = "commit"
                conn.commit()
            except Exception:
                conn.rollback()
                self._logger.exception(
                    "batch_write_answer_data failed step=%s session=%s topic=%s",
                    step,
                    session_key,
                    topic_key,
                )
                raise
        return {"ok": True, "wrong_question_id": wrong_question_id}

    def _batch_write_answer_candidates_noncritical(
        self,
        conn: sqlite3.Connection,
        *,
        session_key: str,
        topic_key: str,
        error_candidate_data: list[dict[str, Any]] | None,
        positive_candidate_data: dict[str, Any] | None,
        positive_evidence_data: dict[str, Any] | None,
    ) -> None:
        if not (error_candidate_data or positive_candidate_data or positive_evidence_data):
            return
        conn.execute("SAVEPOINT answer_candidates")
        try:
            self._batch_write_answer_candidates(
                conn,
                error_candidate_data=error_candidate_data,
                positive_candidate_data=positive_candidate_data,
                positive_evidence_data=positive_evidence_data,
            )
        except Exception:
            try:
                conn.execute("ROLLBACK TO SAVEPOINT answer_candidates")
            except sqlite3.DatabaseError as rollback_exc:
                self._log_warning(
                    "batch_write_answer_data candidate savepoint rollback failed session=%s topic=%s error=%s",
                    session_key,
                    topic_key,
                    rollback_exc,
                )
            self._logger.exception(
                "batch_write_answer_data candidate knowledge write failed session=%s topic=%s",
                session_key,
                topic_key,
            )
        finally:
            try:
                conn.execute("RELEASE SAVEPOINT answer_candidates")
            except sqlite3.DatabaseError as release_exc:
                self._log_warning(
                    "batch_write_answer_data candidate savepoint release failed session=%s topic=%s error=%s",
                    session_key,
                    topic_key,
                    release_exc,
                )

    def _batch_write_session(
        self, conn: sqlite3.Connection, session_key: str, mode: str
    ) -> None:
        conn.execute(
            """
            INSERT INTO sessions (id, mode, started_at, topics_touched)
            VALUES (?, ?, datetime('now'), '[]')
            ON CONFLICT(id) DO NOTHING
            """,
            (session_key, str(mode or "companion")),
        )

    def _batch_write_qa_record(
        self,
        conn: sqlite3.Connection,
        *,
        session_key: str,
        topic_key: str | None,
        question: dict[str, Any],
        user_answer: str,
        eval_result: dict[str, Any],
        mode: str,
        response_time_ms: int | None,
        history_limit: int,
    ) -> None:
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
                topic_key,
                self._json_dumps(question or {}),
                str(user_answer or ""),
                self._json_dumps(eval_result or {}),
                str(mode or "companion"),
                int(response_time_ms) if response_time_ms is not None else None,
            ),
        )
        self._trim_append_only_rows(
            conn,
            table="qa_records",
            group_column="topic_id",
            group_value=topic_key,
            history_limit=history_limit,
        )

    def _batch_update_session_topics(
        self, conn: sqlite3.Connection, session_key: str, topic_key: str
    ) -> None:
        row = conn.execute(
            "SELECT topics_touched FROM sessions WHERE id = ?", (session_key,)
        ).fetchone()
        touched = (
            self._json_loads(row["topics_touched"], []) if row is not None else []
        )
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

    def _batch_write_mastery(
        self,
        conn: sqlite3.Connection,
        *,
        mastery_snapshot: dict[str, Any] | None,
        topic_key: str,
        history_limit: int,
    ) -> None:
        if not mastery_snapshot:
            return
        snapshot_topic = str(mastery_snapshot.get("topic_id") or topic_key)
        conn.execute(
            """
            INSERT INTO mastery_snapshots (
                topic_id, mastery, accuracy, recency, consistency,
                confidence, level, attempts, flags, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                snapshot_topic,
                float(mastery_snapshot.get("mastery") or 0.0),
                float(mastery_snapshot.get("accuracy") or 0.0),
                float(mastery_snapshot.get("recency") or 0.0),
                float(mastery_snapshot.get("consistency") or 0.0),
                float(mastery_snapshot.get("confidence") or 0.0),
                str(mastery_snapshot.get("level") or ""),
                int(mastery_snapshot.get("attempts") or 0),
                self._json_dumps(
                    mastery_snapshot.get("flags")
                    if isinstance(mastery_snapshot.get("flags"), list)
                    else []
                ),
            ),
        )
        self._trim_append_only_rows(
            conn,
            table="mastery_snapshots",
            group_column="topic_id",
            group_value=snapshot_topic,
            history_limit=history_limit,
        )

    def _batch_write_wrong_question(
        self,
        conn: sqlite3.Connection,
        *,
        wrong_question_data: dict[str, Any] | None,
        topic_key: str,
    ) -> str:
        if not wrong_question_data:
            return ""
        wrong_question_id = str(wrong_question_data.get("id") or uuid.uuid4())
        conn.execute(
            """
            INSERT INTO wrong_questions (
                id, topic_id, question, user_answer, expected_answer,
                error_type, verdict, status, retry_count,
                consecutive_correct, max_correct_difficulty,
                last_error_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 0, 0, 0,
                    datetime('now'), datetime('now'), datetime('now'))
            """,
            (
                wrong_question_id,
                str(wrong_question_data.get("topic_id") or topic_key),
                self._json_dumps(wrong_question_data.get("question") or {}),
                str(wrong_question_data.get("user_answer") or ""),
                str(wrong_question_data.get("expected_answer") or ""),
                str(wrong_question_data.get("error_type") or "unknown"),
                str(wrong_question_data.get("verdict") or "wrong"),
            ),
        )
        return wrong_question_id

    def _batch_write_fsrs_card(
        self,
        conn: sqlite3.Connection,
        *,
        fsrs_card: dict[str, Any] | None,
        fsrs_rating: int | None,
        topic_key: str,
    ) -> None:
        if not fsrs_card:
            return
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
                str(fsrs_card.get("topic_id") or topic_key),
                self._json_dumps(fsrs_card or {}),
                str((fsrs_card or {}).get("state") or ""),
                int(fsrs_rating or 0),
            ),
        )

    def _batch_write_review_log(
        self,
        conn: sqlite3.Connection,
        *,
        review_log_data: dict[str, Any] | None,
        topic_key: str,
        history_limit: int,
    ) -> None:
        if not review_log_data:
            return
        review_topic = str(review_log_data.get("topic_id") or topic_key)
        conn.execute(
            """
            INSERT INTO review_log (
                topic_id, card_id, rating, scheduled_days, actual_days, created_at
            )
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                review_topic,
                review_log_data.get("card_id"),
                int(review_log_data.get("rating") or 0),
                int(review_log_data.get("scheduled_days") or 0),
                int(review_log_data.get("actual_days") or 0),
            ),
        )
        self._trim_append_only_rows(
            conn,
            table="review_log",
            group_column="topic_id",
            group_value=review_topic,
            history_limit=history_limit,
        )

    def _batch_write_answer_candidates(
        self,
        conn: sqlite3.Connection,
        *,
        error_candidate_data: list[dict[str, Any]] | None,
        positive_candidate_data: dict[str, Any] | None,
        positive_evidence_data: dict[str, Any] | None,
    ) -> None:
        for candidate in error_candidate_data or []:
            self._batch_upsert_candidate_with_evidence(conn, candidate)
        positive_item_id = ""
        if positive_candidate_data:
            positive_item_id = self._batch_upsert_candidate_with_evidence(
                conn, positive_candidate_data
            )
        if not positive_evidence_data:
            return
        evidence = dict(positive_evidence_data)
        if positive_item_id and not evidence.get("item_id"):
            evidence["item_id"] = positive_item_id
        self._batch_insert_candidate_evidence(conn, evidence)
        self._batch_recompute_candidate_score(conn, str(evidence.get("item_id") or ""))

    def _batch_upsert_topic(
        self, conn: sqlite3.Connection, topic: dict[str, Any]
    ) -> None:
        topic_id = str(topic.get("id") or "").strip()
        name = str(topic.get("name") or topic_id).strip()
        if not topic_id or not name:
            return
        conn.execute(
            """
            INSERT INTO topics (
                id, name, subject, chapter, stage, unit, depth, difficulty,
                prerequisites, related, typical_misconceptions, skills,
                question_types, examples, course_family, aliases, source, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                name = CASE WHEN topics.source = 'seed' THEN topics.name ELSE excluded.name END,
                subject = CASE WHEN topics.source = 'seed' THEN topics.subject ELSE excluded.subject END,
                chapter = CASE WHEN topics.source = 'seed' THEN topics.chapter ELSE excluded.chapter END,
                stage = CASE
                    WHEN topics.source = 'seed' AND topics.stage = '' AND excluded.stage != '' THEN excluded.stage
                    WHEN topics.source = 'seed' THEN topics.stage
                    ELSE excluded.stage
                END,
                unit = CASE
                    WHEN topics.source = 'seed' AND (topics.unit = '' OR topics.unit = topics.chapter) AND excluded.unit != '' THEN excluded.unit
                    WHEN topics.source = 'seed' THEN topics.unit
                    ELSE excluded.unit
                END,
                depth = CASE WHEN topics.source = 'seed' THEN topics.depth ELSE excluded.depth END,
                difficulty = CASE WHEN topics.source = 'seed' THEN topics.difficulty ELSE excluded.difficulty END,
                prerequisites = CASE WHEN topics.source = 'seed' THEN topics.prerequisites ELSE excluded.prerequisites END,
                related = CASE WHEN topics.source = 'seed' THEN topics.related ELSE excluded.related END,
                typical_misconceptions = CASE WHEN topics.source = 'seed' THEN topics.typical_misconceptions ELSE excluded.typical_misconceptions END,
                skills = CASE
                    WHEN topics.source = 'seed' AND topics.skills = '[]' AND excluded.skills != '[]' THEN excluded.skills
                    WHEN topics.source = 'seed' THEN topics.skills
                    ELSE excluded.skills
                END,
                question_types = CASE
                    WHEN topics.source = 'seed' AND topics.question_types = '[]' AND excluded.question_types != '[]' THEN excluded.question_types
                    WHEN topics.source = 'seed' THEN topics.question_types
                    ELSE excluded.question_types
                END,
                examples = CASE
                    WHEN topics.source = 'seed' AND topics.examples = '[]' AND excluded.examples != '[]' THEN excluded.examples
                    WHEN topics.source = 'seed' THEN topics.examples
                    ELSE excluded.examples
                END,
                course_family = CASE
                    WHEN topics.source = 'seed' AND topics.course_family = '' AND excluded.course_family != '' THEN excluded.course_family
                    WHEN topics.source = 'seed' THEN topics.course_family
                    ELSE excluded.course_family
                END,
                aliases = CASE
                    WHEN topics.source = 'seed' AND topics.aliases = '[]' AND excluded.aliases != '[]' THEN excluded.aliases
                    WHEN topics.source = 'seed' THEN topics.aliases
                    ELSE excluded.aliases
                END,
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
                ),
                str(topic.get("unit") or topic.get("chapter") or ""),
                safe_int(topic.get("depth"), 1),
                safe_float(topic.get("difficulty"), 0.5),
                self._json_dumps(
                    topic.get("prerequisites")
                    if isinstance(topic.get("prerequisites"), list)
                    else []
                ),
                self._json_dumps(
                    topic.get("related") if isinstance(topic.get("related"), list) else []
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
                    topic.get("aliases") if isinstance(topic.get("aliases"), list) else []
                ),
                str(topic.get("source") or "runtime"),
            ),
        )

    def _batch_upsert_candidate_with_evidence(
        self, conn: sqlite3.Connection, candidate: dict[str, Any]
    ) -> str:
        item_type = str(candidate.get("item_type") or "").strip()
        dedupe_key = str(candidate.get("dedupe_key") or "").strip()
        if not item_type or not dedupe_key:
            return ""
        payload = (
            candidate.get("payload")
            if isinstance(candidate.get("payload"), dict)
            else {}
        )
        source = str(candidate.get("source") or "runtime").strip() or "runtime"
        existing = conn.execute(
            "SELECT * FROM candidate_knowledge_items WHERE item_type = ? AND dedupe_key = ? LIMIT 1",
            (item_type, dedupe_key),
        ).fetchone()
        if existing is None:
            item_id = str(candidate.get("id") or uuid.uuid4())
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
                    self._json_dumps(payload),
                    source,
                    str(candidate.get("status") or "candidate"),
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
                (self._json_dumps(payload), source, item_id),
            )
        for evidence in candidate.get("evidence") or []:
            evidence_data = dict(evidence) if isinstance(evidence, dict) else {}
            evidence_data["item_id"] = item_id
            self._batch_insert_candidate_evidence(conn, evidence_data)
        self._batch_recompute_candidate_score(conn, item_id)
        return item_id

    def _batch_insert_candidate_evidence(
        self, conn: sqlite3.Connection, evidence: dict[str, Any]
    ) -> None:
        item_id = str(evidence.get("item_id") or "").strip()
        if not item_id:
            return
        conn.execute(
            """
            INSERT INTO knowledge_evidence (item_id, event_type, weight, context_json, created_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            """,
            (
                item_id,
                str(evidence.get("event_type") or ""),
                float(evidence.get("weight") or 0.0),
                self._json_dumps(
                    evidence.get("context")
                    if isinstance(evidence.get("context"), dict)
                    else {}
                ),
            ),
        )
        self._trim_append_only_rows(
            conn,
            table="knowledge_evidence",
            group_column="item_id",
            group_value=item_id,
            history_limit=safe_int(evidence.get("history_limit"), _DEFAULT_APPEND_ONLY_HISTORY_LIMIT),
        )

    def _batch_recompute_candidate_score(
        self, conn: sqlite3.Connection, item_id: str
    ) -> None:
        item_key = str(item_id or "").strip()
        if not item_key:
            return
        row = conn.execute(
            "SELECT * FROM candidate_knowledge_items WHERE id = ?", (item_key,)
        ).fetchone()
        candidate = self._candidate_from_row(row)
        if candidate is None:
            return
        rows = conn.execute(
            """
            SELECT *
            FROM knowledge_evidence
            WHERE item_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (item_key, _DEFAULT_APPEND_ONLY_HISTORY_LIMIT),
        ).fetchall()
        evidence = [
            item
            for item in (self._evidence_from_row(evidence_row) for evidence_row in rows)
            if item is not None
        ]
        score_parts = KnowledgeQualityStore._score_parts(candidate, evidence)
        conn.execute(
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
                score_parts["score"],
                _next_candidate_status(candidate, score_parts),
                score_parts["evidence_count"],
                score_parts["positive_count"],
                score_parts["negative_count"],
                score_parts["conflict_count"],
                item_key,
            ),
        )

    def list_interactions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = (
            self._require_read_conn()
            .execute(
                """
                SELECT id, kind, input_text, output_text, metadata, created_at
                FROM interactions
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            )
            .fetchall()
        )
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

    def _qa_record_from_row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
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

    def _fsrs_card_from_row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
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

    def export_json(self) -> dict[str, Any]:
        from dataclasses import asdict

        from .store_notebook import NotebookStore

        memory_decks = MemoryDeckStore(self)
        notebooks = NotebookStore(self)
        return {
            STORE_CONFIG: self.get_raw(STORE_CONFIG) or {},
            STORE_STATE: self.get_raw(STORE_STATE) or {},
            "notebooks": [asdict(nb) for nb in notebooks.list_notebooks(limit=5000)],
            # include_content=True: a JSON backup must carry full note bodies,
            # not the snippet-only rows the UI list path returns.
            "notes": [asdict(note) for note in notebooks.list_notes(limit=5000, include_content=True)],
            "interactions": self.list_interactions(limit=1000),
            "topics": self.list_topics(limit=5000),
            "mastery_overview": self.list_mastery_overview(limit=5000),
            "wrong_questions": self.list_wrong_questions(limit=5000),
            "fsrs_cards": self.list_fsrs_cards(limit=5000),
            "sessions": self.list_sessions(limit=5000),
            "qa_records": self.list_qa_records(limit=5000),
            "review_log": self.list_review_log(limit=5000),
            "candidate_knowledge_items": self.list_candidate_items(limit=5000),
            "knowledge_evidence": self.list_knowledge_evidence(limit=5000),
            "anonymous_knowledge_stats": self.list_anonymous_knowledge_stats(
                limit=5000
            ),
            "knowledge_contribution_queue": self.list_knowledge_contribution_queue(
                limit=5000
            ),
            "memory_decks": memory_decks.list_decks(limit=5000),
            "memory_items": memory_decks.list_items(limit=5000, include_archived=True),
            "memory_due_reviews": memory_decks.due_reviews(limit=5000),
        }


from .store_schema import (
    _ensure_column,
    _init_db,
    _load_seed_if_empty,
    _trim_append_only_rows,
)
from .store_topics import (
    average_latest_mastery,
    count_topics,
    count_tracked_mastery_topics,
    ensure_topic,
    find_topic_by_name,
    get_topic,
    list_topics,
    load_knowledge_seed,
    upsert_topic,
)
from .store_knowledge import (
    add_knowledge_evidence,
    candidate_status_counts,
    get_candidate_by_key,
    get_candidate_item,
    list_candidate_items,
    list_knowledge_evidence,
    list_recent_knowledge_evidence,
    update_candidate_score_status,
    upsert_candidate_item,
)
from .store_knowledge_contribution import (
    anonymous_knowledge_stats_summary,
    clear_knowledge_contribution_queue,
    enqueue_knowledge_contribution_snapshot,
    list_anonymous_knowledge_stats,
    list_knowledge_contribution_queue,
    upsert_anonymous_knowledge_stat,
)
from .store_qa import (
    add_qa_record,
    add_wrong_question,
    ensure_session,
    get_retry_wrong_question,
    list_qa_records,
    list_qa_records_for_topic,
    list_sessions,
    list_wrong_questions,
    mark_wrong_question_resolved,
    record_wrong_question_correct,
)
from .store_fsrs import (
    append_mastery_snapshot,
    append_review_log,
    get_fsrs_card,
    get_latest_mastery,
    list_fsrs_cards,
    list_mastery_overview,
    list_review_log,
    upsert_fsrs_card,
)
from .store_maintenance import json_loads, purge_all, transaction

StudyStore._init_db = _init_db  # type: ignore[method-assign]
StudyStore._ensure_column = _ensure_column  # type: ignore[method-assign]
StudyStore._trim_append_only_rows = _trim_append_only_rows  # type: ignore[method-assign]
StudyStore._load_seed_if_empty = _load_seed_if_empty  # type: ignore[method-assign]
StudyStore.load_knowledge_seed = load_knowledge_seed  # type: ignore[method-assign]
StudyStore.upsert_topic = upsert_topic  # type: ignore[method-assign]
StudyStore.ensure_topic = ensure_topic  # type: ignore[method-assign]
StudyStore.get_topic = get_topic  # type: ignore[method-assign]
StudyStore.find_topic_by_name = find_topic_by_name  # type: ignore[method-assign]
StudyStore.list_topics = list_topics  # type: ignore[method-assign]
StudyStore.count_topics = count_topics  # type: ignore[method-assign]
StudyStore.count_tracked_mastery_topics = count_tracked_mastery_topics  # type: ignore[method-assign]
StudyStore.average_latest_mastery = average_latest_mastery  # type: ignore[method-assign]
StudyStore.upsert_candidate_item = upsert_candidate_item  # type: ignore[method-assign]
StudyStore.add_knowledge_evidence = add_knowledge_evidence  # type: ignore[method-assign]
StudyStore.get_candidate_item = get_candidate_item  # type: ignore[method-assign]
StudyStore.get_candidate_by_key = get_candidate_by_key  # type: ignore[method-assign]
StudyStore.list_candidate_items = list_candidate_items  # type: ignore[method-assign]
StudyStore.list_knowledge_evidence = list_knowledge_evidence  # type: ignore[method-assign]
StudyStore.list_recent_knowledge_evidence = list_recent_knowledge_evidence  # type: ignore[method-assign]
StudyStore.update_candidate_score_status = update_candidate_score_status  # type: ignore[method-assign]
StudyStore.candidate_status_counts = candidate_status_counts  # type: ignore[method-assign]
StudyStore.upsert_anonymous_knowledge_stat = upsert_anonymous_knowledge_stat  # type: ignore[method-assign]
StudyStore.list_anonymous_knowledge_stats = list_anonymous_knowledge_stats  # type: ignore[method-assign]
StudyStore.anonymous_knowledge_stats_summary = anonymous_knowledge_stats_summary  # type: ignore[method-assign]
StudyStore.enqueue_knowledge_contribution_snapshot = (
    enqueue_knowledge_contribution_snapshot  # type: ignore[method-assign]
)
StudyStore.list_knowledge_contribution_queue = list_knowledge_contribution_queue  # type: ignore[method-assign]
StudyStore.clear_knowledge_contribution_queue = clear_knowledge_contribution_queue  # type: ignore[method-assign]
StudyStore.ensure_session = ensure_session  # type: ignore[method-assign]
StudyStore.list_sessions = list_sessions  # type: ignore[method-assign]
StudyStore.add_qa_record = add_qa_record  # type: ignore[method-assign]
StudyStore.list_qa_records = list_qa_records  # type: ignore[method-assign]
StudyStore.list_qa_records_for_topic = list_qa_records_for_topic  # type: ignore[method-assign]
StudyStore.add_wrong_question = add_wrong_question  # type: ignore[method-assign]
StudyStore.get_retry_wrong_question = get_retry_wrong_question  # type: ignore[method-assign]
StudyStore.list_wrong_questions = list_wrong_questions  # type: ignore[method-assign]
StudyStore.mark_wrong_question_resolved = mark_wrong_question_resolved  # type: ignore[method-assign]
StudyStore.record_wrong_question_correct = record_wrong_question_correct  # type: ignore[method-assign]
StudyStore.append_mastery_snapshot = append_mastery_snapshot  # type: ignore[method-assign]
StudyStore.get_latest_mastery = get_latest_mastery  # type: ignore[method-assign]
StudyStore.list_mastery_overview = list_mastery_overview  # type: ignore[method-assign]
StudyStore.get_fsrs_card = get_fsrs_card  # type: ignore[method-assign]
StudyStore.upsert_fsrs_card = upsert_fsrs_card  # type: ignore[method-assign]
StudyStore.list_fsrs_cards = list_fsrs_cards  # type: ignore[method-assign]
StudyStore.append_review_log = append_review_log  # type: ignore[method-assign]
StudyStore.list_review_log = list_review_log  # type: ignore[method-assign]
StudyStore.transaction = transaction  # type: ignore[method-assign]
StudyStore.json_loads = json_loads  # type: ignore[method-assign]
StudyStore.purge_all = purge_all  # type: ignore[method-assign]
