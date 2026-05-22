from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from plugin.plugins.study_companion.memory_deck_store import (
    MemoryDeckStore,
    build_cloze_prompt,
    diff_recitation,
    normalize_rating,
    rating_from_word_result,
    split_passage_text,
)
from plugin.plugins.study_companion.store import StudyStore

pytestmark = pytest.mark.unit


class _Logger:
    def warning(self, *args, **kwargs):
        return None


def _store(tmp_path: Path) -> StudyStore:
    seed = (
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "study_companion"
        / "static"
        / "knowledge_graph_seed.json"
    )
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger(), seed)
    store.open()
    return store


def test_memory_deck_crud_review_recitation_and_cascade(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        memory = MemoryDeckStore(store)
        deck = memory.create_deck(name="Exam Words", deck_type="word", language="en")
        word = memory.add_word(
            deck_id=deck["id"],
            word="abandon",
            meaning="give up",
            example_sentence="Do not abandon the plan.",
            tags=["exam"],
        )["item"]

        assert word["item_type"] == "word"
        assert word["metadata"]["example_sentence"] == "Do not abandon the plan."
        assert memory.get_fsrs_card(word["id"]) is not None
        assert store.get_fsrs_card(word["id"]) is None

        reviewed = memory.review_item(
            item_id=word["id"],
            rating="hard",
            correct=False,
            error_type="spelling",
            session_id="memory-session",
        )
        assert reviewed["rating"] == 2
        assert reviewed["review_record"]["session_id"] == "memory-session"

        passage_deck = memory.create_deck(name="Textbook", deck_type="passage")
        imported = memory.import_passage(
            deck_id=passage_deck["id"],
            title="Short Text",
            text="First sentence. Second sentence.\n\nThird sentence.",
        )
        attempt = memory.add_recitation_attempt(
            item_id=imported["items"][0]["id"],
            user_input_text="First sentence. Second.",
            hint_count=1,
        )
        assert attempt["attempt"]["missing_count"] > 0
        assert attempt["review"]["review_record"]["id"]

        deleted = memory.delete_deck(passage_deck["id"])
        assert deleted["deleted"] == 1
        with sqlite3.connect(store.db_path) as conn:
            counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "memory_items",
                    "memory_fsrs_cards",
                    "memory_review_log",
                    "review_records",
                    "recitation_attempts",
                )
            }
        assert counts["recitation_attempts"] == 0
        assert counts["memory_items"] == 1
        assert counts["memory_fsrs_cards"] == 1
    finally:
        store.close()


def test_memory_word_import_skips_bad_rows_and_dedupes_by_word(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        memory = MemoryDeckStore(store)
        deck = memory.create_deck(name="Vocabulary", deck_type="word")
        result = memory.import_words_csv(
            deck_id=deck["id"],
            content=(
                "word,meaning,example_sentence,tags\n"
                "cat,猫,A cat sleeps,animal\n"
                "bad row,,,\n"
                "cat,猫科动物,A cat runs,animal updated\n"
            ),
        )

        assert result["imported_count"] == 1
        assert result["updated_count"] == 1
        assert result["skipped_rows"] == [
            {"line": 3, "reason": "word and meaning are required"}
        ]
        items = memory.list_items(deck_id=deck["id"], limit=10)
        assert len(items) == 1
        assert items[0]["answer"] == "猫科动物"
        assert items[0]["metadata"]["example_sentence"] == "A cat runs"
    finally:
        store.close()


def test_memory_passage_split_recitation_diff_and_rating_mapping() -> None:
    chunks = split_passage_text("Alpha beta.\n\nGamma delta!")
    diff = diff_recitation("Alpha beta.", "Alpha zeta.", hint_count=2)

    assert [chunk["paragraph_index"] for chunk in chunks] == [1, 2]
    assert chunks[0]["sentences"] == ["Alpha beta."]
    assert diff["missing_count"] > 0
    assert diff["extra_count"] > 0
    assert diff["hint_count"] == 2
    assert rating_from_word_result("unknown_word").value == 1
    assert rating_from_word_result("spelling").value == 2
    assert rating_from_word_result("meaning_confused").value == 2
    assert rating_from_word_result("example_misunderstood").value == 3
    assert rating_from_word_result("correct", correct=True).value == 4


def test_memory_text_helpers_handle_empty_long_and_cloze_inputs() -> None:
    assert split_passage_text("") == []

    chunks = split_passage_text("A" * 5101)
    assert [len(chunk["text"]) for chunk in chunks] == [5000, 101]

    cloze = build_cloze_prompt("Remember important vocabulary.")
    assert cloze["answer"] == "Remember"
    assert "____" in cloze["prompt"]

    fallback = build_cloze_prompt("你好世界")
    assert fallback["answer"]
    assert "____" in fallback["prompt"]


def test_memory_rating_aliases() -> None:
    assert normalize_rating("again").value == 1
    assert normalize_rating("forgot").value == 1
    assert normalize_rating("hard").value == 2
    assert normalize_rating("good").value == 3
    assert normalize_rating("easy").value == 4
    assert normalize_rating(4).value == 4
    assert normalize_rating("unknown").value == 3


def test_memory_due_reviews_sort_by_deck_and_retrievability(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        memory = MemoryDeckStore(store)
        alpha = memory.create_deck(name="Alpha", deck_type="word")
        beta = memory.create_deck(name="Beta", deck_type="word")
        alpha_weak = memory.add_word(deck_id=alpha["id"], word="weak", meaning="low")[
            "item"
        ]
        alpha_strong = memory.add_word(
            deck_id=alpha["id"], word="strong", meaning="high"
        )["item"]
        beta_word = memory.add_word(deck_id=beta["id"], word="beta", meaning="b")[
            "item"
        ]

        with sqlite3.connect(store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            for item_id, stability in (
                (alpha_weak["id"], 0.2),
                (alpha_strong["id"], 5.0),
                (beta_word["id"], 0.1),
            ):
                row = conn.execute(
                    "SELECT card_data FROM memory_fsrs_cards WHERE item_id = ?",
                    (item_id,),
                ).fetchone()
                card = store._json_loads(row["card_data"], {})
                card["stability"] = stability
                card["last_review"] = "2026-01-01T00:00:00Z"
                card["due"] = "2026-01-02T00:00:00Z"
                conn.execute(
                    "UPDATE memory_fsrs_cards SET card_data = ? WHERE item_id = ?",
                    (store._json_dumps(card), item_id),
                )
            conn.commit()

        due = memory.due_reviews(limit=10)

        assert [item["item"]["prompt"] for item in due[:3]] == [
            "weak",
            "strong",
            "beta",
        ]
        assert due[0]["deck"]["name"] == "Alpha"
        assert due[0]["retrievability"] <= due[1]["retrievability"]
    finally:
        store.close()


def test_memory_due_reviews_include_low_retention_future_due_card(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        memory = MemoryDeckStore(store)
        deck = memory.create_deck(name="Retention", deck_type="word")
        item = memory.add_word(deck_id=deck["id"], word="fade", meaning="lose")[
            "item"
        ]

        future_due = "2099-01-01T00:00:00Z"
        with sqlite3.connect(store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT card_data FROM memory_fsrs_cards WHERE item_id = ?",
                (item["id"],),
            ).fetchone()
            card = store._json_loads(row["card_data"], {})
            card["stability"] = 0.1
            card["last_review"] = "2020-01-01T00:00:00Z"
            card["due"] = future_due
            conn.execute(
                """
                UPDATE memory_fsrs_cards
                SET card_data = ?, next_due = ?
                WHERE item_id = ?
                """,
                (store._json_dumps(card), future_due, item["id"]),
            )
            conn.commit()

        due = memory.due_reviews(limit=10)

        assert [review["item_id"] for review in due] == [item["id"]]
        assert due[0]["due"] == future_due
        assert due[0]["retrievability"] < 0.90
    finally:
        store.close()


def test_memory_drafts_do_not_create_formal_items(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        memory = MemoryDeckStore(store)
        deck = memory.create_deck(name="Draft Target", deck_type="word")
        draft = memory.create_word_draft(word="orbit", meaning="path around a body")

        assert draft["status"] == "candidate"
        assert draft["item_type"] == "memory_draft"
        assert memory.list_items(deck_id=deck["id"], limit=10) == []
        assert store.list_candidate_items(item_type="memory_draft", limit=10)
    finally:
        store.close()


def test_memory_json_import_export_compat_status_and_missing_review(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        memory = MemoryDeckStore(store)
        deck = memory.create_deck(name="JSON", deck_type="word")
        imported = memory.import_words_json(
            deck_id=deck["id"],
            content='[{"word": "dog", "meaning": "狗", "tags": ["pet", "Pet"]}]',
        )

        item = imported["items"][0]
        assert imported["imported_count"] == 1
        assert item["metadata"]["tags"] == ["pet"]

        compat = memory.compat_card_payload(item)
        assert compat["front"] == "dog"
        assert compat["card_type"] == "memory"
        assert compat["is_due"] is True

        exported = memory.export_deck_json(deck["id"])
        assert exported["deck"]["id"] == deck["id"]
        assert exported["items"][0]["prompt"] == "dog"

        summary = memory.status_summary(limit=5)
        assert summary["deck_count"] == 1
        assert summary["item_count"] == 1
        assert summary["due_count"] >= 1

        with pytest.raises(ValueError, match="memory item not found"):
            memory.review_item(item_id="missing", rating="good")
    finally:
        store.close()


def test_memory_recitation_error_draft_is_candidate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        memory = MemoryDeckStore(store)
        draft = memory.create_recitation_error_draft(
            expected="Alpha beta.", actual="Alpha zeta."
        )

        assert draft["status"] == "candidate"
        assert draft["item_type"] == "memory_draft"
        assert draft["payload"]["draft_type"] == "recitation_error"
        assert draft["payload"]["diff"]["wrong_count"] > 0
    finally:
        store.close()
