from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .models import STORE_CONFIG, STORE_STATE, StudyConfig, StudyState, build_config, json_copy


class StudyStore:
    """SQLite main store with JSON import/export support for seeds and backups."""

    def __init__(self, db_path: Path, seed_json_path: Path, logger: Any) -> None:
        self.db_path = Path(db_path)
        self.seed_json_path = Path(seed_json_path)
        self._logger = logger
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        with self._lock:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10.0)
            self._conn.row_factory = sqlite3.Row
            self._init_db()
            self._load_seed_if_empty()

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
        conn.commit()

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

    def export_json(self) -> dict[str, Any]:
        return {
            STORE_CONFIG: self.get_raw(STORE_CONFIG) or {},
            STORE_STATE: self.get_raw(STORE_STATE) or {},
            "interactions": self.list_interactions(limit=1000),
        }
