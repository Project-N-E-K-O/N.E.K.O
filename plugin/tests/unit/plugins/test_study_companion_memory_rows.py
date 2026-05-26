from __future__ import annotations

import json

import pytest

from plugin.plugins.study_companion.memory_rows import (
    card_from_joined_row,
    card_from_row,
    deck_from_row,
    item_from_joined_row,
    item_from_row,
    recitation_from_row,
    review_from_row,
    safe_int,
)

pytestmark = pytest.mark.unit


def _loads(value: object, default: object) -> object:
    try:
        return json.loads(str(value))
    except Exception:
        return default


def test_memory_row_helpers_handle_missing_rows_defaults_and_bad_ints() -> None:
    assert deck_from_row(None) is None
    assert item_from_row(None, _loads) is None
    assert card_from_row(None, _loads) is None
    assert review_from_row(None) is None
    assert recitation_from_row(None) is None
    assert safe_int("bad", 7) == 7


def test_memory_row_helpers_convert_deck_item_card_review_and_recitation() -> None:
    deck = deck_from_row(
        {
            "id": 123,
            "name": "Deck",
            "deck_type": "word",
            "subject": None,
            "language": "en",
            "source": "csv",
            "created_at": "c",
            "updated_at": "u",
        }
    )
    item = item_from_row(
        {
            "id": 123,
            "deck_id": "deck",
            "item_type": "word",
            "prompt": "front",
            "answer": "back",
            "metadata_json": '{"tags": ["x"]}',
            "fsrs_card_id": None,
            "status": "active",
            "created_at": "c",
            "updated_at": "u",
        },
        _loads,
    )
    card = card_from_row(
        {
            "id": "5",
            "item_id": 123,
            "card_data": '{"due": "now"}',
            "fsrs_state": None,
            "last_rating": "3",
            "updated_at": "u",
        },
        _loads,
    )
    review = review_from_row(
        {
            "id": 1,
            "item_id": 123,
            "rating": "4",
            "correct": 1,
            "elapsed_ms": None,
            "error_type": None,
            "reviewed_at": "r",
            "session_id": None,
        }
    )
    recitation = recitation_from_row(
        {
            "id": 2,
            "passage_item_id": 123,
            "review_record_id": None,
            "user_input_text": "text",
            "missing_count": "1",
            "extra_count": "2",
            "wrong_order_count": "3",
            "hint_count": "4",
            "score": "0.5",
            "reviewed_at": "r",
        }
    )

    assert deck is not None and deck["id"] == "123" and deck["item_count"] == 0
    assert item is not None and item["metadata"] == {"tags": ["x"]}
    assert card is not None and card["id"] == 5 and card["next_due"] == ""
    assert review is not None and review["correct"] is True
    assert recitation is not None and recitation["score"] == 0.5


def test_memory_joined_row_helpers_use_join_aliases() -> None:
    row = {
        "item_id": "item",
        "deck_id": "deck",
        "deck_name": "Deck",
        "deck_type": "word",
        "item_type": "word",
        "prompt": "front",
        "answer": "back",
        "metadata_json": "{}",
        "fsrs_card_id": 8,
        "status": "active",
        "item_created_at": "ic",
        "item_updated_at": "iu",
        "card_id": 9,
        "card_data": "{}",
        "fsrs_state": "review",
        "last_rating": 3,
        "card_updated_at": "cu",
    }

    assert item_from_joined_row(row, _loads)["created_at"] == "ic"
    assert card_from_joined_row(row, _loads)["id"] == 9
