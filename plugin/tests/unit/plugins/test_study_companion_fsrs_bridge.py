from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from plugin.plugins.study_companion.fsrs_bridge import (
    FSRSBridge,
    StudyFsrsRating,
    create_card,
    get_due_reviews,
    rate_answer,
    retrievability,
)

pytestmark = pytest.mark.unit


def test_study_fsrs_rating_updates_stability_difficulty_and_due() -> None:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    card = create_card("quadratic_vertex_form", now)

    again, again_schedule = rate_answer(card, StudyFsrsRating.Again, now)
    hard, _ = rate_answer(card, StudyFsrsRating.Hard, now)
    good, _ = rate_answer(card, StudyFsrsRating.Good, now)
    easy, _ = rate_answer(card, StudyFsrsRating.Easy, now)

    assert again.stability < card.stability
    assert again.difficulty > card.difficulty
    assert again_schedule["rating"] == 1
    assert hard.stability > again.stability
    assert good.stability > hard.stability
    assert easy.stability > good.stability
    assert easy.difficulty < good.difficulty < hard.difficulty
    assert again.due < hard.due < good.due < easy.due


def test_study_fsrs_retrievability_and_due_sorting() -> None:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    fresh = create_card("fresh", now)
    fresh = fresh.__class__.from_dict(
        {**fresh.to_dict(), "due": (now + timedelta(days=2)).isoformat()}
    )
    old = create_card("old", now - timedelta(days=20))
    old = old.__class__.from_dict(
        {
            **old.to_dict(),
            "stability": 2.0,
            "due": (now - timedelta(days=1)).isoformat(),
        }
    )
    weak = create_card("weak", now - timedelta(days=8))
    weak = weak.__class__.from_dict(
        {
            **weak.to_dict(),
            "stability": 3.0,
            "due": (now + timedelta(days=2)).isoformat(),
        }
    )

    assert retrievability(fresh, now) == 1.0
    assert retrievability(old, now) < retrievability(weak, now)

    due = get_due_reviews([fresh, old, weak], now, retention_target=0.90)

    assert [item["topic_id"] for item in due] == ["old", "weak"]
    assert due[0]["priority"] > due[1]["priority"]


def test_study_fsrs_bridge_wraps_card_rating_and_due_review_methods() -> None:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    bridge = FSRSBridge(retention_target=0.90)
    card = bridge.new_knowledge_card("bridge_topic", now)

    updated, schedule = bridge.rate_answer(card, StudyFsrsRating.Good, now)
    due_card = updated.__class__.from_dict(
        {
            **updated.to_dict(),
            "due": (now - timedelta(days=1)).isoformat(),
            "last_review": (now - timedelta(days=10)).isoformat(),
            "stability": 2.0,
        }
    )
    reviews = bridge.get_due_reviews([due_card], now)

    assert schedule["topic_id"] == "bridge_topic"
    assert updated.reps == 1
    assert reviews and reviews[0]["topic_id"] == "bridge_topic"


def test_study_fsrs_preserves_memory_card_metadata_after_review() -> None:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    card = create_card("phase7_memory", now).__class__.from_dict(
        {
            **create_card("phase7_memory", now).to_dict(),
            "card_type": "memory",
            "front": "What does FSRS schedule?",
            "back": "Memory card reviews.",
            "source": "manual",
            "tags": ["phase7", "memory"],
        }
    )

    updated, schedule = rate_answer(card, StudyFsrsRating.Good, now + timedelta(days=1))

    assert schedule["topic_id"] == "phase7_memory"
    assert updated.card_type == "memory"
    assert updated.front == "What does FSRS schedule?"
    assert updated.back == "Memory card reviews."
    assert updated.source == "manual"
    assert updated.tags == ["phase7", "memory"]
