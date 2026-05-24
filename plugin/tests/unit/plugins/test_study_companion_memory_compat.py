from __future__ import annotations

from datetime import datetime, timezone

import pytest

from plugin.plugins.study_companion.fsrs_bridge import FSRSBridge, create_card
from plugin.plugins.study_companion.memory_compat import compat_card_payload

pytestmark = pytest.mark.unit


def test_compat_card_payload_maps_memory_item_and_due_fsrs_card() -> None:
    card = create_card("legacy-topic", datetime(2026, 1, 1, tzinfo=timezone.utc))
    card.card_type = "memory"
    card.front = "front"
    card.back = "back"
    item = {
        "id": "item-1",
        "deck_id": "deck-1",
        "prompt": "front",
        "answer": "back",
        "metadata": {"legacy_topic_id": "legacy-topic", "tags": ["tag"], "source": "csv"},
        "created_at": "created",
        "updated_at": "updated",
    }

    payload = compat_card_payload(
        item,
        get_fsrs_card=lambda item_id: {"card": card.to_dict(), "last_rating": 3},
        fsrs=FSRSBridge(),
    )

    assert payload["id"] == "item-1"
    assert payload["topic_id"] == "legacy-topic"
    assert payload["front"] == "front"
    assert payload["back"] == "back"
    assert payload["tags"] == ["tag"]
    assert payload["last_rating"] == 3
    assert payload["card_type"] == "memory"
    assert payload["is_due"] is True
    assert payload["retrievability"] >= 0.0


def test_compat_card_payload_handles_missing_card_and_metadata() -> None:
    payload = compat_card_payload(
        {"id": "item-2", "prompt": "Q", "answer": "A"},
        get_fsrs_card=lambda item_id: None,
        fsrs=FSRSBridge(),
    )

    assert payload["topic_id"] == "item-2"
    assert payload["due"] == ""
    assert payload["is_due"] is False
    assert payload["retrievability"] == 0.0
    assert payload["tags"] == []


def test_compat_card_payload_falls_back_to_fsrs_card_faces() -> None:
    card = create_card("item-blank", datetime(2026, 1, 1, tzinfo=timezone.utc))
    card.front = "Fallback front"
    card.back = "Fallback back"

    payload = compat_card_payload(
        {"id": "item-blank", "prompt": "", "answer": ""},
        get_fsrs_card=lambda item_id: {"card": card.to_dict(), "last_rating": 0},
        fsrs=FSRSBridge(),
    )

    assert payload["front"] == "Fallback front"
    assert payload["back"] == "Fallback back"


def test_compat_card_payload_normalizes_string_tags() -> None:
    payload = compat_card_payload(
        {"id": "item-3", "metadata": {"tags": "math, science math"}},
        get_fsrs_card=lambda item_id: None,
        fsrs=FSRSBridge(),
    )

    assert payload["tags"] == ["math", "science"]
