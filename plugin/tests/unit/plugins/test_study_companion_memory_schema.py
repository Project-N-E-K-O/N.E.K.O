from __future__ import annotations

import sqlite3

import pytest

from plugin.plugins.study_companion.memory_schema import (
    ensure_memory_schema,
    normalize_deck_type,
    normalize_item_type,
)

pytestmark = pytest.mark.unit


def test_memory_schema_creates_tables_indexes_and_is_idempotent() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    ensure_memory_schema(conn)
    ensure_memory_schema(conn)

    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    indexes = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    }

    assert {"decks", "memory_items", "memory_fsrs_cards", "review_records"} <= tables
    assert "idx_mem_fsrs_cards_next_due" in indexes
    assert "idx_memory_items_word_dedupe" in indexes


def test_memory_schema_adds_next_due_to_existing_card_table() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE memory_fsrs_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            card_data TEXT NOT NULL
        )
        """
    )

    ensure_memory_schema(conn)

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(memory_fsrs_cards)").fetchall()
    }
    assert "next_due" in columns


def test_memory_schema_normalizes_unknown_deck_and_item_types() -> None:
    assert normalize_deck_type("word") == "word"
    assert normalize_deck_type("???") == "custom"
    assert normalize_item_type("cloze") == "cloze"
    assert normalize_item_type(None) == "custom"
