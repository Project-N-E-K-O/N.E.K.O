from __future__ import annotations

from typing import Any


def active_item_card_rows(conn: Any, *, deck_id: str = "") -> list[Any]:
    params: list[Any] = []
    deck_clause = ""
    if deck_id:
        deck_clause = "AND mi.deck_id = ?"
        params.append(str(deck_id))
    return (
        conn.execute(
            f"""
            SELECT
                mi.id AS item_id,
                mi.deck_id AS deck_id,
                mi.item_type AS item_type,
                mi.prompt AS prompt,
                mi.answer AS answer,
                mi.metadata_json AS metadata_json,
                mi.fsrs_card_id AS fsrs_card_id,
                mi.status AS status,
                mi.created_at AS item_created_at,
                mi.updated_at AS item_updated_at,
                d.name AS deck_name,
                d.deck_type AS deck_type,
                mfc.id AS card_id,
                mfc.card_data AS card_data,
                mfc.fsrs_state AS fsrs_state,
                mfc.last_rating AS last_rating,
                mfc.next_due AS next_due,
                mfc.updated_at AS card_updated_at
            FROM memory_items mi
            JOIN decks d ON d.id = mi.deck_id
            JOIN memory_fsrs_cards mfc ON mfc.item_id = mi.id
            WHERE mi.status = 'active' {deck_clause}
            """,
            params,
        )
        .fetchall()
    )


def item_row_by_metadata_value(
    conn: Any,
    *,
    deck_id: str,
    item_type: str,
    key: str | tuple[str, ...],
    value: str,
    json_loads: Any,
) -> Any | None:
    target = str(value or "").strip()
    if not target:
        return None
    rows = conn.execute(
        """
        SELECT *
        FROM memory_items
        WHERE deck_id = ? AND item_type = ?
        """,
        (str(deck_id or ""), str(item_type or "")),
    ).fetchall()
    keys = (key,) if isinstance(key, str) else key
    for row in rows:
        metadata = json_loads(row["metadata_json"], {}) or {}
        if any(str(metadata.get(candidate) or "").strip() == target for candidate in keys):
            return row
    return None


__all__ = ["active_item_card_rows", "item_row_by_metadata_value"]
