from __future__ import annotations

from pathlib import Path

import pytest

from plugin.plugins.study_companion.memory_deck_store import MemoryDeckStore
from plugin.plugins.study_companion.store import StudyStore

pytestmark = pytest.mark.unit


class _Logger:
    def warning(self, *args: object, **kwargs: object) -> None:
        return None


def _store(tmp_path: Path) -> StudyStore:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    return store


def test_integration_memory_review_round_trip_updates_fsrs_and_summary(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        memory = MemoryDeckStore(store)
        deck = memory.create_deck(name="Exam Deck", deck_type="word")
        imported = memory.import_words_json(
            deck_id=deck["id"],
            content='[{"word": "derive", "meaning": "obtain by reasoning"}]',
        )
        due_before = memory.due_reviews(limit=10)
        reviewed = memory.review_item(
            item_id=imported["items"][0]["id"],
            rating="good",
            correct=True,
            session_id="memory-session",
        )
        summary = memory.status_summary(limit=5)

        assert [item["item"]["prompt"] for item in due_before] == ["derive"]
        assert reviewed["rating"] == 3
        assert memory.get_fsrs_card(imported["items"][0]["id"])["last_rating"] == 3
        assert summary["deck_count"] == 1
        assert summary["item_count"] == 1
    finally:
        store.close()
