from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from plugin.plugins.study_companion.knowledge_tracker import KnowledgeTracker, MasteryTracker, _difficulty_to_float
from plugin.plugins.study_companion.store import StudyStore


class _Logger:
    def warning(self, *args, **kwargs):
        return None


def _store(tmp_path: Path) -> StudyStore:
    seed = Path(__file__).resolve().parents[3] / "plugins" / "study_companion" / "static" / "knowledge_graph_seed.json"
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger(), seed)
    store.open()
    return store


def test_mastery_tracker_levels_confidence_and_false_mastery() -> None:
    tracker = MasteryTracker()
    assert tracker.get_level(0.10) == "未接触"
    assert tracker.get_level(0.35) == "薄弱"
    assert tracker.get_level(0.55) == "进行中"
    assert tracker.get_level(0.75) == "熟练"
    assert tracker.get_level(0.95) == "掌握"

    first = tracker.update("linear_equation", {"verdict": "correct", "difficulty": 0.5})
    repeated = tracker.update(
        "linear_equation",
        {"verdict": "correct", "difficulty": 0.5},
        recent_results=[{"verdict": "correct"} for _ in range(5)],
    )
    shaky = tracker.update(
        "linear_equation",
        {"verdict": "correct", "difficulty": 0.5},
        recent_results=[
            {"verdict": "correct"},
            {"verdict": "wrong"},
            {"verdict": "correct"},
            {"verdict": "wrong"},
            {"verdict": "correct"},
        ],
    )

    assert first.confidence < repeated.confidence
    assert repeated.mastery > first.mastery
    assert "false_mastery" in shaky.flags


def test_difficulty_integer_levels_are_scaled_from_one_to_five() -> None:
    assert _difficulty_to_float(1) == 0.2
    assert _difficulty_to_float("1") == 0.2
    assert _difficulty_to_float(3) == 0.6
    assert _difficulty_to_float(5) == 1.0
    assert _difficulty_to_float(0.5) == 0.5


def test_knowledge_tracker_on_answer_updates_mastery_wrong_question_and_fsrs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        tracker = KnowledgeTracker(store)
        result = tracker.on_answer(
            topic_id="quadratic_vertex_form",
            question={
                "question": "写出二次函数顶点式。",
                "answer": "y=a(x-h)^2+k",
                "topic": "二次函数顶点式",
                "difficulty": 3,
            },
            user_answer="y=a(x+h)^2+k",
            eval_result={"verdict": "wrong", "score": 20, "error_type": "sign_reversal"},
            mode="teaching",
        )

        assert result["mastery"]["topic_id"] == "quadratic_vertex_form"
        assert result["wrong_question_id"]
        assert store.get_latest_mastery("quadratic_vertex_form") is not None
        assert store.get_fsrs_card("quadratic_vertex_form") is not None
        assert store.list_wrong_questions(topic_id="quadratic_vertex_form")[0]["error_type"] == "sign_reversal"
        assert tracker.get_review_queue(limit=3)
    finally:
        store.close()


def test_wrong_question_resolves_after_three_delayed_correct_variants(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        tracker = KnowledgeTracker(store)
        wrong_id = tracker.on_answer(
            topic_id="linear_function_kb",
            question={"question": "k 表示什么？", "answer": "斜率", "difficulty": 3},
            user_answer="截距",
            eval_result={"verdict": "wrong", "score": 10, "error_type": "misunderstanding"},
            mode="interactive",
        )["wrong_question_id"]
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE wrong_questions SET last_error_at = datetime('now', '-2 days') WHERE id = ?",
                (wrong_id,),
            )

        for _ in range(3):
            tracker.on_answer(
                topic_id="linear_function_kb",
                question={"question": "k 的几何意义是什么？", "answer": "斜率", "difficulty": 3},
                user_answer="斜率",
                eval_result={"verdict": "correct", "score": 90, "error_type": "none"},
                mode="interactive",
            )

        resolved = store.list_wrong_questions(topic_id="linear_function_kb", statuses=("resolved",))
        assert resolved and resolved[0]["id"] == wrong_id
        assert resolved[0]["consecutive_correct"] >= 3
    finally:
        store.close()


def test_easy_integer_difficulty_does_not_resolve_wrong_question_as_hard_evidence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        tracker = KnowledgeTracker(store)
        wrong_id = tracker.on_answer(
            topic_id="linear_function_kb",
            question={"question": "k 表示什么？", "answer": "斜率", "difficulty": 3},
            user_answer="截距",
            eval_result={"verdict": "wrong", "score": 10, "error_type": "misunderstanding"},
            mode="interactive",
        )["wrong_question_id"]
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE wrong_questions SET last_error_at = datetime('now', '-2 days') WHERE id = ?",
                (wrong_id,),
            )

        for _ in range(3):
            tracker.on_answer(
                topic_id="linear_function_kb",
                question={"question": "k 是什么？", "answer": "斜率", "difficulty": 1},
                user_answer="斜率",
                eval_result={"verdict": "correct", "score": 90, "error_type": "none"},
                mode="interactive",
            )

        active = store.list_wrong_questions(topic_id="linear_function_kb", statuses=("retrying",))
        assert active and active[0]["id"] == wrong_id
        assert active[0]["max_correct_difficulty"] == 1
        assert store.list_wrong_questions(topic_id="linear_function_kb", statuses=("resolved",)) == []
    finally:
        store.close()


def test_generic_correct_answer_does_not_advance_unrelated_wrong_questions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        first_id = store.add_wrong_question(
            topic_id="linear_function_kb",
            question={"question": "k 表示什么？", "difficulty": 3},
            user_answer="截距",
            expected_answer="斜率",
            error_type="misunderstanding",
            verdict="wrong",
        )
        second_id = store.add_wrong_question(
            topic_id="linear_function_kb",
            question={"question": "b 表示什么？", "difficulty": 3},
            user_answer="斜率",
            expected_answer="截距",
            error_type="symbol_confusion",
            verdict="wrong",
        )
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE wrong_questions SET last_error_at = datetime('now', '-2 days') WHERE id IN (?, ?)",
                (first_id, second_id),
            )

        for _ in range(3):
            store.record_wrong_question_correct(
                topic_id="linear_function_kb",
                error_type="none",
                difficulty=3,
            )

        rows = store.list_wrong_questions(topic_id="linear_function_kb", statuses=("active", "retrying", "resolved"))
        by_id = {row["id"]: row for row in rows}
        resolved = [row for row in rows if row["status"] == "resolved"]
        untouched = [row for row in rows if row["consecutive_correct"] == 0]

        assert len(resolved) == 1
        assert len(untouched) == 1
        assert {first_id, second_id} == set(by_id)
    finally:
        store.close()


def test_knowledge_seed_loads_idempotently(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        first_count = store.count_topics()
        assert 120 <= first_count <= 150
        loaded_again = store.load_knowledge_seed()
        assert loaded_again == first_count
        assert store.count_topics() == first_count
    finally:
        store.close()


def test_knowledge_seed_and_topic_upsert_tolerate_bad_numeric_fields(tmp_path: Path) -> None:
    knowledge_seed = tmp_path / "bad_knowledge_seed.json"
    knowledge_seed.write_text(
        json.dumps(
            {
                "subject": "math",
                "topics": [
                    {
                        "id": "bad_numeric_topic",
                        "name": "Bad Numeric Topic",
                        "depth": "not-an-int",
                        "difficulty": "not-a-float",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger(), knowledge_seed)
    store.open()
    try:
        topic = store.get_topic("bad_numeric_topic")
        assert topic is not None
        assert topic["depth"] == 1
        assert topic["difficulty"] == 0.5

        store.upsert_topic(
            {
                "id": "bad_runtime_topic",
                "name": "Bad Runtime Topic",
                "depth": "still-not-an-int",
                "difficulty": "still-not-a-float",
            }
        )
        runtime_topic = store.get_topic("bad_runtime_topic")
        assert runtime_topic is not None
        assert runtime_topic["depth"] == 1
        assert runtime_topic["difficulty"] == 0.5
    finally:
        store.close()
