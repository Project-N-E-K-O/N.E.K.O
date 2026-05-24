from __future__ import annotations

import json
import sqlite3

import pytest

from plugin.plugins.study_companion.memory_queries import (
    active_item_card_rows,
    item_row_by_metadata_value,
)
from plugin.plugins.study_companion.memory_schema import ensure_memory_schema

pytestmark = pytest.mark.unit


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_memory_schema(conn)
    conn.execute(
        "INSERT INTO decks(id, name, deck_type) VALUES ('d1', 'Deck 1', 'word')"
    )
    conn.execute(
        "INSERT INTO decks(id, name, deck_type) VALUES ('d2', 'Deck 2', 'word')"
    )
    for item_id, deck_id, status, metadata in (
        ("i1", "d1", "active", {"external_id": "same", "topic_id": "topic"}),
        ("i2", "d1", "archived", {"external_id": "old"}),
        ("i3", "d2", "active", {"topic_id": "topic"}),
    ):
        conn.execute(
            """
            INSERT INTO memory_items(id, deck_id, item_type, prompt, answer, metadata_json, status)
            VALUES (?, ?, 'word', ?, ?, ?, ?)
            """,
            (item_id, deck_id, f"front-{item_id}", f"back-{item_id}", json.dumps(metadata), status),
        )
        conn.execute(
            "INSERT INTO memory_fsrs_cards(item_id, card_data, next_due) VALUES (?, '{}', '2026-01-01T00:00:00Z')",
            (item_id,),
        )
    conn.commit()
    return conn


def test_active_item_card_rows_filters_active_items_and_decks() -> None:
    conn = _conn()

    all_rows = active_item_card_rows(conn)
    deck_rows = active_item_card_rows(conn, deck_id="d1")

    assert [row["item_id"] for row in all_rows] == ["i1", "i3"]
    assert [row["item_id"] for row in deck_rows] == ["i1"]
    assert deck_rows[0]["deck_name"] == "Deck 1"


def test_item_row_by_metadata_value_supports_deck_scope_and_key_aliases() -> None:
    conn = _conn()

    scoped = item_row_by_metadata_value(
        conn,
        deck_id="d1",
        item_type="word",
        key=("external_id", "topic_id"),
        value="same",
        json_loads=lambda value, default: json.loads(value or "{}"),
    )
    any_deck = item_row_by_metadata_value(
        conn,
        deck_id="",
        item_type="word",
        key="topic_id",
        value="topic",
        json_loads=lambda value, default: json.loads(value or "{}"),
    )

    assert scoped is not None and scoped["id"] == "i1"
    assert any_deck is not None and any_deck["id"] in {"i1", "i3"}
    assert (
        item_row_by_metadata_value(
            conn,
            deck_id="d1",
            item_type="word",
            key="external_id",
            value="",
            json_loads=lambda value, default: {},
        )
        is None
    )
