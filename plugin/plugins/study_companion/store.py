from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

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

    def open(self) -> None:
        with self._lock:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False, timeout=10.0
            )
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
            "depth": safe_int(row["depth"], 1),
            "difficulty": safe_float(row["difficulty"], 0.5),
            "prerequisites": StudyStore._json_loads(row["prerequisites"], []),
            "related": StudyStore._json_loads(row["related"], []),
            "typical_misconceptions": StudyStore._json_loads(
                row["typical_misconceptions"], []
            ),
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
        with self._lock:
            row = (
                self._require_conn()
                .execute("SELECT 1 FROM interactions LIMIT 1")
                .fetchone()
            )
            return row is not None

    def get_raw(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = (
                self._require_conn()
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
            rows = (
                self._require_conn()
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
        memory_decks = MemoryDeckStore(self)
        from .store_notebook import NotebookStore

        notebooks = NotebookStore(self)
        return {
            STORE_CONFIG: self.get_raw(STORE_CONFIG) or {},
            STORE_STATE: self.get_raw(STORE_STATE) or {},
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
            "notebooks": [
                asdict(item) for item in notebooks.list_notebooks(limit=5000)
            ],
            "notes": [asdict(item) for item in notebooks.list_notes(limit=5000)],
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
