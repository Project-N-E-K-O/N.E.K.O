from __future__ import annotations

import re

from .store_common import (
    json,
    sqlite3,
    ensure_memory_schema,
    STORE_CONFIG,
    STORE_STATE,
)

_SQL_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COLUMN_DEFINITION_ALLOWLIST = {"TEXT"}


def _validate_sql_identifier(value: str, field: str) -> str:
    text = str(value or "").strip()
    if not _SQL_IDENT_RE.fullmatch(text):
        raise ValueError(f"invalid SQL identifier for {field}: {value!r}")
    return text


def _validate_sql_order_by(value: str) -> str:
    terms: list[str] = []
    for raw_term in str(value or "").split(","):
        parts = raw_term.strip().split()
        if len(parts) not in {1, 2}:
            raise ValueError(f"invalid SQL order_by term: {raw_term!r}")
        column = _validate_sql_identifier(parts[0], "order_by")
        if len(parts) == 1:
            terms.append(column)
            continue
        direction = parts[1].upper()
        if direction not in {"ASC", "DESC"}:
            raise ValueError(f"invalid SQL order_by direction: {parts[1]!r}")
        terms.append(f"{column} {direction}")
    if not terms:
        raise ValueError("invalid SQL order_by: empty expression")
    return ", ".join(terms)


def _validate_column_definition(value: str) -> str:
    text = str(value or "").strip().upper()
    if text not in _COLUMN_DEFINITION_ALLOWLIST:
        raise ValueError(f"invalid SQL column definition: {value!r}")
    return text


def _init_notes_fts(self, conn: sqlite3.Connection) -> None:
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                title,
                content_plain,
                tags,
                content='notes',
                content_rowid='rowid'
            )
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
                INSERT INTO notes_fts(rowid, title, content_plain, tags)
                VALUES (new.rowid, new.title, new.content_plain, new.tags);
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
                INSERT INTO notes_fts(notes_fts, rowid, title, content_plain, tags)
                VALUES ('delete', old.rowid, old.title, old.content_plain, old.tags);
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
                INSERT INTO notes_fts(notes_fts, rowid, title, content_plain, tags)
                VALUES ('delete', old.rowid, old.title, old.content_plain, old.tags);
                INSERT INTO notes_fts(rowid, title, content_plain, tags)
                VALUES (new.rowid, new.title, new.content_plain, new.tags);
            END
            """
        )
    except sqlite3.Error as exc:
        self._log_warning(
            "study notes FTS unavailable; falling back to LIKE search: {}",
            exc,
        )


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
        CREATE TABLE IF NOT EXISTS notebooks (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT UNIQUE NOT NULL,
            notebook_id TEXT REFERENCES notebooks(id) ON DELETE SET NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            content_plain TEXT NOT NULL,
            snippet TEXT NOT NULL DEFAULT '',
            is_ai_generated INTEGER NOT NULL DEFAULT 0,
            source_type TEXT NOT NULL DEFAULT 'manual',
            source_ref TEXT NOT NULL DEFAULT '',
            topic_ids TEXT NOT NULL DEFAULT '[]',
            tags TEXT NOT NULL DEFAULT '[]',
            word_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            edited_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    _init_notes_fts(self, conn)
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
    ensure_memory_schema(conn)
    self._ensure_column(conn, "candidate_knowledge_items", "dedupe_key", "TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mastery_topic_updated ON mastery_snapshots(topic_id, updated_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_wrong_topic_status ON wrong_questions(topic_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_qa_topic_created ON qa_records(topic_id, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_review_topic_created ON review_log(topic_id, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_knowledge_dedupe ON candidate_knowledge_items(item_type, dedupe_key) WHERE dedupe_key IS NOT NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidate_knowledge_status ON candidate_knowledge_items(status, item_type, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_evidence_item ON knowledge_evidence(item_id, created_at DESC)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_anonymous_knowledge_stats_key ON anonymous_knowledge_stats(stat_type, stat_key)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_contribution_queue_status ON knowledge_contribution_queue(status, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notes_notebook_updated ON notes(notebook_id, updated_at DESC, rowid DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notes_source ON notes(source_type, source_ref)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notes_edited ON notes(edited_at DESC, rowid DESC)"
    )
    conn.commit()


@staticmethod
def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, definition: str
) -> None:
    table = _validate_sql_identifier(table, "table")
    column = _validate_sql_identifier(column, "column")
    definition = _validate_column_definition(definition)
    rows = conn.execute("PRAGMA table_info(" + table + ")").fetchall()
    if column in {str(row["name"]) for row in rows}:
        return
    conn.execute("ALTER TABLE " + table + " ADD COLUMN " + column + " " + definition)


@staticmethod
def _trim_append_only_rows(
    conn: sqlite3.Connection,
    *,
    table: str,
    group_column: str,
    group_value: str | None,
    history_limit: int,
    order_by: str = "id DESC",
) -> None:
    table = _validate_sql_identifier(table, "table")
    group_column = _validate_sql_identifier(group_column, "group_column")
    order_by = _validate_sql_order_by(order_by)
    limit = max(1, int(history_limit))
    if group_value is None:
        conn.execute(
            """
            DELETE FROM """
            + table
            + """
            WHERE """
            + group_column
            + """ IS NULL
              AND id NOT IN (
                  SELECT id
                  FROM """
            + table
            + """
                  WHERE """
            + group_column
            + """ IS NULL
                  ORDER BY """
            + order_by
            + """
                  LIMIT ?
              )
            """,
            (limit,),
        )
        return
    conn.execute(
        """
        DELETE FROM """
        + table
        + """
        WHERE """
        + group_column
        + """ = ?
          AND id NOT IN (
              SELECT id
              FROM """
        + table
        + """
              WHERE """
        + group_column
        + """ = ?
              ORDER BY """
        + order_by
        + """
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
