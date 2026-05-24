from __future__ import annotations

from collections.abc import Callable
from typing import Any


JsonLoader = Callable[[object, Any], Any]


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def memory_counts(conn: Any, *, deck_id: str = "") -> dict[str, int]:
    params: list[Any] = []
    deck_predicate = ""
    if deck_id:
        deck_predicate = "WHERE deck_id = ?"
        params.append(deck_id)
    deck_count = conn.execute("SELECT COUNT(*) AS count FROM decks").fetchone()[
        "count"
    ]
    item_count = conn.execute(
        f"SELECT COUNT(*) AS count FROM memory_items {deck_predicate}",
        params,
    ).fetchone()["count"]
    card_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM memory_fsrs_cards mfc
        JOIN memory_items mi ON mi.id = mfc.item_id
        """
        + ("WHERE mi.deck_id = ?" if deck_id else ""),
        params,
    ).fetchone()["count"]
    review_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM review_records rr
        JOIN memory_items mi ON mi.id = rr.item_id
        """
        + ("WHERE mi.deck_id = ?" if deck_id else ""),
        params,
    ).fetchone()["count"]
    recitation_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM recitation_attempts ra
        JOIN memory_items mi ON mi.id = ra.passage_item_id
        """
        + ("WHERE mi.deck_id = ?" if deck_id else ""),
        params,
    ).fetchone()["count"]
    return {
        "deck_count": safe_int(deck_count, 0),
        "item_count": safe_int(item_count, 0),
        "card_count": safe_int(card_count, 0),
        "review_count": safe_int(review_count, 0),
        "recitation_count": safe_int(recitation_count, 0),
    }


def deck_from_row(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "name": str(row["name"] or ""),
        "deck_type": str(row["deck_type"] or ""),
        "subject": str(row["subject"] or ""),
        "language": str(row["language"] or ""),
        "source": str(row["source"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "item_count": safe_int(_optional_column(row, "item_count", 0), 0),
    }


def item_from_row(row: Any, json_loads: JsonLoader) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "deck_id": str(row["deck_id"] or ""),
        "deck_name": str(_optional_column(row, "deck_name", "") or ""),
        "deck_type": str(_optional_column(row, "deck_type", "") or ""),
        "item_type": str(row["item_type"] or ""),
        "prompt": str(row["prompt"] or ""),
        "answer": str(row["answer"] or ""),
        "metadata": json_loads(row["metadata_json"], {}),
        "fsrs_card_id": safe_int(row["fsrs_card_id"], 0),
        "status": str(row["status"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def item_from_joined_row(row: Any, json_loads: JsonLoader) -> dict[str, Any]:
    return {
        "id": str(row["item_id"]),
        "deck_id": str(row["deck_id"] or ""),
        "deck_name": str(row["deck_name"] or ""),
        "deck_type": str(row["deck_type"] or ""),
        "item_type": str(row["item_type"] or ""),
        "prompt": str(row["prompt"] or ""),
        "answer": str(row["answer"] or ""),
        "metadata": json_loads(row["metadata_json"], {}),
        "fsrs_card_id": safe_int(row["fsrs_card_id"], 0),
        "status": str(row["status"] or ""),
        "created_at": str(row["item_created_at"] or ""),
        "updated_at": str(row["item_updated_at"] or ""),
    }


def card_from_row(row: Any, json_loads: JsonLoader) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": safe_int(row["id"], 0),
        "item_id": str(row["item_id"] or ""),
        "card": json_loads(row["card_data"], {}),
        "fsrs_state": str(row["fsrs_state"] or ""),
        "last_rating": safe_int(row["last_rating"], 0),
        "next_due": str(_optional_column(row, "next_due", "") or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def card_from_joined_row(row: Any, json_loads: JsonLoader) -> dict[str, Any]:
    return {
        "id": safe_int(row["card_id"], 0),
        "item_id": str(row["item_id"] or ""),
        "card": json_loads(row["card_data"], {}),
        "fsrs_state": str(row["fsrs_state"] or ""),
        "last_rating": safe_int(row["last_rating"], 0),
        "next_due": str(_optional_column(row, "next_due", "") or ""),
        "updated_at": str(row["card_updated_at"] or ""),
    }


def review_from_row(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": safe_int(row["id"], 0),
        "item_id": str(row["item_id"] or ""),
        "rating": safe_int(row["rating"], 0),
        "correct": bool(row["correct"]),
        "elapsed_ms": safe_int(row["elapsed_ms"], 0),
        "error_type": str(row["error_type"] or ""),
        "reviewed_at": str(row["reviewed_at"] or ""),
        "session_id": str(row["session_id"] or ""),
    }


def recitation_from_row(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": safe_int(row["id"], 0),
        "passage_item_id": str(row["passage_item_id"] or ""),
        "review_record_id": safe_int(row["review_record_id"], 0),
        "user_input_text": str(row["user_input_text"] or ""),
        "missing_count": safe_int(row["missing_count"], 0),
        "extra_count": safe_int(row["extra_count"], 0),
        "wrong_order_count": safe_int(row["wrong_order_count"], 0),
        "hint_count": safe_int(row["hint_count"], 0),
        "score": float(row["score"] or 0.0),
        "reviewed_at": str(row["reviewed_at"] or ""),
    }


def _optional_column(row: Any, column: str, default: Any = None) -> Any:
    try:
        return row[column]
    except (KeyError, IndexError):
        return default


__all__ = [
    "card_from_joined_row",
    "card_from_row",
    "deck_from_row",
    "item_from_joined_row",
    "item_from_row",
    "memory_counts",
    "recitation_from_row",
    "review_from_row",
    "safe_int",
]
