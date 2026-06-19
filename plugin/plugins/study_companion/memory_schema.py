from __future__ import annotations

import re
from typing import Any


DECK_TYPES = {"word", "passage", "formula", "custom"}
ITEM_TYPES = {"word", "sentence", "paragraph", "cloze", "custom"}
_SQLITE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize_deck_type(value: object) -> str:
    text = str(value or "custom").strip().lower()
    return text if text in DECK_TYPES else "custom"


def normalize_item_type(value: object) -> str:
    text = str(value or "custom").strip().lower()
    return text if text in ITEM_TYPES else "custom"


def ensure_memory_schema(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS decks (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            deck_type TEXT NOT NULL,
            subject TEXT,
            language TEXT,
            source TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_items (
            id TEXT PRIMARY KEY,
            deck_id TEXT NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
            item_type TEXT NOT NULL,
            prompt TEXT NOT NULL,
            answer TEXT NOT NULL,
            metadata_json TEXT,
            fsrs_card_id INTEGER REFERENCES memory_fsrs_cards(id) ON DELETE SET NULL,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_fsrs_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL UNIQUE REFERENCES memory_items(id) ON DELETE CASCADE,
            card_data TEXT NOT NULL,
            fsrs_state TEXT DEFAULT 'new',
            last_rating INTEGER,
            next_due TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_review_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
            card_id INTEGER REFERENCES memory_fsrs_cards(id),
            rating INTEGER,
            scheduled_days INTEGER,
            actual_days INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
            rating INTEGER NOT NULL,
            correct INTEGER NOT NULL,
            elapsed_ms INTEGER,
            error_type TEXT,
            reviewed_at TEXT DEFAULT (datetime('now')),
            session_id TEXT REFERENCES sessions(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recitation_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            passage_item_id TEXT NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
            review_record_id INTEGER REFERENCES review_records(id) ON DELETE SET NULL,
            user_input_text TEXT NOT NULL,
            missing_count INTEGER DEFAULT 0,
            extra_count INTEGER DEFAULT 0,
            wrong_order_count INTEGER DEFAULT 0,
            hint_count INTEGER DEFAULT 0,
            score REAL,
            reviewed_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    _ensure_column(conn, "memory_fsrs_cards", "next_due", "TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_items_deck ON memory_items(deck_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_items_card ON memory_items(fsrs_card_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mem_fsrs_cards_item ON memory_fsrs_cards(item_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mem_fsrs_cards_next_due ON memory_fsrs_cards(next_due)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mem_review_log_item ON memory_review_log(item_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mem_review_log_card ON memory_review_log(card_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_review_records_item ON review_records(item_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_review_records_session ON review_records(session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_recitation_attempts_item ON recitation_attempts(passage_item_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_recitation_attempts_review ON recitation_attempts(review_record_id)"
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_items_word_dedupe
        ON memory_items(deck_id, prompt)
        WHERE item_type = 'word'
        """
    )


def _ensure_column(conn: Any, table: str, column: str, definition: str) -> None:
    _validate_sqlite_identifier(table)
    _validate_sqlite_identifier(column)
    existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _validate_sqlite_identifier(value: str) -> None:
    if not _SQLITE_IDENTIFIER_RE.fullmatch(str(value or "")):
        raise ValueError(f"invalid sqlite identifier: {value!r}")


__all__ = [
    "DECK_TYPES",
    "ITEM_TYPES",
    "ensure_memory_schema",
    "normalize_deck_type",
    "normalize_item_type",
]
